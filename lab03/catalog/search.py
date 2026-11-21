"""商品检索与可见性（REQ-003/011/012/024）。

两种口径必须分开：
- 列表/检索：所有角色都只看已发布与已预售，已售出永不出现（REQ-012）。
- 详情：游客只看已发布/已预售，卖家看自己的任意状态，客服与管理员看任意状态。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Q, QuerySet

from accounts.models import ROLE_GUEST
from accounts.permissions import STAFF_ROLES, role_of

from .models import PRESALE, PUBLISHED, Product

SORT_CHOICES: dict[str, tuple[str, ...]] = {
    "newest": ("-created_at", "-id"),
    "oldest": ("created_at", "id"),
    "price_asc": ("price", "id"),
    "price_desc": ("-price", "-id"),
}
PAGE_SIZE = 12
PUBLIC_STATUSES = (PUBLISHED, PRESALE)


def listable() -> QuerySet[Product]:
    """列表与检索的商品集合：只有已发布/已预售，不看角色，已售出永不出现。"""
    return Product.objects.filter(status__in=PUBLIC_STATUSES)


def visible_to(user: object) -> QuerySet[Product]:
    """详情口径：能打开哪些商品的详情页（REQ-003/012）。

    已售出的商品不在任何人的列表里，但对应订单的买卖双方可以凭订单进详情，
    管理员与客服可按职责查看任意状态；那一部分要等 M4 的订单表，Day 2 先按角色。
    """
    products = Product.objects.all()
    role = role_of(user)
    if role in STAFF_ROLES:
        return products
    if role == ROLE_GUEST:
        return products.filter(status__in=PUBLIC_STATUSES)
    return products.filter(Q(status__in=PUBLIC_STATUSES) | Q(seller_id=user.pk))  # type: ignore[attr-defined]


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


def search(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    keyword: str | None = None,
    category_id: int | None = None,
    tag: str | None = None,
    price_min: str | None = None,
    price_max: str | None = None,
    sort: str = "newest",
    page: int = 1,
) -> QuerySet[Product]:
    """多条件检索：条件之间是「与」，多关键词之间是「或」（REQ-011/012）。"""
    products = listable()
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
    products = products.order_by(*SORT_CHOICES.get(sort, SORT_CHOICES["newest"]))
    current = page if isinstance(page, int) and page > 0 else 1
    offset = (current - 1) * PAGE_SIZE
    return products[offset : offset + PAGE_SIZE]
