"""Day2–7 review regressions for state transitions and operational boundaries."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from catalog.models import OFFSHELF, PRESALE, PUBLISHED, VIOLATED
from catalog.services import takedown
from common.errors import Conflict
from common.timebox import FINAL_WINDOW
from communication import services as disputes
from communication.models import VERDICT_REFUND
from reservations import services as reservations
from reviews import services as reviews
from tests.conftest import make_presale, make_product
from tests.helpers import build_order
from trade import services as trade
from trade.models import CANCELED, WAITING_SHIP, Refund


def test_search_status_filter(client, seller, category):
    """Public status filtering still excludes nonpublic inventory."""
    make_product(seller, category, status=PUBLISHED)
    make_presale(seller, category)
    response = client.get('/api/products/', {'status': PUBLISHED})
    assert [row['status'] for row in response.json()['items']] == [PUBLISHED]
    assert client.get('/api/products/', {'status': OFFSHELF}).json()['items'] == []
    assert client.get('/api/products/', {'status': PRESALE}).json()['items']


def test_stale_cancel_cannot_override_payment(seller, buyer, category):
    """A stale pending-order object cannot cancel a paid order."""
    order = build_order(seller, category, buyer)
    stale = type(order).objects.get(pk=order.pk)
    trade.pay(order, actor=buyer, txn_no='paid-first')
    with pytest.raises(trade.OrderConflict):
        trade.cancel(stale, actor=seller)
    order.refresh_from_db()
    assert order.status == WAITING_SHIP
    assert order.payments.count() == 1 and not order.refunds.exists()


@pytest.mark.parametrize('close_with', ['cancel', 'takedown'])
def test_late_payment_does_not_restore_order(seller, buyer, category, admin, close_with):
    """Cancellation and takedown stay terminal with one late refund entry."""
    order = build_order(seller, category, buyer)
    stale = type(order).objects.get(pk=order.pk)
    if close_with == 'cancel':
        trade.cancel(order, actor=buyer)
    else:
        takedown(order.product, actor=admin, reason='违反规则')
    for _attempt in range(2):
        with pytest.raises(trade.OrderConflict):
            trade.pay(stale, actor=buyer, txn_no='late-twice')
    order.refresh_from_db()
    assert order.status == CANCELED
    assert order.product.status == (VIOLATED if close_with == 'takedown' else PUBLISHED)
    assert order.payments.count() == order.refunds.count() == 1


def test_takedown_of_unpaid_presale_never_refunds(seller, buyer, category, admin):
    """No receipt exists when an unpaid reservation is taken down."""
    product = make_presale(seller, category)
    reservation = reservations.reserve(product, buyer=buyer)
    takedown(product, actor=admin, reason='违反规则')
    reservation.refresh_from_db()
    assert reservation.status == CANCELED
    assert not Refund.objects.filter(reservation=reservation).exists()


def test_presale_final_window_opens_at_midnight(seller, buyer, category):
    """One second before midnight is refused, at midnight it is allowed."""
    product = make_presale(seller, category)
    reservation = reservations.reserve(product, buyer=buyer)
    reservations.pay_deposit(reservation, actor=buyer, txn_no='deposit')
    reservations.seller_confirm(reservation, actor=seller)
    reservations.confirm_arrival(reservation, actor=seller)
    opens = reservation.final_due_at - FINAL_WINDOW
    with pytest.raises(reservations.ReservationConflict, match='尚未开启'):
        reservations.pay_final(reservation, actor=buyer, txn_no='final',
                               now=opens - timedelta(seconds=1))
    reservations.pay_final(reservation, actor=buyer, txn_no='final', now=opens)
    assert reservation.order.status == WAITING_SHIP


def test_dispute_chat_and_shipped_refund(seller, buyer, category, support, other):
    """Staff sees only a linked dispute; shipped refund keeps inventory off sale."""
    product = make_product(seller, category)
    chat = disputes.open_conversation(buyer, product.pk)
    order = build_order(seller, category, buyer, product=product, to='待收货')
    with pytest.raises(PermissionDenied):
        disputes.read_conversation(chat, support)
    ticket = disputes.raise_dispute(order.pk, buyer)
    assert disputes.read_conversation(chat, support).pk == chat.pk
    with pytest.raises(PermissionDenied):
        disputes.read_conversation(chat, other)
    disputes.accept_dispute(ticket, support)
    disputes.resolve_dispute(ticket, support, verdict=VERDICT_REFUND, note='处理已发货纠纷')
    product.refresh_from_db()
    assert product.status == OFFSHELF


def test_review_appeal_window(seller, buyer, category):
    """An appeal is refused at the end of the review window."""
    order = build_order(seller, category, buyer, to='待收货')
    trade.confirm_receipt(order, actor=buyer, now=timezone.now())
    review = reviews.submit(order, buyer, rating=3)
    with pytest.raises(Conflict, match='窗口已结束'):
        reviews.appeal(review, seller, reason='说明', now=reviews.deadline(order))


def test_simulated_payments_fail_closed(client, settings, seller, buyer, category):
    """When simulation is disabled HTTP cannot generate payment records."""
    settings.SIMULATED_PAYMENTS = False
    order = build_order(seller, category, buyer)
    client.force_login(buyer)
    response = client.post(f'/api/orders/{order.pk}/pay/', {'txn_no': 'no-channel'},
                           content_type='application/json')
    assert response.status_code == 503
    assert not order.payments.exists()
