"""应用配置。"""

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """商品中心。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    verbose_name = "商品中心"
