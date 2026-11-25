"""读模型共用的商品首图选择。"""

from django.db.models import Case, OuterRef, Subquery, Value, When

from .models import ProductImage


def thumbnail(product_key="pk"):
    """用子查询读取排序最前的图片，无额外逐行查询。"""
    images = ProductImage.objects.filter(product_id=OuterRef(product_key)).order_by(
        Case(When(url="/static/demo/item.svg", then=Value(1)), default=Value(0)), "sort", "id"
    )
    return Subquery(images.values("url")[:1])
