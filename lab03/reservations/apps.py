"""预售预约应用配置。"""

from django.apps import AppConfig


class ReservationsConfig(AppConfig):
    """M3 预售预约。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "reservations"
    verbose_name = "预售预约"
