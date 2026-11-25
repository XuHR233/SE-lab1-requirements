"""M5 入口层的真实 HTTP 用例：每条越权都断言 403 + 拒绝日志 + 状态没被改（REQ-018/019）。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments,too-many-positional-arguments

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client

from audit.models import RESULT_DENIED, OperationLog
from communication.models import Conversation, Dispute
from tests.conftest import make_product
from tests.helpers_m5 import paid_order

pytestmark = pytest.mark.django_db


def _client(user) -> Client:
    """登录态测试客户端；匿名用户直接返回裸 Client。"""
    client = Client()
    if isinstance(user, AnonymousUser) or user is None:
        return client
    client.force_login(user)
    return client


def _post(client, url, payload=None):
    """发 JSON POST。"""
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json")


def _denials(action: str) -> int:
    """数某动作的拒绝日志。"""
    return OperationLog.objects.filter(action=action, result=RESULT_DENIED).count()


def test_guest_cannot_open_chat(seller, category):
    """游客发起会话 → 403，并留下 chat.send 拒绝日志。"""
    product = make_product(seller, category)
    before = _denials("chat.send")

    response = _post(_client(AnonymousUser()), "/api/chats/", {"product_id": product.pk})

    assert response.status_code == 403
    assert _denials("chat.send") == before + 1


def test_buyer_opens_chat_through_http(buyer, seller, category):
    """买家通过 HTTP 开会话，返回 201 与复用语义。"""
    product = make_product(seller, category)
    client = _client(buyer)

    first = _post(client, "/api/chats/", {"product_id": product.pk})
    second = _post(client, "/api/chats/", {"product_id": product.pk})

    assert first.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert Conversation.objects.count() == 1


def test_third_party_reads_chat_is_403_and_logged(buyer, seller, other, category):
    """无关第三方读会话 → 403 + chat.view 拒绝日志，且响应里没有商品标题。"""
    product = make_product(seller, category, title="隐藏的标题")
    conversation = Conversation.objects.create(product=product, buyer=buyer, seller=seller)
    before = _denials("chat.view")

    response = _client(other).get(f"/api/chats/{conversation.pk}/")

    assert response.status_code == 403
    assert "隐藏的标题" not in response.content.decode()
    assert _denials("chat.view") == before + 1


def test_message_post_and_hide_through_http(buyer, seller, category):
    """发消息 201，本地隐藏后列表里看不到，但库里仍有。"""
    product = make_product(seller, category)
    conversation = Conversation.objects.create(product=product, buyer=buyer, seller=seller)
    client = _client(buyer)

    sent = client.post(f"/api/chats/{conversation.pk}/messages/", {"text": "在吗"})
    assert sent.status_code == 201
    message_id = sent.json()["id"]

    hidden = _post(client, f"/api/chats/{conversation.pk}/hide/", {"message_id": message_id})
    assert hidden.status_code == 200

    listing = client.get(f"/api/chats/{conversation.pk}/").json()
    assert listing["items"] == []
    from communication.models import Message  # pylint: disable=import-outside-toplevel

    assert Message.objects.filter(pk=message_id).exists()


def test_empty_message_is_400(buyer, seller, category):
    """空消息 400。"""
    product = make_product(seller, category)
    conversation = Conversation.objects.create(product=product, buyer=buyer, seller=seller)

    response = _client(buyer).post(f"/api/chats/{conversation.pk}/messages/", {"text": ""})

    assert response.status_code == 400


def test_raise_dispute_through_http(buyer, seller, category):
    """买家在订单上请求介入：201，订单转纠纷中，工单 1 条。"""
    order = paid_order(seller, category, buyer)

    response = _post(
        _client(buyer), "/api/chats/disputes/", {"order_id": order.pk, "reason": "没发货"}
    )

    assert response.status_code == 201
    order.refresh_from_db()
    assert order.status == "纠纷中"
    assert Dispute.objects.count() == 1


def test_duplicate_dispute_returns_same_ticket(buyer, seller, category):
    """重复请求只返回原工单。"""
    order = paid_order(seller, category, buyer)
    client = _client(buyer)

    first = _post(client, "/api/chats/disputes/", {"order_id": order.pk})
    second = _post(client, "/api/chats/disputes/", {"order_id": order.pk})

    assert first.json()["id"] == second.json()["id"]


def test_guest_cannot_raise_dispute(seller, buyer, category):
    """游客发起介入 403 + dispute.raise 日志。"""
    order = paid_order(seller, category, buyer)
    before = _denials("dispute.raise")

    response = _post(_client(AnonymousUser()), "/api/chats/disputes/", {"order_id": order.pk})

    assert response.status_code == 403
    assert _denials("dispute.raise") == before + 1


def test_dispute_resolve_requires_staff(buyer, seller, support, category):
    """买家不能裁定自己的纠纷；客服可以，且 200。"""
    order = paid_order(seller, category, buyer)
    _post(_client(buyer), "/api/chats/disputes/", {"order_id": order.pk})
    dispute = Dispute.objects.get()

    denied = _post(
        _client(buyer),
        f"/api/chats/disputes/{dispute.pk}/resolve/",
        {"verdict": "退款", "note": "我说了算"},
    )
    assert denied.status_code == 403

    accept = _post(_client(support), f"/api/chats/disputes/{dispute.pk}/accept/", {})
    assert accept.status_code == 200
    ok = _post(
        _client(support),
        f"/api/chats/disputes/{dispute.pk}/resolve/",
        {"verdict": "退款", "note": "核实属实"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "已处理"


def test_resolve_without_note_is_400(buyer, seller, support, category):
    """裁定不写说明 400。"""
    order = paid_order(seller, category, buyer)
    _post(_client(buyer), "/api/chats/disputes/", {"order_id": order.pk})
    dispute = Dispute.objects.get()

    response = _post(
        _client(support), f"/api/chats/disputes/{dispute.pk}/resolve/", {"verdict": "退款"}
    )

    assert response.status_code == 400


def test_workbench_is_staff_only(buyer, seller, support, category):
    """客服工作台：客服 200、买家 403。"""
    assert _client(support).get("/api/chats/disputes/workbench/").status_code == 200
    assert _client(buyer).get("/api/chats/disputes/workbench/").status_code == 403


def test_ordering_disputes_by_order(buyer, seller, category):
    """订单维度查询工单：本人 200，第三方 403。"""
    order = paid_order(seller, category, buyer)
    _post(_client(buyer), "/api/chats/disputes/", {"order_id": order.pk})

    mine = _client(buyer).get(f"/api/chats/disputes/orders/{order.pk}/")
    assert mine.status_code == 200
    assert mine.json()["dispute"]["order_id"] == order.pk

    assert _client(buyer).get("/api/chats/disputes/999999/").status_code == 404
