"""共享测试夹具：四类主体、分类，以及「已预售/已发布商品」工厂。

M1/M2 的测试文件里各自也有同名夹具，模块级定义会覆盖这里的定义，
所以旧用例的账号与状态不受影响；新的 M3/M4 用例从这里取主体。
"""

# pylint: disable=redefined-outer-name,unused-argument
# 理由：pytest 的 fixture 按参数名注入。

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from catalog.models import (
    PRESALE,
    PUBLISHED,
    Category,
    Product,
    ProductImage,
)

User = get_user_model()


@pytest.fixture
def category(db):
    """一个分类，所有用例共用。"""
    return Category.objects.create(name="数码")


@pytest.fixture
def seller(db):
    """卖家本人。"""
    return User.objects.create_user(username="seller", phone="13800000001", password="pw-A1b2c3")


@pytest.fixture
def buyer(db):
    """买家：与卖家无关的普通账号（同一账号也能卖东西，这里只是用例分工）。"""
    return User.objects.create_user(username="buyer", phone="13800000005", password="pw-A1b2c3")


@pytest.fixture
def other(db):
    """与商品无关的另一个普通用户。"""
    return User.objects.create_user(username="other", phone="13800000002", password="pw-A1b2c3")


@pytest.fixture
def admin(db):
    """管理员，用于审核与违规下架。"""
    return User.objects.create_user(
        username="root", phone="13800000003", password="pw-A1b2c3", is_staff=True
    )


@pytest.fixture
def support(db):
    """客服：只有退款与裁定权，没有下架权（REQ-002）。"""
    return User.objects.create_user(
        username="cs", phone="13800000004", password="pw-A1b2c3", is_support=True
    )


def make_product(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seller,
    category,
    *,
    status=PUBLISHED,
    title="旧相机",
    price="100.00",
    is_presale=False,
    deposit="0.00",
    final_amount="0.00",
    presale_valid_days=0,
    with_image=True,
):
    """按指定状态造一个商品；默认带一张图，满足上架的最低内容要求。"""
    product = Product.objects.create(
        seller=seller,
        category=category,
        title=title,
        price=Decimal(price),
        status=status,
        is_presale=is_presale,
        deposit=Decimal(deposit),
        final_amount=Decimal(final_amount),
        presale_valid_days=presale_valid_days,
    )
    if with_image:
        ProductImage.objects.create(product=product, url="https://img.example/1.jpg")
    return product


def make_presale(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seller,
    category,
    *,
    price="100.00",
    deposit="20.00",
    final_amount="80.00",
    presale_valid_days=30,
    status=PRESALE,
    title="预售相机",
):
    """一个可预约的预售商品：定金 20 + 尾款 80 = 价格 100，有效期 30 天。"""
    return make_product(
        seller,
        category,
        status=status,
        title=title,
        price=price,
        is_presale=True,
        deposit=deposit,
        final_amount=final_amount,
        presale_valid_days=presale_valid_days,
    )
