"""Django 应用配置。"""

from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    """沟通与纠纷（M5）。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "communication"
    verbose_name = "沟通与纠纷"
