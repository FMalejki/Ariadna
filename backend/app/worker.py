"""Background worker: drains due discovery tasks and purges expired artifacts.

Runs as a separate process (`python -m app.worker`). It uses the same DB-backed task table as the API
(DiscoveryTask is the durable queue, with retry/backoff/dead-letter), so no extra broker is required for
correctness. Redis + RQ/Celery is a PLANNED scaling option (see docs/LLM_WIKI/decisions/0006-task-queue.md).
"""

from __future__ import annotations

import logging
import signal
import time

from app.artifacts import purge_expired
from app.audit import configure_logging
from app.db import get_session_factory
from app.main import init_runtime
from app.services import automation, discovery

log = logging.getLogger("osint.worker")
_stop = False


def tick(batch: int = 25) -> int:
    with get_session_factory()() as session:
        try:
            done = discovery.run_pending(session, limit=batch, actor="worker")
            purge_expired(session)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("worker tick failed")
            return 0
        try:  # automation runs advance in bounded chunks (one run's failure must not block discovery)
            advanced = automation.advance_due_runs(session)
        except Exception:
            session.rollback()
            log.exception("automation tick failed")
            advanced = 0
    return len(done) + advanced


def main(poll_seconds: float = 5.0) -> None:
    global _stop
    configure_logging()
    init_runtime()
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("_stop", True))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("_stop", True))
    log.info("worker started")
    while not _stop:
        n = tick()
        if n == 0:
            time.sleep(poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
