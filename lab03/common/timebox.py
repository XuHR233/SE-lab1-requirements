"""时间窗口口径：预售尾款的「次日 00:00 起满 24 小时」在 REQ-006 与 REQ-016 都用同一算法。"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .errors import InvalidInput

# 尾款窗口长度：确认到货后的下一个自然日，整天可付尾款。
FINAL_WINDOW = timedelta(hours=24)


def local_midnight(moment: datetime, *, days: int = 0) -> datetime:
    """取 moment 所在本地时区的当天 00:00 再偏移 days 天。"""
    local = timezone.localtime(moment)
    base = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days)


def next_day_window_end(moment: datetime) -> datetime:
    """确认到货发生在 moment 时，买家可以付尾款的窗口终点。

    例如 11-22 23:00 确认到货 → 窗口为 11-23 00:00 至 11-24 00:00，终点是 11-24 00:00。
    """
    return local_midnight(moment, days=1) + FINAL_WINDOW


def read_period(params):
    """读取 ISO 时间范围；无偏移的值按应用时区解释。"""
    return _parse(params.get("start")), _parse(params.get("end"))


def _parse(text):
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        raise InvalidInput("时间必须为 ISO 8601 格式")
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
