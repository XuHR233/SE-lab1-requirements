"""商品中心 HTTP 入口：视图只做参数搬运，权限与状态一律由用例层把关。

每个视图都被 `json_errors` 包住，把用例层的异常翻译成 403/409/400：
- `PermissionDenied` → 403（REQ-002 要求的「无权限提示」）
- `VersionConflict` / `StateConflict` → 409（版本过期、状态不允许）
- `InvalidContent` / `ValueError` → 400（入参或内容不合法）
"""

from __future__ import annotations

import json
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from . import services
from .models import Category, Product
from .search import search, visible_to
from .state import StateConflict

def _json_body(request: HttpRequest) -> dict:
    """把请求体当 JSON 解析；空体当作空字典，坏 JSON 直接 400。"""
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise services.InvalidContent("请求体不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise services.InvalidContent("请求体必须是 JSON 对象")
    return payload


def _optional_int(value: object, name: str = "参数") -> int | None:
    """把可选参数转成整数；空值给 None，非整数给 400。"""
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise services.InvalidContent(f"{name} 必须是整数") from exc


def _category(pk: object) -> Category | None:
    """按主键取分类；空值给 None（草稿允许暂时不选分类）。"""
    key = _optional_int(pk, "category_id")
    if key is None:
        return None
    category = Category.objects.filter(pk=key).first()
    if category is None:
        raise services.InvalidContent("分类不存在")
    return category


def _content(data: dict) -> dict:
    """从请求体里挑选内容字段，`category_id` 换成分类对象。"""
    fields = {key: data[key] for key in services.CONTENT_FIELDS if key in data}
    if "category_id" in data:
        fields["category"] = _category(data["category_id"])
    return fields


def _row(product: Product) -> dict:
    """商品的对外表示：只给页面需要的字段，不外泄卖家联系方式等。"""
    return {
        "id": product.pk,
        "title": product.title,
        "price": str(product.price),
        "status": product.status,
        "version": product.version,
        "is_presale": product.is_presale,
    }


def json_errors(view):
    """把用例层异常翻译成 4xx JSON（错误响应里不带商品内容）。"""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """执行视图并统一处理异常。"""
        try:
            return view(request, *args, **kwargs)
        except PermissionDenied as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except (services.VersionConflict, StateConflict) as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except (services.InvalidContent, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapper


@json_errors
@require_http_methods(["GET", "POST"])
def products(request: HttpRequest) -> HttpResponse:
    """GET 按条件检索商品；POST 新建草稿（草稿允许内容不完整）。"""
    if request.method == "POST":
        data = _json_body(request)
        product = services.save_draft(
            services.DraftInput(
                seller=request.user,
                category=_category(data.get("category_id")),
                title=data.get("title", ""),
                price=data.get("price"),
                description=data.get("description", ""),
                brand=data.get("brand", ""),
                is_presale=bool(data.get("is_presale", False)),
            )
        )
        return JsonResponse({"id": product.pk, "status": product.status}, status=201)

    rows = search(
        keyword=request.GET.get("keyword"),
        category_id=_optional_int(request.GET.get("category"), "category"),
        tag=request.GET.get("tag"),
        price_min=request.GET.get("price_min"),
        price_max=request.GET.get("price_max"),
        sort=request.GET.get("sort", "newest"),
        page=_optional_int(request.GET.get("page"), "page") or 1,
    )
    return JsonResponse({"items": [_row(item) for item in rows]})


@json_errors
@require_http_methods(["GET"])
def product_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """商品详情：看不见的商品一律当不存在，避免泄露草稿与违规内容。"""
    if not visible_to(request.user).filter(pk=pk).exists():
        return JsonResponse({"error": "商品不存在"}, status=404)
    product = Product.objects.get(pk=pk)
    return JsonResponse(_row(product))


@json_errors
@require_http_methods(["POST"])
def product_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """商品写操作总入口：按 action 分派，权限/状态/审核流水都在用例层。"""
    product = Product.objects.filter(pk=pk).first()
    if product is None:
        return JsonResponse({"error": "商品不存在"}, status=404)
    data = _json_body(request)
    actor = request.user

    if action == "submit":
        services.submit_for_audit(product, actor=actor)
    elif action == "update":
        services.update_draft(
            product,
            actor=actor,
            expected_version=_optional_int(data.get("version"), "version"),
            **_content(data),
        )
    elif action == "images":
        services.add_image(product, data.get("url", ""), actor=actor)
    elif action == "revise":
        services.revise_content(
            product,
            actor=actor,
            expected_version=_optional_int(data.get("version"), "version"),
            **_content(data),
        )
    elif action == "audit":
        services.audit(
            product,
            auditor=actor,
            approve=bool(data.get("approve")),
            reason=data.get("reason", ""),
        )
    elif action == "unpublish":
        services.unpublish(product, actor=actor)
    elif action == "withdraw":
        services.back_to_draft(product, actor=actor)
    elif action == "takedown":
        services.takedown(product, actor=actor, reason=data.get("reason", ""))
    elif action == "terminate":
        services.terminate_trade(product, actor=actor, reason=data.get("reason", ""))
    elif action == "delete":
        services.delete_draft(product, actor=actor)
    else:
        return JsonResponse({"error": f"未知动作 {action}"}, status=404)

    product.refresh_from_db()
    return JsonResponse(_row(product))
