# 实验三：二手交易系统实现

按实验一需求、实验二 UML 和实验三工作卡实现的 Django 项目。本仓库保存实现代码、测试与验收记录。

- 运行环境：Atlas（Ubuntu 24.04 / Python 3.12.3），数据库为 PostgreSQL 16。
- 工具链：pytest、Pylint（项目门槛 `fail-under=9.0`）。

## 从空库验收

在含 `manage.py` 的目录执行（公共仓库中为 `lab03/`）。需要 Bash、Python 3.12（含 venv）、Docker Compose 和 OpenSSL。使用新检出目录，不复用其他站点的环境或数据库。

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
export PG_HOST=127.0.0.1 PG_PORT=15432 PG_DB=selab3_ui_browser PG_USER=selab3_demo
export PG_PASSWORD="$(openssl rand -hex 24)"
export DJANGO_SECRET_KEY="$(openssl rand -hex 32)"
export DEMO_UI=1 DEMO_SMS_SKIP=1 SIMULATED_PAYMENTS=1 DEMO_CLOCK=1
docker compose -p lab3-ta up --wait db
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
mkdir -p tmp
venv/bin/python manage.py shell -c "from demo.seed import seed; seed('tmp/ui-browser-accounts.tsv')"
venv/bin/python manage.py runserver --noreload --insecure 127.0.0.1:18738
```

如 PostgreSQL 宿主机端口已占用，在启动前更换 `PG_PORT`。若 `lab3-ta` 已用于其他测试，换一个项目名使用新的空卷。种子只对空库执行一次。浏览器打开 `http://127.0.0.1:18738/`，另开终端查看 `tmp/ui-browser-accounts.tsv` 获取随机测试密码，勿提交或截图展示凭据。

在同一目录的另一终端运行 `venv/bin/python ops/check_demo_http.py`，预期四条 `PASS`：四角色登录、现货、预售、客服转交与模拟退款。脚本会写入业务记录、推进模拟时间，只能对本次隔离库运行。

停止网页用 `Ctrl+C`，结束数据库用 `docker compose -p lab3-ta down`（使用启动时的终端和项目名）。默认保留数据库卷；不能用新生成的密码接着启动旧卷。此模式仅供本机验收，不接实际支付、短信或物流。Compose 只启动 PostgreSQL，Django 在宿主机运行。

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

以上为课程内测进度，不代表生产外部渠道、性能或运维目标已验收。详细范围见 [Day0–Day7 验收对照](docs/DAY0-DAY7-ACCEPTANCE.md)；后续变更见 [CR-001](docs/CR-001.md)、[CR-002](docs/CR-002.md) 和 [CR-003](docs/CR-003.md)。

## 商品接口示例

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/products/` | 检索对外可见的已发布、已预售商品；已售出不出现在列表中 |
| `POST /api/products/` | 普通账号新建商品草稿 |
| `GET /api/products/<id>/` | 查看有权访问的商品详情；历史订单与预约另读其快照 |
| `POST /api/products/<id>/<action>/` | 提审、修改、添加图片、审核、下架、退回草稿或删除草稿等 |

常见错误：`403` 无权限、`404` 不存在或不可见、`409` 状态或版本冲突、`400` 输入不合法。
