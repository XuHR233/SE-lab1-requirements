"""沟通与纠纷用例层（REQ-018/019）。

本模块**只**通过 `communication.api` 里导出的东西读写 Day 2/3/4 的数据：
商品状态用 `product_transit` 走状态机，订单状态用 `order_request_refund` /
`order_confirm_receipt` / `order_close` 这些已有用例，从不直接改它们的表。
"""

from __future__ import annotations

from typing import NoReturn

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Case, OuterRef, Subquery, Value, When

from audit.models import RESULT_ALLOWED, RESULT_DENIED, record
from common.errors import Conflict, InvalidInput
from demo.clock import business_now

from .api import (
    ORDER_ACTIVE_STATUSES,
    ORDER_DISPUTING,
    ORDER_WAITING_SHIP,
    PRODUCT_PUBLISHED,
    Order,
    Product,
    allowed_by_role,
    order_open_dispute,
    order_refund_after_dispute,
    order_resume,
    order_terminate_after_dispute,
)
from .models import (
    DISPUTE_CLOSED,
    DISPUTE_OPEN,
    Conversation,
    Dispute,
    DisputeSignal,
    Message,
    MessageHide,
    OPEN_ORDER_STATUSES,
    VERDICT_REFUND,
    VERDICTS,
)

MAX_TEXT = 1000
MAX_IMAGES = 6
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp")

# 识别规则只看关键词，不做判定（REQ-019「识别环节只做提示」）。
KEYWORDS: tuple[str, ...] = (
    "骗子",
    "假货",
    "退款",
    "投诉",
    "举报",
    "质量问题",
    "损坏",
    "不发货",
    "没收到",
)


class NotFoundError(Exception):
    """对当前用户不存在可见对象（不泄露存在性）。"""


class ChatConflict(Conflict):
    """会话或消息状态冲突。"""


class DisputeConflict(Conflict):
    """工单状态冲突。"""


def _moment(now):
    """时间一律走这个入口，测试与定时任务才能传「现在」。"""
    return now or business_now()


def _user(request_user) -> object | None:
    """把 Django 的 AnonymousUser 归一成 None，服务层只认「有 pk 的人」。"""
    if request_user is None or not getattr(request_user, "is_authenticated", False):
        return None
    return request_user


def _log(
    action: str, result: str, *, actor, target_type: str = "", target_id=0, detail=None
) -> None:
    """写一条操作日志：actor 可能是 AnonymousUser，归一成 NULL。"""
    record(
        action=action,
        result=result,
        actor=actor if getattr(actor, "pk", None) else None,
        target_type=target_type,
        target_id=target_id or "",
        detail={"reason": detail} if result == RESULT_DENIED else (detail or {}),
    )


def _deny(actor, action: str, message: str, *, target_type: str = "", target_id=0) -> NoReturn:
    """权限或当事人不匹配：先记 denied 日志，再抛 403（REQ-002/026）。"""
    _log(
        action,
        RESULT_DENIED,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        detail=message,
    )
    raise PermissionDenied(message)


def _allow(actor, action: str, *, target_type: str = "", target_id=0, detail=None) -> None:
    """重要动作放行也留一条日志（REQ-022）。"""
    _log(
        action,
        RESULT_ALLOWED,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )


# --- 会话与消息（REQ-018）---------------------------------------------------


def _blocked(actor) -> bool:
    """被封号或客服账号不能收发消息（REQ-018 异常项）。账号只有 is_active 一个封号位。"""
    return actor is not None and (not actor.is_active or getattr(actor, "is_support", False))


PUBLIC_CHAT_STATUSES = (PRODUCT_PUBLISHED, "已预售")


