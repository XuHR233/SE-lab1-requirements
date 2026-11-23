"""重试暂时无法写入 PostgreSQL 的审计事件。"""

from django.core.management.base import BaseCommand, CommandError

from audit.models import save_pending
from audit.spool import retry_pending


class Command(BaseCommand):
    """命令退出异常时，由调度者告警。"""

    help = "重试暂未写入数据库的操作日志"

    def handle(self, *args, **options):
        """汇报已写入与失败数，不输出日志内容。"""
        written, failed = retry_pending(save_pending)
        self.stdout.write(f"审计重试成功 {written} 条，仍失败 {failed} 条")
        if failed:
            raise CommandError("仍有未写入的操作日志")
