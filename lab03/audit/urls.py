"""日志查询路由。"""

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET

from common.api import json_errors

from .query import query_logs, read_log


@require_GET
@json_errors
def logs(request):
    """返回过滤后的日志列表或订单时间线。"""
    return JsonResponse(query_logs(request.user, request.GET))


@require_GET
@json_errors
def log_detail(request, log_id):
    """查看一条属于当前查询权限的审计日志。"""
    data = read_log(request.user, log_id)
    if data is None:
        return JsonResponse({"error": "日志不存在"}, status=404)
    return JsonResponse(data)


urlpatterns = [path("logs/", logs), path("logs/<int:log_id>/", log_detail)]
