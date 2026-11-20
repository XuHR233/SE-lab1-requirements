"""商品检索与可见性（REQ-003/011/012/024）。"""

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


def visible_to(user: object) -> QuerySet[Product]:
    """能看见哪些商品（REQ-003/012）：游客只看已发布/已预售，卖家额外看自己的。"""
    products = Product.objects.all()
    role = role_of(user)
    if role in STAFF_ROLES:
        return products
    if role == ROLE_GUEST:
        return products.filter(status__in=PUBLIC_STATUSES)
    return products.filter(Q(status__in=PUBLIC_STATUSES) | Q(seller_id=user.pk))


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
    user: object = None,
    keyword: str | None = None,
    category_id: int | None = None,
    tag: str | None = None,
    price_min: str | None = None,
    price_max: str | None = None,
    sort: str = "newest",
    page: int = 1,
) -> QuerySet[Product]:
    """多条件检索：条件之间是「与」，多关键词之间是「或」（REQ-011）。"""
    products = visible_to(user)
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
