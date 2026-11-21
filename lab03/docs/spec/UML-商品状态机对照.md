# 商品状态机与 UML 对照（复核用）

本文件是给复核者（人或 AI）用的逐条对账表：UML 的每一根箭头，在代码里对应哪个函数，在测试里对应哪个用例。
**权威顺序**：用户 2026-11-21 明确「REQ 与 UML 冲突时以 UML 为准」，因此本表以实验二的两张状态图为唯一基准。

## 1. 基准文件（实验二交付物）

| 文件 | 行数 | 承载的转换数 |
| --- | --- | --- |
| `lab02/UML/状态图.puml` | 47 | 13 条（成交主线） |
| `lab02/UML/状态图2_异常与退出.puml` | 70 | 12 条（驳回/撤回/重审/回退/违规/删除） |
| 合计 | — | **25 条** |

两个易错点，先在这里钉住：

- `状态图.puml:45` 的 `S7 -[hidden]right-> S8` 是 **PlantUML 布局线，不是转换**（第 3 行注释已声明本图不含隐藏布局线）。
  所以是 25 条，不是 26 条；「已售出→已下架」这条边不存在，已售出是终态。
- `状态图.puml:27` 的 `[*] --> S1 : 卖家新建` 是**初始伪状态**，不计入 25 条；它在代码里是 `save_draft()`。

## 2. 代码落点

仓库 `git@github.com:XuHR233/SE-lab3-implementation.git`，提交 `2aca33a` 之后在本机 `atlas` 的 `<local-path>/`（该仓库只放代码，私有库）。

| 关注点 | 位置 |
| --- | --- |
| 11 个状态常量 | `catalog/models.py` 顶部（`DRAFT`…`DELETED`）+ `PRODUCT_STATUSES` 11 元组 |
| 25 条转换表 | `catalog/state.py:25-37` 的 `PRODUCT_TRANSITIONS`，`catalog/state.py:39-43` 摊平成 `ALLOWED_PAIRS` |
| 表判定（不碰库） | `catalog/state.py:50` `can_transit(source, target)` |
| 落库转换（条件 UPDATE） | `catalog/state.py:55` `transit(product, target, *, bump_version=False)` |
| 用例层入口 | `catalog/services.py`：`submit_for_audit`、`audit`、`revise_content`、`unpublish`、`back_to_draft`、`takedown`、`terminate_trade`、`delete_draft`、`update_draft`（行号随改动漂移，用下面的命令现查） |
| HTTP 入口 | `catalog/views.py`：`products`、`product_detail`、`product_action`（`/api/products/…`），异常映射见 `json_errors` |
| 拒绝时统一记日志 | `catalog/services.py` 的 `_deny()`（权限不足，403）与 `_deny_transit()`（状态不允许，409） |
| 验收测试 | `tests/test_m2.py`（39 个 test） |

行号会随改动漂移，需要现查时用：

```bash
cd <local-path> && grep -n '^def ' catalog/services.py && sed -n '25,43p' catalog/state.py
```

## 3. 25 条转换逐条对账

「代码入口」是**做这件事的函数**；「测试」是覆盖它的 test 名。M4 才实现的边（锁货/支付/退款）只有表内允许与 121 组合测试守卫，标为 `待 Day 4`。

### 3.1 状态图（一）成交主线 13 条

| # | UML 来源 | 转换 | 触发者 | 代码入口 | 测试 |
| --- | --- | --- | --- | --- | --- |
| 1 | `状态图.puml:28` | 草稿 → 待审核 | 卖家 | `submit_for_audit` | `test_submit_moves_to_pending_and_opens_one_audit_record` |
| 2 | `状态图.puml:29` | 待审核 → 已发布 | 管理员 | `audit` | `test_approve_publishes_normal_product` |
| 3 | `状态图.puml:30` | 待审核 → 已预售 | 管理员 | `audit` | `test_approve_publishes_presale_product_as_presale` |
| 4 | `状态图.puml:33` | 已发布 → 待付款锁定 | 买家下单 | 待 Day 4（M4） | `test_state_machine_covers_all_121_combinations`（表） |
| 5 | `状态图.puml:34` | 待付款锁定 → 交易中 | 支付回调 | 待 Day 4（M4） | 同上（表） |
| 6 | `状态图.puml:35` | 已预售 → 交易中 | 尾款支付 | 待 Day 4（M4） | 同上（表） |
| 7 | `状态图.puml:36` | 交易中 → 已售出 | 确认收货/自动确认 | 待 Day 4（M4） | 同上（表） |
| 8 | `状态图.puml:38` | 待付款锁定 → 已发布 | 取消/超时放货 | 待 Day 4（M4） | 同上（表） |
| 9 | `状态图.puml:39` | 交易中 → 已发布 | 未发货退款（普通） | 待 Day 4（M4） | 同上（表） |
| 10 | `状态图.puml:40` | 交易中 → 已预售 | 未发货退款（预售） | 待 Day 4（M4） | 同上（表） |
| 11 | `状态图.puml:42` | 已发布 → 已下架 | 卖家 | `unpublish` | `test_unpublish_hides_product` |
| 12 | `状态图.puml:43` | 已预售 → 已下架 | 卖家（先退定金） | `unpublish` | `test_presale_product_can_be_unpublished_by_seller` |
| 13 | `状态图.puml:44` | 交易中 → 已下架 | 客服/管理员裁定终止 | `terminate_trade` | `test_support_terminates_trading_product` |

