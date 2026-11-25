"""定时清理入口：预约与订单的超时统一在一条命令里推进（REQ-006/013/015/016）。

cron 示例（每 5 分钟）：
    */5 * * * * cd <local-path> && venv/bin/python manage.py sweep_expired
        >> tmp/sweep.log 2>&1
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from demo.clock import business_now
from reservations import services as reservations
from reviews import services as reviews
from trade import services as trade


class Command(BaseCommand):
    """按当前时间（或 --now 指定的时间）跑一轮超时清理。"""

    help = "推进定金超时、预约有效期届满、尾款逾期、订单支付超时与自动确认/退款"

    def add_arguments(self, parser):
        """`--now` 用 ISO 时间模拟某一刻，便于验收演示。"""
        parser.add_argument("--now", default="", help="ISO 时间，例如 2026-11-22T23:00:00+08:00")

    def handle(self, *args, **options):
        """返回一句话汇总，方便 cron 日志里看。"""
        moment = self._now(options.get("now", ""))
        closed_reservations = reservations.sweep(now=moment)
        closed_orders = trade.sweep(now=moment)
        auto_reviews, reminders = reviews.sweep(now=moment)
        self.stdout.write(f"自动评价 {auto_reviews} 条，评价申诉提醒 {reminders} 条")
        self.stdout.write(
            f"预约关闭 {closed_reservations} 条，订单推进 {closed_orders} 条（{moment.isoformat()}）"
        )

    @staticmethod
    def _now(text: str):
        """把 --now 解析成带时区的时间；解析不了直接拒绝执行。"""
        if not text:
            return business_now()
        moment = parse_datetime(text)
        if moment is None:
            raise CommandError("--now 必须是 ISO 8601 时间")
        if timezone.is_naive(moment):
            return timezone.make_aware(moment)
        return moment
