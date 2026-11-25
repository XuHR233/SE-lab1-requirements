from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trade', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='product_snapshot',
            field=models.JSONField(blank=True, default=dict, verbose_name='订单商品快照'),
        ),
        migrations.AddField(
            model_name='order',
            name='refunded_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='订单退款时间'),
        ),
    ]
