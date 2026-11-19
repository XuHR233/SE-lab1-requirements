"""日志只追加：UPDATE / DELETE 一律被数据库拒绝（REQ-022）。"""

from django.db import migrations

SQL_UP = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'operation_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_operationlog_no_update
BEFORE UPDATE ON audit_operationlog
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE TRIGGER trg_operationlog_no_delete
BEFORE DELETE ON audit_operationlog
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""

SQL_DOWN = """
DROP TRIGGER IF EXISTS trg_operationlog_no_update ON audit_operationlog;
DROP TRIGGER IF EXISTS trg_operationlog_no_delete ON audit_operationlog;
DROP FUNCTION IF EXISTS audit_log_append_only();
"""


class Migration(migrations.Migration):
    """把只追加规则落到数据库层，应用代码绕不过去。"""

    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(SQL_UP, SQL_DOWN)]