def open_conversation(actor, product_id, *, order_id=None, now=None):
    """就在售商品向卖家发起会话；同一买卖双方同一商品复用原会话。"""
    actor = _user(actor)
    if actor is None:
        _deny(actor, "chat.send", "请先登录")
    product = Product.objects.filter(pk=product_id).first() if product_id else None
    if product is None:
        raise InvalidInput("商品不存在")
    # 入会话只做角色预检（M1 的 allowed_by_role），当事人归属由后面各步各自判。
    if not allowed_by_role(actor, "chat.send") or _blocked(actor):
        _deny(actor, "chat.send", "当前身份不能发起会话")
    if product.seller_id == actor.pk and order_id is None:
        _deny(actor, "chat.send", "不能和自己聊天", target_type="product", target_id=product.pk)
    order = None
    if order_id is not None:
        order = Order.objects.filter(pk=order_id).first()
        if order is None or order.product_id != product.pk:
            raise InvalidInput("订单与该商品不匹配")
        if actor.pk not in (order.buyer_id, order.seller_id):
            _deny(actor, "chat.view", "只有订单双方可以关联订单会话", target_type="order", target_id=order.pk)
    if order is None and product.status not in PUBLIC_CHAT_STATUSES:
        _deny(actor, "chat.send", "商品不在售，不能发起咨询", target_type="product", target_id=product.pk)
    buyer_id = order.buyer_id if order else actor.pk
    conversation, created = Conversation.objects.get_or_create(
        product=product,
        buyer_id=buyer_id,
        seller_id=product.seller_id,
        defaults={"order": order, "created_at": _moment(now)},
    )
    if not created and order is not None and conversation.order_id is None:
        conversation.order = order
        conversation.save(update_fields=["order"])
    if created:
        _allow(
            actor,
            "chat.send",
            target_type="conversation",
            target_id=conversation.pk,
            detail={"product_id": product.pk},
        )
    return conversation


def list_conversations(actor):
    """我参与的会话。客服/管理员不在这里看，走纠纷工单（REQ-018 权限口径）。"""
    actor = _user(actor)
    if actor is None:
        return Conversation.objects.none()
    return Conversation.objects.filter(models_q(actor)).order_by("-last_message_at", "-id")


def models_q(actor):
    """我的会话过滤条件：买家或卖家是自己。"""
    from django.db.models import Q  # pylint: disable=import-outside-toplevel

    return Q(buyer_id=actor.pk) | Q(seller_id=actor.pk)


def _chat_orders(product_id, buyer_id, seller_id):
    return Order.objects.filter(product_id=product_id, buyer_id=buyer_id, seller_id=seller_id)


def _order_priority(rows):
    ongoing = Case(When(status__in=ORDER_ACTIVE_STATUSES, then=Value(True)),
                   default=Value(False), output_field=BooleanField())
    return rows.order_by(ongoing.desc(), "-created_at", "-pk")


def list_chat_orders(conversations):
    """批量取列表的默认订单；进行中优先，否则取最近创建的订单。"""
    candidates = _chat_orders(OuterRef("product_id"), OuterRef("buyer_id"), OuterRef("seller_id"))
    rows = list(conversations.annotate(
        selected_order_id=Subquery(_order_priority(candidates).values("pk")[:1]),
    ))
    orders = Order.objects.in_bulk({row.selected_order_id for row in rows if row.selected_order_id})
    return [(row, orders.get(row.selected_order_id)) for row in rows]


def chat_order(conversation: Conversation, *, order_id=None) -> Order | None:
    """解析本次查看的订单，不修改会话上的旧关联。"""
    rows = _chat_orders(conversation.product_id, conversation.buyer_id, conversation.seller_id)
    if order_id is None:
        return _order_priority(rows.select_related("dispute")).first()
    order = rows.select_related("dispute").filter(pk=order_id).first()
    if order is None:
        raise InvalidInput("订单与该会话不匹配")
    return order


def chat_context(conversation: Conversation, actor, *, order_id=None, dispute_id=None):
    """订单双方按订单看工单；工作人员只能查看会话中有工单的订单。"""
    read_conversation(conversation, actor)
    if actor.pk in (conversation.buyer_id, conversation.seller_id) and dispute_id is None:
        order = chat_order(conversation, order_id=order_id)
        return order, getattr(order, "dispute", None)
    tickets = conversation.disputes.select_related("order").filter(
        status__in=(DISPUTE_OPEN, DISPUTE_CLOSED),
    )
    if order_id is not None:
        tickets = tickets.filter(order_id=order_id)
    if dispute_id is not None:
        tickets = tickets.filter(pk=dispute_id)
    ticket = tickets.order_by("-created_at", "-pk").first()
    if ticket is None:
        _deny(actor, "chat.view", "该订单或工单不属于可查看的会话",
              target_type="conversation", target_id=conversation.pk)
    return ticket.order, ticket


