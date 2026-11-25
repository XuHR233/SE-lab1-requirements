"""M4 验收测试：R-401 下单与锁货 / R-402 支付与托管 / R-403 发货收货 /
R-404 退款 / R-405 结算与手续费 / R-406 超时清理。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-locals,too-many-lines
# 理由：pytest fixture 按参数名注入；整条链路写在一个用例里更便于对照 REQ 原文。

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.utils import timezone

from audit.models import RESULT_DENIED, RESULT_ALLOWED, OperationLog
from catalog.models import LOCKED, PRESALE, PUBLISHED, SOLD, TRADING
from catalog.services import revise_content
from catalog.state import StateConflict
from common.errors import InvalidInput
from common.timebox import FINAL_WINDOW, next_day_window_end
from reservations.services import (
    confirm_arrival,
    pay_deposit,
    pay_final,
    reserve,
    seller_confirm,
)
from tests.conftest import make_presale, make_product
from trade.fees import fee_breakdown
from trade.models import (
    CANCELED,
    KIND_DEPOSIT,
    KIND_FINAL,
    COMPLETED,
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
from trade.services import (
    OrderConflict,
    cancel,
    confirm_receipt,
    create_order,
    orders_for,
    pay,
    request_refund,
    ship,
    sweep,
)

User = get_user_model()


def order_now(product, buyer, *, now=None, request_key="", expected_version=None):
    """下单，默认用当前时刻。"""
    return create_order(
        product,
        buyer=buyer,
        request_key=request_key,
        expected_version=expected_version,
        now=now,
    )


def test_order_locks_product_with_payment_window(seller, buyer, category):
    """下单与锁货是一个原子操作：订单待付款、商品待付款锁定、窗口 15 分钟（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    product.refresh_from_db()
    assert order.status == WAITING_PAY
    assert order.expires_at == base + timedelta(minutes=15)
    assert product.status == LOCKED
    assert product.version == 2
    assert OperationLog.objects.filter(action="order.create", result=RESULT_ALLOWED).count() == 1


def test_presale_cannot_be_ordered_directly(seller, buyer, category):
    """已预售商品只接受预约，不能直接下单（REQ-013）。"""
    product = make_presale(seller, category, status=PUBLISHED)
    with pytest.raises(OrderConflict, match="预约"):
        order_now(product, buyer)


def test_seller_cannot_buy_own_product(seller, category):
    """同一账号不能购买自己发布的商品（REQ-001）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(PermissionDenied):
        order_now(product, seller)


def test_guest_cannot_order(seller, category):
    """游客不能下单，且留 denied 日志（REQ-002/026）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(PermissionDenied):
        order_now(product, AnonymousUser())
    assert OperationLog.objects.filter(action="order.create", result=RESULT_DENIED).count() == 1


def test_same_buyer_repeat_returns_same_order(seller, buyer, category):
    """同一买家重复提交返回原待付款订单，不会开第二笔（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    first = order_now(product, buyer)
    again = order_now(product, buyer)
    assert again.pk == first.pk
    assert Order.objects.filter(product=product).count() == 1


def test_other_buyer_sees_locked_message(seller, buyer, other, category):
    """商品被锁定后其他买家得到「该商品已被锁定」（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order_now(product, buyer)
    with pytest.raises(OrderConflict, match="已被锁定"):
        order_now(product, other)


def test_stale_version_is_rejected(seller, buyer, category):
    """旧页面按版本号下单被拒（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(OrderConflict, match="请刷新"):
        order_now(product, buyer, expected_version=product.version + 10)


def test_pay_moves_order_and_product(seller, buyer, category):
    """支付成功：订单待发货、商品交易中，写支付流水号与时间（REQ-014）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    pay(order, actor=buyer, txn_no="T-001", now=base + timedelta(minutes=2))
    product.refresh_from_db()
    assert order.status == WAITING_SHIP
    assert order.paid_at is not None
    assert product.status == TRADING
    assert Payment.objects.filter(order=order, kind=KIND_ORDER).count() == 1


def test_payment_callback_is_idempotent(seller, buyer, category):
    """同一支付流水号回调三次只有一条流水、一次状态推进（REQ-014 幂等）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    for minute in (1, 2, 3):
        pay(order, actor=buyer, txn_no="T-002", now=base + timedelta(minutes=minute))
    assert Payment.objects.filter(txn_no="T-002").count() == 1
    assert order.status == WAITING_SHIP


def test_amount_mismatch_is_rejected(seller, buyer, category):
    """支付金额与应付金额不一致：拒绝入账并转人工（REQ-014）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    with pytest.raises(InvalidInput, match="金额"):
        pay(order, actor=buyer, txn_no="T-003", amount="1.00")
    assert order.status == WAITING_PAY


