"""演示时钟的持久化。"""

from datetime import timedelta

from django.db import migrations, models


class Migration(migrations.Migration):
    """新增演示时钟表，不影响业务表。"""

    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="DemoClock",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False,
                                           auto_created=True, verbose_name="ID")),
                ("offset", models.DurationField(default=timedelta)),
            ],
        ),
    ]