def _can_read(conversation: Conversation, actor) -> bool:
    """会话可见性：双方，或已介入纠纷的客服/管理员（REQ-018）。"""
    actor = _user(actor)
    if actor is None or not actor.is_active:
        return False
    if actor.pk in (conversation.buyer_id, conversation.seller_id):
        return True
    joined = conversation.disputes.filter(
        status__in=(DISPUTE_OPEN, DISPUTE_CLOSED)
    ).exists()
    return bool(allowed_by_role(actor, "dispute.read") and joined)


def read_conversation(conversation: Conversation, actor) -> Conversation:
    """读会话与消息；拒绝无关第三方并记录日志。"""
    actor = _user(actor)
    if _can_read(conversation, actor):
        return conversation
    _deny(
        actor,
        "chat.view",
        "无权查看该会话",
        target_type="conversation",
        target_id=conversation.pk,
    )


def _can_send(conversation, actor, dispute_id, *, lock=False):
    if actor is None or not actor.is_active:
        return False
    if allowed_by_role(actor, "chat.send"):
        return actor.pk in (conversation.buyer_id, conversation.seller_id)
    if not dispute_id or not allowed_by_role(actor, "dispute.handle"):
        return False
    rows = conversation.disputes.select_for_update() if lock else conversation.disputes
    ticket = rows.filter(pk=dispute_id).first()
    return bool(ticket and ticket.is_open and ticket.handler_id == actor.pk and (
        not ticket.escalated_at or allowed_by_role(actor, "dispute.takeover")
    ))


def require_message_sender(conversation, actor, *, dispute_id=None):
    """上传附件前的快速权限检查；落消息时在事务内复核。"""
    if _user(actor) and not actor.is_active:
        _deny(actor, "chat.send", "账号已被封禁，不能收发消息",
              target_type="conversation", target_id=conversation.pk)
    if not _can_send(conversation, _user(actor), dispute_id):
        _deny(actor, "chat.send", "仅会话双方及当前接单的处理人可以发消息",
              target_type="conversation", target_id=conversation.pk)


def send_message(  # pylint: disable=too-many-arguments
    conversation: Conversation, actor, *, text="", image=None, now=None, dispute_id=None
):
    """双方或当前工单处理人在同一会话发送文字或图片。"""
    actor = _user(actor)
    require_message_sender(conversation, actor, dispute_id=dispute_id)
    body = (text or "").strip()
    if not body and not (image or "").strip():
        raise InvalidInput("消息不能为空")
    if len(body) > MAX_TEXT:
        raise InvalidInput(f"消息不能超过 {MAX_TEXT} 字")
    url = (image or "").strip()
    if url and not url.lower().endswith(IMAGE_SUFFIXES):
        raise InvalidInput("只支持 jpg/png/gif/webp 图片")
    with transaction.atomic():
        if _can_send(conversation, actor, dispute_id, lock=True):
            return _save_message(conversation, actor, body, url, dispute_id=dispute_id, now=now)
    return _deny(actor, "chat.send", "工单处理权限已变更，请刷新页面",
                 target_type="conversation", target_id=conversation.pk)


def _save_message(conversation, actor, body, url, *, dispute_id, now):
    Conversation.objects.select_for_update().get(pk=conversation.pk)
    if url and conversation.messages.exclude(image="").count() >= MAX_IMAGES:
        raise InvalidInput(f"一个会话最多发送 {MAX_IMAGES} 张图片")
    staff_role = actor.role if allowed_by_role(actor, "dispute.handle") else ""
    message = Message.objects.create(conversation=conversation, sender=actor, text=body,
                                     image=url, staff_role=staff_role, created_at=_moment(now))
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at)
    if not staff_role:
        scan(conversation, now=message.created_at)
    detail = {"message_id": message.pk, "has_image": bool(url)}
    if staff_role:
        detail["dispute_id"] = dispute_id
    _allow(actor, "chat.send", target_type="conversation", target_id=conversation.pk, detail=detail)
    return message


