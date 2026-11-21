"""Lead workflow endpoints (person/topic -> search -> fetch -> track -> on-chain -> expand). Every mutation: analyst role + audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import SessionDep, analyst, viewer
from app.config import get_settings
from app.models import User
from app.services import leads as svc

router = APIRouter(prefix="/leads", tags=["leads"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.LeadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class ProposeIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200, description="person or topic")
    extra_terms: list[str] = Field(default_factory=list, max_length=10)


@router.post("/queries/propose", dependencies=[Depends(viewer)])
def propose(body: ProposeIn):
    """Deterministic query suggestions (no LLM, nothing is sent anywhere). The analyst edits/approves each one."""
    return {"subject": body.subject.strip(), "queries": _guard(svc.propose_queries, body.subject, body.extra_terms)}


@router.get("/config", dependencies=[Depends(viewer)])
def lead_config():
    s = get_settings().public_view()
    return {
        "tavily_configured": s["tavily_configured"], "search_default_backend": s["search_default_backend"],
        "btc_esplora_configured": s["btc_esplora_configured"], "evm_explorer_networks": s["evm_explorer_networks"],
        "ddgs_notice": "ddgs is an UNOFFICIAL, keyless scraper of public search pages. Its use may conflict with the engines' terms of service; reliability is not guaranteed. Results are leads, not evidence.",
        "fetch_blocked_domains": get_settings().fetch_blocked_domains,
    }


class StartIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)


@router.post("/sessions")
def start(body: StartIn, session: SessionDep, user: User = Depends(analyst)):
    inv = _guard(svc.start_session, session, subject=body.subject, actor=user.username, notes=body.notes)
    return {"investigation_id": inv.id, "name": inv.name}


@router.get("/sessions/{inv_id}", dependencies=[Depends(viewer)])
def get_state(inv_id: str, session: SessionDep):
    return _guard(svc.state, session, inv_id)


@router.get("/runs/{run_id}", dependencies=[Depends(viewer)])
def get_run(run_id: str, session: SessionDep):
    return _guard(svc.run_detail, session, run_id)


class SearchIn(BaseModel):
    investigation_id: str
    query: str = Field(min_length=1, max_length=400)
    backend: str | None = Field(default=None, pattern="^(auto|tavily|ddgs)$")
    max_results: int | None = Field(default=None, ge=1, le=20)


@router.post("/search")
def search(body: SearchIn, session: SessionDep, user: User = Depends(analyst)):
    return _guard(svc.search, session, inv_id=body.investigation_id, query=body.query, backend=body.backend, max_results=body.max_results, actor=user.username)


class FetchIn(BaseModel):
    investigation_id: str
    url: str = Field(min_length=1, max_length=2000)


@router.post("/fetch")
def fetch(body: FetchIn, session: SessionDep, user: User = Depends(analyst)):
    return _guard(svc.fetch, session, inv_id=body.investigation_id, url=body.url, actor=user.username)


class TrackIn(BaseModel):
    investigation_id: str
    fetch_run_id: str
    network: str
    address: str
    rationale: str = Field(min_length=1, max_length=2000)


@router.post("/track")
def track(body: TrackIn, session: SessionDep, user: User = Depends(analyst)):
    return _guard(
        svc.track_address, session, inv_id=body.investigation_id, fetch_run_id=body.fetch_run_id, network=body.network,
        address=body.address, rationale=body.rationale, actor=user.username,
    )


class OnchainIn(BaseModel):
    investigation_id: str
    entity_id: str
    refresh: bool = False


@router.post("/onchain")
def onchain(body: OnchainIn, session: SessionDep, user: User = Depends(analyst)):
    return _guard(svc.lookup_onchain, session, inv_id=body.investigation_id, entity_id=body.entity_id, refresh=body.refresh, actor=user.username)


class LoadMoreIn(BaseModel):
    investigation_id: str
    entity_id: str
    lookup_run_id: str  # the latest history page the analyst has seen; the next cursor is read from its stored observation


@router.post("/onchain/more")
def onchain_more(body: LoadMoreIn, session: SessionDep, user: User = Depends(analyst)):
    """Load the next page of a wallet's transaction history (a separate, audited provider run)."""
    return _guard(svc.load_more, session, inv_id=body.investigation_id, entity_id=body.entity_id, lookup_run_id=body.lookup_run_id, actor=user.username)


class CounterpartyIn(BaseModel):
    network: str
    address: str
    direction: str
    lookup_run_id: str | None = None  # the history page this counterparty was seen in (defaults to the request's run)


class ExpandIn(BaseModel):
    investigation_id: str
    entity_id: str
    lookup_run_id: str
    counterparties: list[CounterpartyIn] = Field(min_length=1, max_length=svc.MAX_EXPAND)


@router.post("/expand")
def expand(body: ExpandIn, session: SessionDep, user: User = Depends(analyst)):
    return _guard(
        svc.expand, session, inv_id=body.investigation_id, entity_id=body.entity_id, lookup_run_id=body.lookup_run_id,
        counterparties=[c.model_dump() for c in body.counterparties], actor=user.username,
    )


class GraphExpandIn(BaseModel):
    entity_id: str


@router.post("/graph-expand")
def graph_expand(body: GraphExpandIn, session: SessionDep, user: User = Depends(analyst)):
    """Graph "expand neighbours" for a wallet: reuse the stored lookup (or run one), link top counterparties, one step."""
    return _guard(svc.expand_wallet, session, entity_id=body.entity_id, actor=user.username)
