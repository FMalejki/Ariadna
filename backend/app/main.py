"""FastAPI application: modular monolith entrypoint. All routes live under /api/v1 (OpenAPI at /api/v1/openapi.json)."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import automation, knowledge, leads, operations, system
from app.audit import configure_logging
from app.config import get_settings
from app.db import Base, get_engine, get_session_factory
from app.providers.registry import sync_providers
from app.security import bootstrap_admin
from app.services import wiki as wiki_svc

log = logging.getLogger("osint.api")
API_PREFIX = "/api/v1"


def init_runtime() -> None:
    """Idempotent startup: schema (SQLite dev only; PostgreSQL uses Alembic), providers, admin, wiki mirror."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(get_engine())
    with get_session_factory()() as session:
        sync_providers(session)
        created = bootstrap_admin(session)
        try:
            wiki_svc.sync_from_disk(session)
        except Exception:  # wiki mirror must never block the API
            log.exception("wiki sync failed")
        session.commit()
    if created:
        log.info("bootstrapped admin user from OSINT_ADMIN_PASSWORD")
    elif not settings.admin_password:
        log.warning("OSINT_ADMIN_PASSWORD is not set: no admin user is bootstrapped (create users manually)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_runtime()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OSINT Knowledge Platform",
        version="0.1.0",
        description="Persistent OSINT evidence & knowledge-graph platform. Evidence over assumptions; see docs/LLM_WIKI.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        log.info("request", extra={"path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - start) * 1000)})
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    for r in (system.router, knowledge.router, operations.router, leads.router, automation.router):
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
