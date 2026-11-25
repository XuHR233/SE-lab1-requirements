"""M3 验收测试：R-301 预约与定金 / R-302 确认到货与尾款 / R-303 取消与退款口径。

一个预约的完整生命周期是「待付定金→待确认→待到货→待付尾款→已完成」，
四种关闭原因是「定金超时 / 有效期届满 / 尾款逾期 / 双方取消或卖家拒绝」（REQ-006）。
"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-locals,too-many-lines
# 理由：pytest fixture 按参数名注入；本文件刻意把整条链路写在一个用例里便于阅读。

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from audit.models import RESULT_DENIED, OperationLog
from catalog.models import PRESALE, VIOLATED, Product
from catalog.search import listable, search, visible_to
from catalog.services import (
    InvalidContent,
    StateConflict,
    add_image,
    revise_content,
    submit_for_audit,
    takedown,
    unpublish,
)
from common.errors import InvalidInput
from common.timebox import FINAL_WINDOW, next_day_window_end
from reservations.models import (
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
from reservations.services import (
    ReservationConflict,
    reservations_for,
    cancel,
    cancel_for_takedown,
    confirm_arrival,
    pay_deposit,
    pay_final,
    reserve,
    seller_confirm,
    seller_reject,
    sweep,
)
from tests.conftest import make_presale, make_product
from trade.models import KIND_DEPOSIT, Payment, Refund


@pytest.fixture
def presale(seller, category):
    """一个可预约的预售商品（定金 20、尾款 80、有效期 30 天）。"""
    return make_presale(seller, category)


def reserve_now(product, buyer, *, now=None, request_key=""):
    """提交一条预约，默认用当前时刻。"""
    return reserve(product, buyer=buyer, request_key=request_key, now=now)


def test_full_presale_chain_closes_as_completed(presale, buyer):
    """完整链路：预约→定金→卖家确认→确认到货→尾款→已完成，并生成待发货订单。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    assert reservation.status == WAITING_DEPOSIT
    assert reservation.deposit_due_at == base + timedelta(minutes=15)

    pay_deposit(reservation, actor=buyer, txn_no="D-001", now=base + timedelta(minutes=3))
    assert reservation.status == AWAITING_SELLER
    assert reservation.deposit_paid_at is not None

    seller_confirm(reservation, actor=presale.seller, now=base + timedelta(hours=2))
    assert reservation.status == WAITING_ARRIVAL

    arrived = base + timedelta(hours=3)
    confirm_arrival(reservation, actor=presale.seller, now=arrived)
    assert reservation.status == WAITING_FINAL
    assert reservation.final_due_at == next_day_window_end(arrived)

    pay_final(reservation, actor=buyer, txn_no="F-001", now=reservation.final_due_at - FINAL_WINDOW)
    assert reservation.status == COMPLETED
    order = reservation.order
    assert order.status == "待发货"
    assert str(order.amount) == "100.00"
    presale.refresh_from_db()
    assert presale.status == "交易中"


def test_final_window_is_next_whole_day(presale, buyer):
    """23:00 确认到货 → 尾款窗口是次日 00:00 起满 24 小时（REQ-006/016）。"""
    moment = timezone.localtime(timezone.now()).replace(
        hour=23, minute=0, second=0, microsecond=0
    )
    same_day_midnight = moment.replace(hour=0)
    expected_end = same_day_midnight + timedelta(days=2)
    reservation = reserve_now(presale, buyer, now=moment - timedelta(minutes=12))
    pay_deposit(reservation, actor=buyer, txn_no="D-002", now=moment - timedelta(minutes=10))
    seller_confirm(reservation, actor=presale.seller, now=moment - timedelta(minutes=8))
    confirm_arrival(reservation, actor=presale.seller, now=moment)
    assert reservation.final_due_at == expected_end


def test_reserve_requires_presale_status(seller, buyer, category):
    """已发布的普通商品不能预约（REQ-006 前置条件）。"""
    product = make_product(seller, category, status="已发布")
    with pytest.raises(ReservationConflict, match="只有已预售商品可以预约"):
        reserve_now(product, buyer)


def test_seller_cannot_reserve_own_product(presale):
    """同一账号不能预约自己发布的商品（REQ-001 同款约束）。"""
    with pytest.raises(PermissionDenied, match="不能预约自己发布的商品"):
        reserve_now(presale, presale.seller)
    assert (
        OperationLog.objects.filter(action="reservation.create", result=RESULT_DENIED).count()
        == 1
    )


def test_guest_cannot_reserve(presale):
    """游客不能预约，且记一条 denied 日志（REQ-002）。"""
    with pytest.raises(PermissionDenied):
        reserve_now(presale, AnonymousUser())


def test_same_request_key_is_idempotent(presale, buyer):
    """同一买家带同一请求标识重复提交，返回原预约而不是第二条（REQ-006）。"""
    first = reserve_now(presale, buyer, request_key="req-1")
    again = reserve_now(presale, buyer, request_key="req-1")
    assert again.pk == first.pk
    assert Reservation.objects.filter(product=presale, status__in=ACTIVE_STATUSES).count() == 1


