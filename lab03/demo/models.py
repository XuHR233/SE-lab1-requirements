"""演示用业务时钟偏移，不修改服务器时间。"""

from datetime import timedelta

from django.db import models


class DemoClock(models.Model):
    """内测库中的单行时钟偏移，跨进程和重启保留。"""

    offset = models.DurationField(default=timedelta)
