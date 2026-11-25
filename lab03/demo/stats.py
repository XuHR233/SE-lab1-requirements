"""统计原型所需的成交图表，只对卖家本人及工作人员开放。"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from common.timebox import local_midnight
from trade.models import COMPLETED, Order

PERIODS = ("30", "7", "month")


def period_start(moment, period):
    """近期筛选沿用北京时间自然日。"""
    today = local_midnight(moment)
    if period == "month":
        return today.replace(day=1)
    return today - timedelta(days=int(period) - 1)


def dashboard(seller, *, moment, start):
    """按所选范围聚合成交金额、手续费、分类和近期成交。"""
    recent_start = local_midnight(moment, days=-6)
    completed = Order.objects.filter(seller=seller, status=COMPLETED, received_at__lt=moment)
    interval = completed.filter(received_at__gte=start)
    totals = interval.aggregate(amount=Sum("amount"), fee=Sum("settlement__fee"))
    by_day = completed.filter(received_at__gte=recent_start).annotate(day=TruncDate("received_at"))
    daily = {row["day"]: row for row in by_day.values("day").annotate(
        count=Count("id"), amount=Sum("amount"))}
    days = [timezone.localtime(recent_start + timedelta(days=offset)).date() for offset in range(7)]
    categories = interval.values("product__category__name").annotate(amount=Sum("amount"))
    latest = interval.order_by("-received_at", "-pk").values(
        "product__title", "received_at", "amount", "settlement__fee")[:6]
    return {
        "amount": totals["amount"] or Decimal("0.00"),
        "fee": totals["fee"] or Decimal("0.00"),
        "daily": [{"day": day, "count": 0, "amount": "0.00", **daily.get(day, {})} for day in days],
        "categories": list(categories.order_by("-amount", "product__category__name")),
        "latest": list(latest),
    }
