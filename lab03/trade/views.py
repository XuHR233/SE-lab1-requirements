"""交易与资金 HTTP 入口（REQ-013~017）：下单、支付、取消、发货、收货、退款。"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from catalog.models import Product
from common.api import json_body, json_errors, optional_int

from . import services
from .models import Order


def _order(pk: int) -> HttpResponse | Order:
    """取订单；不存在就 404。"""
    order = Order.objects.filter(pk=pk).first()
    if order is None:
        return JsonResponse({"error": "订单不存在"}, status=404)
    return order


def _row(order: Order) -> dict:
    """订单的对外表示：金额、状态、关键时间点与托管结论。"""
    return {
        "id": order.pk,
        "product_id": order.product_id,
        "status": order.status,
        "amount": str(order.amount),
        "is_presale": order.is_presale,
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "auto_confirm_at": (
            order.auto_confirm_at.isoformat() if order.auto_confirm_at else None
        ),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "tracking_no": order.tracking_no,
        "close_reason": order.close_reason,
    }


def _can_view(user: object, order: Order) -> bool:
    """订单双方或客服/管理员可看（REQ-002/012）。"""
    from accounts.permissions import (  # pylint: disable=import-outside-toplevel
        STAFF_ROLES,
        can,
        role_of,
    )

    if role_of(user) in STAFF_ROLES:
        return True
    return can(user, "order.view", owner_id=order.buyer_id) or can(
        user, "order.view", owner_id=order.seller_id
    )


@json_errors
@require_http_methods(["GET", "POST"])
def orders(request: HttpRequest) -> HttpResponse:
    """GET 我的订单；POST 对已发布商品下单（同一原子操作建单并锁货）。"""
    if request.method == "POST":
        data = json_body(request)
        wanted = optional_int(data.get("product_id"), "product_id") or 0
        product = Product.objects.filter(pk=wanted).first()
        if product is None:
            return JsonResponse({"error": "商品不存在"}, status=404)
        order = services.create_order(
            product,
            buyer=request.user,
            request_key=data.get("request_key", ""),
            expected_version=optional_int(data.get("version"), "version"),
        )
        return JsonResponse(_row(order), status=201)

    rows = services.orders_for(request.user)
    return JsonResponse({"items": [_row(item) for item in rows]})


@json_errors
@require_http_methods(["GET"])
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """单条订单详情：看不到的一律 404。"""
    order = _order(pk)
    if isinstance(order, HttpResponse):
        return order
    if not _can_view(request.user, order):
        return JsonResponse({"error": "订单不存在"}, status=404)
    return JsonResponse(_row(order))


@json_errors
@require_http_methods(["POST"])
def order_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """订单写操作总入口：权限、窗口与退款规则都在用例层。"""
    order = _order(pk)
    if isinstance(order, HttpResponse):
        return order
    data = json_body(request)
    actor = request.user

    if action == "pay":
        services.pay(
            order,
            actor=actor,
            txn_no=data.get("txn_no", ""),
            amount=data.get("amount"),
        )
    elif action == "cancel":
        services.cancel(order, actor=actor, reason=data.get("reason", ""))
    elif action == "ship":
        services.ship(order, actor=actor, tracking_no=data.get("tracking_no", ""))
    elif action == "receive":
        services.confirm_receipt(order, actor=actor)
    elif action == "refund":
        services.request_refund(order, actor=actor, reason=data.get("reason", ""))
    else:
        return JsonResponse({"error": f"未知动作 {action}"}, status=404)

    order.refresh_from_db()
    return JsonResponse(_row(order))
