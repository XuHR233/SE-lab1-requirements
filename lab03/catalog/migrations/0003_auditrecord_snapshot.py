"""审核流水加内容快照：日后改稿也能凭审核记录还原当时提交的是什么。"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """只加一列，历史流水照旧（老记录快照为空字典）。"""

    dependencies = [
        ("catalog", "0002_auditrecord_withdrawn_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditrecord",
            name="snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="提交时的内容快照"),
        ),
    ]
