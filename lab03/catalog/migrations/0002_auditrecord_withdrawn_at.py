"""审核流水加撤回时间：卖家撤回提交时关闭待审流水，不再算「等审核」。"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """只加一列，历史流水保持 untouched。"""

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditrecord",
            name="withdrawn_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="撤回时间"),
        ),
    ]
