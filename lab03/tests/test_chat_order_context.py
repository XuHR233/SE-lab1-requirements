"""复购复用消息，但订单状态、快照和工单各自独立。"""

import pytest
from django.test import Client

from catalog.models import ProductImage
from communication import services
from tests.conftest import make_product
from tests.helpers_m5 import create_order, paid_order, pay_order
from trade.services import cancel

CHAT = "/api/chats/"


def _client(actor):
    client = Client()
    client.force_login(actor)
    return client


def _open_chat(client, product, **scope):
    response = client.post(
        CHAT, {"product_id": product.pk, **scope}, content_type="application/json",
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture(name="refunded")
def _refunded(seller, buyer, category, support):
    order = paid_order(seller, category, buyer)
    ticket = services.raise_dispute(order.pk, buyer, reason="第一笔未发货")
    services.send_message(ticket.conversation, buyer, text="这是之前的留言")
    services.accept_dispute(ticket, support)
    services.resolve_dispute(ticket, support, verdict="退款", note="第一笔已退款")
    order.product.refresh_from_db()
    assert order.product.status == "已发布"
    return order, ticket


@pytest.mark.django_db
def test_repurchase_order_entry_does_not_reuse_old_order(refunded, buyer, seller):
    """买卖双方从新订单进入时，复用会话和留言，不复用已退款订单。"""
    old, old_ticket = refunded
    ProductImage.objects.filter(product=old.product).update(url="https://img.example/2.jpg")
    new = pay_order(create_order(old.product, buyer), buyer)
    assert new.pk != old.pk and new.product_id == old.product_id
    path = f"{CHAT}{old_ticket.conversation_id}/"
    for actor in (buyer, seller):
        client = _client(actor)
        row = _open_chat(client, old.product, order_id=new.pk)
        assert row["id"] == old_ticket.conversation_id
        assert (row["order_id"], row["order_status"]) == (new.pk, "待发货")
        new_view = client.get(path, {"order_id": new.pk}).json()
        old_view = client.get(path, {"order_id": old.pk}).json()
        assert new_view["conversation"]["order_status"] == "待发货"
        assert new_view["conversation"]["thumbnail"] == "https://img.example/2.jpg"
        assert new_view["dispute"] is None
        assert old_view["conversation"]["order_status"] == "已退款"
        assert old_view["conversation"]["thumbnail"] == "https://img.example/1.jpg"
        assert old_view["dispute"]["id"] == old_ticket.pk
        assert old_view["items"] == new_view["items"]
        assert [row["text"] for row in new_view["items"]] == ["这是之前的留言"]


def _default_order(client, path):
    detail = client.get(path).json()["conversation"]
    rows = client.get(CHAT).json()["items"]
    listing = next(row for row in rows if row["id"] == detail["id"])
    fields = ("order_id", "order_status", "order_is_historical")
    assert all(listing[key] == detail[key] for key in fields)
    return tuple(detail[key] for key in fields)


@pytest.mark.django_db
def test_messages_default_order_uses_all_three_parties_and_latest_history(refunded, buyer, other):
    """同一商品被另一买家锁定时，旧会话不会串到另一买家的订单。"""
    old, ticket = refunded
    path = f"{CHAT}{ticket.conversation_id}/"
    client = _client(buyer)
    assert _default_order(client, path) == (old.pk, "已退款", True)
    latest = create_order(old.product, buyer)
    assert _default_order(client, path) == (latest.pk, "待付款", False)
    assert client.get(path).json()["dispute"] is None
    cancel(latest, actor=buyer)
    old.product.refresh_from_db()
    other_order = create_order(old.product, other)
    assert _default_order(client, path) == (latest.pk, "已取消", True)
    assert client.get(path, {"order_id": other_order.pk}).status_code == 400
    assert _client(other).get(path).status_code == 403


@pytest.mark.django_db
def test_consultation_does_not_default_to_order_for_another_product(buyer, seller, category):
    """同一对账号就另一件商品有订单，咨询仍无订单或工单。"""
    ordered = paid_order(seller, category, buyer)
    product = make_product(seller, category)
    client = _client(buyer)
    row = _open_chat(client, product)
    path = f"{CHAT}{row['id']}/"
    assert _default_order(client, path) == (None, None, False)
    assert client.get(path).json()["dispute"] is None
    assert client.get(path, {"order_id": ordered.pk}).status_code == 400


@pytest.mark.django_db
def test_staff_and_members_see_the_dispute_for_the_selected_order(refunded, buyer, support):
    """客服从旧工单进入仍看退款单，复购单可以在同一会话独立发起工单。"""
    old, previous = refunded
    new = pay_order(create_order(old.product, buyer), buyer)
    path = f"{CHAT}{previous.conversation_id}/"
    agent, member = _client(support), _client(buyer)
    assert agent.get(path, {"order_id": new.pk}).status_code == 403
    response = member.post(
        CHAT + "disputes/", {"order_id": new.pk, "conversation_id": previous.conversation_id},
        content_type="application/json",
    )
    assert response.status_code == 201
    current = response.json()
    assert current["id"] != previous.pk and current["order_id"] == new.pk
    assert member.get(path).json()["dispute"]["id"] == current["id"]
    for ticket_id, order_id, order_status in (
        (previous.pk, old.pk, "已退款"), (current["id"], new.pk, "纠纷中"),
    ):
        view = agent.get(path, {"dispute_id": ticket_id}).json()
        assert (view["conversation"]["order_id"], view["conversation"]["order_status"]) == (
            order_id, order_status,
        )
        assert view["dispute"]["id"] == ticket_id
    assert agent.get(path, {"dispute_id": previous.pk, "order_id": new.pk}).status_code == 403
