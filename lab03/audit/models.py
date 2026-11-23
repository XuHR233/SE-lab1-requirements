"""操作日志：只追加，不可修改删除（REQ-022）。"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, models, transaction
from django.utils import timezone

from accounts.permissions import role_of

from . import context, spool

LOGGER = logging.getLogger(__name__)
PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")

RESULT_ALLOWED = "allowed"
RESULT_DENIED = "denied"


class OperationLog(models.Model):
    """一条操作记录。actor 允许为空，用于记录未登录的越权尝试。"""

    event_id = models.UUIDField(null=True, unique=True, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="操作者",
    )
    actor_label = models.CharField("操作者标识", max_length=64, blank=True)
    actor_role = models.CharField("操作时角色", max_length=16, blank=True)
    action = models.CharField("动作", max_length=64)
    target_type = models.CharField("目标类型", max_length=32, blank=True)
    target_id = models.CharField("目标 ID", max_length=64, blank=True)
    result = models.CharField("结果", max_length=8)
    ip = models.GenericIPAddressField("来源 IP", null=True, blank=True)
    client_type = models.CharField("端类型", max_length=16, blank=True)
    detail = models.JSONField("附加信息", default=dict)
    created_at = models.DateTimeField("时间", default=timezone.now, editable=False)

    class Meta:
        """按时间倒序查询是主要访问方式。"""

        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["action", "result"]),
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            models.Index(fields=["-created_at", "-id"]),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.result}"


def record(  # pylint: disable=too-many-arguments
    *,
    action: str,
    result: str,
    actor: Any = None,
    target_type: str = "",
    target_id: str = "",
    ip: str | None = None,
    client_type: str = "",
    detail: dict | None = None,
) -> OperationLog | None:
    """写入一条日志。调用方不负责去重，重复调用就是多条记录。

    来源 IP 与端类型默认从请求上下文取（`audit/context.py`），调用方要覆盖时再显式传。
    """
    actor_id = getattr(actor, "pk", None)
    event_id = uuid4()
    payload = {
        "actor_id": actor_id,
        "actor_label": f"user:{actor_id}" if actor_id is not None else "",
        "actor_role": role_of(actor) if actor is not None else "system",
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id else "",
        "result": result,
        "ip": ip if ip is not None else context.current_ip(),
        "client_type": (client_type or context.current_client_type())[:16],
        "detail": _redact(detail or {}),
        "created_at": timezone.now().isoformat(),
    }
    try:
        with transaction.atomic():
            return OperationLog.objects.create(event_id=event_id, **payload)
    except DatabaseError:
        LOGGER.warning("审计写入失败，进入重试：事件 %s，动作 %s", event_id, action)
        try:
            spool.enqueue(event_id, payload)
        except OSError:
            LOGGER.exception("审计重试无法落盘，事件 %s", event_id)
    return None


def _redact(value):
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return PHONE.sub(r"\1****\2", value)
    return value


def save_pending(event_id, payload):
    """持久事件标识保证崩溃后重复重试也只写一行。"""
    return OperationLog.objects.get_or_create(event_id=event_id, defaults=payload)
