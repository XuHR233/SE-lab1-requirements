"""客服介入变更：真实 HTTP、转交与裁定竞争。"""

# pylint: disable=redefined-outer-name
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections
from django.test import Client

from audit.models import OperationLog
from communication import services
from tests.helpers_m5 import paid_order


def _session(actor):
    client = Client()
    client.force_login(actor)
    return client


def _action(client, path, action, **data):
    return client.post(path + action + "/", data, content_type="application/json")


@pytest.fixture
def case(seller, buyer, category):
    """从订单发起介入，不必预先开会话。"""
    order = paid_order(seller, category, buyer)
    ticket = services.raise_dispute(order.pk, buyer, reason="请核实履约")
    return ticket, f"/api/chats/disputes/{ticket.pk}/", f"/api/chats/{ticket.conversation_id}/"


@pytest.mark.django_db
def test_handoff_http(case, support, admin, buyer, *, seller, other):
    """接单后同屏交流；转交收回旧权限；只有接管人能结案。"""
    ticket, path, chat = case
    agent, manager = _session(support), _session(admin)
    message = {"text": "我来核实。", "dispute_id": ticket.pk}
    resolution = {"verdict": "退款", "note": "核实应退款"}
    assert ticket.conversation_id
    assert agent.get(chat).status_code == 200
    assert _session(other).get(chat).status_code == 403
    assert agent.post(chat + "messages/", message).status_code == 403
    assert _action(agent, path, "resolve", **resolution).status_code == 409
    assert _action(agent, path, "reject", note="恢复流程").status_code == 409
    assert _action(agent, path, "accept").json()["handler_id"] == support.pk
    assert _action(manager, path, "accept").status_code == 409
    assert _action(manager, path, "resolve", **resolution).status_code == 409
    assert agent.post(chat + "messages/", message).json()["staff_role"] == "support"
    assert _action(agent, path, "escalate", note=" ").status_code == 400
    escalation = _action(agent, path, "escalate", note="需要复核证据").json()
    assert escalation["handler_id"] is None and escalation["escalated_at"]
    assert _action(agent, path, "accept").status_code == 409
    assert _action(manager, path, "resolve", **resolution).status_code == 409
    assert _action(manager, path, "accept").json()["handler_id"] == admin.pk
    assert agent.post(chat + "messages/", message).status_code == 403
    assert _action(agent, path, "reject", note="恢复流程").status_code == 409
    assert manager.post(chat + "messages/", message).json()["staff_role"] == "admin"
    _verify_closure(case, manager, (buyer, seller), message, resolution)


def _verify_closure(case, manager, parties, message, resolution):
    ticket, path, chat = case
    for actor in parties:
        assert [row["staff_role"] for row in _session(actor).get(chat).json()["items"]] == [
            "support", "admin",
        ]
    decision = _action(manager, path, "resolve", **resolution).json()
    assert decision["handler_id"] != ticket.raised_by_id and decision["status"] == "已处理"
    assert _action(manager, path, "resolve", **resolution).status_code == 409
    assert manager.post(chat + "messages/", message).status_code == 403
    assert _session(parties[0]).post(chat + "messages/", {"text": "已收到"}).status_code == 201
    ticket.order.refresh_from_db()
    assert ticket.order.status == "已退款" and ticket.order.refunds.count() == 1
    history = OperationLog.objects.filter(target_type="dispute", target_id=str(ticket.pk),
                                          result="allowed").order_by("id")
    actions = [entry.detail["action"] for entry in history]
    assert actions == ["accept", "escalate", "takeover", "resolve"]
    assert history.last().actor_id == decision["handler_id"]
    timeline = manager.get(f"/api/logs/?order_id={ticket.order_id}").json()["rows"]
    assert {item["id"] for item in timeline} >= {entry.pk for entry in history}


def _race(action, *actors):
    ready = Barrier(len(actors), timeout=10)

    def worker(actor):
        try:
            ready.wait()
            return action(actor).handler_id
        except services.DisputeConflict:
            return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(actors)) as pool:
        return list(pool.map(worker, actors))


@pytest.mark.django_db(transaction=True)
def test_two_staff_cannot_claim_or_close_twice(case, support, admin):
    """并发接单恰好一人成功，重复并发裁定也只写一次退款。"""
    ticket, _, _ = case
    claimed = _race(lambda actor: services.accept_dispute(ticket, actor), support, admin)
    assert claimed.count(None) == 1
    winner = support if support.pk in claimed else admin

    def resolve(actor):
        return services.resolve_dispute(ticket, actor, verdict="退款", note="核实")

    assert _race(resolve, winner, winner).count(winner.pk) == 1
    assert ticket.order.refunds.count() == 1
