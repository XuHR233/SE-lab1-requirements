"""M3/M4 测试用的状态构造器：把预约与订单推进到指定阶段。

只用于测试——生产路径必须走各自的用例层。凡是要「推进」的地方都调用真实服务函数，
所以这些阶段不是伪造的脏数据，而是合法走到那一步的结果。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.utils import timezone

from catalog.models import PUBLISHED
from reservations.models import Reservation
from reservations.services import (
    confirm_arrival,
    pay_deposit,
    reserve,
    seller_confirm,
)
from tests.conftest import make_presale, make_product
from trade.models import WAITING_PAY, WAITING_RECEIPT, WAITING_SHIP, Order
from trade.services import create_order, pay, ship

RESERVATION_STEPS = {"待付定金": 0, "待确认": 1, "待到货": 2, "待付尾款": 3}


def _txn(prefix: str) -> str:
    """每次支付都用唯一流水号，避免幂等判定干扰测试。"""
    return f"{prefix}-{uuid4().hex[:10]}"


def build_reservation(
    seller, category, buyer, *, to: str = "待付定金", now=None, product=None
) -> Reservation:
    """把一条预约推进到指定状态（真实服务调用，不是直写数据库）。"""
    base = now or timezone.now()
    product = product or make_presale(seller, category)
    reservation = reserve(product, buyer=buyer, now=base)
    step = RESERVATION_STEPS[to]
    if step >= 1:
        pay_deposit(
            reservation, actor=buyer, txn_no=_txn("D"), now=base + timedelta(minutes=1)
        )
    if step >= 2:
        seller_confirm(reservation, actor=seller, now=base + timedelta(minutes=2))
    if step >= 3:
        confirm_arrival(reservation, actor=seller, now=base + timedelta(minutes=3))
    reservation.refresh_from_db()
    assert reservation.status == to
    return reservation


def build_order(seller, category, buyer, *, to: str = "待付款", now=None, product=None) -> Order:
    """把一笔普通商品订单推进到指定状态（同样走真实服务调用）。"""
    base = now or timezone.now()
    product = product or make_product(seller, category, status=PUBLISHED, price="100.00")
    order = create_order(product, buyer=buyer, now=base)
    assert order.status == WAITING_PAY
    if to in ("待发货", "待收货"):
        pay(order, actor=buyer, txn_no=_txn("O"), now=base + timedelta(minutes=1))
        order.refresh_from_db()
        assert order.status == WAITING_SHIP
    if to == "待收货":
        ship(
            order,
            actor=seller,
            tracking_no="SF12345678",
            now=base + timedelta(minutes=2),
        )
        order.refresh_from_db()
        assert order.status == WAITING_RECEIPT
    return order


def presale_product(seller, category, *, title="预售相机", price="100.00"):
    """一条已预售商品：定金 20、尾款 80、有效期 30 天。"""
    return make_presale(seller, category, title=title, price=price)


def plain_product(seller, category, *, title="普通相机", price="100.00"):
    """一条已发布商品。"""
    return make_product(seller, category, status=PUBLISHED, title=title, price=price)
