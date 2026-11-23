"""卖家维度聚合（REQ-021）。"""

from decimal import Decimal

from django.db.models import Count, Q

from catalog.models import PRESALE, PUBLISHED, Product
from common.errors import InvalidInput
from trade.models import COMPLETED, REFUNDED, Order


def seller_stats(seller, *, start=None, end=None):
    """成交按收货时刻，退款按退款记录时刻；在架量是当前快照。"""
    if start and end and start >= end:
        raise InvalidInput("开始时间必须早于结束时间")
    completed = Q(status=COMPLETED)
    refunded = Q(status=REFUNDED)
    if start:
        completed &= Q(received_at__gte=start)
        refunded &= Q(refunds__created_at__gte=start)
    if end:
        completed &= Q(received_at__lt=end)
        refunded &= Q(refunds__created_at__lt=end)
    totals = Order.objects.filter(seller=seller).aggregate(
        completed=Count("pk", filter=completed, distinct=True),
        refunded=Count("pk", filter=refunded, distinct=True),
    )
    denominator = totals["completed"] + totals["refunded"]
    rate = Decimal(totals["refunded"]) / denominator if denominator else None
    return {
        **totals,
        "on_sale": Product.objects.filter(seller=seller, status__in=(PUBLISHED, PRESALE)).count(),
        "refund_rate": str(rate.quantize(Decimal("0.0001"))) if rate is not None else "—",
    }
