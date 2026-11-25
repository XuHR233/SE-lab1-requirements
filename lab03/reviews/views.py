"""评价、卖家主页统计和管理员申诉 API。"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import ROLE_ADMIN
from accounts.permissions import role_of
from catalog.search import visible_to
from common.api import json_body, json_errors, optional_int
from common.errors import InvalidInput
from trade.models import COMPLETED, Order

from . import services
from .models import Appeal, Review


def review_payload(review):
    """序列化可展示的评价。"""
    return {"id": review.pk, "author_id": review.author_id, "target_id": review.target_id,
            "rating": review.rating, "content": review.content, "automatic": review.automatic}


def visible_reviews(**filters):
    """筛掉已删除和被屏蔽的评价。"""
    return Review.objects.filter(**filters, deleted_at__isnull=True, hidden_at__isnull=True)


@require_http_methods(["POST", "DELETE"])
@json_errors
def order_review(request, order_id):
    """提交或删除本人的订单评价。"""
    order = get_object_or_404(Order, pk=order_id)
    if request.method == "DELETE":
        services.delete(order, request.user)
        return JsonResponse({"deleted": True})
    body = json_body(request)
    review = services.submit(order, request.user, rating=body.get("rating"),
                             content=body.get("content", ""))
    return JsonResponse(review_payload(review))


@require_POST
@json_errors
def raise_appeal(request, review_id):
    """登记评价申诉。"""
    review = get_object_or_404(Review.objects.select_related("order"), pk=review_id)
    entry = services.appeal(review, request.user, reason=json_body(request).get("reason"))
    return JsonResponse({"id": entry.pk, "due_at": entry.due_at}, status=201)


@require_GET
@json_errors
def appeal_queue(request):
    """管理员查看待处理及超期提醒。"""
    if role_of(request.user) != ROLE_ADMIN:
        return JsonResponse({"error": "只有管理员可查看"}, status=403)
    page = optional_int(request.GET.get("page", 1), "页码")
    if page is None or not 1 <= page <= 100000:
        raise InvalidInput("页码超出范围")
    pending = Appeal.objects.filter(resolved_at__isnull=True).order_by("due_at", "id")
    offset = (page - 1) * 100
    fields = pending.values("id", "review_id", "reason", "due_at", "reminded_at")
    rows = list(fields[offset:offset + 101])
    return JsonResponse({"pending": rows[:100], "page": page, "has_next": len(rows) > 100})


@require_POST
@json_errors
def resolve_appeal(request, appeal_id):
    """管理员结案。"""
    entry = get_object_or_404(Appeal.objects.select_related("review__order"), pk=appeal_id)
    body = json_body(request)
    services.resolve(entry, request.user, upheld=body.get("upheld"), note=body.get("note"))
    return JsonResponse({"resolved": True})


@require_GET
def product_reviews(request, product_id):
    """按指定商品展示有效评价。"""
    entries = visible_reviews(order__product_id=product_id)
    if not visible_to(request.user).filter(pk=product_id).exists():
        if not request.user.is_authenticated:
            return JsonResponse({"error": "商品不存在"}, status=404)
        historical = Order.objects.filter(
            product_id=product_id, buyer=request.user, status=COMPLETED,
        )
        if not historical.exists():
            return JsonResponse({"error": "商品不存在"}, status=404)
        entries = entries.filter(order__in=historical)
    entries = entries.order_by("-id")[:100]
    return JsonResponse({"reviews": [review_payload(review) for review in entries]})