### 3.2 状态图（二）异常与退出 12 条

| # | UML 来源 | 转换 | 触发者 | 代码入口 | 测试 |
| --- | --- | --- | --- | --- | --- |
| 14 | `状态图2:30` | 待审核 → 已驳回 | 管理员（必填理由） | `audit` | `test_reject_requires_reason` |
| 15 | `状态图2:31` | 待审核 → 草稿 | 卖家撤回 | `back_to_draft` | `test_seller_withdraws_pending_submission` |
| 16 | `状态图2:32` | 已驳回 → 草稿 | 卖家 | `back_to_draft` | `test_reject_then_resubmit_accumulates_two_records` |
| 17 | `状态图2:35` | 已发布 → 待审核 | 卖家改内容 | `revise_content` | `test_revise_bumps_version_and_returns_to_pending` |
| 18 | `状态图2:36` | 已预售 → 待审核 | 卖家改内容 | `revise_content` | 同一函数，预售走同一分支 |
| 19 | `状态图2:39` | 已下架 → 草稿 | 卖家 | `back_to_draft` | `test_relist_must_pass_audit_again` |
| 20 | `状态图2:42` | 已发布 → 违规下架 | 管理员 | `takedown` | `test_takedown_requires_reason_and_admin_role` |
| 21 | `状态图2:43` | 已预售 → 违规下架 | 管理员 | `takedown` | 121 组合测试（表） |
| 22 | `状态图2:44` | 待付款锁定 → 违规下架 | 管理员 | `takedown` | 121 组合测试（表） |
| 23 | `状态图2:45` | 已下架 → 违规下架 | 管理员 | `takedown` | 121 组合测试（表） |
| 24 | `状态图2:46` | 违规下架 → 草稿 | 卖家 | `back_to_draft` | `test_violated_product_goes_back_to_draft` |
| 25 | `状态图2:49` | 草稿 → 已删除 | 卖家（逻辑删除） | `delete_draft` | `test_only_draft_can_be_deleted_and_row_is_kept` |

## 4. 图上的注释也是约束：三条角色规则

UML 的注释不是装饰，代码必须照做，这三条都有独立测试：

| UML 注释 | 说的什么 | 代码怎么落实 | 测试 |
| --- | --- | --- | --- |
| `状态图2:59-63`（SLOCK 注） | 待付款锁定期间卖家不能主动下架、改价、改内容；也不存在「卖家下架」这条转换 | `unpublish` 显式只放行 `PUBLISHED/PRESALE`；`revise_content` 只放行 `PUBLISHED/PRESALE`；表里 `LOCKED` 也没有指向 `OFFSHELF` 的边 | `test_seller_cannot_unpublish_locked_or_trading`、`test_trading_product_cannot_be_revised` |
| `状态图2:51-57`（S9 注） | 交易中不可违规下架，只能未发货退款恢复，或已发货后裁定终止转已下架 | `takedown` 先查表（`TRADING` 没有指向 `VIOLATED` 的边）再拒绝并记日志 | `test_trading_product_cannot_be_taken_down` |
| `状态图2:65-68`（S10 注） | 物理删除一律禁止，只做逻辑删除 | `delete_draft` 只改 `status=DELETED`，行留在库里 | `test_only_draft_can_be_deleted_and_row_is_kept` |

