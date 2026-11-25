"""Day 5（M5 沟通与纠纷）的实现层用例：REQ-018 / REQ-019 的每一条行为实例。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments,too-many-positional-arguments

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import RESULT_DENIED, OperationLog
from communication import services
from communication.api import PRODUCT_OFFSHELF, PRODUCT_PUBLISHED, Product
from communication.models import (
    DISPUTE_CLOSED,
    DISPUTE_OPEN,
    VERDICT_REFUND,
    VERDICT_TERMINATE,
    Conversation,
    Dispute,
    DisputeSignal,
    Message,
    MessageHide,
)
from common.errors import Conflict, InvalidInput
from trade.services import confirm_receipt as order_confirm_receipt
from trade.services import ship as order_ship

from .conftest import make_product
from .helpers_m5 import paid_order, reserve_presale

pytestmark = pytest.mark.django_db


def _denials(action: str) -> int:
    """数某个动作的拒绝日志条数。"""
    return OperationLog.objects.filter(action=action, result=RESULT_DENIED).count()


def _chat(buyer, product):
    """买家就商品开一个会话。"""
    return services.open_conversation(buyer, product.pk)


# --- REQ-018 会话与消息 ------------------------------------------------------


def test_open_conversation_reuses_same_session(buyer, seller, category):
    """同一买家同一商品重复发起只复用一条会话。"""
    product = make_product(seller, category)

    first = _chat(buyer, product)
    second = _chat(buyer, product)

    assert first.pk == second.pk
    assert Conversation.objects.count() == 1


def test_seller_cannot_chat_with_self(seller, category):
    """卖家不能和自己就自己的商品开会话，且留一条拒绝日志。"""
    product = make_product(seller, category)
    before = _denials("chat.send")

    with pytest.raises(PermissionDenied):
        _chat(seller, product)

    assert _denials("chat.send") == before + 1


def test_guest_cannot_open_conversation(seller, category):
    """游客不能发起会话。"""
    product = make_product(seller, category)

    with pytest.raises(PermissionDenied):
        services.open_conversation(None, product.pk)


def test_draft_product_cannot_start_conversation(buyer, seller, category):
    """草稿商品不在售，游客侧不能开会话（REQ-018「就在售商品发起」）。"""
    product = make_product(seller, category, status="草稿")

    with pytest.raises(PermissionDenied):
        _chat(buyer, product)


def test_both_parties_can_read_and_third_party_is_denied_and_logged(
    buyer, seller, other, category
):
    """会话双方可读，无关第三方 403 并记日志。"""
    conversation = _chat(buyer, make_product(seller, category))

    assert services.read_conversation(conversation, buyer).pk == conversation.pk
    assert services.read_conversation(conversation, seller).pk == conversation.pk

    before = _denials("chat.view")
    with pytest.raises(PermissionDenied):
        services.read_conversation(conversation, other)
    assert _denials("chat.view") == before + 1


def test_message_is_empty_rejected(buyer, seller, category):
    """空消息（既无文字也无图片）被拒绝。"""
    conversation = _chat(buyer, make_product(seller, category))

    with pytest.raises(InvalidInput, match="消息不能为空"):
        services.send_message(conversation, buyer, text="   ")


def test_message_too_long_is_rejected(buyer, seller, category):
    """超过 1000 字的消息被拒绝。"""
    conversation = _chat(buyer, make_product(seller, category))

    with pytest.raises(InvalidInput, match="1000"):
        services.send_message(conversation, buyer, text="字" * 1001)


def test_non_image_attachment_is_rejected(buyer, seller, category):
    """非 jpg/png/gif/webp 的附件被拒绝。"""
    conversation = _chat(buyer, make_product(seller, category))

    with pytest.raises(InvalidInput, match="jpg"):
        services.send_message(conversation, buyer, image="/media/chat/pay.exe")


def test_image_message_is_accepted(buyer, seller, category):
    """图片消息落库为地址，`created_at` 与最后消息时间同步。"""
    conversation = _chat(buyer, make_product(seller, category))

    message = services.send_message(conversation, buyer, image="/media/chat/a.png")

    assert message.image == "/media/chat/a.png"
    conversation.refresh_from_db()
    assert conversation.last_message_at == message.created_at


def test_third_party_cannot_send_message(buyer, seller, other, category):
    """非会话双方不能发消息，记 denied。"""
    conversation = _chat(buyer, make_product(seller, category))
    before = _denials("chat.send")

    with pytest.raises(PermissionDenied):
        services.send_message(conversation, other, text="插一句")

    assert _denials("chat.send") == before + 1


def test_blocked_account_cannot_send_message(buyer, seller, category):
    """被封号（is_active=False）的账号不能收发消息。"""
    conversation = _chat(buyer, make_product(seller, category))
    buyer.is_active = False
    buyer.save(update_fields=["is_active"])

    with pytest.raises(PermissionDenied, match="封禁"):
        services.send_message(conversation, buyer, text="还在吗")


def test_message_cannot_be_deleted_only_hidden_locally(buyer, seller, category):
    """消息不可删除，只能本地隐藏：记录仍在，对隐藏方不展示。"""
    conversation = _chat(buyer, make_product(seller, category))
    message = services.send_message(conversation, buyer, text="这是我的地址")

    hidden = services.hide_message(conversation, buyer, message.pk)

    assert hidden.pk == message.pk
    assert Message.objects.filter(pk=message.pk).exists()
    assert MessageHide.objects.filter(message=message, user=buyer).count() == 1
    assert Message.objects.get(pk=message.pk).text == "这是我的地址"


# --- REQ-019 识别 ------------------------------------------------------------


def test_keyword_hit_creates_signal_once(buyer, seller, category):
    """关键词命中只提示一次，不判定责任也不退款。"""
    conversation = _chat(buyer, make_product(seller, category))

    services.send_message(conversation, buyer, text="你这是在骗人")
    services.send_message(conversation, buyer, text="骗子！")

    kinds = list(conversation.signals.values_list("kind", flat=True))
    assert kinds == [DisputeSignal.KIND_KEYWORD]


def test_stalled_conversation_creates_signal(buyer, seller, category):
    """同一会话超过 24 小时无新消息产生「长时间无响应」提示。"""
    conversation = _chat(buyer, make_product(seller, category))
    services.send_message(conversation, buyer, text="发货了吗")
    conversation.refresh_from_db()
    later = conversation.last_message_at + dt.timedelta(hours=25)

    signals = services.scan(conversation, now=later)

    assert [s.kind for s in signals] == [DisputeSignal.KIND_STALLED]


def test_signal_can_be_ignored_without_blocking_flow(buyer, seller, category):
    """用户忽略提示后标记 ignored_at，流程不受影响。"""
    conversation = _chat(buyer, make_product(seller, category))
    services.send_message(conversation, buyer, text="我要退款")

    signal = services.ignore_signal(conversation, buyer, DisputeSignal.KIND_KEYWORD)

    assert signal.ignored_at is not None
    services.send_message(conversation, buyer, text="算了，我再等等")


# --- REQ-019 介入与裁定 ------------------------------------------------------


def _paid_order(buyer, seller, category):
    """造一个「待发货」的普通订单（付过款、商品交易中）。"""
    return paid_order(seller, category, buyer)


def test_raise_dispute_moves_order_to_disputing(buyer, seller, category):
    """发起介入：订单转纠纷中，并建一条工单。"""
    order = _paid_order(buyer, seller, category)

    dispute = services.raise_dispute(order.pk, buyer, reason="卖家不发货")

    order.refresh_from_db()
    assert order.status == "纠纷中"
    assert Dispute.objects.filter(order=order, status=DISPUTE_OPEN).count() == 1
    assert dispute.reason == "卖家不发货"


def test_repeat_raise_returns_single_dispute(buyer, seller, category):
    """同一订单重复请求介入只建一条工单（REQ-019）。"""
    order = _paid_order(buyer, seller, category)

    first = services.raise_dispute(order.pk, buyer)
    second = services.raise_dispute(order.pk, buyer)

    assert first.pk == second.pk
    assert Dispute.objects.count() == 1


def test_third_party_cannot_raise_dispute(buyer, seller, other, category):
    """无关第三方不能发起介入，记 denied。"""
    order = _paid_order(buyer, seller, category)
    before = _denials("dispute.raise")

    with pytest.raises(PermissionDenied):
        services.raise_dispute(order.pk, other)

    assert _denials("dispute.raise") == before + 1


def test_dispute_on_closed_order_is_rejected(buyer, seller, category):
    """已完成的订单可以留痕投诉，但不能被拉回纠纷中（M4 拒绝并抛冲突）。"""
    order = _paid_order(buyer, seller, category)
    order_ship(order, actor=seller, tracking_no="SF12345")
    order_confirm_receipt(order, actor=buyer, now=timezone.now() + dt.timedelta(days=8))
    order.refresh_from_db()

    with pytest.raises(Conflict, match="不能介入纠纷"):
        services.raise_dispute(order.pk, buyer)


def test_support_refund_before_shipping_restores_product(buyer, seller, support, category):
    """未发货裁定退款：按 REQ-016 恢复已发布，商品可再次售卖。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)

    services.accept_dispute(dispute, support)
    services.resolve_dispute(dispute, support, verdict=VERDICT_REFUND, note="核实卖家不发货")

    order.refresh_from_db()
    dispute.refresh_from_db()
    product = Product.objects.get(pk=order.product_id)
    assert order.status == "已退款"
    assert dispute.status == DISPUTE_CLOSED
    assert dispute.handler_id == support.pk
    assert dispute.note == "核实卖家不发货"
    assert product.status == PRODUCT_PUBLISHED


