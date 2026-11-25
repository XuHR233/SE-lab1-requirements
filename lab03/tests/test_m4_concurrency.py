"""50 个买家抢同一件商品：只允许 1 笔订单成功（REQ-013 验收）。

为什么单独放在一个文件里：这条用例必须用非事务的 pytest.mark.django_db 模式，
因为 Django 的事务级测试只包住当前线程，工作线程自己拿连接时看不到主线程那笔未提交的
商品与买家数据；而 pytest-django 的 marker 是模块级的。其余 M4 用例仍在 tests/test_m4.py
的事务模式里跑，两者互不影响。
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import threading
from collections import Counter

import pytest
from django.contrib.auth import get_user_model
from django.db import connections

from catalog.models import LOCKED, PUBLISHED, Product
from tests.conftest import make_product
from trade.models import WAITING_PAY, Order
from trade.services import OrderConflict, create_order

User = get_user_model()

pytestmark = pytest.mark.django_db(transaction=True)


def test_fifty_concurrent_orders_only_one_wins(seller, category):
    """50 个买家同时下单：只有 1 笔落库，其余 49 个得到「已被锁定」（REQ-013 验收）。"""
    product = make_product(seller, category, status=PUBLISHED)
    buyers = [
        User.objects.create_user(username=f"b{i}", phone=f"1390000{i:04d}", password="pw-A1b2c3")
        for i in range(50)
    ]
    start = threading.Barrier(len(buyers))
    product_pk = product.pk
    outcomes: list[tuple[str, int | str]] = []
    lock = threading.Lock()

    def attempt(user):
        """每个线程抢一次下单；异常必须带回主线程，否则看不出失败原因。"""
        start.wait()
        try:
            fresh = Product.objects.get(pk=product_pk)
            made: tuple[str, int | str] = ("ok", create_order(fresh, buyer=user).pk)
        except OrderConflict as exc:
            made = ("conflict", str(exc))
        except Exception as exc:  # pylint: disable=broad-except
            made = ("error", repr(exc))
        finally:
            connections.close_all()
        with lock:
            outcomes.append(made)

    threads = [threading.Thread(target=attempt, args=(user,)) for user in buyers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    kinds = Counter(kind for kind, _ in outcomes)
    assert kinds == Counter({"ok": 1, "conflict": 49}), outcomes
    assert {value for kind, value in outcomes if kind == "conflict"} == {"该商品已被锁定"}
    assert Order.objects.filter(product_id=product_pk).count() == 1
    assert Order.objects.filter(product_id=product_pk, status=WAITING_PAY).count() == 1
    product.refresh_from_db()
    assert product.status == LOCKED
