# 实验三：二手交易系统实现

依据实验二 UML 设计实现的 Django 项目，按需求卡逐条推进。

- 方案文档：`lab03/实验三_实施方案.md`
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
pylint config accounts
```
