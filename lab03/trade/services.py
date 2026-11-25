"""交易与资金用例层（M4）：下单锁货、支付、发货、收货、退款、结算、超时清理。
# pylint: disable=duplicate-code
# 理由：与 reservations.services 的 _deny/_allow 是同一套「先记日志再抛/再返回」形状，
# 差异只在 target_type；抽公共函数要跨模块传模型类型，读起来反而更绕。


本层是交易动作的唯一业务入口：先判权限（角色 + 是否订单当事人），再判状态；
被拒一律先写 denied 日志再抛异常；商品状态改动一律经 catalog/state.py 的 transit()。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet

from accounts.permissions import can
from audit.models import RESULT_ALLOWED, RESULT_DENIED, record
from catalog.models import LOCKED, OFFSHELF, PRESALE, PUBLISHED, SOLD, TRADING, Product
from catalog.state import StateConflict, can_transit, transit
from common.errors import Conflict, InvalidInput
from common.timebox import next_day_window_end
from demo.clock import business_now
from reservations.models import WAITING_FINAL, Reservation

from .fees import fee_breakdown
from .models import (
    ACTIVE_STATUSES,
    CANCELED,
    COMPLETED,
    DISPUTING,
    KIND_ORDER,
    REFUNDED,
    RESULT_LATE,
    RESULT_OK,
    WAITING_PAY,
    WAITING_RECEIPT,
    WAITING_SHIP,
    Order,
    Payment,
    Refund,
    Settlement,
)

PAYMENT_WINDOW = timedelta(minutes=15)
SHIP_DEADLINE = timedelta(hours=48)
AUTO_REFUND_DEADLINE = timedelta(days=15)
AUTO_CONFIRM_WINDOW = timedelta(days=7)


class OrderConflict(Conflict):
    """订单状态、版本或并发冲突。"""


def _moment(now):
    """测试与定时任务都要能把「现在」传进来，所以时间一律走这个入口。"""
    return now or business_now()


def _deny(actor: object, action: str, message: str, *, order: Order | None = None) -> None:
    """权限或当事人不匹配：先记 denied 日志，再抛 403。"""
    record(
        action=action,
        result=RESULT_DENIED,
        actor=actor,
        target_type="order",
        target_id=getattr(order, "pk", "") or "",
        detail={"reason": message},
    )
    raise PermissionDenied(message)


def _allow(
    actor: object,
    action: str,
    *,
    order: Order | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    """重要资金与状态动作放行也留一条日志（REQ-022）。"""
    record(
        action=action,
        result=RESULT_ALLOWED,
        actor=actor,
        target_type="order",
        target_id=getattr(order, "pk", "") or "",
        detail=detail or {},
    )


def _product(order: Order) -> Product:
    """重新读一次商品，避免用订单里缓存的旧状态去推状态机。"""
    return Product.objects.get(pk=order.product_id)


def _amount(value: object) -> Decimal:
    """支付金额必须是正的有限数字。"""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidInput("支付金额必须是数字") from exc
    if not amount.is_finite() or amount <= 0:
        raise InvalidInput("支付金额必须大于 0")
    return amount


def record_payment(
    *,
    kind: str,
    txn_no: str,
    amount: object,
    target: Order | Reservation,
    now=None,
    result: str = RESULT_OK,
) -> tuple[Payment, bool]:
    """记一条支付流水；同一流水号只记一条，重复回调不重复记账（REQ-014）。

    返回 (流水, 是否新建)。流水号已被占用时返回原流水，调用方据此判断是重复回调
    还是流水号被挪用到别的单据。
    """
    if not isinstance(target, (Order, Reservation)):
        raise InvalidInput("支付流水必须关联订单或预约")
    text = str(txn_no or "").strip()
    if not 1 <= len(text) <= 64:
        raise InvalidInput("支付流水号须为 1 至 64 字符")
    is_order = isinstance(target, Order)
    paid = _amount(amount)
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                order=target if is_order else None,
                reservation=None if is_order else target,
                kind=kind,
                txn_no=text,
                amount=paid,
                result=result,
                paid_at=_moment(now),
            )
    except IntegrityError as exc:
        existing = Payment.objects.filter(txn_no=text).first()
        if existing is None:
            raise
        if (existing.order_id, existing.reservation_id, existing.kind, existing.amount) != (
            target.pk if is_order else None, None if is_order else target.pk, kind, paid,
        ):
            raise InvalidInput("支付流水已关联其他单据、款项类型或金额") from exc
        return existing, False
    return payment, True


def _active_order(product: Product) -> Order | None:
    """该商品当前的有效订单（待付款/待发货/待收货/纠纷中）。"""
    return Order.objects.filter(product_id=product.pk, status__in=ACTIVE_STATUSES).first()


def create_order(
    product: Product,
    *,
    buyer: object,
    request_key: str = "",
    expected_version: int | None = None,
    now=None,
) -> Order:
    """已发布商品下单：创建待付款订单与锁货在同一事务里完成（REQ-013）。

    锁货用带 status 条件的 UPDATE 落地，并发下只有一笔能把它从已发布推走；
    同一商品同时最多一笔有效订单，还有部分唯一索引在数据库层兜底。
    """
    if getattr(buyer, "pk", None) == product.seller_id:
        _deny(buyer, "order.create", "不能购买自己发布的商品")
    if not can(buyer, "order.create", owner_id=product.seller_id):
        _deny(buyer, "order.create", "只有买家/卖家账号可以下单")
    if product.is_presale:
        raise OrderConflict("预售商品需要通过预约购买，不能直接下单")
    moment = _moment(now)
    existing = Order.objects.filter(product=product, buyer=buyer, status=WAITING_PAY).first()
    if existing is not None:
        return existing
    if expected_version is not None and product.version != expected_version:
        raise OrderConflict("商品已被修改，请刷新后重试")
    if product.status == LOCKED:
        raise OrderConflict("该商品已被锁定")
    if product.status != PUBLISHED:
        raise OrderConflict(f"{product.status} 状态不能下单")
    if _active_order(product) is not None:
        raise OrderConflict("该商品已被锁定")
    filters: dict[str, object] = {
        "pk": product.pk, "status": PUBLISHED, "version": product.version,
    }
    try:
        with transaction.atomic():
            locked = Product.objects.filter(**filters).update(
                status=LOCKED, version=F("version") + 1, updated_at=moment
            )
            order = (
                Order.objects.create(
                    product=product,
                    buyer=buyer,
                    seller=product.seller,
                    amount=product.price,
                    status=WAITING_PAY,
                    request_key=str(request_key or "")[:64],
                    expires_at=moment + PAYMENT_WINDOW,
                )
                if locked == 1
                else None
            )
        if order is None:
            existing = Order.objects.filter(
                product=product, buyer=buyer, status=WAITING_PAY
            ).first()
            if existing is not None:
                return existing
            raise OrderConflict("该商品已被锁定")
    except IntegrityError as exc:
        raise OrderConflict("该商品已被锁定") from exc
    _allow(buyer, "order.create", order=order, detail={"amount": str(order.amount)})
    return order


def _record_late_payment(order: Order, *, txn_no: str, amount: object, now) -> None:
    """关闭后到账的迟到款项：只记流水与原路退回，不恢复单据（REQ-014）。"""
    paid = order.amount if amount is None else _amount(amount)
    payment, created = record_payment(
        kind=KIND_ORDER, txn_no=txn_no, amount=paid, target=order, now=now, result=RESULT_LATE
    )
    if not created:
        return
    Refund.objects.create(
        order=order, amount=payment.amount, reason="订单已关闭，迟到支付款项原路退回"
    )


def _payment_on_closed_order(order, *, txn_no, amount, moment):
    _record_late_payment(order, txn_no=txn_no, amount=amount, now=moment)
    return "订单已关闭，迟到支付款项原路退回"


def _apply_payment(order, *, txn_no, amount, moment):
    if order.status in (CANCELED, REFUNDED):
        return _payment_on_closed_order(order, txn_no=txn_no, amount=amount, moment=moment)
    existing = Payment.objects.filter(txn_no=txn_no, order=order, kind=KIND_ORDER).first()
    if existing and existing.result == RESULT_OK:
        if amount is not None and _amount(amount) != existing.amount:
            raise InvalidInput("支付金额与原流水不一致")
        return ""
    if order.status != WAITING_PAY:
        raise OrderConflict(f"{order.status} 状态不能支付")
    if order.expires_at is not None and moment >= order.expires_at:
        close_order(order, CANCELED, reason="支付超时自动取消", now=moment)
        _record_late_payment(order, txn_no=txn_no, amount=amount, now=moment)
        return "订单支付超时，迟到支付款项原路退回"
    if amount is not None and _amount(amount) != order.amount:
        raise InvalidInput("支付金额与应付金额不一致，已转人工处理")
    payment, created = record_payment(
        kind=KIND_ORDER, txn_no=txn_no, amount=order.amount, target=order, now=moment
    )
    if not created:
        raise OrderConflict("该支付流水已处理，不能重新推进订单")
    _advance_paid_order(order, moment)
    return payment


def _advance_paid_order(order, moment):
    Order.objects.filter(pk=order.pk).update(
        status=WAITING_SHIP, paid_at=moment, version=F("version") + 1
    )
    order.refresh_from_db()
    transit(_product(order), TRADING)


def pay(order: Order, *, actor: object, txn_no: str, amount: object = None, now=None) -> Order:
    """串行处理支付、取消和下架；迟到款记账后返回冲突，不恢复订单。"""
    if not can(actor, "order.pay", owner_id=order.buyer_id):
        _deny(actor, "order.pay", "只有该订单的买家可以支付", order=order)
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        locked = Order.objects.select_for_update().get(pk=order.pk)
        result = _apply_payment(
            locked, txn_no=str(txn_no or "").strip(), amount=amount, moment=_moment(now)
        )
    order.refresh_from_db()
    if isinstance(result, str):
        if result:
            raise OrderConflict(result)
        return order
    _allow(actor, "order.pay", order=order,
           detail={"amount": str(order.amount), "txn_no": result.txn_no})
    return order


def _restore_product(order: Order) -> None:
    """把商品放回去：取消与退款都要恢复公开可售（REQ-013/016）。"""
    product = _product(order)
    target = PRESALE if product.is_presale else PUBLISHED
    if can_transit(product.status, target):
        transit(product, target)


def close_order(  # pylint: disable=unused-argument
    order: Order, status: str, *, reason: str, now=None
) -> Order:
    """关闭一笔未完成的订单并把商品放回去；条件 UPDATE 保证只有一个结果先生效。

    ponytail: now 不落库也不用它算超时判定，保留参数是为了和同层其他函数统一调用签名，
    将来要记「关闭发生时刻」时从这里接 close_at 字段。
    """
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status=WAITING_PAY).update(
            status=status,
            close_reason=str(reason or "")[:200],
            version=F("version") + 1,
        )
        if moved != 1:
            raise OrderConflict("订单状态已变更")
        order.refresh_from_db()
        _restore_product(order)
    return order


def cancel(order: Order, *, actor: object, reason: str = "", now=None) -> Order:
    """买家或卖家取消待付款订单：订单已取消、商品恢复已发布（REQ-013）。"""
    if not can(actor, "order.cancel"):
        _deny(actor, "order.cancel", "无权取消订单", order=order)
    if getattr(actor, "pk", None) not in (order.buyer_id, order.seller_id):
        _deny(actor, "order.cancel", "只有订单双方可以取消待付款订单", order=order)
    if order.status != WAITING_PAY:
        raise OrderConflict(f"{order.status} 状态不能取消")
    closed = close_order(order, CANCELED, reason=reason or "取消待付款订单", now=now)
    _allow(actor, "order.cancel", order=closed, detail={"reason": reason or "取消待付款订单"})
    return closed


def _auto_confirm_at(order: Order, moment) -> object:
    """自动确认收货时间：普通订单 7 天；预售订单从确认到货次日起满 1 整天（REQ-016）。"""
    if order.is_presale and order.source_reservation is not None:
        arrived = order.source_reservation.arrived_at or moment
        return next_day_window_end(arrived)
    return moment + AUTO_CONFIRM_WINDOW


def ship(order: Order, *, actor: object, tracking_no: str, now=None) -> Order:
    """卖家确认发货：订单转待收货并记运单号，重复确认只生效一次（REQ-015）。"""
    if not can(actor, "order.ship", owner_id=order.seller_id):
        _deny(actor, "order.ship", "只有该订单的卖家可以确认发货", order=order)
    tracking = str(tracking_no or "").strip()
    if order.status == WAITING_RECEIPT and order.tracking_no == tracking:
        return order
    if order.status != WAITING_SHIP:
        raise OrderConflict(f"{order.status} 状态不能发货")
    if not 6 <= len(tracking) <= 60 or not tracking.isalnum():
        raise InvalidInput("运单号不合法")
    moment = _moment(now)
    with transaction.atomic():
        moved = Order.objects.filter(pk=order.pk, status=WAITING_SHIP).update(
            status=WAITING_RECEIPT,
            tracking_no=tracking,
            shipped_at=moment,
            auto_confirm_at=_auto_confirm_at(order, moment),
            version=F("version") + 1,
        )
        if moved != 1:
            raise OrderConflict("订单已发货，重复确认发货只生效一次")
        order.refresh_from_db()
    _allow(actor, "order.ship", order=order, detail={"tracking_no": tracking})
    return order


def _settle(order: Order, *, now=None) -> Settlement:
    """生成结算记录并放款；同一订单只结算一次（REQ-017）。"""
    billing = fee_breakdown(order.amount)
    moment = _moment(now)
    settlement, created = Settlement.objects.get_or_create(
        order=order,
        defaults={
            "gross": billing["gross"],
            "fee": billing["fee"],
            "channel_fee": billing["channel_fee"],
            "platform_fee": billing["platform_fee"],
            "seller_net": billing["seller_net"],
            "paid_out_at": moment,
        },
    )
    if not created and settlement.paid_out_at is None:
        Settlement.objects.filter(pk=settlement.pk).update(paid_out_at=moment)
        settlement.refresh_from_db()
    return settlement


def _sell(order: Order) -> None:
    """订单完成后商品转已售出；自动确认时商品可能已不在交易中，按当前状态判定。"""
    product = _product(order)
    if product.status == SOLD:
        return
    if can_transit(product.status, SOLD):
        transit(product, SOLD)
    else:
        _deny(None, "product.transit", f"{product.status} 不能转已售出", order=order)


def confirm_receipt(order: Order, *, actor: object, now=None) -> Order:
    """买家确认收货：订单已完成、商品已售出、生成结算并放款（REQ-016/017）。"""
    if not can(actor, "order.receive", owner_id=order.buyer_id):
        _deny(actor, "order.receive", "只有该订单的买家可以确认收货", order=order)
    if order.status == COMPLETED and Settlement.objects.filter(order=order).exists():
        return order
    if order.status != WAITING_RECEIPT:
        raise OrderConflict(f"{order.status} 状态不能确认收货")
    moment = _moment(now)
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status=WAITING_RECEIPT).update(
            status=COMPLETED, received_at=moment, version=F("version") + 1
        )
        if moved != 1:
            raise OrderConflict("订单状态已变更")
        order.refresh_from_db()
        _sell(order)
        settlement = _settle(order, now=moment)
    _allow(
        actor,
        "order.receive",
        order=order,
        detail={"fee": str(settlement.fee), "seller_net": str(settlement.seller_net)},
    )
    return order


def _has_paid(order: Order) -> bool:
    """订单是否真的收到过钱：普通订单看订单流水，预售订单的定金与尾款挂在预约上。"""
    if Payment.objects.filter(order=order, result=RESULT_OK).exists():
        return True
    if order.source_reservation_id is None:
        return False
    return Payment.objects.filter(
        reservation_id=order.source_reservation_id, result=RESULT_OK
    ).exists()


def request_refund(  # pylint: disable=unused-argument
    order: Order, *, actor: object, reason: str = "", now=None
) -> Order:
    """未发货全额退款：订单已退款，商品恢复已发布或已预售（REQ-016）。

    买家自助申请；客服裁定同样走这里。已发货后的退货不在一期，转线下。
    """
    allowed = can(actor, "order.refund_request", owner_id=order.buyer_id) or can(
        actor, "order.refund"
    )
    if not allowed:
        _deny(actor, "order.refund_request", "无权申请退款", order=order)
    if order.status != WAITING_SHIP:
        raise OrderConflict("只有未发货订单可以全额退款；已发货后的退货不在一期")
    if not _has_paid(order):
        raise OrderConflict("订单尚未支付，没有可退款项")
    text = reason or "未发货全额退款"
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status=WAITING_SHIP).update(
            status=REFUNDED, close_reason=text[:200], version=F("version") + 1
        )
        if moved != 1:
            raise OrderConflict("订单状态已变更")
        order.refresh_from_db()
        _restore_product(order)
        Refund.objects.create(order=order, amount=order.amount, reason=text[:200])
    _allow(actor, "order.refund", order=order, detail={"reason": text})
    return order


def _auto_refund(order: Order) -> None:
    """系统自动退款：超过 15 天仍未发货（REQ-015）。"""
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status=WAITING_SHIP).update(
            status=REFUNDED, close_reason="超过 15 天未发货，系统自动全额退款", version=F("version") + 1
        )
        if moved != 1:
            return
        order.refresh_from_db()
        _restore_product(order)
        Refund.objects.create(order=order, amount=order.amount, reason="超过 15 天未发货自动退款")
    _allow(None, "order.refund", order=order, detail={"reason": "超过 15 天未发货自动退款"})


# 纠纷介入可以接手的订单状态（REQ-019：介入后订单转纠纷中，未发货就得等裁定）。
OPEN_FOR_DISPUTE: tuple[str, ...] = (WAITING_SHIP, WAITING_RECEIPT)
# 裁定退款能接手的订单状态（REQ-019：未发货裁定退款、已发货裁定终止）。
REFUNDABLE_DISPUTE: tuple[str, ...] = (WAITING_SHIP, WAITING_RECEIPT, DISPUTING)


def open_dispute(order: Order, *, reason: str = "请求客服介入", now=None) -> Order:  # pylint: disable=unused-argument
    """订单转纠纷中：纠纷期间不再自动确认收货与放款（REQ-019）。

    M5 的工单层在用户请求介入时调用它，所以这里不判「谁有权发起」——
    发起权由 M5 判（订单双方或客服自动识别）；本函数只做状态与联动。
    纠纷中仍是有效订单，商品继续处于交易中，不会因为介入被放回公开可售。
    """
    if order.status == DISPUTING:
        return order
    with transaction.atomic():
        moved = Order.objects.filter(pk=order.pk, status__in=OPEN_FOR_DISPUTE).update(
            status=DISPUTING, auto_confirm_at=None, version=F("version") + 1
        )
        if moved != 1:
            raise OrderConflict(f"{order.status} 状态不能介入纠纷")
        order.refresh_from_db()
    _allow(None, "dispute.handle", order=order, detail={"to": DISPUTING, "reason": reason})
    return order


def resume_order(order: Order) -> Order:
    """纠纷被驳回后恢复履约：按已发生的事实退回原状态，并接回自动确认时限（REQ-019）。

    已发货的按发货时间重算自动确认时间；未发货的回到待发货，继续走 15 天自动退款。
    """
    with transaction.atomic():
        moved = Order.objects.filter(pk=order.pk, status=DISPUTING).update(
            status=WAITING_RECEIPT if order.shipped_at else WAITING_SHIP,
            auto_confirm_at=_auto_confirm_at(order, _moment(None)) if order.shipped_at else None,
            version=F("version") + 1,
        )
        if moved != 1:
            raise OrderConflict(f"{order.status} 状态不能恢复履约")
        order.refresh_from_db()
    _allow(None, "order.resume", order=order, detail={"to": order.status})
    return order


def refund_after_dispute(
    order: Order, *, actor: object, reason: str = "客服裁定退款"
) -> Order:
    """未发货裁定退款：订单已退款，商品恢复已发布或已预售（REQ-016/019）。

    与买家自助的 request_refund 是同一条资金路径，区别只在于接手的订单状态包含纠纷中。
    """
    if not can(actor, "order.refund"):
        _deny(actor, "order.refund", "只有客服或管理员可以裁定退款", order=order)
    if order.status not in REFUNDABLE_DISPUTE:
        raise OrderConflict(f"{order.status} 状态不能裁定退款")
    if not _has_paid(order):
        raise OrderConflict("订单尚未支付，没有可退款项")
    text = str(reason or "客服裁定退款")[:200]
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status__in=REFUNDABLE_DISPUTE).update(
            status=REFUNDED, close_reason=text, version=F("version") + 1
        )
        if moved != 1:
            raise OrderConflict("订单状态已变更")
        order.refresh_from_db()
        if order.shipped_at:
            transit(_product(order), OFFSHELF)
        else:
            _restore_product(order)
        Refund.objects.create(order=order, amount=order.amount, reason=text)
    _allow(actor, "order.refund", order=order, detail={"reason": text})
    return order


def terminate_after_dispute(
    order: Order, *, actor: object, reason: str = "客服裁定终止"
) -> Order:
    """已发货裁定终止：订单关闭，钱由线下完成（REQ-016/019）。

    商品去向按 UML 商品状态图：25 条转换里没有「交易中 → 违规下架」这条边，
    REQ-016 要求的是「转已下架」，即「交易中 → 已下架」（OFFSHELF）。
    ponytail: 一期不伪造第三方通道的退款流水，平台内退款走 refund_after_dispute。
    """
    if not can(actor, "order.refund"):
        _deny(actor, "order.refund", "只有客服或管理员可以裁定终止", order=order)
    if not order.shipped_at:
        raise OrderConflict("未发货订单请裁定退款")
    text = str(reason or "客服裁定终止")[:200]
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=order.product_id)
        moved = Order.objects.filter(pk=order.pk, status__in=REFUNDABLE_DISPUTE).update(
            status=CANCELED, close_reason=text, version=F("version") + 1
        )
        if moved != 1:
            raise OrderConflict(f"{order.status} 状态不能裁定终止")
        order.refresh_from_db()
        product = _product(order)
        if product.status == TRADING:
            transit(product, OFFSHELF)
    _allow(actor, "order.terminate", order=order, detail={"reason": text})
    return order


def sweep(*, now=None) -> int:
    """定时清理：待付款超时、超期未发货自动退款、到点自动确认收货放款。"""
    moment = _moment(now)
    closed = 0
    for order in Order.objects.filter(status=WAITING_PAY, expires_at__lte=moment):
        try:
            close_order(order, CANCELED, reason="支付超时自动取消", now=moment)
        except OrderConflict:
            continue
        _allow(None, "order.cancel", order=order, detail={"reason": "支付超时自动取消"})
        closed += 1
    overdue = Order.objects.filter(
        status=WAITING_SHIP, paid_at__isnull=False, paid_at__lte=moment - AUTO_REFUND_DEADLINE
    )
    for order in overdue:
        _auto_refund(order)
        closed += 1
    waiting = Order.objects.filter(status=WAITING_RECEIPT, auto_confirm_at__lte=moment)
    for order in waiting.select_related("buyer", "seller", "product"):
        try:
            confirm_receipt(order, actor=order.buyer, now=moment)
        except (OrderConflict, StateConflict):
            continue
        closed += 1
    return closed


def orders_for(user: object) -> QuerySet[Order]:
    """订单列表：订单双方看自己的，客服与管理员看全部（REQ-002/012）。"""
    role = getattr(user, "role", "")
    if getattr(user, "is_authenticated", False) and role in ("support", "admin"):
        return Order.objects.all()

    me = getattr(user, "pk", None)
    return Order.objects.filter(Q(buyer_id=me) | Q(seller_id=me))


def create_presale_order(reservation: Reservation, *, now=None) -> Order:
    """尾款到账后生成预售订单（待发货）并把商品推向交易中（REQ-006）。

    预售订单不再经过待付款：定金与尾款都已在预约阶段收到。
    """
    existing = Order.objects.filter(source_reservation=reservation).first()
    if existing is not None:
        return existing
    moment = _moment(now)
    total = reservation.deposit + reservation.final_amount
    with transaction.atomic():
        order = Order.objects.create(
            product=reservation.product,
            buyer=reservation.buyer,
            seller=reservation.seller,
            source_reservation=reservation,
            status=WAITING_SHIP,
            amount=total,
            deposit_amount=reservation.deposit,
            final_amount=reservation.final_amount,
            paid_at=moment,
            auto_confirm_at=next_day_window_end(reservation.arrived_at or moment),
        )
        transit(_product(order), TRADING)
    _allow(reservation.buyer, "order.create", order=order, detail={"amount": str(total)})
    return order


def ensure_final_payable(reservation: Reservation) -> None:
    """尾款窗口校验：只有待付尾款且在窗口内的预约可以付尾款（REQ-006）。"""
    if reservation.status != WAITING_FINAL:
        raise OrderConflict(f"{reservation.status} 状态不能支付尾款")
    if reservation.final_due_at is None:
        raise OrderConflict("尚未确认到货，尾款窗口未开启")
