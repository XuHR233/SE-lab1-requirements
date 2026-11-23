"""日志查询和 PostgreSQL 只追加约束验收。"""

# pylint: disable=too-many-arguments,too-many-positional-arguments

from datetime import timedelta

from django.utils import timezone

from audit.models import OperationLog
from tests.helpers import build_order


def test_staff_filter_and_default_confirmation(client, admin, support, buyer, seller, category):
    """客服可组合筛选、管理员查询空条件需确认。"""
    order = build_order(seller, category, buyer)
    client.force_login(support)
    result = client.get("/api/logs/", {
        "product_id": order.product_id, "actor_id": buyer.pk, "action": "order.create",
        "result": "allowed",
    })
    assert result.status_code == 200
    assert [row["actor_id"] for row in result.json()["rows"]] == [buyer.pk]
    client.force_login(admin)
    assert client.get("/api/logs/").json()["confirmation_required"]
    assert client.get("/api/logs/", {"confirm": "1"}).json()["rows"]


def test_order_timeline_permissions(client, seller, buyer, other, category):
    """当事人只见时间线；他人被拒绝并留痕。"""
    order = build_order(seller, category, buyer)
    params = {"order_id": order.pk}
    client.force_login(buyer)
    response = client.get("/api/logs/", params)
    assert [row["action"] for row in response.json()["rows"]] == ["order.create"]
    assert "detail" not in response.json()["rows"][0]
    client.force_login(other)
    assert client.get("/api/logs/", params).status_code == 403
    assert OperationLog.objects.filter(actor=other, action="log.view", result="denied").exists()


def test_archived_detail_and_pagination(client, admin, support):
    """跨页稳定倒序，180 天前的日志只对管理员开放。"""
    now = timezone.now()
    old = OperationLog.objects.create(action="archive-test", result="allowed",
                                      created_at=now - timedelta(days=181))
    OperationLog.objects.bulk_create([
        OperationLog(action="archive-test", result="allowed", created_at=now)
        for _index in range(51)
    ])
    detail_url = f"/api/logs/{old.pk}/"
    client.force_login(support)
    assert client.get(detail_url).status_code == 404
    page = client.get("/api/logs/", {"action": "archive-test"}).json()
    assert page["has_next"]
    assert len(page["rows"]) == 50
    first = [row["id"] for row in page["rows"]]
    assert first == sorted(first, reverse=True)
    page = client.get("/api/logs/", {"action": "archive-test", "page": 2}).json()
    assert len(page["rows"]) == 1 and not page["has_next"]
    assert page["rows"][0]["id"] < first[-1]
    client.force_login(admin)
    assert client.get(detail_url).json()["id"] == old.pk
