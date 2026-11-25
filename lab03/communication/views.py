"""沟通与纠纷 HTTP 入口（REQ-018/019）。

视图只做参数搬运与可见性过滤；状态变化一律交给 `communication.services`，
跨模块的订单/商品变化一律经 `communication.api` 调 Day 2/3/4 的公开函数。
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from catalog.imagery import thumbnail
from common.api import json_body, json_errors, optional_int

from . import services
from .api import ORDER_ACTIVE_STATUSES, Order
from .models import Conversation, Dispute


def _conversation(pk: int) -> HttpResponse | Conversation:
    """取会话；不存在就 404。"""
    conversation = (Conversation.objects.select_related("product", "order")
                    .annotate(thumbnail=thumbnail("product_id")).filter(pk=pk).first())
    if conversation is None:
        return JsonResponse({"error": "会话不存在"}, status=404)
    return conversation


def _dispute(pk: int) -> HttpResponse | Dispute:
    """取纠纷工单；不存在就 404。"""
    dispute = Dispute.objects.select_related("order").filter(pk=pk).first()
    if dispute is None:
        return JsonResponse({"error": "纠纷工单不存在"}, status=404)
    return dispute


def _row(conversation: Conversation) -> dict:
    """会话的对外表示。"""
    order = conversation.order
    return {
        "id": conversation.pk,
        "product_id": conversation.product_id,
        "buyer_id": conversation.buyer_id,
        "seller_id": conversation.seller_id,
        "order_id": conversation.order_id,
        "title": conversation.product.title,
        "price": str(order.amount if order else conversation.product.price),
        "order_status": order.status if order else None,
        "thumbnail": getattr(conversation, "thumbnail", None),
        "created_at": conversation.created_at.isoformat(),
        "last_message_at": (
            conversation.last_message_at.isoformat() if conversation.last_message_at else None
        ),
    }


def _message_row(message) -> dict:
    """消息的对外表示：图片给地址，不给本地路径。"""
    return {
        "id": message.pk,
        "sender_id": message.sender_id,
        "staff_role": message.staff_role,
        "text": message.text,
        "image": message.image,
        "created_at": message.created_at.isoformat(),
    }


def _dispute_row(dispute: Dispute) -> dict:
    """工单的对外表示：结论与说明只给纠纷双方与客服/管理员。"""
    return {
        "id": dispute.pk,
        "order_id": dispute.order_id,
        "conversation_id": dispute.conversation_id,
        "handler_id": dispute.handler_id,
        "status": dispute.status,
        "reason": dispute.reason,
        "raised_source": dispute.raised_source,
        "escalated_at": dispute.escalated_at.isoformat() if dispute.escalated_at else None,
        "escalation_reason": dispute.escalation_reason,
        "order__status": dispute.order.status,
        "verdict": dispute.verdict,
        "note": dispute.note,
        "created_at": dispute.created_at.isoformat(),
        "closed_at": dispute.closed_at.isoformat() if dispute.closed_at else None,
    }


def _hidden_ids(conversation: Conversation, actor) -> set[int]:
    """这个人自己隐藏掉的消息（别人的隐藏与我无关）。"""
    if not getattr(actor, "pk", None):
        return set()
    from .models import MessageHide  # pylint: disable=import-outside-toplevel

    return set(
        MessageHide.objects.filter(
            user_id=actor.pk, message__conversation=conversation
        ).values_list("message_id", flat=True)
    )


@json_errors
@require_http_methods(["GET", "POST"])
def chats(request: HttpRequest) -> HttpResponse:
    """GET 我的会话；POST 就在售商品向卖家发起会话（重复发起复用原会话）。"""
    if request.method == "POST":
        data = json_body(request)
        conversation = services.open_conversation(
            request.user,
            optional_int(data.get("product_id"), "product_id"),
            order_id=optional_int(data.get("order_id"), "order_id"),
        )
        return JsonResponse(_row(conversation), status=201)
    visible = services.list_conversations(request.user).select_related("product", "order")
    rows = [_row(item) for item in visible]
    return JsonResponse({"items": rows})


@json_errors
@require_http_methods(["GET"])
def chat_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """会话消息；非参与方一律 403 并记日志（REQ-018）。"""
    conversation = _conversation(pk)
    if isinstance(conversation, HttpResponse):
        return conversation
    services.read_conversation(conversation, request.user)
    hidden = _hidden_ids(conversation, request.user)
    items = [
        _message_row(message)
        for message in conversation.messages.select_related("sender")
        if message.pk not in hidden
    ]
    return JsonResponse(
        {
            "id": conversation.pk,
            "conversation": _row(conversation),
            "items": items,
            "signals": [
                {
                    "kind": signal.kind,
                    "reason": signal.reason,
                    "ignored": signal.ignored_at is not None,
                }
                for signal in conversation.signals.all()
            ],
        }
    )


@json_errors
@require_http_methods(["POST"])
def chat_message(request: HttpRequest, pk: int) -> HttpResponse:
    """在会话里发一条文字或图片消息（图片给对象存储地址或本地上传地址）。"""
    conversation = _conversation(pk)
    if isinstance(conversation, HttpResponse):
        return conversation
    message = services.send_message(
        conversation,
        request.user,
        text=request.POST.get("text", ""),
        image=request.POST.get("image", ""),
        dispute_id=optional_int(request.POST.get("dispute_id"), "dispute_id"),
    )
    return JsonResponse(_message_row(message), status=201)


@json_errors
@require_http_methods(["POST"])
def chat_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """会话上的动作：hide（本地隐藏）、ignore-signal（忽略提示）。"""
    conversation = _conversation(pk)
    if isinstance(conversation, HttpResponse):
        return conversation
    data = json_body(request)
    if action == "hide":
        services.hide_message(
            conversation, request.user, optional_int(data.get("message_id"), "message_id")
        )
        return JsonResponse({"hidden": True})
    if action == "ignore-signal":
        signal = services.ignore_signal(conversation, request.user, str(data.get("kind", "")))
        return JsonResponse({"kind": signal.kind, "ignored": signal.ignored_at is not None})
    return JsonResponse({"error": "未知动作"}, status=404)


@json_errors
@require_http_methods(["GET", "POST"])
def disputes(request: HttpRequest) -> HttpResponse:
    """GET 我的纠纷工单；POST 发起客服介入（同一订单只建一条）。"""
    if request.method == "POST":
        data = json_body(request)
        dispute = services.raise_dispute(
            optional_int(data.get("order_id"), "order_id"),
            request.user,
            reason=str(data.get("reason", "")),
            conversation_id=optional_int(data.get("conversation_id"), "conversation_id"),
        )
        return JsonResponse(_dispute_row(dispute), status=201)
    rows = [_dispute_row(item) for item in services.list_disputes(request.user)]
    return JsonResponse({"items": rows})


@json_errors
@require_http_methods(["GET"])
def dispute_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """单条工单：纠纷双方与客服/管理员可看，无关第三方 403 并记日志（REQ-019）。"""
    dispute = _dispute(pk)
    if isinstance(dispute, HttpResponse):
        return dispute
    services.read_dispute(dispute, request.user)
    return JsonResponse(_dispute_row(dispute))


@json_errors
@require_http_methods(["POST"])
def dispute_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """工单上的动作：接单/接管、转交管理员、裁定、驳回介入。"""
    dispute = _dispute(pk)
    if isinstance(dispute, HttpResponse):
        return dispute
    data = json_body(request)
    if action == "accept":
        return JsonResponse(_dispute_row(services.accept_dispute(dispute, request.user)))
    if action == "escalate":
        return JsonResponse(_dispute_row(services.escalate_dispute(
            dispute, request.user, note=str(data.get("note", "")),
        )))
    if action == "resolve":
        return JsonResponse(
            _dispute_row(
                services.resolve_dispute(
                    dispute,
                    request.user,
                    verdict=str(data.get("verdict", "")),
                    note=str(data.get("note", "")),
                )
            )
        )
    if action == "reject":
        return JsonResponse(
            _dispute_row(
                services.reject_dispute(dispute, request.user, note=str(data.get("note", "")))
            )
        )
    return JsonResponse({"error": "未知动作"}, status=404)


@json_errors
@require_http_methods(["GET"])
def order_dispute(request: HttpRequest, pk: int) -> HttpResponse:
    """某订单的纠纷工单（纠纷双方与客服/管理员可看）。"""
    order = Order.objects.filter(pk=pk).first()
    if order is None:
        return JsonResponse({"error": "订单不存在"}, status=404)
    dispute = Dispute.objects.filter(order_id=order.pk).first()
    if dispute is None:
        return JsonResponse({"dispute": None})
    services.read_dispute(dispute, request.user)
    return JsonResponse({"dispute": _dispute_row(dispute)})


@json_errors
@require_http_methods(["GET"])
def dispute_orders(request: HttpRequest) -> HttpResponse:
    """客服工作台：进行中的订单，便于发起或跟进介入（REQ-019）。"""
    services.require_staff(request.user)
    disputed = set(
        Dispute.objects.filter(order__status__in=ORDER_ACTIVE_STATUSES).values_list(
            "order_id", flat=True
        )
    )
    rows = []
    orders = Order.objects.filter(status__in=ORDER_ACTIVE_STATUSES).order_by("-id")[:50]
    for order in orders:
        rows.append(
            {
                "order_id": order.pk,
                "product_id": order.product_id,
                "status": order.status,
                "has_dispute": order.pk in disputed,
            }
        )
    return JsonResponse({"items": rows})