def test_pay_after_window_closes_order(seller, buyer, category):
    """15 分钟未支付：订单自动取消、商品恢复已发布（REQ-013/014）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    with pytest.raises(OrderConflict, match="超时"):
        pay(order, actor=buyer, txn_no="T-004", now=base + timedelta(minutes=16))
    product.refresh_from_db()
    assert order.status == CANCELED
    assert product.status == PUBLISHED
    assert Order.objects.filter(product=product, status__in=(WAITING_PAY,)).count() == 0


def test_late_payment_is_refunded_not_restored(seller, buyer, category):
    """取消后到账的迟到支付：只记流水与原路退回，不恢复订单（REQ-014）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    cancel(order, actor=buyer)
    with pytest.raises(OrderConflict, match="迟到支付"):
        pay(order, actor=buyer, txn_no="T-005")
    assert Payment.objects.filter(txn_no="T-005", result=RESULT_LATE).count() == 1
    assert Refund.objects.filter(order=order).count() == 1
    assert order.status == CANCELED


def test_buyer_and_seller_can_cancel_and_free_product(seller, buyer, category):
    """买卖双方都能取消待付款订单，商品恢复已发布且可被再次下单（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    cancel(order, actor=seller)
    product.refresh_from_db()
    assert order.status == CANCELED
    assert product.status == PUBLISHED
    again = order_now(product, buyer)
    assert again.status == WAITING_PAY
    assert OperationLog.objects.filter(action="order.cancel", result=RESULT_ALLOWED).count() == 1


def test_locked_product_cannot_be_revised(seller, buyer, category):
    """锁定期卖家不能改价改内容，必须先取消订单（REQ-013）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order_now(product, buyer)
    product.refresh_from_db()
    with pytest.raises(StateConflict):
        revise_content(product, actor=seller, expected_version=product.version, price="90.00")


def test_ship_requires_seller_and_valid_tracking(seller, buyer, category):
    """只有卖家能发货，运单号必须像样；重复发货只生效一次（REQ-015）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    pay(order, actor=buyer, txn_no="T-006")
    with pytest.raises(PermissionDenied):
        ship(order, actor=buyer, tracking_no="SF123456")
    with pytest.raises(InvalidInput, match="运单号"):
        ship(order, actor=seller, tracking_no="bad")
    ship(order, actor=seller, tracking_no="SF123456")
    assert order.status == WAITING_RECEIPT
    ship(order, actor=seller, tracking_no="SF123456")
    assert order.status == WAITING_RECEIPT


def test_receive_settles_with_fee(seller, buyer, category):
    """确认收货：订单完成、商品已售出、发放款指令并扣 15 元手续费（REQ-016/017）。"""
    product = make_product(seller, category, status=PUBLISHED, price="1000.00")
    order = order_now(product, buyer)
    pay(order, actor=buyer, txn_no="T-007")
    ship(order, actor=seller, tracking_no="SF123457")
    confirm_receipt(order, actor=buyer)
    product.refresh_from_db()
    assert order.status == COMPLETED
    assert product.status == SOLD
    settlement = Settlement.objects.get(order=order)
    assert settlement.fee == Decimal("15.00")
    assert settlement.channel_fee == Decimal("6.00")
    assert settlement.platform_fee == Decimal("9.00")
    assert settlement.seller_net == Decimal("985.00")
    assert settlement.paid_out_at is not None


def test_settlement_is_idempotent(seller, buyer, category):
    """同一订单只结算一次，重复确认不会多发一笔（REQ-017）。"""
    product = make_product(seller, category, status=PUBLISHED, price="20000.00")
    order = order_now(product, buyer)
    pay(order, actor=buyer, txn_no="T-008")
    ship(order, actor=seller, tracking_no="SF123458")
    confirm_receipt(order, actor=buyer)
    confirm_receipt(order, actor=buyer)
    assert Settlement.objects.filter(order=order).count() == 1
    assert Settlement.objects.get(order=order).fee == Decimal("200.00")


@pytest.mark.parametrize(
    ("amount", "fee", "channel", "platform", "net"),
    [
        ("1000.00", "15.00", "6.00", "9.00", "985.00"),
        ("20000.00", "200.00", "120.00", "80.00", "19800.00"),
        ("13333.00", "200.00", "80.00", "120.00", "13133.00"),
        ("99.99", "1.50", "0.60", "0.90", "98.49"),
    ],
)
def test_fee_breakdown_cases(amount, fee, channel, platform, net):
    """手续费四组验收：1.5% 拆 0.6% + 0.9%、封顶 200、两位小数四舍五入（REQ-017）。"""
    result = fee_breakdown(Decimal(amount))
    assert result["fee"] == Decimal(fee)
    assert result["channel_fee"] == Decimal(channel)
    assert result["platform_fee"] == Decimal(platform)
    assert result["seller_net"] == Decimal(net)


def test_refund_before_ship_restores_published(seller, buyer, category):
    """未发货全额退款：订单已退款、商品回到已发布且无需重新送审（REQ-016）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    pay(order, actor=buyer, txn_no="T-009")
    request_refund(order, actor=buyer, reason="不想要了")
    product.refresh_from_db()
    assert order.status == REFUNDED
    assert product.status == PUBLISHED
    assert Refund.objects.filter(order=order).count() == 1