def test_support_terminate_marks_product_offshelf(buyer, seller, support, category):
    """已发货裁定终止：订单取消、商品保持交易中不动。

    UML 商品状态图没有「交易中 → 违规下架」这条边（25 条转换里没有），
    所以 M4 的 terminate_after_dispute 只关单，不改商品状态。
    """
    order = _paid_order(buyer, seller, category)

    order_ship(order, actor=seller, tracking_no="SF12345")
    order.refresh_from_db()
    dispute = services.raise_dispute(order.pk, buyer)

    services.accept_dispute(dispute, support)
    services.resolve_dispute(dispute, support, verdict=VERDICT_TERMINATE, note="买家证据不足，终止")

    order.refresh_from_db()
    product = Product.objects.get(pk=order.product_id)
    assert order.status == "已取消"
    assert product.status == PRODUCT_OFFSHELF


def test_resolve_requires_note_and_staff(buyer, seller, support, admin, category):
    """裁定必须由客服/管理员执行、必须写说明。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)

    with pytest.raises(InvalidInput, match="处理说明"):
        services.resolve_dispute(dispute, support, verdict=VERDICT_REFUND, note="  ")
    with pytest.raises(InvalidInput, match="裁定结论"):
        services.resolve_dispute(dispute, support, verdict="随便", note="说明")
    with pytest.raises(PermissionDenied):
        services.resolve_dispute(dispute, buyer, verdict=VERDICT_REFUND, note="我自己说了算")

    services.accept_dispute(dispute, admin)
    services.resolve_dispute(dispute, admin, verdict=VERDICT_REFUND, note="管理员裁定退款")


def test_closed_dispute_cannot_be_resolved_twice(buyer, seller, support, category):
    """工单处理完后再裁定会冲突。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)
    services.accept_dispute(dispute, support)
    services.resolve_dispute(dispute, support, verdict=VERDICT_REFUND, note="退款")

    with pytest.raises(services.DisputeConflict, match="已处理"):
        services.resolve_dispute(dispute, support, verdict=VERDICT_REFUND, note="再退一次")


