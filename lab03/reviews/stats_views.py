"""卖家主页和后台统计入口。"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from accounts.models import ROLE_ADMIN, ROLE_SUPPORT, User
from accounts.permissions import role_of
from common.api import json_errors
from common.timebox import read_period

from .stats import seller_stats
from .views import review_payload, visible_reviews


@require_GET
@json_errors
def seller_profile(request, seller_id, *, staff=False):
    """卖家主页和后台复用同一统计口径。"""
    if staff and role_of(request.user) not in (ROLE_ADMIN, ROLE_SUPPORT):
        return JsonResponse({"error": "无权查看后台统计"}, status=403)
    users = User.objects.all() if staff else User.objects.filter(is_active=True)
    seller = get_object_or_404(users, pk=seller_id)
    start, end = read_period(request.GET)
    stats = seller_stats(seller, start=start, end=end)
    entries = visible_reviews(target=seller).order_by("-id")[:100]
    reviews = [review_payload(review) for review in entries]
    return JsonResponse({"seller_id": seller.pk, "stats": stats, "reviews": reviews})
