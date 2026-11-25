"""预售预约用例层（M3）：预约、定金、卖家确认、确认到货、尾款、取消与超时清理。
# pylint: disable=duplicate-code
# 理由：与 trade.services 的 _deny/_allow 是同一套「先记日志再抛/再返回」形状，
# 差异只在 target_type；抽公共函数要跨模块传模型类型，读起来反而更绕。


REQ-006 的每个阶段都只在这里落地：先判权限（角色 + 是否预约当事人），再判状态与时间窗口；
被拒一律先记 denied 日志再抛异常。资金的账面动作（支付流水、退款单）交给 M4 的 trade 应用。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from accounts.models import ROLE_ADMIN, ROLE_SUPPORT
from accounts.permissions import can, role_of
from audit.models import RESULT_ALLOWED, RESULT_DENIED, record
from catalog.models import PRESALE, Product
from common.errors import Conflict, InvalidInput
from common.timebox import FINAL_WINDOW, next_day_window_end
from demo.clock import business_now
from trade.models import KIND_DEPOSIT, KIND_FINAL, Payment, Refund
from trade.services import create_presale_order, record_payment

from .models import (
    ACTIVE_STATUSES,
    AWAITING_SELLER,
    CANCELED,
    COMPLETED,
    REJECTED,
    WAITING_ARRIVAL,
    WAITING_DEPOSIT,
    WAITING_FINAL,
    Reservation,
)

DEPOSIT_WINDOW = timedelta(minutes=15)
MAX_VALID_DAYS = 180


class ReservationConflict(Conflict):
    """预约状态、版本或并发冲突。"""


def _moment(now):
    """时间一律走这个入口，测试与定时任务才能传「现在」。"""
    return now or business_now()


def _deny(
    actor: object, action: str, message: str, *, reservation: Reservation | None = None
) -> None:
    """权限或当事人不匹配：先记 denied 日志，再抛 403（REQ-002/026）。"""
    record(
        action=action,
        result=RESULT_DENIED,
        actor=actor,
        target_type="reservation",
        target_id=getattr(reservation, "pk", "") or "",
        detail={"reason": message},
    )
    raise PermissionDenied(message)


def _allow(
    actor: object,
    action: str,
    *,
    reservation: Reservation | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    """重要动作放行也留一条日志（REQ-022）。"""
    record(
        action=action,
        result=RESULT_ALLOWED,
        actor=actor,
        target_type="reservation",
        target_id=getattr(reservation, "pk", "") or "",
        detail=detail or {},
    )


def _close(
    reservation: Reservation,
    status: str,
    *,
    reason: str,
    refund: Decimal = Decimal("0.00"),
    now=None,
) -> Reservation:
    """关闭一条有效预约，并把应退定金记成退款单（资金动作由 M4 执行）。"""
    moment = _moment(now)
    with transaction.atomic():
        moved = Reservation.objects.filter(
            pk=reservation.pk, status=reservation.status, status__in=ACTIVE_STATUSES
        ).update(
            status=status,
            close_reason=str(reason or "")[:200],
            refund_due=refund,
            closed_at=moment,
        )
        if moved != 1:
            raise ReservationConflict("预约状态已变更")
        reservation.refresh_from_db()
        if refund > 0:
            Refund.objects.create(
                reservation=reservation, amount=refund, reason=str(reason or "")[:200]
            )
    return reservation


def _active_reservation(product: Product) -> Reservation | None:
    """该商品当前的有效预约（最多一条，由部分唯一索引保证）。"""
    return Reservation.objects.filter(product_id=product.pk, status__in=ACTIVE_STATUSES).first()


def reserve(
    product: Product,
    *,
    buyer: object,
    request_key: str = "",
    expected_version: int | None = None,
    now=None,
) -> Reservation:
    """提交预约：原子生成「待付定金」预约并锁货，同一商品同时最多 1 条（REQ-006）。

    并发下只有一条能落库：部分唯一索引拒绝第二条，这里把它翻译成 409。
    同一买家带同一请求标识重复提交返回原预约，旧页面提交按商品版本号拒绝。
    """
    if getattr(buyer, "pk", None) == product.seller_id:
        _deny(buyer, "reservation.create", "不能预约自己发布的商品")
    if not can(buyer, "reservation.create", owner_id=product.seller_id):
        _deny(buyer, "reservation.create", "只有买家/卖家账号可以预约商品")
    if product.status != PRESALE:
        raise ReservationConflict("只有已预售商品可以预约")
    moment = _moment(now)
    existing = _active_reservation(product)
    if existing is not None:
        key = str(request_key or "")
        if key and existing.buyer_id == getattr(buyer, "pk", None) and existing.request_key == key:
            return existing
        raise ReservationConflict("该商品已有有效预约")
    if expected_version is not None and product.version != expected_version:
        raise ReservationConflict("商品已被修改，请刷新后重试")
    if product.deposit <= 0 or product.final_amount <= 0:
        raise InvalidInput("该预售商品未设置定金与尾款金额")
    days = int(product.presale_valid_days or 0)
    if not 1 <= days <= MAX_VALID_DAYS:
        raise InvalidInput("预约有效期必须在 1 到 180 天之间")
    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=product.pk)
            if product.status != PRESALE:
                raise ReservationConflict("只有已预售商品可以预约")
            if expected_version is not None and product.version != expected_version:
                raise ReservationConflict("商品已被修改，请刷新后重试")
            reservation = Reservation.objects.create(
                product=product,
                buyer=buyer,
                seller=product.seller,
                status=WAITING_DEPOSIT,
                deposit=product.deposit,
                final_amount=product.final_amount,
                deposit_due_at=moment + DEPOSIT_WINDOW,
                expires_at=moment + timedelta(days=days),
                request_key=str(request_key or "")[:64],
            )
    except IntegrityError as exc:
        raise ReservationConflict("该商品已有有效预约") from exc
    _allow(
        buyer,
        "reservation.create",
        reservation=reservation,
        detail={"deposit": str(reservation.deposit), "final": str(reservation.final_amount)},
    )
    return reservation


def pay_deposit(
    reservation: Reservation, *, actor: object, txn_no: str, now=None
) -> Reservation:
    """买家付定金：15 分钟内完成则预约转待确认；超时则关闭且不收钱（REQ-006）。"""
    if not can(actor, "reservation.pay", owner_id=reservation.buyer_id):
        _deny(actor, "reservation.pay", "只有该预约的买家可以支付定金", reservation=reservation)
    reservation.refresh_from_db()
    if reservation.status != WAITING_DEPOSIT:
        if Payment.objects.filter(reservation=reservation, txn_no=txn_no).exists():
            return reservation
        raise ReservationConflict(f"{reservation.status} 状态不能支付定金")
    moment = _moment(now)
    if moment >= reservation.deposit_due_at:
        _close(reservation, CANCELED, reason="定金支付超时，预约关闭并放货", now=moment)
        raise ReservationConflict("定金支付已超时，预约已关闭（未收款不涉及退款）")
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=reservation.product_id)
        reservation.refresh_from_db()
        if reservation.status != WAITING_DEPOSIT:
            if Payment.objects.filter(
                reservation=reservation, txn_no=txn_no, kind=KIND_DEPOSIT
            ).exists():
                return reservation
            raise ReservationConflict("预约状态已变更")
        payment, created = record_payment(
            kind=KIND_DEPOSIT,
            txn_no=txn_no,
            amount=reservation.deposit,
            target=reservation,
            now=moment,
        )
        if not created:
            return reservation
        moved = Reservation.objects.filter(
            pk=reservation.pk, status=WAITING_DEPOSIT
        ).update(status=AWAITING_SELLER, deposit_paid_at=moment)
        if moved != 1:
            raise ReservationConflict("预约状态已变更，定金将原路退回")
        reservation.refresh_from_db()
    _allow(
        actor,
        "reservation.pay",
        reservation=reservation,
        detail={"kind": KIND_DEPOSIT, "txn_no": payment.txn_no, "amount": str(payment.amount)},
    )
    return reservation


def seller_confirm(reservation: Reservation, *, actor: object, now=None) -> Reservation:
    """卖家确认预约：待确认→待到货，等卖家到货后再点「确认到货」（REQ-006）。"""
    if not can(actor, "reservation.confirm", owner_id=reservation.seller_id):
        _deny(actor, "reservation.confirm", "只有卖家本人可以确认预约", reservation=reservation)
    if reservation.status != AWAITING_SELLER:
        raise ReservationConflict(f"{reservation.status} 状态不能确认预约")
    moment = _moment(now)
    with transaction.atomic():
        moved = Reservation.objects.filter(pk=reservation.pk, status=AWAITING_SELLER).update(
            status=WAITING_ARRIVAL, seller_confirmed_at=moment
        )
        if moved != 1:
            raise ReservationConflict("预约状态已变更")
        reservation.refresh_from_db()
    _allow(actor, "reservation.confirm", reservation=reservation)
    return reservation


def seller_reject(
    reservation: Reservation, *, actor: object, reason: str = "", now=None
) -> Reservation:
    """卖家拒绝预约：定金全额原路退回并放货（REQ-006）。"""
    if not can(actor, "reservation.confirm", owner_id=reservation.seller_id):
        _deny(actor, "reservation.confirm", "只有卖家本人可以拒绝预约", reservation=reservation)
    if reservation.status != AWAITING_SELLER:
        raise ReservationConflict(f"{reservation.status} 状态不能拒绝预约")
    text = reason or "卖家拒绝预约"
    closed = _close(reservation, REJECTED, reason=text, refund=reservation.deposit, now=now)
    _allow(actor, "reservation.confirm", reservation=closed, detail={"reason": text})
    return closed


def confirm_arrival(reservation: Reservation, *, actor: object, now=None) -> Reservation:
    """卖家确认到货：待付尾款窗口从下一天 00:00 起满 24 小时（REQ-006/016）。"""
    if not can(actor, "reservation.confirm", owner_id=reservation.seller_id):
        _deny(actor, "reservation.confirm", "只有卖家本人可以确认到货", reservation=reservation)
    if reservation.status != WAITING_ARRIVAL:
        raise ReservationConflict(f"{reservation.status} 状态不能确认到货")
    moment = _moment(now)
    with transaction.atomic():
        moved = Reservation.objects.filter(pk=reservation.pk, status=WAITING_ARRIVAL).update(
            status=WAITING_FINAL, arrived_at=moment, final_due_at=next_day_window_end(moment)
        )
        if moved != 1:
            raise ReservationConflict("预约状态已变更")
        reservation.refresh_from_db()
    _allow(
        actor,
        "reservation.confirm",
        reservation=reservation,
        detail={"final_due_at": reservation.final_due_at.isoformat()},
    )
    return reservation


def pay_final(reservation: Reservation, *, actor: object, txn_no: str, now=None) -> Reservation:
    """买家付尾款：成功后生成订单、商品转交易中、预约完成（REQ-006）。"""
    if not can(actor, "reservation.pay", owner_id=reservation.buyer_id):
        _deny(actor, "reservation.pay", "只有该预约的买家可以支付尾款", reservation=reservation)
    reservation.refresh_from_db()
    if reservation.status != WAITING_FINAL:
        raise ReservationConflict(f"{reservation.status} 状态不能支付尾款")
    moment = _moment(now)
    if reservation.final_due_at is None:
        raise ReservationConflict("尚未确认到货，尾款窗口未开启")
    if moment < reservation.final_due_at - FINAL_WINDOW:
        raise ReservationConflict("尾款窗口尚未开启，请在确认到货次日支付")
    if moment >= reservation.final_due_at:
        _close(reservation, CANCELED, reason="尾款逾期，预约关闭且定金不退", now=moment)
        raise ReservationConflict("尾款已逾期，预约关闭且定金不退")
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=reservation.product_id)
        reservation.refresh_from_db()
        if reservation.status != WAITING_FINAL:
            if Payment.objects.filter(
                reservation=reservation, txn_no=txn_no, kind=KIND_FINAL
            ).exists():
                return reservation
            raise ReservationConflict("预约状态已变更")
        payment, created = record_payment(
            kind=KIND_FINAL,
            txn_no=txn_no,
            amount=reservation.final_amount,
            target=reservation,
            now=moment,
        )
        if not created:
            return reservation
        moved = Reservation.objects.filter(pk=reservation.pk, status=WAITING_FINAL).update(
            status=COMPLETED, closed_at=moment
        )
        if moved != 1:
            raise ReservationConflict("预约状态已变更，尾款将原路退回")
        reservation.refresh_from_db()
        order = create_presale_order(reservation, now=moment)
    _allow(
        actor,
        "reservation.pay",
        reservation=reservation,
        detail={"kind": KIND_FINAL, "txn_no": payment.txn_no, "order": order.pk},
    )
    return reservation


def cancel(reservation: Reservation, *, actor: object, reason: str = "", now=None) -> Reservation:
    """买家或卖家取消预约：按阶段决定定金全额退、不退或未收款（REQ-006）。

    - 待付定金（尚未收款）：直接关闭，不涉及退款。
    - 卖家确认前（待确认）：双方取消或卖家拒绝都全额退定金。
    - 卖家确认后（待到货/待付尾款）：买家取消不退定金；卖家取消按卖家违约全额退。
    """
    if not can(actor, "reservation.cancel"):
        _deny(actor, "reservation.cancel", "无权取消预约", reservation=reservation)
    party = getattr(actor, "pk", None)
    if party not in (reservation.buyer_id, reservation.seller_id):
        _deny(actor, "reservation.cancel", "只有预约双方可以取消预约", reservation=reservation)
    if reservation.status not in ACTIVE_STATUSES:
        raise ReservationConflict(f"{reservation.status} 状态不能取消")
    is_seller = party == reservation.seller_id
    refund, status = _cancel_terms(reservation, is_seller=is_seller)
    text = reason or ("卖家取消预约" if is_seller else "买家取消预约")
    closed = _close(reservation, status, reason=text, refund=refund, now=now)
    _allow(
        actor,
        "reservation.cancel",
        reservation=closed,
        detail={"refund": str(refund), "reason": text},
    )
    return closed


def _cancel_terms(reservation: Reservation, *, is_seller: bool) -> tuple[Decimal, str]:
    """取消条款：返回 (应退定金, 目标状态)。"""
    if reservation.status == WAITING_DEPOSIT:
        return Decimal("0.00"), REJECTED if is_seller else CANCELED
    if reservation.status == AWAITING_SELLER:
        return reservation.deposit, REJECTED if is_seller else CANCELED
    if is_seller:
        return reservation.deposit, CANCELED
    return Decimal("0.00"), CANCELED


def cancel_for_takedown(product: Product, *, reason: str, now=None) -> int:
    """商品被违规下架：取消有效预约并把已收定金全额原路退回（REQ-006/009）。"""
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().filter(
            product=product, status__in=ACTIVE_STATUSES
        ).first()
        if reservation is None:
            return 0
        _close(
            reservation,
            CANCELED,
            reason=f"商品违规下架：{reason}"[:200],
            refund=reservation.deposit if reservation.deposit_paid_at else Decimal("0.00"),
            now=now,
        )
    _allow(None, "reservation.cancel", reservation=reservation, detail={"reason": "商品违规下架"})
    return 1


def sweep(*, now=None) -> int:
    """定时清理：定金超时、有效期届满、尾款逾期都归入已取消并放货（REQ-006）。"""
    moment = _moment(now)
    closed = 0
    for reservation in Reservation.objects.filter(
        status=WAITING_DEPOSIT, deposit_due_at__lte=moment
    ):
        _close(reservation, CANCELED, reason="定金支付超时，预约关闭并放货", now=moment)
        closed += 1
    for reservation in Reservation.objects.filter(
        status__in=(AWAITING_SELLER, WAITING_ARRIVAL), expires_at__lte=moment
    ):
        _close(
            reservation,
            CANCELED,
            reason="预约有效期届满，定金全额原路退回",
            refund=reservation.deposit,
            now=moment,
        )
        closed += 1
    for reservation in Reservation.objects.filter(
        status=WAITING_FINAL, final_due_at__lte=moment
    ):
        _close(reservation, CANCELED, reason="尾款逾期，预约关闭且定金不退", now=moment)
        closed += 1
    return closed


def reservations_for(user: object) -> QuerySet[Reservation]:
    """预约列表：当事人看自己的，客服与管理员看全部（REQ-002）。"""
    if role_of(user) in (ROLE_SUPPORT, ROLE_ADMIN):
        return Reservation.objects.all()
    party = getattr(user, "pk", None)
    return Reservation.objects.filter(Q(buyer=party) | Q(seller=party))
