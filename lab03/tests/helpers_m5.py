"""M5 用例专用的最小构造器：只走公开的订单/预约用例把单据推进到某个状态。

M5 不 import 别人的测试构造器，自己写一遍，顺便证明「只调对外接口」不是空话。
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from common.timebox import FINAL_WINDOW
from trade.models import Order
from trade.services import create_order as new_order
from trade.services import pay

from .conftest import make_presale, make_product


def _txn(prefix: str) -> str:
    """每次支付用唯一流水号，避免幂等判定把用例串起来。"""
    return f"{prefix}-{uuid4().hex[:10]}"


def create_order(product, buyer, *, now=None) -> Order:
    """下单一笔普通商品订单（待付款）。"""
    return new_order(product, buyer=buyer, now=now)


def pay_order(order, buyer, *, now=None) -> Order:
    """支付一笔已下的订单，返回刷新后的订单（待发货）。"""
    pay(order, actor=buyer, txn_no=_txn("O"), now=now)
    order.refresh_from_db()
    return order


def paid_order(seller, category, buyer, *, now=None) -> Order:
    """造一笔「待发货」的普通订单。"""
    product = make_product(seller, category, price="100.00")
    return pay_order(create_order(product, buyer, now=now), buyer, now=now)


def reserve_presale(seller, category, buyer, *, now=None):
    """造一条「已完成」的预售预约（定金+尾款都付了），返回商品与预约。"""
    from reservations.services import (  # pylint: disable=import-outside-toplevel
        confirm_arrival,
        pay_deposit,
        pay_final,
        reserve,
        seller_confirm,
    )

    product = make_presale(seller, category)
    reservation = reserve(product, buyer=buyer, now=now)
    pay_deposit(reservation, actor=buyer, txn_no=_txn("D"), now=now)
    seller_confirm(reservation, actor=seller, now=now)
    confirm_arrival(reservation, actor=seller, now=now)
    opens = reservation.final_due_at - FINAL_WINDOW
    pay_final(reservation, actor=buyer, txn_no=_txn("F"), now=opens)
    reservation.refresh_from_db()
    return product, reservation


def amount(value: str) -> Decimal:
    """两位小数的金额字面量。"""
    return Decimal(value)
