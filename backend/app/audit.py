"""Audit log helper + structured logging setup."""

from __future__ import annotations

import json
import logging
import sys

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    session: Session,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    **details,
) -> AuditLog:
    row = AuditLog(actor=actor, action=action, target_type=target_type, target_id=target_id, details=details)
    session.add(row)
    session.flush()
    return row


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("provider", "task_id", "tool_run_id", "investigation_id", "path", "status", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