def hide_message(conversation: Conversation, actor, message_id):
    """本地隐藏一条消息：消息记录本身不改不删，任一方都不能单方面删掉它（REQ-018）。"""
    actor = _user(actor)
    if actor is None or actor.pk not in (conversation.buyer_id, conversation.seller_id):
        _deny(
            actor,
            "chat.view",
            "只有会话双方可以隐藏消息",
            target_type="conversation",
            target_id=conversation.pk,
        )
    message = conversation.messages.filter(pk=message_id).first() if message_id else None
    if message is None:
        raise InvalidInput("消息不存在")
    MessageHide.objects.get_or_create(message=message, user=actor)
    _allow(
        actor,
        "chat.hide",
        target_type="message",
        target_id=message.pk,
        detail={"scope": "local"},
    )
    return message


def ignore_signal(conversation: Conversation, actor, kind: str):
    """用户忽略提示，不影响正常流程（REQ-019 异常项）。"""
    actor = _user(actor)
    if actor is None or actor.pk not in (conversation.buyer_id, conversation.seller_id):
        _deny(
            actor,
            "chat.view",
            "只有会话双方可以忽略提示",
            target_type="conversation",
            target_id=conversation.pk,
        )
    signal = conversation.signals.filter(kind=kind).first()
    if signal is None:
        raise InvalidInput("没有这条提示")
    if signal.ignored_at is None:
        DisputeSignal.objects.filter(pk=signal.pk, ignored_at__isnull=True).update(
            ignored_at=business_now()
        )
        signal.refresh_from_db()
    _allow(actor, "chat.ignore_signal", target_type="conversation", target_id=conversation.pk)
    return signal


# --- 纠纷识别（REQ-019 前半）-------------------------------------------------


def scan(conversation: Conversation, *, now=None) -> list[DisputeSignal]:
    """扫一遍会话，命中就留一条提示；只提示，不判定责任、不退款。"""
    moment = _moment(now)
    hits = []
    texts = " ".join(
        message.text for message in conversation.messages.order_by("-id")[:20]
    )
    for keyword in KEYWORDS:
        if keyword in texts:
            hits.append((DisputeSignal.KIND_KEYWORD, f"会话中出现「{keyword}」"))
            break
    if conversation.last_message_at is not None:
        last = Conversation.objects.get(pk=conversation.pk).last_message_at
        if (moment - last).total_seconds() >= 24 * 3600:
            hits.append((DisputeSignal.KIND_STALLED, "该会话已超过 24 小时没有新消息"))
    if conversation.order_id is not None:
        refunds = (
            conversation.order.refunds.count() if hasattr(conversation.order, "refunds") else 0
        )
        if refunds >= 2:
            hits.append((DisputeSignal.KIND_REPEATED_REFUND, "该订单已有多次退款申请"))
    created = []
    for kind, reason in hits:
        try:
            with transaction.atomic():
                signal = DisputeSignal.objects.create(
                    conversation=conversation, kind=kind, reason=reason
                )
                created.append(signal)
        except IntegrityError:
            continue
    return created


# --- 纠纷工单（REQ-019 后半）-------------------------------------------------


def _dispute_target(dispute: Dispute, actor) -> dict:
    """工单的当事人：订单买卖双方，外加客服/管理员。"""
    order = dispute.order
    party = actor is not None and actor.pk in (order.buyer_id, order.seller_id)
    staff = actor is not None and allowed_by_role(actor, "dispute.read")
    return {"party": party, "staff": staff}


def _order_conversation(order):
    return Conversation.objects.get_or_create(
        product_id=order.product_id, buyer_id=order.buyer_id, seller_id=order.seller_id,
        defaults={"order": order},
    )[0]


