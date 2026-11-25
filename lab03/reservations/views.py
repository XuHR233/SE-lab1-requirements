"""预售预约 HTTP 入口（REQ-006）：建预约、列表、按动作分派。"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from catalog.models import Product
from catalog.history import for_document
from common.api import json_body, json_errors, optional_int

from . import services
from .models import ACTIVE_STATUSES, Reservation


def _reservation(pk: int) -> HttpResponse | Reservation:
    """取预约；不存在就 404。"""
    reservation = Reservation.objects.filter(pk=pk).first()
    if reservation is None:
        return JsonResponse({"error": "预约不存在"}, status=404)
    return reservation


def _row(reservation: Reservation) -> dict:
    """预约的对外表示：金额、状态与三个关键时间点。"""
    return {
        "id": reservation.pk,
        "product_id": reservation.product_id,
        "product_snapshot": for_document(reservation),
        "status": reservation.status,
        "deposit": str(reservation.deposit),
        "final_amount": str(reservation.final_amount),
        "deposit_due_at": reservation.deposit_due_at.isoformat(),
        "final_due_at": (
            reservation.final_due_at.isoformat() if reservation.final_due_at else None
        ),
        "refund_due": str(reservation.refund_due),
        "close_reason": reservation.close_reason,
        "order_id": getattr(getattr(reservation, "order", None), "pk", None),
    }


def _can_view(user: object, reservation: Reservation) -> bool:
    """当事人或客服/管理员可以看这条预约（REQ-002）。"""
    from accounts.permissions import (  # pylint: disable=import-outside-toplevel
        STAFF_ROLES,
        role_of,
        can,
    )

    if role_of(user) in STAFF_ROLES:
        return True
    return can(user, "reservation.view", owner_id=reservation.buyer_id) or can(
        user, "reservation.view", owner_id=reservation.seller_id
    )


@json_errors
@require_http_methods(["GET"])
def reservation_list(request: HttpRequest) -> HttpResponse:
    """当前主体能看到的预约：当事人看自己的，客服与管理员看全部。"""
    rows = services.reservations_for(request.user)
    return JsonResponse({"items": [_row(item) for item in rows]})


@json_errors
@require_http_methods(["POST"])
def reserve_view(request: HttpRequest, pk: int) -> HttpResponse:
    """对已预售商品提交预约：幂等键与商品版本号都从请求体拿。"""
    product = Product.objects.filter(pk=pk).first()
    if product is None:
        return JsonResponse({"error": "商品不存在"}, status=404)
    data = json_body(request)
    reservation = services.reserve(
        product,
        buyer=request.user,
        request_key=data.get("request_key", ""),
        expected_version=optional_int(data.get("version"), "version"),
    )
    return JsonResponse(_row(reservation), status=201)


@json_errors
@require_http_methods(["GET"])
def reservation_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """单条预约详情：看不到的一律 404，不泄露别人在谈什么。"""
    reservation = _reservation(pk)
    if isinstance(reservation, HttpResponse):
        return reservation
    if not _can_view(request.user, reservation):
        return JsonResponse({"error": "预约不存在"}, status=404)
    return JsonResponse(_row(reservation))


@json_errors
@require_http_methods(["POST"])
def reservation_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """预约写操作总入口：权限、时间窗口与退款条款都在用例层。"""
    reservation = _reservation(pk)
    if isinstance(reservation, HttpResponse):
        return reservation
    data = json_body(request)
    actor = request.user

    if action in ("pay-deposit", "pay-final") and not settings.SIMULATED_PAYMENTS:
        return JsonResponse({"error": "未启用模拟支付，真实支付渠道未配置"}, status=503)

    if action == "pay-deposit":
        services.pay_deposit(
            reservation, actor=actor, txn_no=data.get("txn_no", "")
        )
    elif action == "confirm":
        services.seller_confirm(reservation, actor=actor)
    elif action == "reject":
        services.seller_reject(reservation, actor=actor, reason=data.get("reason", ""))
    elif action == "arrive":
        services.confirm_arrival(reservation, actor=actor)
    elif action == "pay-final":
        services.pay_final(reservation, actor=actor, txn_no=data.get("txn_no", ""))
    elif action == "cancel":
        services.cancel(reservation, actor=actor, reason=data.get("reason", ""))
    else:
        return JsonResponse({"error": f"未知动作 {action}"}, status=404)

    reservation.refresh_from_db()
    return JsonResponse(_row(reservation))


def active_ids() -> list[int]:
    """有有效预约的商品主键（供商品检索隐藏用）。"""
    return [
        reservation.product_id
        for reservation in Reservation.objects.filter(status__in=ACTIVE_STATUSES)
    ]