## 5. 与 REQ 的关系：哪里有冲突、哪里是 UML 说了算

逐条比对过 `requirements.md` 的 REQ-010（第 131 行列的 25 种转换）与两张图，**25 条边完全一致，没有边级的冲突**。
UML 比 REQ 多说的是「谁可以触发」，REQ-008/REQ-009 的异常段与之一致。三处按 UML/最小权限收紧，记在这里以免被当成偏差：

1. **交易中→已下架不给卖家。** `unpublish` 只放行 `PUBLISHED/PRESALE`。这条边在表里存在，但触发者只能是客服/管理员（`terminate_trade`，权限取 `order.refund`）。
   依据：`状态图.puml:44` 写的是「已发货后裁定终止或退款」，`状态图2:59-63` 注释说明卖家没有这条转换；REQ-008 异常段同口径。
2. **违规下架不给客服。** `takedown` 要 `product.takedown`，该动作的允许角色只有 `ROLE_ADMIN`（`accounts/permissions.py:15`）。
   依据：REQ-002「客服只有退款权，无封号权与下架权」；客服的处置走第 1 条的裁定终止。
3. **被拒的转换要留日志。** REQ-010 异常段末句要求「不属于上表的其他转换请求返回状态冲突并记录日志」，UML 没提这条，但两者不冲突，已由 `_deny()` 与 `_deny_transit()` 统一实现（`audit` 表里 `action="product.transit"`、`result="denied"`，`detail` 带 `from`/`to`/`reason`）。

## 6. 复核怎么做（四条命令，都可复现）

```bash
cd <local-path>
source venv/bin/activate

# ① 边集是否就是 25 条：应当打印 25
python - <<'PY'
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from catalog.state import ALLOWED_PAIRS
print(len(ALLOWED_PAIRS))
PY

# ② 状态机全量组合：25 过 / 96 拒 / 121 总
python -m pytest tests/test_m2.py -v -k "121 or exactly_the_25"

# ③ 全部验收测试与风格
python -m pytest tests
PYTHONPATH=. python -m pylint config accounts audit catalog tests manage.py --score=y
```

预期输出（2026-11-21 实测）：① `25`；③ `153 passed`、`10.00/10`。

## 7. 还没做的部分（不是遗漏，是排期）

上面 7 条 M4 边（#4–#10）属于 Day 4 的交易与资金模块（锁货、支付回调、尾款、确认收货、取消/超时放货、两种未发货退款）。目前它们由两样东西守住：`PRODUCT_TRANSITIONS` 里的存在性与 `test_state_machine_covers_all_121_combinations` 的 121 组合断言（25 过、96 拒）。Day 4 接上去时不需要改状态机，只需要在订单服务里调用 `transit()`。

## 8. 2026-11-21 复核整改（针对独立复核报告）

独立复核（另一个模型，对照 REQ 与 UML 读代码并动态复现）提了 4 组 Day 2 问题，处置如下。
只列与本状态机相关的；完整清单见 `docs/evidence/day2-audit-fix.md`。

| 复核结论 | 状态机层面的事实 | 处置 |
| --- | --- | --- |
| ABA 旧版本覆盖：旧 v1 页面提交后库里变成 v3/1.00 | 与边集无关，是 `revise_content` 先存内容后转状态、版本只在内存比较 | 改成一条带 `version=expected_version` 条件的 UPDATE，`rowcount!=1` 即 409 |
| 管理员可建商品、图片入口无权限主体 | 边集没错，缺的是入口前的权限判断 | `save_draft` 补 `product.create`、`add_image` 补 `product.edit` 与归属判断 |
| 改稿不校验内容、有待审商品能直接通过 | `已发布→待审核` 这条边是通的，但过去不校验内容 | `revise_content` 写库前校验、`audit` 通过前复查；-1.00 价格与缺图都会被拦 |
| 撤回后遗留待审流水 | `待审核→草稿` 这条边是通的，但流水没跟着关 | `AuditRecord` 加 `withdrawn_at`，撤回时 `_close_open_audit()` |
| 25 条精确校验自我循环 | 边集本身经复核独立抽出 13+12 与代码一致 | 测试改硬编码 25 条边与 11 状态名，不再从被测表里取期望值 |

整改后的门控：`pytest tests` → **153 passed**；`pylint` → **10.00/10**。
本表第 3 节的行号与函数名已同步（函数名去掉了行号后缀，免得每次改动都过期）。