def test_refund_of_presale_order_restores_presale(seller, buyer, category):
    """预售订单退款后商品回到已预售（REQ-016）。"""
    product = make_presale(seller, category)
    base = timezone.now()
    reservation = reserve(product, buyer=buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-100", now=base + timedelta(minutes=1))
    seller_confirm(reservation, actor=seller, now=base + timedelta(minutes=2))
    confirm_arrival(reservation, actor=seller, now=base + timedelta(minutes=3))
    pay_final(reservation, actor=buyer, txn_no="F-100", now=reservation.final_due_at - FINAL_WINDOW)
    order = reservation.order
    assert order.status == WAITING_SHIP
    paid = {item.kind for item in Payment.objects.filter(reservation=reservation, result=RESULT_OK)}
    assert paid == {KIND_DEPOSIT, KIND_FINAL}
    request_refund(order, actor=buyer, reason="货源问题")
    product.refresh_from_db()
    assert order.status == REFUNDED
    assert product.status == PRESALE


def test_refund_requires_paid_order(seller, buyer, category):
    """没付过款的订单不能走退款（REQ-016 只支持未发货前已付款的全额退款）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    with pytest.raises(OrderConflict):
        request_refund(order, actor=buyer)


def test_auto_refund_after_fifteen_days(seller, buyer, category):
    """超过 15 天未发货：系统自动全额退款（REQ-015）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    pay(order, actor=buyer, txn_no="T-010", now=base + timedelta(minutes=1))
    assert sweep(now=base + timedelta(days=16)) >= 1
    order.refresh_from_db()
    product.refresh_from_db()
    assert order.status == REFUNDED
    assert product.status == PUBLISHED


def test_auto_confirm_after_seven_days(seller, buyer, category):
    """普通订单发货后满 7 天自动确认收货并放款（REQ-016）。"""
    product = make_product(seller, category, status=PUBLISHED)
    base = timezone.now()
    order = order_now(product, buyer, now=base)
    pay(order, actor=buyer, txn_no="T-011", now=base + timedelta(minutes=1))
    ship(order, actor=seller, tracking_no="SF123459", now=base + timedelta(minutes=2))
    assert order.auto_confirm_at == base + timedelta(minutes=2) + timedelta(days=7)
    assert sweep(now=order.auto_confirm_at + timedelta(minutes=1)) >= 1
    order.refresh_from_db()
    product.refresh_from_db()
    assert order.status == COMPLETED
    assert product.status == SOLD


def test_presale_auto_confirm_uses_arrival_window(seller, buyer, category):
    """预售订单在确认到货后从下一天 00:00 起满 1 日自动确认并放款（REQ-016）。"""
    product = make_presale(seller, category)
    base = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    arrived = base + timedelta(hours=23)
    reservation = reserve(product, buyer=buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-101", now=base + timedelta(minutes=1))
    seller_confirm(reservation, actor=seller, now=base + timedelta(minutes=2))
    confirm_arrival(reservation, actor=seller, now=arrived)
    opens = reservation.final_due_at - FINAL_WINDOW
    pay_final(reservation, actor=buyer, txn_no="F-101", now=opens)
    order = reservation.order
    assert order.auto_confirm_at == next_day_window_end(reservation.arrived_at)
    ship(order, actor=seller, tracking_no="SF123460", now=opens + timedelta(minutes=10))
    assert sweep(now=order.auto_confirm_at + timedelta(minutes=1)) >= 1
    order.refresh_from_db()
    product.refresh_from_db()
    assert order.status == COMPLETED
    assert product.status == SOLD
    assert Settlement.objects.filter(order=order).count() == 1


def test_orders_for_scope(seller, buyer, other, support, category):
    """订单列表：当事人看自己的，客服看全部（REQ-002）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    assert orders_for(other).count() == 0
    assert orders_for(buyer).count() == 1
    assert orders_for(seller).count() == 1
    assert orders_for(support).count() == 1
    assert order.status == WAITING_PAY


def test_unique_active_order_index(seller, buyer, other, category):
    """数据库层部分唯一索引：同商品不能存在第二笔有效订单（并发兜底）。"""
    product = make_product(seller, category, status=PUBLISHED)
    order = order_now(product, buyer)
    with pytest.raises(IntegrityError):
        Order.objects.create(
            product=product,
            buyer=other,
            seller=seller,
            amount=Decimal("100.00"),
            status=WAITING_PAY,
            expires_at=order.expires_at,
        )
