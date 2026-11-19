"""操作日志：只追加，不可修改删除（REQ-022）。"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

RESULT_ALLOWED = "allowed"
RESULT_DENIED = "denied"


class OperationLog(models.Model):
    """一条操作记录。actor 允许为空，用于记录未登录的越权尝试。"""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="操作者",
    )
    actor_label = models.CharField("操作者标识", max_length=64, blank=True)
    action = models.CharField("动作", max_length=64)
    target_type = models.CharField("目标类型", max_length=32, blank=True)
    target_id = models.CharField("目标 ID", max_length=64, blank=True)
    result = models.CharField("结果", max_length=8)
    ip = models.GenericIPAddressField("来源 IP", null=True, blank=True)
    client_type = models.CharField("端类型", max_length=16, blank=True)
    detail = models.JSONField("附加信息", default=dict)
    created_at = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        """按时间倒序查询是主要访问方式。"""

        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"
        ordering = ["-id"]
        indexes = [models.Index(fields=["action", "result"])]

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
) -> OperationLog:
    """写入一条日志。调用方不负责去重，重复调用就是多条记录。"""
    actor_id = getattr(actor, "pk", None)
    label = ""
    if actor_id is not None:
        label = f"user:{actor_id}"
    return OperationLog.objects.create(
        actor=actor if actor_id is not None else None,
        actor_label=label,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else "",
        result=result,
        ip=ip,
        client_type=client_type,
        detail=detail or {},
    )
