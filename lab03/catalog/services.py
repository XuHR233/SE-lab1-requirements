"""商品中心用例层（REQ-004/005/007/008/009）。

本层是商品操作的唯一业务入口：每个函数先判权限（角色 + 归属），再判状态，
被拒一律记 denied 日志后再抛异常；状态改动只能经 catalog/state.py 落地。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.permissions import can
from audit.models import RESULT_DENIED, record

from .models import (
    DELETED,
    DRAFT,
    OFFSHELF,
    PENDING,
    PRESALE,
    PUBLISHED,
    REJECTED,
    TRADING,
    VIOLATED,
    AuditRecord,
    Product,
)
from .state import StateConflict, can_transit, transit

# 可改的内容字段；is_presale 参与内容版本，因为它决定审核通过后落哪个状态。
CONTENT_FIELDS = ("title", "description", "price", "category", "brand", "is_presale")

# 只有这两个状态还能改内容：待审核要改就先撤回，上架中要改走 revise_content（REQ-007）。
EDITABLE_STATUSES = (DRAFT, REJECTED)


class InvalidContent(Exception):
    """必填项缺失或参数不合法。"""


class VersionConflict(Exception):
    """旧页面提交，库里的版本已经不是它看到的那个（REQ-007 的 ABA）。"""


def _deny(actor: object, action: str, message: str, *, product: Product | None = None) -> None:
    """权限或归属被拒：先记 denied 日志，再抛 403（REQ-002/026）。"""
    record(
        action=action,
        result=RESULT_DENIED,
        actor=actor,
        target_type="product",
        target_id=getattr(product, "pk", "") or "",
        detail={"reason": message},
    )
    raise PermissionDenied(message)


def _deny_transit(product: Product, actor: object, target: str, message: str) -> None:
    """状态机拒绝一次转换并记日志（REQ-010 异常：被拒的转换要留下记录）。"""
    record(
        action="product.transit",
        result=RESULT_DENIED,
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"from": product.status, "to": target, "reason": message},
    )
    raise StateConflict(message)


def _as_price(value: object) -> Decimal:
    """价格必须是正的有限数字，否则拒绝（REQ-004）。"""
    try:
        price = Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidContent("价格必须是数字") from exc
    if not price.is_finite() or price <= 0:
        raise InvalidContent("价格必须大于 0")
    return price


def _check_fields(  # pylint: disable=too-many-arguments
    *,
    title: object,
    price: object,
    category_id: int | None,
    has_images: bool,
) -> None:
    """提交审核与审核通过前的内容校验（REQ-004/005）。"""
    if not str(title or "").strip():
        raise InvalidContent("标题不能为空")
    _as_price(price)
    if category_id is None:
        raise InvalidContent("必须选择分类")
    if not has_images:
        raise InvalidContent("至少上传一张图片")


def _check_submittable(product: Product) -> None:
    """用一个商品对象跑内容校验。"""
    _check_fields(
        title=product.title,
        price=product.price,
        category_id=product.category_id,
        has_images=product.has_images(),
    )


def _candidate(product: Product, updates: dict[str, object]) -> dict[str, object]:
    """把本次改动叠到当前值上，用于「改之前」校验，避免脏值先写进库。"""
    if "category" in updates:
        category = updates["category"]
        category_id = None if category is None else category.pk  # type: ignore[attr-defined]
    else:
        category_id = product.category_id
    return {
        "title": updates.get("title", product.title),
        "price": updates.get("price", product.price),
        "category_id": category_id,
        "has_images": product.has_images(),
    }


def _db_updates(updates: dict[str, object]) -> dict[str, object]:
    """把业务字段翻译成 UPDATE 用的列名（category 走 category_id）。"""
    columns: dict[str, object] = {}
    for key, value in updates.items():
        if key == "category":
            columns["category_id"] = None if value is None else value.pk  # type: ignore[attr-defined]
        elif key == "price":
            columns["price"] = _as_price(value)
        elif key == "is_presale":
            columns["is_presale"] = bool(value)
        elif key in ("title", "brand"):
            columns[key] = str(value).strip()
        elif key == "description":
            columns[key] = str(value)
        else:
            raise InvalidContent(f"字段 {key} 不能修改")
    return columns


def _pick_content(fields: dict[str, object]) -> dict[str, object]:
    """只保留内容白名单里的字段，其余一律忽略。"""
    return {key: value for key, value in fields.items() if key in CONTENT_FIELDS}


@dataclass(frozen=True)
class DraftInput:
    """创建草稿需要的字段，避免函数签名过长。"""

    seller: object
    category: object
    title: str
    price: Decimal
    description: str = ""
    brand: str = ""
    is_presale: bool = False


def save_draft(data: DraftInput) -> Product:
    """保存为草稿；草稿不对外可见、不可被下单（REQ-004）。"""
    if not can(data.seller, "product.create"):
        _deny(data.seller, "product.create", "只有买家/卖家账号可以发布商品")
    title = str(data.title or "").strip()
    if not title:
        raise InvalidContent("标题不能为空")
    if len(title) > 120:
        raise InvalidContent("标题不能超过 120 字")
    return Product.objects.create(
        seller=data.seller,
        category=data.category,
        title=title,
        price=_as_price(data.price),
        description=data.description,
        brand=data.brand,
        is_presale=bool(data.is_presale),
        status=DRAFT,
    )


def update_draft(
    product: Product,
    *,
    actor: object,
    expected_version: int | None = None,
    **fields: object,
) -> Product:
    """草稿/已驳回改内容：已驳回改稿后回到草稿（状态图二 S3→S1）。

    草稿阶段允许内容不完整（还没传图片），所以这里只校验标题与价格。
    """
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以改稿", product=product)
    if product.status not in EDITABLE_STATUSES:
        _deny_transit(product, actor, DRAFT, f"{product.status} 状态不允许改稿")
    updates = _pick_content(fields)
    if not updates:
        raise InvalidContent("没有可修改的字段")
    candidate = _candidate(product, updates)
    if not str(candidate["title"] or "").strip():
        raise InvalidContent("标题不能为空")
    _as_price(candidate["price"])
    columns = _db_updates(updates)
    columns["status"] = DRAFT
    filters: dict[str, object] = {"pk": product.pk, "status__in": EDITABLE_STATUSES}
    if expected_version is not None:
        filters["version"] = expected_version
    updated = Product.objects.filter(**filters).update(
        version=F("version") + 1, updated_at=timezone.now(), **columns
    )
    if updated != 1:
        if expected_version is not None:
            raise VersionConflict("草稿已被修改，请刷新后重试")
        _deny_transit(product, actor, DRAFT, "商品状态已变更，无法改稿")
    product.refresh_from_db()
    return product


def add_image(product: Product, url: str, *, actor: object) -> None:
    """为草稿/已驳回的商品补一张图片，提交审核前至少要有一张（REQ-004）。"""
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以维护商品图片", product=product)
    if product.status not in EDITABLE_STATUSES:
        _deny_transit(product, actor, DRAFT, f"{product.status} 状态不能改图片")
    if not str(url or "").strip():
        raise InvalidContent("图片地址不能为空")
    product.images.create(url=str(url).strip())


def submit_for_audit(product: Product, *, actor: object) -> Product:
    """草稿/已驳回→待审核，并开一条待审流水。

    待审核期间重复提交是幂等的，不会堆出第二条记录（REQ-004）。
    """
    if product.status == PENDING:
        return product
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以提交审核", product=product)
    if not can_transit(product.status, PENDING):
        _deny_transit(product, actor, PENDING, f"当前状态 {product.status} 不能提交审核")
    _check_submittable(product)
    with transaction.atomic():
        transit(product, PENDING)
        AuditRecord.objects.create(product=product, submitted_version=product.version)
    record(
        action="product.submit",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
    )
    return product


def _open_audit(product: Product) -> AuditRecord:
    """取这次提交对应的待审流水；商品若是直接塞进库的就补一条。"""
    pending = (
        AuditRecord.objects.filter(
            product=product, approve__isnull=True, withdrawn_at__isnull=True
        )
        .order_by("-id")
        .first()
    )
    if pending is None:
        return AuditRecord.objects.create(product=product, submitted_version=product.version)
    return pending


def _close_open_audit(product: Product, reason: str) -> None:
    """关闭还没结论的待审流水（卖家撤回提交），不再算「等审核」。"""
    AuditRecord.objects.filter(
        product=product, approve__isnull=True, withdrawn_at__isnull=True
    ).update(withdrawn_at=timezone.now(), reason=reason)


def audit(
    product: Product,
    *,
    auditor: object,
    approve: bool,
    reason: str = "",
) -> Product:
    """待审核→已发布/已预售（通过）或已驳回（驳回必填理由）。"""
    if product.status != PENDING:
        target = REJECTED if not approve else (PRESALE if product.is_presale else PUBLISHED)
        _deny_transit(product, auditor, target, "该商品状态已变更，无法审核")
    if not can(auditor, "product.audit", owner_id=product.seller_id):
        _deny(auditor, "product.audit", "无权审核该商品", product=product)
    if not approve and not str(reason or "").strip():
        raise InvalidContent("驳回必须填写理由")
    if approve:
        try:
            _check_submittable(product)
        except InvalidContent as exc:
            record(
                action="product.audit",
                result=RESULT_DENIED,
                actor=auditor,
                target_type="product",
                target_id=product.pk,
                detail={"reason": str(exc)},
            )
            raise
    with transaction.atomic():
        pending = _open_audit(product)
        if approve:
            transit(product, PRESALE if product.is_presale else PUBLISHED)
        else:
            transit(product, REJECTED)
        pending.auditor = auditor
        pending.approve = approve
        pending.reason = str(reason or "").strip()
        pending.audited_at = timezone.now()
        pending.save(update_fields=["auditor", "approve", "reason", "audited_at"])
    return product


def revise_content(
    product: Product,
    *,
    actor: object,
    expected_version: int,
    **fields: object,
) -> Product:
    """已发布/已预售→待审核：内容改动、版本号 +1、开待审流水（REQ-007）。

    版本号比较与内容写入都在同一条 UPDATE 的条件里（version=expected_version），
    因此旧页面提交时即使商品还停在已发布，也一定被拒，不会覆盖新内容。
    """
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以修改", product=product)
    if product.status not in (PUBLISHED, PRESALE):
        _deny_transit(product, actor, PENDING, "当前状态不允许修改内容")
    updates = _pick_content(fields)
    if not updates:
        raise InvalidContent("没有可修改的字段")
    candidate = _candidate(product, updates)
    _check_fields(
        title=candidate["title"],
        price=candidate["price"],
        category_id=candidate["category_id"],  # type: ignore[arg-type]
        has_images=candidate["has_images"],  # type: ignore[arg-type]
    )
    columns = _db_updates(updates)
    with transaction.atomic():
        updated = (
            Product.objects.filter(
                pk=product.pk,
                status__in=(PUBLISHED, PRESALE),
                version=expected_version,
            ).update(
                status=PENDING,
                version=F("version") + 1,
                updated_at=timezone.now(),
                **columns,
            )
        )
        if updated != 1:
            raise VersionConflict("商品已被修改，请刷新后重试")
        AuditRecord.objects.create(product=product, submitted_version=expected_version + 1)
    product.refresh_from_db()
    record(
        action="product.revise",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"version": product.version},
    )
    return product


def unpublish(product: Product, *, actor: object) -> Product:
    """卖家主动下架：只有已发布/已预售能下架（状态图一 S4/S5→S8，状态图二 SLOCK 注）。

    待付款锁定与交易中都不给卖家这条转换：前者要先由买家或卖家取消订单放货，
    后者只能由客服或管理员裁定终止。
    """
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以主动下架", product=product)
    if product.status not in (PUBLISHED, PRESALE):
        _deny_transit(product, actor, OFFSHELF, f"{product.status} 状态不允许卖家主动下架")
    return transit(product, OFFSHELF)


def back_to_draft(product: Product, *, actor: object) -> Product:
    """已下架/违规下架/已驳回/待审核→草稿，重新上架必须再走一遍审核（REQ-008/009）。"""
    if not can(actor, "product.edit", owner_id=product.seller_id):
        _deny(actor, "product.edit", "只有卖家本人可以退回草稿", product=product)
    if not can_transit(product.status, DRAFT):
        _deny_transit(product, actor, DRAFT, f"{product.status} 状态不能退回草稿")
    with transaction.atomic():
        if product.status == PENDING:
            _close_open_audit(product, "卖家撤回提交")
        transit(product, DRAFT)
    return product


def takedown(product: Product, *, actor: object, reason: str) -> Product:
    """管理员违规下架：已发布/已预售/待付款锁定/已下架→违规下架（REQ-009）。"""
    if not can(actor, "product.takedown"):
        _deny(actor, "product.takedown", "无权执行违规下架", product=product)
    if not str(reason or "").strip():
        raise InvalidContent("违规下架必须填写理由")
    if not can_transit(product.status, VIOLATED):
        _deny_transit(product, actor, VIOLATED, f"{product.status} 状态不允许违规下架")
    transit(product, VIOLATED)
    record(
        action="product.takedown",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"reason": str(reason).strip()},
    )
    return product


def terminate_trade(product: Product, *, actor: object, reason: str) -> Product:
    """交易中→已下架：状态层原语，只做「裁定终止」这一步（状态图一 S6→S8）。

    前置条件（订单属于该商品、协商或举证已核实、退款已按 REQ-016 走出结果）
    由 M4 的订单与退款流程负责；Day 2 不伪造这些条件，因此本函数只是原语，
    不是可以直接对外暴露的闭环业务动作。卖家没有这条转换（状态图二 SLOCK 注）。
    """
    if not can(actor, "order.refund"):
        _deny(actor, "order.refund", "只有客服或管理员可以裁定终止交易", product=product)
    if not str(reason or "").strip():
        raise InvalidContent("裁定终止必须填写理由")
    if product.status != TRADING:
        _deny_transit(product, actor, OFFSHELF, "只有交易中的商品可以裁定终止")
    transit(product, OFFSHELF)
    record(
        action="product.terminate",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"reason": str(reason).strip()},
    )
    return product


def delete_draft(product: Product, *, actor: object) -> Product:
    """草稿→已删除：只有草稿能删（状态图二 S1→S10）。

    删除是逻辑删除，行留在库里，历史订单与日志仍可追溯（状态图二 S10 注）。
    """
    if not can(actor, "product.delete", owner_id=product.seller_id):
        _deny(actor, "product.delete", "只有卖家本人可以删除草稿", product=product)
    if product.status != DRAFT:
        _deny_transit(product, actor, DELETED, "只有草稿可以删除")
    transit(product, DELETED)
    record(
        action="product.delete",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"from": DRAFT},
    )
    return product
