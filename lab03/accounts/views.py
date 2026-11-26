"""注册与登录（REQ-001）。"""

from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as sign_in
from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .models import User

PHONE_RE = re.compile(r"1[3-9][0-9]{9}")
LOGIN_WINDOW = 300
LOGIN_PER_ACCOUNT = 8
LOGIN_PER_ADDRESS = 60


def _fail(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _phone(request: HttpRequest) -> str:
    return "".join((request.POST.get("phone") or "").split())


def _count_attempt(key: str) -> int:
    if cache.add(key, 1, timeout=LOGIN_WINDOW):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=LOGIN_WINDOW)
        return 1


@require_http_methods(["POST"])
def register(request: HttpRequest) -> JsonResponse:
    """手机号注册。重复手机号拒绝，并发下靠唯一约束兜底。"""
    phone = _phone(request)
    code = (request.POST.get("code") or "").strip()
    password = request.POST.get("password") or ""
    if not PHONE_RE.fullmatch(phone):
        return _fail("手机号格式不正确")
    # ponytail: 短信为开发期桩，接真实通道时替换成 verify_code()
    skip = settings.DEMO_UI and settings.DEMO_SMS_SKIP and request.POST.get("skip_sms") == "1"
    if not skip and (not settings.SMS_DEV_CODE or code != settings.SMS_DEV_CODE):
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


@never_cache
@require_http_methods(["POST"])
def sign_in_view(request: HttpRequest) -> JsonResponse:
    """手机号 + 密码登录。失败不区分"手机号不存在"与"密码错"。"""
    phone = _phone(request)
    password = request.POST.get("password") or ""
    address = request.META.get("REMOTE_ADDR", "unknown")
    address_key = "login:address:" + address
    if _count_attempt(address_key) > LOGIN_PER_ADDRESS:
        return _fail("登录尝试过多，请稍后再试", 429)
    if not PHONE_RE.fullmatch(phone) or not password or len(password) > 1024:
        return _fail("手机号或密码不正确")
    account_key = "login:account:" + salted_hmac("login-attempt", f"{address}:{phone}").hexdigest()
    if _count_attempt(account_key) > LOGIN_PER_ACCOUNT:
        return _fail("登录尝试过多，请稍后再试", 429)
    user = authenticate(request, username=phone, password=password)
    if user is None:
        return _fail("手机号或密码不正确")
    cache.delete(account_key)
    sign_in(request, user)
    return JsonResponse({"id": user.pk})


@require_POST
def sign_out_view(request: HttpRequest) -> JsonResponse:
    """结束当前浏览器会话，保留 CSRF 校验。"""
    logout(request)
    return JsonResponse({"logged_out": True})
