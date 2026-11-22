"""商品状态机（REQ-010）：11 个状态、25 条允许转换，其余一律拒绝。"""

from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from common.errors import Conflict

from .models import (
    DELETED,
    DRAFT,
    LOCKED,
    OFFSHELF,
    PENDING,
    PRESALE,
    PUBLISHED,
    REJECTED,
    SOLD,
    TRADING,
    VIOLATED,
    Product,
)

# REQ-010 原文的 25 条转换，一条不多一条不少。
PRODUCT_TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({PENDING, DELETED}),
    PENDING: frozenset({PUBLISHED, PRESALE, REJECTED, DRAFT}),
    REJECTED: frozenset({DRAFT}),
    PUBLISHED: frozenset({PENDING, OFFSHELF, LOCKED, VIOLATED}),
    PRESALE: frozenset({PENDING, OFFSHELF, TRADING, VIOLATED}),
    LOCKED: frozenset({TRADING, PUBLISHED, VIOLATED}),
    TRADING: frozenset({SOLD, PUBLISHED, PRESALE, OFFSHELF}),
    SOLD: frozenset(),
    OFFSHELF: frozenset({DRAFT, VIOLATED}),
    VIOLATED: frozenset({DRAFT}),
    DELETED: frozenset(),
}

ALLOWED_PAIRS: frozenset[tuple[str, str]] = frozenset(
    (source, target)
    for source, targets in PRODUCT_TRANSITIONS.items()
    for target in targets
)


class StateConflict(Conflict):
    """状态转换不被允许，或并发下已被别人改掉。"""


def can_transit(source: str, target: str) -> bool:
    """只判断表，不碰数据库。"""
    return (source, target) in ALLOWED_PAIRS


def transit(product: Product, target: str, *, bump_version: bool = False) -> Product:
    """把商品推到目标状态。表里没有这条转换就拒绝。

    用带 status 条件的 UPDATE 落地：并发下只有把状态从旧值推走的那一次
    rowcount 为 1，其余为 0，因此同一状态不会被两次转换同时推走。
    """
    source = product.status
    if not can_transit(source, target):
        raise StateConflict(f"商品状态不允许从 {source} 变为 {target}")
    fields: dict[str, object] = {"status": target, "updated_at": timezone.now()}
    if bump_version:
        fields["version"] = F("version") + 1
    with transaction.atomic():
        updated = Product.objects.filter(pk=product.pk, status=source).update(**fields)
    if updated != 1:
        raise StateConflict(f"商品 {product.pk} 当前状态已不是 {source}")
    product.refresh_from_db()
    return product
