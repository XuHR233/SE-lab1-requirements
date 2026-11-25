"""内测页面、授权读工作台和显式快进端点。"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from accounts.models import ROLE_ADMIN, ROLE_MEMBER, User
from accounts.permissions import STAFF_ROLES, role_of
from audit.models import record
from catalog.models import Category, Tag
from common.api import json_body, json_errors
from reservations import services as reservations
from reviews import services as reviews
from reviews.stats import seller_stats
from trade import services as trade

from . import clock
from . import desk as read_model
from .stats import PERIODS, dashboard, period_start


def _require_demo():
    if not settings.DEMO_UI:
        raise Http404


@require_GET
@never_cache
@ensure_csrf_cookie
def home(request):
    """单一验收入口；浏览器仍使用普通 Django 会话。"""
    _require_demo()
    return render(request, "demo/home.html", {
        "demo_config": {"user": request.user.pk, "role": role_of(request.user),
                        "smsSkip": settings.DEMO_SMS_SKIP, "payments": settings.SIMULATED_PAYMENTS,
                        "clock": clock.enabled(), "smsCode": bool(settings.SMS_DEV_CODE)},
    })


@require_GET
@never_cache
@json_errors
def desk(request):
    """游客只读分类，业务数据按当事人或工作人员授权。"""
    _require_demo()
    actor = request.user
    data = {"categories": list(Category.objects.values("id", "name")),
            "tags": list(Tag.objects.values_list("name", flat=True)),
            "now": clock.business_now()}
    if actor.is_authenticated:
        data.update(products=read_model.products_for(actor),
                    disputes=read_model.disputes_for(actor))
    if role_of(actor) == ROLE_MEMBER:
        data.update(orders=read_model.orders_for(actor),
                    reservations=read_model.reservations_for(actor))
    return JsonResponse(data)


@require_GET
@never_cache
@json_errors
def seller_dashboard(request, pk):
    """卖家工作台的私有成交明细；其他卖家仍使用公开主页。"""
    _require_demo()
    role = role_of(request.user)
    if role not in STAFF_ROLES and (role != ROLE_MEMBER or request.user.pk != pk):
        raise PermissionDenied("仅本人或工作人员可以查看成交明细")
    seller = get_object_or_404(User, pk=pk)
    period = request.GET.get("period", "30")
    if period not in PERIODS:
        return JsonResponse({"error": "统计区间无效"}, status=400)
    moment = clock.business_now()
    start = period_start(moment, period)
    return JsonResponse({"stats": seller_stats(seller, start=start, end=moment),
                         "dashboard": dashboard(seller, moment=moment, start=start)})


@require_POST
@never_cache
@json_errors
def advance_clock(request):
    """管理员快进业务时钟或按当前时间执行原有定时任务。"""
    _require_demo()
    if not clock.enabled():
        raise Http404
    if role_of(request.user) != ROLE_ADMIN:
        raise PermissionDenied("只有内测管理员可以推进时间")
    step = json_body(request).get("step")
    moment = clock.business_now() if step == "sweep" else clock.advance(step)
    counts = {"预约关闭": reservations.sweep(now=moment), "订单推进": trade.sweep(now=moment)}
    counts["自动评价"], counts["申诉提醒"] = reviews.sweep(now=moment)
    record(action="demo.clock", result="allowed", actor=request.user,
           detail={"step": step, "business_now": moment.isoformat(), **counts})
    return JsonResponse({"now": moment, "counts": counts})