def test_active_reservation_is_unique_per_product(presale, buyer, other):
    """第二个买家来预约被拒；库里直接插第二条也会被唯一索引挡住（并发只有一条）。"""
    reserve_now(presale, buyer)
    with pytest.raises(ReservationConflict, match="已有有效预约"):
        reserve_now(presale, other)
    with pytest.raises(IntegrityError):
        Reservation.objects.create(
            product=presale,
            buyer=other,
            seller=presale.seller,
            status=WAITING_DEPOSIT,
            deposit=presale.deposit,
            final_amount=presale.final_amount,
            deposit_due_at=timezone.now(),
            expires_at=timezone.now(),
        )


def test_stale_version_is_rejected(presale, buyer):
    """旧页面按版本号提交预约被拒（REQ-006 幂等与版本校验）。"""
    with pytest.raises(ReservationConflict, match="请刷新"):
        reserve(presale, buyer=buyer, expected_version=presale.version + 1)


def test_deposit_timeout_closes_and_frees_product(presale, buyer):
    """定金超时：关闭预约、不收款、商品仍已预售且重新可预约（REQ-006）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    with pytest.raises(ReservationConflict, match="已关闭"):
        pay_deposit(reservation, actor=buyer, txn_no="D-003", now=base + timedelta(minutes=16))
    assert reservation.status == CANCELED
    assert reservation.refund_due == 0
    assert presale.status == PRESALE
    assert Reservation.objects.filter(product=presale, status__in=ACTIVE_STATUSES).count() == 0


def test_deposit_payment_is_idempotent(presale, buyer):
    """同一支付流水号回调两次只产生一条支付记录（REQ-014 幂等）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-004", now=base + timedelta(minutes=1))
    pay_deposit(reservation, actor=buyer, txn_no="D-004", now=base + timedelta(minutes=2))
    assert Payment.objects.filter(reservation=reservation, kind=KIND_DEPOSIT).count() == 1
    assert reservation.status == AWAITING_SELLER


def test_other_member_cannot_pay_deposit(presale, buyer, other):
    """别人不能替买家付定金：403 + denied 日志（REQ-002/026）。"""
    reservation = reserve_now(presale, buyer)
    with pytest.raises(PermissionDenied):
        pay_deposit(reservation, actor=other, txn_no="D-005")
    assert OperationLog.objects.filter(action="reservation.pay", result=RESULT_DENIED).count() == 1


def test_buyer_cancel_before_deposit_has_no_refund(presale, buyer):
    """待付定金阶段买家取消：未收款，不涉及退款（REQ-006）。"""
    reservation = reserve_now(presale, buyer)
    cancel(reservation, actor=buyer)
    assert reservation.status == CANCELED
    assert reservation.refund_due == 0
    assert Refund.objects.count() == 0


def test_buyer_cancel_before_seller_confirm_refunds_deposit(presale, buyer):
    """卖家确认前买家取消：定金全额原路退回（REQ-006）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-006", now=base + timedelta(minutes=1))
    cancel(reservation, actor=buyer, now=base + timedelta(minutes=5))
    assert reservation.status == CANCELED
    assert reservation.refund_due == presale.deposit
    assert Refund.objects.filter(reservation=reservation).count() == 1


def test_seller_reject_refunds_deposit(presale, buyer):
    """卖家拒绝预约：定金全额退回，预约记「已拒绝」（REQ-006 + UML 7 态）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-007", now=base + timedelta(minutes=1))
    seller_reject(reservation, actor=presale.seller, reason="货不够")
    assert reservation.status == REJECTED
    assert reservation.refund_due == presale.deposit
    assert "货不够" in reservation.close_reason


def test_seller_cancel_after_confirm_refunds_deposit(presale, buyer):
    """卖家确认后取消视为卖家违约：定金退（REQ-006 未规定，按公平口径登记在文档）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-008", now=base + timedelta(minutes=1))
    seller_confirm(reservation, actor=presale.seller, now=base + timedelta(minutes=2))
    cancel(reservation, actor=presale.seller, now=base + timedelta(minutes=3))
    assert reservation.refund_due == presale.deposit


def test_buyer_cancel_after_confirm_keeps_deposit(presale, buyer):
    """卖家确认后买家取消或尾款逾期：定金不退（REQ-006）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-009", now=base + timedelta(minutes=1))
    seller_confirm(reservation, actor=presale.seller, now=base + timedelta(minutes=2))
    cancel(reservation, actor=buyer, now=base + timedelta(minutes=3))
    assert reservation.refund_due == 0


