"""Day 0 冒烟：数据库能连、账号唯一性生效（REQ-001）。"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction


@pytest.mark.django_db
def test_database_is_reachable() -> None:
    """数据库连接可用。"""
    with connection.cursor() as cursor:
        cursor.execute("select 1")
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db
def test_phone_must_be_unique() -> None:
    """同一手机号只能注册一个账号。"""
    user_model = get_user_model()
    user_model.objects.create_user(username="a", phone="13800000000", password="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        user_model.objects.create_user(username="b", phone="13800000000", password="x")
