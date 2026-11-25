"""商品检索与可见性（REQ-003/011/012/024）。

两种口径必须分开：
- 列表/检索：所有角色都只看已发布与已预售，已售出永不出现（REQ-012）。
- 详情：游客只看已发布/已预售，卖家看自己的任意状态，客服与管理员看任意状态。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import F, Max, Q, QuerySet

from accounts.models import ROLE_GUEST
from accounts.permissions import STAFF_ROLES, role_of

from .models import PRESALE, PUBLISHED, Product

SORT_CHOICES: dict[str, tuple[str, ...]] = {
    "price_asc": ("price", "id"),
    "price_desc": ("-price", "-id"),
}
PAGE_SIZE = 12
PUBLIC_STATUSES = (PUBLISHED, PRESALE)


def _reserved_ids() -> list[int]:
    """有有效预约的预售商品：锁货期间从所有人的列表与搜索里消失（REQ-003/006）。"""
    from reservations.models import (  # pylint: disable=import-outside-toplevel
        active_product_ids,
    )

    return list(active_product_ids())


def listable() -> QuerySet[Product]:
    """列表与检索的商品集合：只有已预售且未被预约的已发布商品，已售出永不出现。"""
    return Product.objects.filter(status__in=PUBLIC_STATUSES).exclude(pk__in=_reserved_ids())


def visible_to(user: object) -> QuerySet[Product]:
    """详情口径：能打开哪些商品的详情页（REQ-003/012）。

    非公开商品只允许卖家、当前订单或预约的买家及工作人员查看当前详情。
    历史买家从自己的业务单据读取快照，不继承当前详情或图片权限。
    """
    products = Product.objects.all()
    role = role_of(user)
    if role in STAFF_ROLES:
        return products
    public = products.filter(status__in=PUBLIC_STATUSES).exclude(pk__in=_reserved_ids())
    if role == ROLE_GUEST:
        return public
    party = getattr(user, "pk", None)
    party_scope = (
        Q(seller_id=party)
    )
    from reservations.models import ACTIVE_STATUSES as ACTIVE_RESERVATIONS  # pylint: disable=import-outside-toplevel
    from trade.models import ACTIVE_STATUSES as ACTIVE_ORDERS  # pylint: disable=import-outside-toplevel

    party_scope |= Q(orders__buyer_id=party, orders__status__in=ACTIVE_ORDERS)
    party_scope |= Q(reservations__buyer_id=party, reservations__status__in=ACTIVE_RESERVATIONS)
    return products.filter(Q(pk__in=public.values_list("pk", flat=True)) | party_scope).distinct()


def _keyword_filter(keyword: str) -> Q:
    """多关键词之间是「或」，每个词在标题/品牌/描述/卖家名里模糊匹配（REQ-011）。"""
    words = [word for word in keyword.split() if word]
    query = Q(pk__in=[])
    for word in words:
        query |= (
            Q(title__icontains=word)
            | Q(brand__icontains=word)
            | Q(description__icontains=word)
            | Q(seller__username__icontains=word)
        )
    return query


def _price_bounds(
    price_min: str | None, price_max: str | None
) -> tuple[Decimal | None, Decimal | None]:
    try:
        low = Decimal(price_min) if price_min not in (None, "") else None
        high = Decimal(price_max) if price_max not in (None, "") else None
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("价格区间必须是数字") from exc
    if low is not None and low < 0:
        raise ValueError("价格区间不能为负数")
    if low is not None and high is not None and low > high:
        raise ValueError("价格区间下限不能大于上限")
    return low, high


def _publication_filters(products, status, period):
    products = products.annotate(published_at=Max("audits__audited_at",
                                                  filter=Q(audits__approve=True)))
    if status:
        if status not in PUBLIC_STATUSES:
            return products.none()
        products = products.filter(status=status)
    start, end = period
    if start and end and start >= end:
        raise ValueError("发布时间起点必须早于终点")
    if start:
        products = products.filter(published_at__gte=start)
    if end:
        products = products.filter(published_at__lt=end)
    return products


def search(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    keyword: str | None = None,
    category_id: int | None = None,
    tag: str | None = None,
    price_min: str | None = None,
    price_max: str | None = None,
    sort: str = "newest",
    page: int = 1,
    status=None,
    published_period=(None, None),
) -> QuerySet[Product]:
    """多条件检索：条件之间是「与」，多关键词之间是「或」（REQ-011/012）。"""
    products = _publication_filters(listable(), status, published_period)
    keyword = (keyword or "").strip()
    if keyword:
        products = products.filter(_keyword_filter(keyword))
    if category_id:
        products = products.filter(category_id=category_id)
    if tag:
        products = products.filter(tags__name=tag).distinct()
    low, high = _price_bounds(price_min, price_max)
    if low is not None:
        products = products.filter(price__gte=low)
    if high is not None:
        products = products.filter(price__lte=high)
    if sort in SORT_CHOICES:
        products = products.order_by(*SORT_CHOICES[sort])
    elif sort == "oldest":
        products = products.order_by(F("published_at").asc(nulls_last=True), "id")
    else:
        products = products.order_by(F("published_at").desc(nulls_last=True), "-id")
    current = page if isinstance(page, int) and page > 0 else 1
    offset = (current - 1) * PAGE_SIZE
    return products[offset : offset + PAGE_SIZE]
