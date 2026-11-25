# 实验三：二手交易系统实现

依据实验二 UML 设计实现的 Django 项目，按需求卡逐条推进。

- 方案与需求：在实验一、实验二仓库（`requirements.md`、`lab02/UML/`），**本仓库只放实现代码**
- 运行环境：atlas（Ubuntu 24.04 / Python 3.12.3）
- 数据库：PostgreSQL 16（容器 `selab3-pg`，127.0.0.1:15432）
- 工具链：pytest + pylint（`fail-under=9.0`）

## 起跑

```bash
cp .env.example .env      # 填 PG_PASSWORD
docker compose up -d
. venv/bin/activate
python manage.py migrate
pytest
pylint config accounts audit catalog tests manage.py
```

## 接口

JSON 接口挂在 `/api/` 下。Atlas 的隔离网页验收与启动步骤见 [验收说明](docs/ATLAS-DEMO.md)，默认不启用演示或模拟支付。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/products/` | 检索商品（列表口径：只有已发布/已预售，已售出对谁都不可见） |
| `POST /api/products/` | 新建草稿（只有买家/卖家账号可以） |
| `GET /api/products/<id>/` | 商品详情（看不见的当不存在） |
| `POST /api/products/<id>/<动作>/` | 提交、改稿、传图、审核、下架、撤回、违规下架、终止、删除 |

错误约定：403 无权限、409 版本过期或状态不允许、400 入参或内容不合法；每次拒绝都写一条
`result=denied` 的操作日志（`audit/OperationLog`）。

## 模块进度

| 模块 | 状态 | 入口 |
|---|---|---|
| M1 账号与权限 | 已完成 | ccounts/、pi/accounts/ |
| M2 商品中心 | 已完成 | catalog/、pi/products/ |
| M3 预售预约 | 已完成（Day 3） | eservations/、pi/products/<id>/reservations/、pi/reservations/ |
| M4 交易与资金 | 已完成（Day 4） | 	rade/、pi/orders/、manage.py sweep_expired |
| M5 沟通与纠纷 | 未开始 | — |
| M6 评价与统计 | 未开始 | — |
| M7 日志与运维 | 部分（操作日志只追加已落地） | udit/ |
