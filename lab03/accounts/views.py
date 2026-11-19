"""注册与登录（REQ-001）。"""

from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as sign_in
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from .models import User

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _fail(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


@require_http_methods(["POST"])
def register(request: HttpRequest) -> JsonResponse:
    """手机号注册。重复手机号拒绝，并发下靠唯一约束兜底。"""
    phone = (request.POST.get("phone") or "").strip()
    code = (request.POST.get("code") or "").strip()
    password = request.POST.get("password") or ""
    if not PHONE_RE.match(phone):
        return _fail("手机号格式不正确")
    # ponytail: 短信为开发期桩，接真实通道时替换成 verify_code()
    if code != settings.SMS_DEV_CODE:
        return _fail("验证码不正确")
    try:
        validate_password(password)
    except ValidationError as exc:
        return _fail(" ".join(exc.messages))
    if User.objects.filter(phone=phone).exists():
        return _fail("该手机号已注册")
    try:
        with transaction.atomic():
            user = User.objects.create_user(username=phone, phone=phone, password=password)
    except IntegrityError:
        return _fail("该手机号已注册")
    sign_in(request, user)
    return JsonResponse({"id": user.pk, "phone": user.phone}, status=201)


@require_http_methods(["POST"])
def sign_in_view(request: HttpRequest) -> JsonResponse:
    """手机号 + 密码登录。失败不区分"手机号不存在"与"密码错"。"""
    phone = (request.POST.get("phone") or "").strip()
    password = request.POST.get("password") or ""
    user = authenticate(request, username=phone, password=password)
    if user is None:
        return _fail("手机号或密码不正确")
    sign_in(request, user)
    return JsonResponse({"id": user.pk})