def raise_dispute(order_id, actor, *, reason="", conversation_id=None, source="用户", now=None):
    """请求客服介入：订单转纠纷中，同一订单只建一条工单（REQ-019）。"""
    actor = _user(actor)
    order = Order.objects.filter(pk=order_id).first() if order_id else None
    if order is None:
        raise InvalidInput("订单不存在")
    if source == "用户" and (actor is None or actor.pk not in (order.buyer_id, order.seller_id)):
        _deny(actor, "dispute.raise", "只有订单双方可以请求介入", target_type="order", target_id=order.pk)
    existing = Dispute.objects.filter(order=order).first()
    if existing is not None:
        return existing
    if order.status not in OPEN_ORDER_STATUSES:
        raise DisputeConflict(f"{order.status} 状态的订单不能发起介入")
    conversations = Conversation.objects.filter(
        product_id=order.product_id, buyer_id=order.buyer_id, seller_id=order.seller_id
    )
    conversation = conversations.filter(pk=conversation_id).first() if conversation_id else None
    if conversation_id is not None and conversation is None:
        raise InvalidInput("会话与该订单不匹配")
    with transaction.atomic():
        # 订单转纠纷中由 M4 的 open_dispute 负责；M5 从不直接改订单表。
        order_open_dispute(order, reason=(reason or "用户请求客服介入")[:200])
        try:
            with transaction.atomic():
                dispute = Dispute.objects.create(
                    order=order,
                    conversation=conversation or _order_conversation(order),
                    raised_by=actor if getattr(actor, "pk", None) else None,
                    raised_source=source,
                    reason=(reason or "用户请求客服介入")[:200],
                    created_at=_moment(now),
                )
        except IntegrityError as exc:
            raise DisputeConflict("该订单已有纠纷工单") from exc
        order.refresh_from_db()
    _allow(
        actor,
        "dispute.raise",
        target_type="order",
        target_id=order.pk,
        detail={"dispute_id": dispute.pk, "source": source},
    )
    return dispute


def _require_dispute_role(dispute, actor, action="dispute.handle"):
    actor = _user(actor)
    if actor is None or not actor.is_active or not allowed_by_role(actor, action):
        _deny(actor, action, "当前身份不能执行此工单操作",
              target_type="dispute", target_id=dispute.pk)
    return actor


def _open_ticket(dispute):
    dispute = Dispute.objects.select_for_update().get(pk=dispute.pk)
    if not dispute.is_open:
        raise DisputeConflict("工单已处理")
    return dispute


def _require_handler(dispute, actor):
    if dispute.handler_id != actor.pk or (
        dispute.escalated_at and not allowed_by_role(actor, "dispute.takeover")
    ):
        raise DisputeConflict("只有当前接单的处理人可以操作，请先接单或刷新页面")


@transaction.atomic
def accept_dispute(dispute: Dispute, actor) -> Dispute:
    """同一工单只能由一个工作人员接单；升级后只允许管理员接管。"""
    actor = _require_dispute_role(dispute, actor)
    ticket = _open_ticket(dispute)
    if ticket.escalated_at and not allowed_by_role(actor, "dispute.takeover"):
        raise DisputeConflict("工单已转交，仅管理员可以接管")
    if ticket.handler_id and ticket.handler_id != actor.pk:
        raise DisputeConflict("工单已由其他工作人员接单")
    if ticket.handler_id == actor.pk and ticket.conversation_id:
        return ticket
    new_claim = ticket.handler_id is None
    ticket.handler = actor
    if not ticket.conversation_id:
        ticket.conversation = _order_conversation(ticket.order)
    ticket.save(update_fields=["handler", "conversation"])
    if new_claim:
        _allow(actor, "dispute.handle", target_type="dispute", target_id=ticket.pk,
               detail={"action": "takeover" if ticket.escalated_at else "accept",
                       "order_id": ticket.order_id})
    return ticket


@transaction.atomic
def escalate_dispute(dispute: Dispute, actor, *, note: str, now=None) -> Dispute:
    """客服交回自己的处理权，等待唯一的管理员接管。"""
    actor = _require_dispute_role(dispute, actor, "dispute.escalate")
    ticket = _open_ticket(dispute)
    _require_handler(ticket, actor)
    text = (note or "").strip()
    if not text or len(text) > 200:
        raise InvalidInput("请填写 1 至 200 字的转交原因")
    ticket.handler = None
    ticket.escalated_at = _moment(now)
    ticket.escalation_reason = text
    ticket.save(update_fields=["handler", "escalated_at", "escalation_reason"])
    _allow(actor, "dispute.escalate", target_type="dispute", target_id=ticket.pk,
           detail={"action": "escalate", "reason": text, "order_id": ticket.order_id})
    return ticket


def read_dispute(dispute: Dispute, actor) -> Dispute:
    """读工单：纠纷双方与客服/管理员；无关第三方一律拒绝并记日志。"""
    actor = _user(actor)
    target = _dispute_target(dispute, actor)
    if not (target["party"] or target["staff"]):
        _deny(actor, "dispute.read", "无权查看该纠纷", target_type="dispute", target_id=dispute.pk)
    return dispute


