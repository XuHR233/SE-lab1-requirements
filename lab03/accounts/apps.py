"""accounts app 配置。"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """账号与权限模块（M1）。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