def test_third_party_cannot_read_dispute(buyer, seller, other, category):
    """无关第三方读工单被拒并记日志。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)
    before = _denials("dispute.read")

    with pytest.raises(PermissionDenied):
        services.read_dispute(dispute, other)

    assert _denials("dispute.read") == before + 1
    assert services.read_dispute(dispute, seller).pk == dispute.pk
    assert services.read_dispute(dispute, buyer).pk == dispute.pk


def test_dispute_blocks_auto_refund(buyer, seller, category):
    """纠纷期间不自动超时退款、不自动放款（REQ-019）。"""
    from trade import services as trade_services  # pylint: disable=import-outside-toplevel

    order = _paid_order(buyer, seller, category)
    services.raise_dispute(order.pk, buyer)
    later = order.paid_at + dt.timedelta(days=20)

    trade_services.sweep(now=later)

    order.refresh_from_db()
    assert order.status == "纠纷中"
    assert not order.refunds.exists()


def test_reject_dispute_returns_order_to_waiting_ship(buyer, seller, support, category):
    """驳回介入：订单回到纠纷前状态，工单留痕。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)

    services.accept_dispute(dispute, support)
    services.reject_dispute(dispute, support, note="证据不足，请自行协商")

    order.refresh_from_db()
    dispute.refresh_from_db()
    assert order.status == "待发货"
    assert dispute.status == DISPUTE_CLOSED
    assert dispute.verdict == "驳回"


