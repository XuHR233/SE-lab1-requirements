"""交易与资金应用配置。"""

from django.apps import AppConfig


class TradeConfig(AppConfig):
    """M4 交易与资金。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "trade"
    verbose_name = "交易与资金"
