"""审计写入失败、恢复重试以及数据库不可改删（REQ-022）。"""

import json

from django.core.management import call_command
from django.db import DatabaseError, transaction
import pytest

from audit.models import OperationLog, record
from audit.spool import enqueue


@pytest.mark.django_db
def test_log_cannot_be_updated_or_deleted():
    """PostgreSQL 触发器拒绝改删，回滚子事务后原值保留。"""
    log = record(action="test", result="allowed")
    rows = OperationLog.objects.filter(pk=log.pk)
    with pytest.raises(DatabaseError), transaction.atomic():
        rows.update(result="denied")
    with pytest.raises(DatabaseError), transaction.atomic():
        rows.delete()
    assert rows.get().result == "allowed"


@pytest.mark.django_db
def test_retry_preserves_event_and_deduplicates(monkeypatch, settings, tmp_path, buyer):
    """模拟数据库断开，不影响调用方；重复交付保留同一条脱敏记录。"""
    settings.AUDIT_RETRY_DIR = tmp_path / "private"

    def fail(**_fields):
        raise DatabaseError("mock database outage")

    with monkeypatch.context() as patch:
        patch.setattr(OperationLog.objects, "create", fail)
        row = record(action="test", result="allowed", actor=buyer,
                     detail={"reason": "联系 13812345678"})
    assert row is None
    queued, = settings.AUDIT_RETRY_DIR.glob("*.json")
    payload = json.loads(queued.read_text())
    call_command("retry_audit")
    enqueue(queued.stem, payload)
    call_command("retry_audit")
    recovered = OperationLog.objects.get(event_id=queued.stem)
    assert recovered.actor_role == "member"
    assert recovered.detail == {"reason": "联系 138****5678"}
    assert not list(settings.AUDIT_RETRY_DIR.glob("*.json"))