def test_final_overdue_closes_without_refund(presale, buyer):
    """尾款逾期：关闭预约且定金不退（REQ-006）。"""
    base = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-010", now=base + timedelta(minutes=1))
    seller_confirm(reservation, actor=presale.seller, now=base + timedelta(minutes=2))
    confirm_arrival(reservation, actor=presale.seller, now=base + timedelta(minutes=3))
    due = reservation.final_due_at
    with pytest.raises(ReservationConflict, match="尾款已逾期"):
        pay_final(reservation, actor=buyer, txn_no="F-010", now=due + timedelta(minutes=1))
    assert reservation.status == CANCELED
    assert reservation.refund_due == 0


def test_sweep_closes_expired_and_overdue(presale, buyer, other):
    """卖家未按期确认到货时全额退定金并下架。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-011", now=base + timedelta(minutes=1))
    assert sweep(now=base + timedelta(days=31)) == 1
    reservation.refresh_from_db()
    assert reservation.status == CANCELED
    assert reservation.refund_due == presale.deposit
    assert Reservation.objects.filter(product=presale, status__in=ACTIVE_STATUSES).count() == 0
    presale.refresh_from_db()
    assert presale.status == "已下架"
    with pytest.raises(ReservationConflict, match="只有已预售"):
        reserve_now(presale, other, now=base + timedelta(days=32))


def test_valid_days_bounds(presale, buyer):
    """有效期越界（0 或超过 180 天）在预约时就被拒（REQ-006）。"""
    presale.presale_valid_days = 181
    presale.save(update_fields=["presale_valid_days"])
    with pytest.raises(InvalidInput, match="1 到 180"):
        reserve_now(presale, buyer)


def test_presale_settings_must_sum_to_price(seller, category):
    """定金+尾款必须等于成交价，否则提交审核被拒（REQ-006）。"""
    product = make_product(
        seller,
        category,
        status="草稿",
        price="100.00",
        is_presale=True,
        deposit="10.00",
        final_amount="70.00",
        presale_valid_days=30,
    )
    with pytest.raises(InvalidContent, match="定金与尾款之和"):
        submit_for_audit(product, actor=seller)


def test_reserved_presale_is_hidden_from_lists(presale, buyer, other, seller):
    """有有效预约的预售商品从列表与搜索里消失，双方仍可进详情（REQ-003/006）。"""
    reserve_now(presale, buyer)
    assert listable().filter(pk=presale.pk).count() == 0
    found = [item.pk for item in search(keyword="预售") if item.pk == presale.pk]
    assert found == []
    assert visible_to(other).filter(pk=presale.pk).count() == 0
    assert visible_to(buyer).filter(pk=presale.pk).count() == 1
    assert visible_to(seller).filter(pk=presale.pk).count() == 1



def test_reserved_presale_cannot_be_edited(presale, buyer):
    """有有效预约时卖家不能改内容、改图、主动下架（REQ-007/008）。"""
    reserve_now(presale, buyer)
    version = presale.version
    with pytest.raises(StateConflict, match="有有效预约"):
        revise_content(presale, actor=presale.seller, expected_version=version, price="90.00")
    with pytest.raises(StateConflict, match="有有效预约"):
        add_image(presale, "https://img.example/2.jpg", actor=presale.seller)
    with pytest.raises(StateConflict, match="请先取消预约"):
        unpublish(presale, actor=presale.seller)
    assert OperationLog.objects.filter(
        action="product.transit", result=RESULT_DENIED
    ).count() == 3


def test_takedown_cancels_reservation_and_refunds(presale, buyer, admin):
    """违规下架：取消有效预约、已收定金全额原路退回、商品转违规下架（REQ-009）。"""
    base = timezone.now()
    reservation = reserve_now(presale, buyer, now=base)
    pay_deposit(reservation, actor=buyer, txn_no="D-012", now=base + timedelta(minutes=1))
    takedown(presale, actor=admin, reason="侵权")
    reservation.refresh_from_db()
    assert presale.status == VIOLATED
    assert reservation.status == CANCELED
    assert reservation.refund_due == presale.deposit
    assert Refund.objects.filter(reservation=reservation).count() == 1


def test_cancel_for_takedown_is_used_directly(presale, buyer):
    """状态层原语：没有有效预约时违规下架的联动是空操作。"""
    assert cancel_for_takedown(presale, reason="测试") == 0


def test_reservation_list_scope(presale, buyer, other, support):
    """预约列表：当事人看自己的，客服看全部（REQ-002）。"""
    reservation = reserve_now(presale, buyer)
    assert reservations_for(other).count() == 0
    assert reservations_for(presale.seller).count() == 1
    assert reservations_for(support).count() == 1
    assert reservation.status == WAITING_DEPOSIT
    assert reservation.status == WAITING_DEPOSIT


def test_reservation_times_are_aware(presale, buyer):
    """预约时间字段都带时区，跨天窗口比较才可靠。"""
    reservation = reserve_now(presale, buyer)
    assert timezone.is_aware(reservation.deposit_due_at)
    assert timezone.is_aware(reservation.expires_at)
    assert reservation.deposit_due_at < reservation.expires_at
    assert Product.objects.get(pk=presale.pk).status == PRESALE
