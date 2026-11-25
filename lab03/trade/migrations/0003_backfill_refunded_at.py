"""Recover the original refund time, excluding subsequent late returns."""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):
    """A legacy refunded order's first refund is its state-changing refund."""
    alias = schema_editor.connection.alias
    orders = apps.get_model("trade", "Order").objects.using(alias)
    refunds = apps.get_model("trade", "Refund").objects.using(alias)
    first = refunds.filter(order_id=OuterRef("pk")).order_by("created_at", "pk")
    orders.filter(status="已退款", refunded_at__isnull=True).update(
        refunded_at=Subquery(first.values("created_at")[:1])
    )


class Migration(migrations.Migration):
    """Backfill only rows with existing evidence, never invent a date."""

    dependencies = [("trade", "0002_order_product_snapshot_order_refunded_at")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
