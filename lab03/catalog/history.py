"""Freeze item information on business documents, never substitute live details."""

from .imagery import thumbnail
from .models import AuditRecord, Product


def capture(product):
    """Capture item content while the caller holds the product write lock."""
    frozen = Product.objects.annotate(cover=thumbnail()).values(
        "title", "description", "brand", "price", "category_id", "category__name", "cover"
    ).get(pk=product.pk)
    frozen["price"] = str(frozen["price"])
    frozen["category"] = frozen.pop("category__name")
    frozen["thumbnail"] = frozen.pop("cover")
    return frozen


def for_document(document):
    """Older documents use only the most recent approved snapshot before creation."""
    if document.product_snapshot:
        return document.product_snapshot
    historical = AuditRecord.objects.filter(
        product_id=document.product_id, approve=True, audited_at__lte=document.created_at
    ).order_by("-audited_at", "-id").values_list("snapshot", flat=True).first() or {}
    images = historical.get("images") or []
    return {**historical, "thumbnail": images[0] if images else None}


def _buyer_documents(actor, product_id):
    from reservations.models import Reservation  # pylint: disable=import-outside-toplevel
    from trade.models import Order  # pylint: disable=import-outside-toplevel

    for model in (Order, Reservation):
        yield model.objects.filter(buyer=actor, product_id=product_id)


def latest_for_buyer(actor, product_id):
    """Use the last purchase or reservation snapshot, if one exists."""
    candidates = [rows.order_by("-created_at", "-pk").first()
                  for rows in _buyer_documents(actor, product_id)]
    latest = max((row for row in candidates if row), key=lambda row: row.created_at, default=None)
    return for_document(latest) if latest else None


def has_historical_image(actor, product_id, url):
    """Only a file listed in the buyer's own frozen document is readable."""
    if not actor.is_authenticated:
        return False
    for rows in _buyer_documents(actor, product_id):
        for document in rows.iterator():
            frozen = for_document(document)
            if url == frozen.get("thumbnail") or url in (frozen.get("images") or []):
                return True
    return False
