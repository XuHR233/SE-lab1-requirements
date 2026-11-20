"""商品中心用例层（REQ-004/005/007/008/009）。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.permissions import can
from audit.models import record

from .models import (
    DRAFT,
    OFFSHELF,
    PENDING,
    PRESALE,
    PUBLISHED,
    REJECTED,
    VIOLATED,
    AuditRecord,
    Product,
)
from .state import StateConflict, transit

CONTENT_FIELDS = ("title", "description", "price", "category", "brand")


class InvalidContent(Exception):
    """必填项缺失或参数不合法。"""


class VersionConflict(Exception):
    """旧页面提交，库里的版本已经不是它看到的那个。"""


@dataclass(frozen=True)
class DraftInput:
    """创建草稿需要的字段，避免函数签名过长。"""

    seller: object
    category: object
    title: str
    price: Decimal
    description: str = ""
    brand: str = ""


def save_draft(data: DraftInput) -> Product:
    """保存为草稿；草稿不对外可见、不可被下单（REQ-004）。"""
    if not str(data.title).strip():
        raise InvalidContent("标题不能为空")
    if Decimal(data.price) <= 0:
        raise InvalidContent("价格必须大于 0")
    return Product.objects.create(
        seller=data.seller,
        category=data.category,
        title=data.title.strip(),
        price=data.price,
        description=data.description,
        brand=data.brand,
        status=DRAFT,
    )


def add_image(product: Product, url: str) -> None:
    """补一张图片，提交审核前至少要有一张。"""
    product.images.create(url=url)


def _check_submittable(product: Product) -> None:
    if not str(product.title).strip():
        raise InvalidContent("标题不能为空")
    if product.price is None or Decimal(product.price) <= 0:
        raise InvalidContent("价格必须大于 0")
    if product.category_id is None:
        raise InvalidContent("必须选择分类")
    if not product.has_images():
        raise InvalidContent("至少上传一张图片")


def submit_for_audit(product: Product, *, actor: object) -> Product:
    """草稿/已驳回→待审核，并开一条待审流水。

    待审核期间重复提交是幂等的，不会堆出第二条记录（REQ-004）。
    """
    if product.status == PENDING:
        return product
    if not can(actor, "product.edit", owner_id=product.seller_id):
        raise StateConflict("只有卖家本人可以提交审核")
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
        AuditRecord.objects.filter(product=product, approve__isnull=True).order_by("-id").first()
    )
    if pending is None:
        return AuditRecord.objects.create(product=product, submitted_version=product.version)
    return pending


def audit(
    product: Product,
    *,
    auditor: object,
    approve: bool,
    reason: str = "",
) -> Product:
    """待审核→已发布/已预售（通过）或已驳回（驳回必填理由）。"""
    if product.status != PENDING:
        raise StateConflict("该商品状态已变更，无法审核")
    if not can(auditor, "product.audit", owner_id=product.seller_id):
        raise StateConflict("无权审核该商品")
    if not approve and not reason.strip():
        raise InvalidContent("驳回必须填写理由")
    with transaction.atomic():
        pending = _open_audit(product)
        if approve:
            transit(product, PRESALE if product.is_presale else PUBLISHED)
        else:
            transit(product, REJECTED)
        pending.auditor = auditor
        pending.approve = approve
        pending.reason = reason.strip()
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
    """已发布/已预售→待审核。版本号不一致就拒绝，不覆盖别人的修改（REQ-007）。"""
    if product.status not in (PUBLISHED, PRESALE):
        raise StateConflict("当前状态不允许修改内容")
    if not can(actor, "product.edit", owner_id=product.seller_id):
        raise StateConflict("只有卖家本人可以修改")
    if expected_version != product.version:
        raise VersionConflict("商品已被修改，请刷新后重试")
    updates = {key: value for key, value in fields.items() if key in CONTENT_FIELDS}
    if not updates:
        raise InvalidContent("没有可修改的字段")
    with transaction.atomic():
        for key, value in updates.items():
            setattr(product, key, value)
        product.save(update_fields=[*updates, "updated_at"])
        transit(product, PENDING, bump_version=True)
    return product


def unpublish(product: Product, *, actor: object) -> Product:
    """卖家主动下架：已发布/已预售→已下架（REQ-008）。"""
    if not can(actor, "product.edit", owner_id=product.seller_id):
        raise StateConflict("只有卖家本人可以下架")
    return transit(product, OFFSHELF)


def back_to_draft(product: Product, *, actor: object) -> Product:
    """已下架/违规下架/已驳回→草稿。重新上架必须再走一遍审核（REQ-008/009）。"""
    if not can(actor, "product.edit", owner_id=product.seller_id):
        raise StateConflict("只有卖家本人可以退回草稿")
    return transit(product, DRAFT)


def takedown(product: Product, *, actor: object, reason: str) -> Product:
    """管理员违规下架：已发布/已预售/待付款锁定/已下架→违规下架（REQ-009）。"""
    if not can(actor, "product.takedown"):
        raise StateConflict("无权执行违规下架")
    if not reason.strip():
        raise InvalidContent("违规下架必须填写理由")
    transit(product, VIOLATED)
    record(
        action="product.takedown",
        result="allowed",
        actor=actor,
        target_type="product",
        target_id=product.pk,
        detail={"reason": reason.strip()},
    )
    return product
