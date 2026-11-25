"""验收页面的最小读模型；写操作仍交给各业务 API。"""

from django.db.models import Count, Prefetch

from accounts.models import ROLE_ADMIN, ROLE_MEMBER
from accounts.permissions import role_of
from catalog.imagery import thumbnail
from catalog.models import DELETED, LOCKED, OFFSHELF, PENDING, PRESALE, PUBLISHED, Product
from communication import services as chats
from communication.models import OPEN_ORDER_STATUSES
from common.timebox import FINAL_WINDOW
from reservations import services as reservations
from reviews.models import Review
from trade import services as trade


def products_for(actor):
    """普通成员只看自己的商品；管理员看送审和可违规下架的商品。"""
    candidates = Product.objects.annotate(image_count=Count("images"), thumbnail=thumbnail())
    if role_of(actor) == ROLE_MEMBER:
        candidates = candidates.filter(seller=actor).exclude(status=DELETED)
    elif role_of(actor) == ROLE_ADMIN:
        candidates = candidates.filter(status__in=(PENDING, PUBLISHED, PRESALE, LOCKED, OFFSHELF))
    else:
        return []
    return list(candidates.values("id", "title", "status", "price", "category_id", "brand",
                                  "description", "version", "is_presale", "deposit",
                                  "final_amount", "presale_valid_days", "image_count",
                                  "thumbnail", "view_count", "updated_at")[:100])


def _review_rows(order):
    return [
        {"id": entry.pk, "author": entry.author_id, "target": entry.target_id,
         "rating": entry.rating, "content": entry.content, "automatic": entry.automatic,
         "appealed": hasattr(entry, "appeal")}
        for entry in order.desk_reviews
    ]


def orders_for(actor):
    """同订单 API 的可见性；附上互评及真实账面结算摘要。"""
    reviews = Review.objects.filter(hidden_at__isnull=True, deleted_at__isnull=True)
    rows = trade.orders_for(actor).select_related("product__category", "settlement", "dispute")
    rows = rows.annotate(thumbnail=thumbnail("product_id"))
    rows = rows.prefetch_related(Prefetch("reviews", reviews.select_related("appeal"),
                                         to_attr="desk_reviews"))[:100]
    return [
        {"id": row.pk, "title": row.product.title, "product_id": row.product_id,
         "buyer_id": row.buyer_id, "seller_id": row.seller_id, "status": row.status,
         "amount": row.amount, "is_presale": row.is_presale, "expires_at": row.expires_at,
         "thumbnail": row.thumbnail, "description": row.product.description,
         "category": row.product.category.name, "created_at": row.created_at,
         "paid_at": row.paid_at, "shipped_at": row.shipped_at, "received_at": row.received_at,
         "auto_confirm_at": row.auto_confirm_at, "tracking_no": row.tracking_no,
         "close_reason": row.close_reason, "reviews": _review_rows(row),
         "has_dispute": hasattr(row, "dispute"),
         "can_raise": row.status in OPEN_ORDER_STATUSES and not hasattr(row, "dispute"),
         "settlement": {"fee": row.settlement.fee, "net": row.settlement.seller_net}
         if hasattr(row, "settlement") else None}
        for row in rows
    ]


def reservations_for(actor):
    """同预约 API 的可见性；计算界面展示用的尾款起点。"""
    rows = reservations.reservations_for(actor).select_related("product", "order")
    rows = rows.annotate(thumbnail=thumbnail("product_id"))[:100]
    return [
        {"id": row.pk, "title": row.product.title, "buyer_id": row.buyer_id,
         "seller_id": row.seller_id, "status": row.status, "deposit": row.deposit,
         "final_amount": row.final_amount, "deposit_due_at": row.deposit_due_at,
         "expires_at": row.expires_at, "final_due_at": row.final_due_at,
         "thumbnail": row.thumbnail, "created_at": row.created_at,
         "description": row.product.description,
         "final_start": row.final_due_at - FINAL_WINDOW if row.final_due_at else None,
         "refund_due": row.refund_due, "close_reason": row.close_reason,
         "order_id": row.order.pk if hasattr(row, "order") else None}
        for row in rows
    ]


def disputes_for(actor):
    """仅返回当前角色按纠纷用例有权访问的记录。"""
    return list(chats.list_disputes(actor).values(
        "id", "order_id", "conversation_id", "status", "reason", "verdict", "note", "handler_id",
        "created_at", "closed_at", "raised_by_id", "raised_source", "order__status",
        "escalated_at", "escalation_reason",
    )[:100])