def list_disputes(actor, *, query=None):
    """客服/管理员看全部工单；其他只看到与自己订单有关的。"""
    actor = _user(actor)
    if actor is None:
        return Dispute.objects.none()
    rows = query if query is not None else Dispute.objects.all()
    if allowed_by_role(actor, "dispute.read"):
        return rows.order_by("-id")
    from django.db.models import Q  # pylint: disable=import-outside-toplevel

    mine = Q(order__buyer_id=actor.pk) | Q(order__seller_id=actor.pk)
    return rows.filter(mine).order_by("-id")


def require_staff(actor) -> None:
    """客服工作台只对客服/管理员开放。"""
    actor = _user(actor)
    if actor is None or not allowed_by_role(actor, "dispute.workbench"):
        _deny(actor, "dispute.read", "只有客服或管理员可以查看工作台")


def _judge(dispute: Dispute, actor, verdict: str, text: str) -> None:
    """把订单交回 M4 走裁定入口：退款、终止都在 M4 里完成状态、版本、日志与商品联动。"""
    order = dispute.order
    if verdict == VERDICT_REFUND:
        order_refund_after_dispute(order, actor=actor, reason=f"客服裁定退款：{text}"[:200])
    else:
        order_terminate_after_dispute(order, actor=actor, reason=f"客服裁定终止：{text}"[:200])


def resolve_dispute(dispute: Dispute, actor, *, verdict: str, note: str, now=None) -> Dispute:
    """客服裁定：退款或终止。必须填写处理说明并留痕（REQ-019）。

    订单怎么变由 M4 的裁定入口负责；这里只关工单。
    """
    actor = _require_dispute_role(dispute, actor)
    if verdict not in VERDICTS:
        raise InvalidInput("裁定结论只能是「退款」或「终止」")
    text = (note or "").strip()
    if not text:
        raise InvalidInput("裁定必须填写处理说明")
    moment = _moment(now)
    with transaction.atomic():
        locked = _open_ticket(dispute)
        _require_handler(locked, actor)
        _judge(locked, actor, verdict, text)
        Dispute.objects.filter(pk=dispute.pk, status=DISPUTE_OPEN).update(
            status=DISPUTE_CLOSED,
            verdict=verdict,
            note=text,
            closed_at=moment,
        )
    dispute.refresh_from_db()
    _allow(
        actor,
        "dispute.handle",
        target_type="dispute",
        target_id=dispute.pk,
        detail={"action": "resolve", "verdict": verdict, "order_id": dispute.order_id},
    )
    return dispute


def reject_dispute(dispute: Dispute, actor, *, note: str, now=None) -> Dispute:
    """驳回介入请求：订单恢复履约（M4 的 resume_order 按发货事实退回原状态），工单留痕。"""
    actor = _require_dispute_role(dispute, actor)
    text = (note or "").strip()
    if not text:
        raise InvalidInput("驳回必须填写处理说明")
    moment = _moment(now)
    with transaction.atomic():
        locked = _open_ticket(dispute)
        _require_handler(locked, actor)
        order = locked.order
        if order.status == ORDER_DISPUTING:
            order_resume(order)
        Dispute.objects.filter(pk=dispute.pk, status=DISPUTE_OPEN).update(
            status=DISPUTE_CLOSED,
            verdict="驳回",
            note=text,
            closed_at=moment,
        )
    dispute.refresh_from_db()
    _allow(
        actor,
        "dispute.handle",
        target_type="dispute",
        target_id=dispute.pk,
        detail={"action": "reject", "order_id": dispute.order_id},
    )
    return dispute


def sweep(*, now=None) -> int:
    """定时任务：扫描进行中的订单会话产生提示；纠纷中的订单不自动完成、不自动放款。"""
    moment = _moment(now)
    count = 0
    orders = Order.objects.filter(status=ORDER_WAITING_SHIP)
    for order in orders.select_related("product"):
        # 会话按「该商品的这对买卖双方」定位：从商品页发起的咨询没有 order_id，
        # 但它讨论的就是这单交易，必须纳入扫描（REQ-019）。
        conversation = Conversation.objects.filter(
            product_id=order.product_id, buyer_id=order.buyer_id
        ).first()
        if conversation is None:
            continue
        count += len(scan(conversation, now=moment))
    return count
