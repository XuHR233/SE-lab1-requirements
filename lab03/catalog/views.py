"""商品中心 HTTP 入口：视图只做参数搬运，权限与状态一律由用例层把关。

统一由 `common.api.json_errors` 把用例层异常翻译成 403/409/400（错误响应不含业务数据）。
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from common.api import json_body, json_errors, optional_int

from . import services
from .models import Category, Product
from .search import search, visible_to

def _category(pk: object) -> Category | None:
    """按主键取分类；空值给 None（草稿允许暂时不选分类）。"""
    key = optional_int(pk, "category_id")
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
        "deposit": str(product.deposit),
        "final_amount": str(product.final_amount),
        "presale_valid_days": product.presale_valid_days,
    }


def _draft(data: dict, seller: object) -> services.DraftInput:
    """把请求体拼成建草稿的入参，预售三件套缺省为 0/0/0，布尔字段走严格解析。"""
    return services.DraftInput(
        seller=seller,
        category=_category(data.get("category_id")),
        title=data.get("title", ""),
        price=data.get("price"),
        description=data.get("description", ""),
        brand=data.get("brand", ""),
        is_presale=(
            services.as_bool(data["is_presale"], "is_presale")
            if "is_presale" in data
            else False
        ),
        deposit=data.get("deposit", 0),
        final_amount=data.get("final_amount", 0),
        presale_valid_days=data.get("presale_valid_days", 0),
    )


@json_errors
@require_http_methods(["GET", "POST"])
def products(request: HttpRequest) -> HttpResponse:
    """GET 按条件检索商品；POST 新建草稿（草稿允许内容不完整）。"""
    if request.method == "POST":
        data = json_body(request)
        product = services.save_draft(_draft(data, seller=request.user))
        return JsonResponse({"id": product.pk, "status": product.status}, status=201)

    rows = search(
        keyword=request.GET.get("keyword"),
        category_id=optional_int(request.GET.get("category"), "category"),
        tag=request.GET.get("tag"),
        price_min=request.GET.get("price_min"),
        price_max=request.GET.get("price_max"),
        sort=request.GET.get("sort", "newest"),
        page=optional_int(request.GET.get("page"), "page") or 1,
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
    data = json_body(request)
    actor = request.user

    if action == "submit":
        services.submit_for_audit(product, actor=actor)
    elif action == "update":
        services.update_draft(
            product,
            actor=actor,
            expected_version=optional_int(data.get("version"), "version"),
            **_content(data),
        )
    elif action == "images":
        services.add_image(
            product,
            data.get("url", ""),
            actor=actor,
            expected_version=optional_int(data.get("version"), "version"),
        )
    elif action == "revise":
        services.revise_content(
            product,
            actor=actor,
            expected_version=optional_int(data.get("version"), "version"),
            **_content(data),
        )
    elif action == "audit":
        if "approve" not in data:
            raise services.InvalidContent("approve 必须是 true 或 false")
        services.audit(
            product,
            auditor=actor,
            approve=services.as_bool(data["approve"], "approve"),
            reason=data.get("reason", ""),
        )
    elif action == "unpublish":
        services.unpublish(product, actor=actor)
    elif action == "withdraw":
        services.back_to_draft(product, actor=actor)
    elif action == "takedown":
        services.takedown(product, actor=actor, reason=data.get("reason", ""))
    elif action == "delete":
        services.delete_draft(product, actor=actor)
    else:
        return JsonResponse({"error": f"未知动作 {action}"}, status=404)

    product.refresh_from_db()
    return JsonResponse(_row(product))
