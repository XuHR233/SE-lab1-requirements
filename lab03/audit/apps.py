"""audit app 配置。"""

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """操作日志模块（REQ-022 / REQ-026）。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