def test_accept_dispute_is_idempotent(buyer, seller, support, admin, category):
    """同一处理人重复接单幂等，其他工作人员不能抢走处理位。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)

    services.accept_dispute(dispute, support)
    assert services.accept_dispute(dispute, support).handler_id == support.pk
    with pytest.raises(services.DisputeConflict, match="其他工作人员"):
        services.accept_dispute(dispute, admin)
    dispute.refresh_from_db()

    assert dispute.handler_id == support.pk


def test_staff_workbench_only_for_staff(buyer, seller, support, category):
    """工作台只对客服/管理员开放。"""
    services.require_staff(support)

    with pytest.raises(PermissionDenied):
        services.require_staff(buyer)


def test_support_cannot_ban_through_dispute(buyer, seller, support, category):
    """客服无权封号：封号动作不属于 dispute.handle 能触达的范围（REQ-019）。"""
    from accounts.permissions import can  # pylint: disable=import-outside-toplevel

    assert can(support, "account.ban") is False
    assert can(support, "dispute.handle") is True


def test_presale_order_refund_via_dispute(buyer, seller, support, category):
    """预售订单经纠纷裁定退款后，商品恢复已预售。"""
    presale, reservation = reserve_presale(seller, category, buyer)
    order = reservation.order
    order.refresh_from_db()
    dispute = services.raise_dispute(order.pk, buyer)

    services.accept_dispute(dispute, support)
    services.resolve_dispute(dispute, support, verdict=VERDICT_REFUND, note="买家反悔")

    order.refresh_from_db()
    presale.refresh_from_db()
    assert order.status == "已退款"
    assert presale.status == "已预售"


def test_duplicate_dispute_signal_ignored(buyer, seller, category):
    """并发重复创建同一类提示时被唯一约束挡住，不抛错。"""
    conversation = _chat(buyer, make_product(seller, category))
    DisputeSignal.objects.create(
        conversation=conversation, kind=DisputeSignal.KIND_KEYWORD, reason="已有"
    )
    services.send_message(conversation, buyer, text="骗子")

    assert conversation.signals.filter(kind=DisputeSignal.KIND_KEYWORD).count() == 1


def test_message_rejects_more_than_six_images(buyer, seller, category):
    """一个会话最多 6 张图片。"""
    conversation = _chat(buyer, make_product(seller, category))
    for i in range(6):
        services.send_message(conversation, buyer, image=f"/media/chat/{i}.png")

    with pytest.raises(InvalidInput, match="6 张"):
        services.send_message(conversation, buyer, image="/media/chat/7.png")


def test_sweep_scans_active_order_conversations(buyer, seller, category):
    """定时扫描把进行中订单的会话也纳入识别（会话可来自商品页，不带 order_id）。"""
    order = _paid_order(buyer, seller, category)
    conversation = Conversation.objects.create(
        product_id=order.product_id, buyer=buyer, seller=seller, order=order
    )
    services.send_message(conversation, buyer, text="你是不是骗子")

    # 发消息时已经顺手扫过一次，关键词提示应当已经落库。
    assert DisputeSignal.KIND_KEYWORD in conversation.signals.values_list("kind", flat=True)

    # 把会话做旧，验证定时扫描也会把「进行中订单的会话」纳入识别。
    Conversation.objects.filter(pk=conversation.pk).update(
        last_message_at=timezone.now() - dt.timedelta(hours=30)
    )
    assert services.sweep() >= 1


def test_dispute_constraint_is_one_per_order(buyer, seller, category):
    """同一订单只能有一条工单（数据库约束兜底）。"""
    order = _paid_order(buyer, seller, category)
    services.raise_dispute(order.pk, buyer)

    with pytest.raises(IntegrityError), transaction.atomic():
        Dispute.objects.create(order=order, reason="再来一条")



def test_hiding_requires_party(buyer, seller, other, category):
    """非会话双方不能隐藏消息。"""
    conversation = _chat(buyer, make_product(seller, category))
    message = services.send_message(conversation, buyer, text="hi")

    with pytest.raises(PermissionDenied):
        services.hide_message(conversation, other, message.pk)


def test_order_dispute_is_one_to_one(buyer, seller, category):
    """订单详情里的纠纷关联可直接取到。"""
    order = _paid_order(buyer, seller, category)
    dispute = services.raise_dispute(order.pk, buyer)

    assert Dispute.objects.get(order_id=order.pk).pk == dispute.pk
