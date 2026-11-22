"""HTTP 层公共件：JSON 请求体解析、可选整数参数、异常到状态码的翻译。"""

from __future__ import annotations

import json
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse

from .errors import Conflict, InvalidInput


def json_body(request: HttpRequest) -> dict:
    """把请求体当 JSON 对象解析；空体给空字典，坏 JSON 记 400。"""
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise InvalidInput("请求体不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidInput("请求体必须是 JSON 对象")
    return payload


def optional_int(value: object, name: str = "参数") -> int | None:
    """可选整数参数：空值给 None，非整数记 400。"""
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise InvalidInput(f"{name} 必须是整数") from exc


def json_errors(view):
    """403 无权限、409 状态/版本冲突、400 入参不合法；错误响应不含业务数据。"""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """执行视图并统一翻译异常。"""
        try:
            return view(request, *args, **kwargs)
        except PermissionDenied as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except Conflict as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except (InvalidInput, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapper
