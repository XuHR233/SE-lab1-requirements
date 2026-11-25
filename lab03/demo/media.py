"""本地内测图片：转码后保存，访问权限由关联的商品或会话控制。"""

from contextlib import contextmanager
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from PIL import Image, ImageOps

from accounts.permissions import can
from catalog import services as catalog
from catalog.models import Product, ProductImage
from catalog.search import visible_to
from common.api import json_errors, optional_int
from common.errors import InvalidInput
from communication import services as chats
from communication.models import Conversation, Message

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
IMAGE_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")


def _webp(upload):
    if upload is None or not 0 < upload.size <= MAX_BYTES:
        raise InvalidInput("请选择不超过 5 MB 的图片")
    try:
        with Image.open(upload, formats=IMAGE_FORMATS) as original:
            if original.width * original.height > MAX_PIXELS:
                raise InvalidInput("图片不得超过 2000 万像素")
            original.thumbnail((1400, 1400))
            transparent = "A" in original.getbands() or "transparency" in original.info
            mode = "RGBA" if transparent else "RGB"
            converted = ImageOps.exif_transpose(original).convert(mode)
            with BytesIO() as output:
                converted.save(output, format="WEBP", quality=86)
                return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidInput("无法读取图片，请选择有效的 JPG、PNG、WebP 或 GIF 文件") from exc


def _require_demo():
    if not settings.DEMO_UI:
        raise Http404


@contextmanager
def _store(request, *, scope, subject):
    picture = _webp(request.FILES.get("file"))
    key = uuid4()
    folder = settings.DEMO_MEDIA_ROOT
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{key}.webp"
    with path.open("xb") as stream:
        try:
            stream.write(picture)
            stream.flush()
            with transaction.atomic():
                yield reverse(f"demo-{scope}-file", kwargs={"pk": subject, "key": key})
        except Exception:
            path.unlink(missing_ok=True)
            raise


def _serve(key):
    path = settings.DEMO_MEDIA_ROOT / f"{key}.webp"
    try:
        return FileResponse(path.open("rb"), content_type="image/webp")
    except FileNotFoundError as exc:
        raise Http404 from exc


@require_POST
@json_errors
def upload_product(request, pk):
    """登记商品图片，继续使用原服务的版本、归属与状态守卫。"""
    _require_demo()
    product = get_object_or_404(Product, pk=pk)
    if not can(request.user, "product.edit", owner_id=product.seller_id):
        raise PermissionDenied("只有卖家本人可以上传商品图片")
    version = optional_int(request.POST.get("version"), "version")
    with _store(request, scope="product", subject=pk) as url:
        catalog.add_image(product, url, actor=request.user, expected_version=version)
    return JsonResponse({"thumbnail": url, "version": product.version}, status=201)


@require_GET
@never_cache
def product_file(request, pk, key):
    """商品公开时可显示，草稿只对授权查看人可见。"""
    _require_demo()
    if not visible_to(request.user).filter(pk=pk).exists():
        raise Http404
    if not ProductImage.objects.filter(product_id=pk, url=request.path).exists():
        raise Http404
    return _serve(key)


@require_POST
@json_errors
def upload_chat(request, pk):
    """图片与消息一起提交；无权发消息时不保留文件。"""
    _require_demo()
    conversation = get_object_or_404(Conversation, pk=pk)
    dispute_id = optional_int(request.POST.get("dispute_id"), "dispute_id")
    chats.require_message_sender(conversation, request.user, dispute_id=dispute_id)
    with _store(request, scope="chat", subject=pk) as url:
        message = chats.send_message(conversation, request.user,
                                     text=request.POST.get("text", ""), image=url,
                                     dispute_id=dispute_id)
    return JsonResponse({"id": message.pk, "image": url}, status=201)


@require_GET
@never_cache
def chat_file(request, pk, key):
    """图片继承会话和消息的可见性；不提供开放的静态文件目录。"""
    _require_demo()
    conversation = get_object_or_404(Conversation, pk=pk)
    try:
        chats.read_conversation(conversation, request.user)
    except PermissionDenied as exc:
        raise Http404 from exc
    messages = Message.objects.filter(conversation=conversation, image=request.path)
    if not messages.exclude(hides__user_id=request.user.pk).exists():
        raise Http404
    return _serve(key)
