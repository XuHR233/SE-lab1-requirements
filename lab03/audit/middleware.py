"""把来源 IP 与端类型绑定到请求上下文，日志自动带上（REQ-022/026）。"""

from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse

from . import context


class AuditContextMiddleware:
    """每个请求进入时绑定 IP 与端类型，请求结束（含异常）时复位。"""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """绑定上下文后执行视图。"""
        tokens = context.bind(
            ip=request.META.get("REMOTE_ADDR"),
            client_type=request.headers.get("X-Client-Type") or "web",
        )
        try:
            return self.get_response(request)
        finally:
            context.reset(tokens)
