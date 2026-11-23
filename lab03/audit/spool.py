"""数据库不可用时，将待重试的日志写入私有目录。"""

import json
import logging
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError

LOGGER = logging.getLogger(__name__)


def enqueue(event_id, payload):
    """先完整写入临时文件再更名，读者不会读到半条事件。"""
    directory = Path(settings.AUDIT_RETRY_DIR)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise OSError("日志重试目录必须为私有目录")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False)
        temporary.replace(directory / f"{event_id}.json")
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def retry_pending(save, *, limit=500):
    """重试有限数量的事件，数据库落地后才移除文件。"""
    directory = Path(settings.AUDIT_RETRY_DIR)
    if directory.exists() and (directory.is_symlink() or directory.stat().st_mode & 0o077):
        raise OSError("日志重试目录必须为私有目录")
    written = failed = 0
    for path in sorted(directory.glob("*.json"))[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            save(path.stem, payload)
            path.unlink(missing_ok=True)
            written += 1
        except FileNotFoundError:
            continue
        except (OSError, ValueError, DatabaseError):
            LOGGER.exception("审计事件重试失败，事件 %s", path.stem)
            failed += 1
    return written, failed
