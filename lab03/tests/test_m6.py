"""评价与统计验收。"""

# pylint: disable=redefined-outer-name,too-many-arguments,too-many-positional-arguments
from datetime import timedelta

import pytest
from django.utils import timezone

from audit.models import OperationLog
from common.errors import Conflict
from reviews import services
from reviews.models import Review
from tests.helpers import build_order
from trade.services import confirm_receipt


@pytest.fixture
def completed(seller, buyer, category):
    """交易完成，评价窗口从收货时刻计算。"""
    order = build_order(seller, category, buyer, to="待收货", now=timezone.now() - timedelta(hours=1))
    return confirm_receipt(order, actor=buyer)


def test_both_parties_and_update(client, completed, buyer, seller, other):
    """双方各评一次；修改不新增；无关人员不可评价。"""
    url = f"/api/orders/{completed.pk}/review/"
    client.force_login(buyer)
    first = client.post(url, {"rating": 4}, content_type="application/json")
    second = client.post(url, {"rating": 3, "content": "复评"}, content_type="application/json")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    client.force_login(seller)
    assert client.post(url, {"rating": 5}, content_type="application/json").status_code == 200
    client.force_login(other)
    assert client.post(url, {"rating": 1}, content_type="application/json").status_code == 403
    reviews_url = f"/api/products/{completed.product_id}/reviews/"
    assert client.get(reviews_url).status_code == 404
    client.force_login(buyer)
    response = client.get(reviews_url)
    assert sorted(item["rating"] for item in response.json()["reviews"]) == [3, 5]


def test_deadline_sweep_does_not_recreate_deleted(completed, buyer, seller):
    """边界前可删除，到期补齐缺的一方且重复运行不增记录。"""
    closed = services.deadline(completed)
    before = closed - timedelta(seconds=1)
    entry = services.submit(completed, buyer, rating=2, now=before)
    services.delete(completed, buyer, now=before)
    with pytest.raises(Conflict):
        services.submit(completed, seller, rating=3, now=closed)
    assert services.sweep(now=closed) == (1, 0)
    assert services.sweep(now=closed) == (0, 0)
    entry.refresh_from_db()
    assert entry.deleted_at and entry.rating == 2
    assert Review.objects.get(order=completed, author=seller).automatic


def test_appeal_reminder_then_admin_hides(client, completed, buyer, seller, admin):
    """提醒不自动改判；管理员判申诉成立后才隐藏。"""
    review = services.submit(completed, buyer, rating=1)
    entry = services.appeal(review, seller, reason="内容有误")
    assert services.sweep(now=entry.due_at - timedelta(seconds=1))[1] == 0
    assert services.sweep(now=entry.due_at)[1] == 1
    assert services.sweep(now=entry.due_at)[1] == 0
    client.force_login(buyer)
    url = f"/api/products/{completed.product_id}/reviews/"
    assert len(client.get(url).json()["reviews"]) == 1
    services.resolve(entry, admin, upheld=True, note="经核实")
    assert client.get(url).json()["reviews"] == []


def test_pending_order_cannot_be_reviewed(client, seller, buyer, category):
    """待付款未完成，买家不能提前提交评价。"""
    order = build_order(seller, category, buyer)
    client.force_login(buyer)
    response = client.post(f"/api/orders/{order.pk}/review/", {"rating": 5},
                           content_type="application/json")
    assert response.status_code == 409


def test_rejected_and_deleted_reviews_are_audited(client, completed, buyer):
    """非法评分不写入评价；拒绝和删除都有记录。"""
    url = f"/api/orders/{completed.pk}/review/"
    client.force_login(buyer)
    assert client.post(url, {"rating": True}, content_type="application/json").status_code == 400
    assert client.post(url, {"rating": 3}, content_type="application/json").status_code == 200
    assert client.delete(url).status_code == 200
    events = OperationLog.objects.filter(target_type="order", target_id=str(completed.pk),
                                         action__startswith="review.").order_by("id")
    assert list(events.values_list("action", "result")) == [
        ("review.submit", "denied"), ("review.submit", "allowed"), ("review.delete", "allowed"),
    ]
