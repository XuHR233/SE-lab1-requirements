"""为空的隔离内测库创建四个可验收账号和基础分类。"""

import os
from pathlib import Path
from secrets import token_urlsafe

from django.conf import settings
from django.db import transaction

from accounts.models import User
from catalog.models import Category, Tag

ACCOUNTS = (
    ("买家", "13900000101", {}),
    ("卖家", "13900000102", {}),
    ("管理员", "13900000103", {"is_staff": True}),
    ("客服", "13900000104", {"is_support": True}),
)


def seed(credentials):
    """一次性建种子；凭据只写权限为 0600 的新文件。"""
    database = settings.DATABASES["default"]
    if (not settings.DEMO_UI or database["HOST"] != "127.0.0.1"
            or database["NAME"] not in {"selab3_ui", "selab3_ui_browser"} or User.objects.exists()):
        raise RuntimeError("只允许初始化空的隔离内测库")
    path = Path(credentials)
    with transaction.atomic(), open(path, "x", encoding="utf-8", opener=_private_file) as output:
        for role, phone, permissions in ACCOUNTS:
            password = token_urlsafe(18)
            User.objects.create_user(username=phone, phone=phone, password=password, **permissions)
            output.write(f"{role}\t{phone}\t{password}\n")
        Category.objects.bulk_create([Category(name=name) for name in ("数码", "图书", "生活", "运动")])
        Tag.objects.bulk_create([Tag(name=name) for name in ("同城", "九成新", "可自提")])


def _private_file(path, flags):
    return os.open(path, flags, 0o600)
