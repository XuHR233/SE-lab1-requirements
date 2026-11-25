from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservation',
            name='product_snapshot',
            field=models.JSONField(blank=True, default=dict, verbose_name='预约商品快照'),
        ),
    ]
