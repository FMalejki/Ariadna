"""Lead automation endpoints. Start/abort need the analyst role and are audited; reading needs viewer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import SessionDep, analyst, viewer
from app.models import User
from app.services import automation as svc
from app.services.leads import LeadError

router = APIRouter(prefix="/automation", tags=["automation"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LeadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/config", dependencies=[Depends(viewer)])
def config():
    return {
        "stages": [{"id": s, "label": svc.STAGE_INFO[s], "requires": svc.DEPENDS.get(s)} for s in svc.STAGES],
        "default_limits": svc.DEFAULT_LIMITS, "hard_caps": svc.HARD_CAPS, "max_expand": svc.MAX_EXPAND,
    }


class LimitsIn(BaseModel):
    max_queries: int | None = None
    max_pages: int | None = None
    max_addresses: int | None = None
    max_seconds: int | None = None


class StartIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    login: str | None = Field(default=None, max_length=64)
    investigation_id: str | None = None
    stages: list[str] = Field(min_length=1, max_length=5)
    queries: list[str] = Field(default_factory=list, max_length=20)
    limits: LimitsIn = Field(default_factory=LimitsIn)


@router.post("/runs")
def start(body: StartIn, session: SessionDep, user: User = Depends(analyst)):
    run = _guard(
        svc.start_run, session, actor=user.username, stages=body.stages, limits=body.limits.model_dump(exclude_none=True),
        full_name=body.full_name, login=body.login, queries=body.queries, investigation_id=body.investigation_id,
    )
    return svc.run_state(session, run.id)


@router.get("/runs", dependencies=[Depends(viewer)])
def runs(session: SessionDep, investigation_id: str | None = None, limit: int = 20):
    return {"runs": svc.list_runs(session, investigation_id=investigation_id, limit=limit)}


@router.get("/runs/{run_id}", dependencies=[Depends(viewer)])
def get_run(run_id: str, session: SessionDep):
    return _guard(svc.run_state, session, run_id)


@router.post("/runs/{run_id}/abort")
def abort(run_id: str, session: SessionDep, user: User = Depends(analyst)):
    _guard(svc.abort_run, session, run_id, user.username)
    return svc.run_state(session, run_id)
