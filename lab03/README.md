# 实验三：二手交易系统实现

按实验一需求、实验二 UML 和实验三工作卡实现的 Django 项目。本仓库保存实现代码、测试与验收记录。

- 运行环境：Atlas（Ubuntu 24.04 / Python 3.12.3），数据库为 PostgreSQL 16。
- 工具链：pytest、Pylint（项目门槛 `fail-under=9.0`）。
- Atlas 网页验收与启动方式见 [Atlas 验收说明](docs/ATLAS-DEMO.md)。短信与支付仅提供内测方式，不接真实渠道。

## 启动与检查

以下为新环境的示例；已有 `.env`、数据库和虚拟环境时请复用，不要覆盖。

```bash
cp .env.example .env
```

在 `.env` 中填写数据库密码等本地配置，再运行：

```bash
docker compose up -d
python3 -m venv venv
. venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
pytest
pylint accounts audit catalog common communication config demo reservations reviews trade tests ops manage.py
```

## 模块进度

| 模块 | 状态 | 代码与入口 |
| --- | --- | --- |
| M1 账号与权限 | 核心已实现；短信为开发内测 | `accounts/`；`/accounts/register`、`/accounts/login`、`/accounts/logout` |
| M2 商品中心 | 已实现草稿、审核、上架、搜索与状态可见性 | `catalog/`；`/api/products/`、`/api/products/<id>/` |
| M3 预售预约（Day 3） | 已实现预约锁货、定金与尾款、到期退款和下架；款项为模拟 | `reservations/`；`/api/products/<id>/reservations/`、`/api/reservations/` |
| M4 交易与资金（Day 4） | 已实现下单锁货、模拟支付、履约、退款与账面结算 | `trade/`；`/api/orders/`、`python manage.py sweep_expired` |
| M5 沟通与纠纷（Day 5） | 已实现图文会话、客服接单、升级与唯一裁定；网页仍手动刷新 | `communication/`；`/api/chats/`、`/api/chats/disputes/` |
| M6 评价与统计（Day 6） | 已实现互评、申诉及成交量、在架数、退款率；扩展指标未实现 | `reviews/`；`/api/orders/<id>/review/`、`/api/sellers/<id>/`、`/api/staff/review-appeals/` |
| M7 日志与运维（Day 7） | 已实现只追加日志、查询和重试；备份恢复脚本已保留，本轮不追加恢复演练或部署调度 | `audit/`、`ops/`；`/api/logs/`、`python manage.py retry_audit` |

以上为课程内测进度，不代表生产外部渠道、性能或运维目标已验收。详细范围见 [Day0–Day7 验收对照](docs/DAY0-DAY7-ACCEPTANCE.md)，后续确认的规则以 [CR-001](docs/CR-001.md) 和 [CR-002](docs/CR-002.md) 为准。

## 商品接口示例

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/products/` | 检索对外可见的已发布、已预售商品；已售出不出现在列表中 |
| `POST /api/products/` | 普通账号新建商品草稿 |
| `GET /api/products/<id>/` | 查看有权访问的商品详情；历史订单与预约另读其快照 |
| `POST /api/products/<id>/<action>/` | 提审、修改、添加图片、审核、下架、退回草稿或删除草稿等 |

常见错误：`403` 无权限、`404` 不存在或不可见、`409` 状态或版本冲突、`400` 输入不合法。
