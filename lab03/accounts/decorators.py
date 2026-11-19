"""视图装饰器：权限判定 + 拒绝留痕（REQ-026）。"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from audit.models import RESULT_ALLOWED, RESULT_DENIED, record

from .permissions import can

OwnerResolver = Callable[[HttpRequest, dict[str, Any]], int | None]


def _client_ip(request: HttpRequest) -> str | None:
    return request.META.get("REMOTE_ADDR")


def require_action(
    action: str, *, resolve_owner: OwnerResolver | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """动作级权限门。resolve_owner 返回目标数据归属人，用于归属校验。"""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            """先判定，拒绝就记日志并抛 403，绝不进入业务函数。"""
            owner_id = resolve_owner(request, kwargs) if resolve_owner else None
            if not can(request.user, action, owner_id=owner_id):
                record(
                    action=action,
                    result=RESULT_DENIED,
                    actor=request.user,
                    target_id=kwargs.get("pk", ""),
                    ip=_client_ip(request),
                    client_type="web",
                )
                raise PermissionDenied(f"无权执行 {action}")
            record(
                action=action,
                result=RESULT_ALLOWED,
                actor=request.user,
                target_id=kwargs.get("pk", ""),
                ip=_client_ip(request),
                client_type="web",
            )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
