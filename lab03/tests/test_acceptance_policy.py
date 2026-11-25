"""Regression tests for the user's revised Lab3 acceptance rules."""

from datetime import timedelta
from uuid import UUID

import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import OperationLog
from catalog.models import OFFSHELF, PENDING, PUBLISHED, AuditRecord, Product, ProductImage
from catalog.search import search
from communication.services import open_conversation
from reservations import services as reservations
from reservations.models import CANCELED
from reviews.models import Review
from reviews.stats import seller_stats
from tests.conftest import make_presale, make_product
from tests.helpers import build_order
from trade import services as trade
from trade.models import REFUNDED, WAITING_SHIP


def test_search_uses_last_approval(seller, category):
    """Latest approval, not the original draft date, drives ordering and filters."""
    first = make_product(seller, category)
    second = make_product(seller, category)
    now = timezone.now()
    for product, days in [(first, 3), (second, 2), (first, 1)]:
        AuditRecord.objects.create(product=product, approve=True, submitted_version=1,
                                   audited_at=now - timedelta(days=days))
    assert [row.pk for row in search()] == [first.pk, second.pk]
    assert [row.pk for row in search(sort="oldest")] == [second.pk, first.pk]
    assert list(search(published_period=(now - timedelta(days=1), now))) == [first]


def test_old_order_only_exposes_item_at_purchase(client, seller, buyer, category):
    """Closing an order never grants access to the next draft or live edits."""
    product = make_product(seller, category, title="原名称")
    Product.objects.filter(pk=product.pk).update(description="原介绍")
    conversation = open_conversation(buyer, product.pk)
    order = trade.create_order(product, buyer=buyer)
    trade.cancel(order, actor=buyer)
    Product.objects.filter(pk=product.pk).update(status=PENDING, title="新名称", description="新介绍")
    client.force_login(buyer)
    assert client.get(f"/api/products/{product.pk}/").status_code == 404
    frozen = client.get(f"/api/orders/{order.pk}/").json()["product_snapshot"]
    assert (frozen["title"], frozen["description"]) == ("原名称", "原介绍")
    assert client.get(f"/api/chats/{conversation.pk}/").json()["conversation"]["title"] == "原名称"


def test_only_frozen_image_is_visible_to_historical_buyer(  # pylint: disable=too-many-arguments
    client, settings, tmp_path, seller, buyer, *, category,
):
    """The order continues to show its backed image but never a newer draft upload."""
    settings.DEMO_UI = True
    settings.DEMO_MEDIA_ROOT = tmp_path
    product = make_product(seller, category, with_image=False)
    old_key, new_key = UUID(int=1), UUID(int=2)
    old_url = reverse("demo-product-file", kwargs={"pk": product.pk, "key": old_key})
    new_url = reverse("demo-product-file", kwargs={"pk": product.pk, "key": new_key})
    ProductImage.objects.create(product=product, url=old_url)
    order = trade.create_order(product, buyer=buyer)
    trade.cancel(order, actor=buyer)
    Product.objects.filter(pk=product.pk).update(status=PENDING)
    ProductImage.objects.filter(product=product).update(url=new_url)
    for key in (old_key, new_key):
        (tmp_path / f"{key}.webp").write_bytes(b"image placeholder")
    client.force_login(buyer)
    assert client.get(old_url).status_code == 200
    assert client.get(new_url).status_code == 404


def test_late_arrival_refunds_and_unpublishes(category, buyer, seller):
    """An arrival request on the deadline cannot bypass the pending scheduler."""
    now = timezone.now()
    presale = make_presale(seller, category)
    row = reservations.reserve(presale, buyer=buyer, now=now)
    row = reservations.pay_deposit(row, actor=buyer, txn_no="policy-deposit", now=now)
    row = reservations.seller_confirm(row, actor=seller, now=now)
    with pytest.raises(reservations.ReservationConflict, match="已到期"):
        reservations.confirm_arrival(row, actor=seller, now=row.expires_at)
    row.refresh_from_db()
    presale.refresh_from_db()
    assert (row.status, row.refund_due, presale.status) == (CANCELED, presale.deposit, OFFSHELF)
    assert reservations.sweep(now=row.expires_at) == 0
    assert row.refunds.count() == 1


def test_refund_only_before_48_hours(seller, buyer, category):
    """The window starts at payment and closes exactly at 48 hours."""
    order = build_order(seller, category, buyer, to=WAITING_SHIP)
    boundary = order.paid_at + timedelta(hours=48)
    with pytest.raises(trade.OrderConflict, match="48 小时"):
        trade.request_refund(order, actor=buyer, now=boundary)
    refunded = trade.request_refund(order, actor=buyer, now=boundary - timedelta(seconds=1))
    assert refunded.status == REFUNDED
    assert refunded.refunded_at == boundary - timedelta(seconds=1)


def test_delivery_deadline_refunds_once(seller, buyer, category):
    """At 15 days, even an attempted shipment first closes and refunds the order."""
    order = build_order(seller, category, buyer, to=WAITING_SHIP)
    boundary = order.paid_at + timedelta(days=15)
    with pytest.raises(trade.OrderConflict, match="已满 15 天"):
        trade.ship(order, actor=seller, tracking_no="TESTYYYY", now=boundary)
    order.refresh_from_db()
    assert (order.status, order.refunded_at, order.shipped_at) == (REFUNDED, boundary, None)
    assert order.product.status == PUBLISHED
    assert trade.sweep(now=boundary) == 0
    assert order.refunds.count() == 1


def test_refund_statistics_ignore_late_return(seller, buyer, category):
    """A late return in a disjoint period cannot count the already refunded order."""
    order = build_order(seller, category, buyer, to=WAITING_SHIP)
    boundary = order.paid_at
    order = trade.request_refund(order, actor=buyer, now=boundary)
    with pytest.raises(trade.OrderConflict, match="迟到"):
        trade.pay(order, actor=buyer, txn_no="policy-late")
    late = order.refunds.order_by("-pk").first()
    late.created_at = boundary + timedelta(days=4)
    late.save(update_fields=["created_at"])
    assert seller_stats(seller, start=boundary, end=boundary + timedelta(days=1))["refunded"] == 1
    assert seller_stats(seller, start=boundary + timedelta(days=1))["refunded"] == 0


def test_auto_review_is_not_appealable(client, seller, buyer, category):
    """Even an automatic review artificially inside the window is ineligible."""
    order = build_order(seller, category, buyer, to="待收货")
    order = trade.confirm_receipt(order, actor=buyer)
    review = Review.objects.create(
        order=order, author=buyer, target=seller, rating=5, automatic=True,
    )
    client.force_login(seller)
    reply = client.post(f"/api/reviews/{review.pk}/appeal/", {"reason": "测试"},
                        content_type="application/json")
    assert reply.status_code == 409
    assert "不可申诉" in reply.json()["error"]
    assert OperationLog.objects.filter(action="review.appeal", result="denied").exists()


def test_invalid_shipment_keeps_audit_reason(client, seller, buyer, category):
    """A shipment rejected during validation is searchable by order and action."""
    order = build_order(seller, category, buyer, to=WAITING_SHIP)
    client.force_login(seller)
    response = client.post(f"/api/orders/{order.pk}/ship/", {"tracking_no": "x"},
                           content_type="application/json")
    assert response.status_code == 400
    row = OperationLog.objects.get(action="order.ship", result="denied", target_id=str(order.pk))
    assert row.detail["reason"] == "运单号不合法"
    assert row.detail["product_id"] == order.product_id
