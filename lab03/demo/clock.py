"""仅隔离内测实例可快进的业务时钟。"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.errors import InvalidInput
from common.timebox import local_midnight

from .models import DemoClock

CLOCK_KEY = 1
STEPS = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1),
         "day": timedelta(days=1), "review": timedelta(days=16)}


def enabled():
    """两个内测开关同时打开才使用偏移。"""
    return settings.DEMO_UI and settings.DEMO_CLOCK


def business_now():
    """默认使用真实时间；演示时仅改变业务窗口的时间参数。"""
    offset = timedelta()
    if enabled():
        offset = (DemoClock.objects.filter(pk=CLOCK_KEY)
                  .values_list("offset", flat=True).first()) or offset
    return timezone.now() + offset


@transaction.atomic
def advance(step):
    """在行锁内单调快进，不接受客户端指定的任意绝对时间。"""
    if not enabled() or not isinstance(step, str) or step not in (*STEPS, "midnight"):
        raise InvalidInput("未启用内测时钟或快进选项无效")
    clock, _ = DemoClock.objects.select_for_update().get_or_create(pk=CLOCK_KEY)
    real_now = timezone.now()
    before = real_now + clock.offset
    after = (local_midnight(before, days=1) if step == "midnight" else before + STEPS[step])
    clock.offset = after - real_now
    clock.save(update_fields=["offset"])
    return after
