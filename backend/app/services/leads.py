"""Lead workflow: person/topic -> web search -> page fetch -> address candidates -> track -> on-chain -> expand.

STATUS: IMPLEMENTED (unit/API-tested with mocked HTTP; live-node result in docs/LLM_WIKI/sessions/).

Every step is an explicit analyst decision, is audited, and creates only what the decision covers:

  propose_queries   deterministic suggestions (no LLM); nothing is sent anywhere
  search            runs `web_search` for ONE approved query -> leads in the ToolRun artifact (no graph writes)
  fetch             runs `web_fetch` for ONE chosen URL -> a web_page observation + address candidates with context
  track             analyst decides an address is worth tracking -> wallet entity + PAGE_REFERENCES_ENTITY (a *mention*)
  lookup_onchain    runs the chain provider for a tracked wallet -> balance / recent tx / counterparties (facts)
  expand            analyst picks counterparties -> wallet entities + WALLET_TRANSACTED_WITH (an on-chain *fact*)

What this module deliberately never does: create an ownership relationship, link a person to a wallet, treat a search
snippet as evidence, or follow links/counterparties automatically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repeatability as rep
from app.artifacts import get_store
from app.audit import audit
from app.config import get_settings
from app.db import utcnow
from app.domain import (
    EntityType,
    EvidenceType,
    ObservationEntityRole,
    ObservationType,
    RelationshipType,
    ReviewStatus,
    ToolRunStatus,
)
from app.models import (
    Artifact,
    Entity,
    Investigation,
    InvestigationItem,
    Observation,
    ObservationEntity,
    Provider,
    Relationship,
    ToolRun,
)
from app.normalize import (
    EVM_NETWORKS,
    _address_ref,
    is_bitcoin_address,
    is_evm_address,
    is_solana_address,
)
from app.providers.base import ProviderContext, ProviderInput, ProviderInputError
from app.providers.onchain import _wei_to_native, sats_to_btc
from app.providers.registry import get_provider, sync_providers
from app.providers.webfetch import website_reference
from app.services import discovery as disc
from app.services import entities as ent_svc
from app.services import ingest as ingest_svc
from app.services import relationships as rel_svc
from app.services import runner

MIN_RATIONALE = 10
MAX_EXPAND = 20
LEAD_PROVIDERS = ("web_search", "web_fetch", "evm_rpc", "solana_rpc", "bitcoin_esplora")


class LeadError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def make_ctx() -> ProviderContext:
    """Provider context for lead runs. Tests monkeypatch this to inject a mocked HTTP client."""
    return ProviderContext(settings=get_settings())


# ----------------------------------------------------------------------------------------------- queries


def _clean_subject(subject: str) -> str:
    s = re.sub(r'["\u201c\u201d\u201e]', "", subject)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        raise LeadError("subject is empty")
    if len(s) > 120:
        raise LeadError("subject is longer than 120 characters")
    return s


def propose_queries(subject: str, extra_terms: list[str] | None = None) -> list[dict]:
    """Deterministic query suggestions. The analyst edits/approves; nothing is executed here."""
    s = _clean_subject(subject)
    q = f'"{s}"'
    out = [
        (q, "exact phrase: baseline mentions of the name"),
        (f'{q} wallet', "mentions of a wallet next to the name"),
        (f'{q} solana', "Solana-specific mentions"),
        (f'{q} ethereum', "Ethereum/EVM-specific mentions"),
        (f'{q} bitcoin', "Bitcoin-specific mentions"),
        (f'{q} ("0x" OR "bc1" OR "wallet address" OR "adres portfela")', "address-like tokens or wallet-address phrases near the name"),
        (f'{q} (donate OR donation OR "send to" OR "wpłata" OR "wplata")', "payment/donation calls that usually quote an address"),
        (f'{q} site:olx.pl', "classified ads (the page text may contain a payment address)"),
        (f'{q} site:github.com', "code/readme pages that often list donation addresses"),
        (f'{q} site:medium.com', "articles/posts that may quote an address"),
    ]
    for term in extra_terms or []:
        t = re.sub(r"\s+", " ", term).strip()[:80]
        if t:
            out.append((f"{q} {t}", f"analyst-supplied term: {t}"))
    seen, res = set(), []
    for query, why in out:
        if query not in seen:
            seen.add(query)
            res.append({"query": query, "purpose": why})
    return res


# ----------------------------------------------------------------------------------------------- helpers


def start_session(session: Session, *, subject: str, actor: str, notes: str = "") -> Investigation:
    s = _clean_subject(subject)
    inv = Investigation(
        name=f"Lead search: {s}"[:200], description=notes[:2000],
        scope={"max_depth": 0, "allowed_providers": None, "allowed_domains": None, "workflow": "lead_search", "subject": s},
        created_by=actor,
    )
    session.add(inv)
    session.flush()
    audit(session, actor, "lead.start", "investigation", inv.id, subject=s)
    return inv


def _inv(session: Session, inv_id: str) -> Investigation:
    inv = session.get(Investigation, inv_id)
    if inv is None:
        raise LeadError("investigation not found", 404)
    return inv


def _row(session: Session, name: str) -> Provider:
    row = next((p for p in sync_providers(session) if p.name == name), None)
    if row is None:
        raise LeadError(f"provider {name} not found", 404)
    return row


def _run(session: Session, run_id: str, provider: str | tuple[str, ...]) -> ToolRun:
    run = session.get(ToolRun, run_id)
    names = (provider,) if isinstance(provider, str) else provider
    if run is None or run.provider_name not in names:
        raise LeadError(f"tool run {run_id} not found for {'/'.join(names)}", 404)
    return run


def run_doc(session: Session, run: ToolRun) -> dict | None:
    """The preserved raw artifact of a run, parsed (None if purged/absent)."""
    if not run.raw_artifact_id:
        return None
    art = session.get(Artifact, run.raw_artifact_id)
    if art is None or art.purged_at is not None:
        return None
    try:
        return json.loads(get_store().get(art.storage_location))
    except (ValueError, OSError):
        return None


def _outcome_dict(run: ToolRun, outcome: runner.RunOutcome | None = None) -> dict:
    return {
        "tool_run_id": run.id, "provider": run.provider_name, "status": run.status, "error": run.error,
        "unavailable": run.status == ToolRunStatus.UNAVAILABLE.value, "retryable": bool(outcome and outcome.retryable),
    }


def _execute(session: Session, name: str, data: ProviderInput, *, inv: Investigation | None, actor: str) -> runner.RunOutcome:
    row = _row(session, name)
    try:
        outcome = runner.run_provider(session, row, data, ctx=make_ctx(), investigation_id=inv.id if inv else None, actor=actor)
    except ProviderInputError as exc:
        raise LeadError(str(exc), 409) from exc
    except runner.RateLimited as exc:
        raise LeadError(str(exc), 429) from exc
    ingest_svc._add_item(session, inv.id if inv else None, "tool_run", outcome.tool_run.id, "manual")
    return outcome


# ----------------------------------------------------------------------------------------------- search / fetch


def search(session: Session, *, inv_id: str, query: str, backend: str | None, max_results: int | None, actor: str) -> dict:
    inv = _inv(session, inv_id)
    q = query.strip()
    if not q:
        raise LeadError("query is empty")
    payload: dict = {"query": q, "backend": backend or "auto"}
    if max_results:
        payload["max_results"] = max_results
    audit(session, actor, "lead.search.approve", "investigation", inv.id, query=q, backend=payload["backend"])
    outcome = _execute(session, "web_search", ProviderInput(reference=None, payload={**payload, "target": f"search:{q}"[:580]}), inv=inv, actor=actor)
    run = outcome.tool_run
    doc = run_doc(session, run) if run.status == ToolRunStatus.SUCCEEDED.value else None
    return {
        **_outcome_dict(run, outcome), "query": q,
        "backend_used": (doc or {}).get("backend_used"), "fallback": (doc or {}).get("fallback"),
        "unofficial_backend": (doc or {}).get("unofficial_backend"), "results": (doc or {}).get("results", []),
        "note": (doc or {}).get("note"),
    }


def fetch(session: Session, *, inv_id: str, url: str, actor: str) -> dict:
    inv = _inv(session, inv_id)
    try:
        ref = website_reference(url)
    except Exception as exc:  # InvalidReference
        raise LeadError(f"url rejected: {exc}") from exc
    audit(session, actor, "lead.fetch.approve", "investigation", inv.id, url=ref.url)
    outcome = _execute(session, "web_fetch", ProviderInput(reference=ref, payload={"url": url}), inv=inv, actor=actor)
    run = outcome.tool_run
    doc = run_doc(session, run) if run.status == ToolRunStatus.SUCCEEDED.value else None
    page = _page_view(doc)
    if page:
        table = mention_repeatability(session, inv)
        for c in page.get("address_candidates") or []:
            c["repeatability"] = _rep_for(table, c["network"], c["address"])
    return {**_outcome_dict(run, outcome), "url": ref.url, "page": page}


def _page_view(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return {k: doc.get(k) for k in (
        "url", "final_url", "redirects", "title", "content_type", "bytes_read", "truncated", "text_chars", "text_sha256", "address_candidates",
    )}


# ----------------------------------------------------------------------------------------------- repeatability


def _terms(inv: Investigation) -> tuple[str | None, str | None]:
    sc = inv.scope or {}
    return sc.get("subject") or None, sc.get("login") or None


def mention_repeatability(session: Session, inv: Investigation) -> dict[tuple[str, str], dict]:
    """(network, address) -> repeatability on the pages fetched in THIS session. Derived on read, never stored, and it
    never touches a relationship status (status is a function of evidence only)."""
    name, login = _terms(inv)
    item_ids = [i.item_id for i in session.scalars(
        select(InvestigationItem).where(InvestigationItem.investigation_id == inv.id, InvestigationItem.item_type == "observation")
    )]
    if not item_ids:
        return {}
    obs = session.scalars(
        select(Observation).where(Observation.id.in_(item_ids), Observation.observation_type == ObservationType.WEB_PAGE.value)
    ).all()
    latest: dict[str, Observation] = {}  # one (the newest) version per page source
    for o in obs:
        cur = latest.get(o.source_id)
        if cur is None or o.seq > cur.seq:
            latest[o.source_id] = o
    with_term: dict[tuple[str, str], list[str]] = {}
    with_addr: dict[tuple[str, str], list[str]] = {}
    for o in latest.values():
        pl = o.normalized_payload or {}
        url = pl.get("final_url") or pl.get("url")
        for c in pl.get("address_candidates", []):
            key = (c["network"], c["address"].lower() if c["network"] in EVM_NETWORKS else c["address"])
            with_addr.setdefault(key, []).append(url)
            if any(rep.context_mentions(ctx, name=name, login=login) for ctx in c.get("contexts", [])):
                with_term.setdefault(key, []).append(url)
    return {k: rep.summarize(with_term.get(k, []), with_addr[k]) | {"scope": "pages fetched in this session"} for k in with_addr}


def _rep_for(table: dict[tuple[str, str], dict], network: str, address: str) -> dict | None:
    return table.get((network, address.lower() if network in EVM_NETWORKS else address))


# ----------------------------------------------------------------------------------------------- track


def _page_observation(session: Session, run: ToolRun) -> Observation:
    obs = session.scalar(
        select(Observation).where(
            Observation.id.in_(run.observation_ids or [""]), Observation.observation_type == ObservationType.WEB_PAGE.value
        )
    )
    if obs is None:
        raise LeadError("the fetch run produced no page observation", 409)
    return obs


def track_address(
    session: Session, *, inv_id: str, fetch_run_id: str, network: str, address: str, rationale: str, actor: str,
    automatic: bool = False,
) -> dict:
    """Analyst decision: this address mentioned on this page is worth tracking. Creates a wallet + a MENTION edge only.

    `automatic=True` is used by the automation rule (name/login in the address context): the same mention edge is created, but
    NO "analyst accepted" confirmation is added, because no analyst reviewed it; the status is computed from the page evidence."""
    inv = _inv(session, inv_id)
    if len(rationale.strip()) < MIN_RATIONALE:
        raise LeadError(f"a rationale of at least {MIN_RATIONALE} characters is required to track an address")
    run = _run(session, fetch_run_id, "web_fetch")
    if run.status != ToolRunStatus.SUCCEEDED.value:
        raise LeadError("the fetch did not succeed; there is no page to cite", 409)
    obs = _page_observation(session, run)
    cand = next(
        (c for c in obs.normalized_payload.get("address_candidates", []) if c["network"] == network and c["address"] == address), None
    )
    if cand is None:
        raise LeadError("that address was not extracted from this page", 404)
    page_ent = session.scalar(
        select(Entity).join(ObservationEntity, ObservationEntity.entity_id == Entity.id).where(
            ObservationEntity.observation_id == obs.id, ObservationEntity.role == ObservationEntityRole.SUBJECT.value
        )
    )
    if page_ent is None:
        raise LeadError("page entity missing", 500)
    ref = _address_ref(network, address, address, None)
    wallet, created = ent_svc.get_or_create_entity(session, ref, obs.observed_at)
    if session.scalar(
        select(ObservationEntity.id).where(
            ObservationEntity.observation_id == obs.id, ObservationEntity.entity_id == wallet.id,
            ObservationEntity.role == ObservationEntityRole.MENTIONED.value,
        )
    ) is None:
        session.add(ObservationEntity(observation_id=obs.id, entity_id=wallet.id, role=ObservationEntityRole.MENTIONED.value, extraction_method="deterministic_address_extractor"))
    rel, rel_created = rel_svc.get_or_create_relationship(
        session, ent_svc.resolve_id(session, page_ent.id), ent_svc.resolve_id(session, wallet.id),
        RelationshipType.PAGE_REFERENCES_ENTITY.value, "analyst-selected page + deterministic address extraction",
    )
    snippet = (cand.get("contexts") or [""])[0]
    rel_svc.add_evidence(
        session, rel, obs, evidence_type=EvidenceType.DIRECT_SOURCE_STATEMENT.value,
        explanation=f"The fetched page {obs.normalized_payload.get('final_url') or obs.normalized_payload.get('url')} contains this address. Context: {snippet}",
        basis=f"deterministic extraction from an analyst-selected page ({cand.get('occurrences', 1)} occurrence(s)); a mention is not ownership",
    )
    if automatic:
        rel_svc.recompute(session, rel)
    else:
        rel_svc.review_relationship(session, rel, ReviewStatus.ACCEPTED.value, actor, f"Track decision. Rationale: {rationale.strip()}")
    ingest_svc._add_item(session, inv.id, "entity", wallet.id, "manual")
    ingest_svc._add_item(session, inv.id, "entity", page_ent.id, "reused")
    ingest_svc._add_item(session, inv.id, "observation", obs.id, "reused")
    audit(
        session, actor, "lead.track", "entity", wallet.id, investigation=inv.id, network=network, address=address,
        page=page_ent.canonical_identifier, rationale=rationale.strip(), relationship=rel.id, automatic=automatic,
    )
    return {
        "wallet_entity_id": wallet.id, "wallet_created": created, "page_entity_id": page_ent.id, "relationship_id": rel.id,
        "relationship_type": rel.relationship_type, "relationship_status": rel.status,
        "note": "A page mention is recorded. It does not establish that anyone owns or controls this address.",
    }


# ----------------------------------------------------------------------------------------------- on-chain


def _provider_for(network: str) -> str:
    if network == "bitcoin":
        return "bitcoin_esplora"
    if network == "solana":
        return "solana_rpc"
    if network in EVM_NETWORKS:
        return "evm_rpc"
    raise LeadError(f"no on-chain provider for network {network!r}")


def onchain_view(payload: dict, network: str) -> dict:
    """Uniform per-network view of an address observation (facts only)."""
    nets: list[dict] = []
    if network == "bitcoin":
        bal = payload.get("balance") or {}
        nets.append({
            "network": "bitcoin", "found": payload.get("found", False), "balance": f"{bal.get('btc')} BTC" if bal else None,
            "tx_count": payload.get("tx_count"), "recent_transactions": payload.get("recent_transactions", []),
            "counterparties": payload.get("counterparties", []), "history_truncated": payload.get("history_truncated"), "history_error": None,
            "has_more": bool(payload.get("next_cursor")), "is_page": bool(payload.get("page")),
        })
    elif network == "solana":
        lamports = payload.get("lamports")
        nets.append({
            "network": "solana", "found": payload.get("found", False),
            "balance": f"{lamports // 10**9}.{lamports % 10**9:09d} SOL".rstrip("0").rstrip(".") if isinstance(lamports, int) else None,
            "tx_count": None, "recent_transactions": payload.get("recent_transactions", []), "counterparties": payload.get("counterparties", []),
            "history_truncated": payload.get("history_truncated"), "history_error": payload.get("history_error"),
            "has_more": bool(payload.get("next_cursor")), "is_page": bool(payload.get("page")),
        })
    else:
        for net, r in sorted((payload.get("per_network") or {}).items()):
            hist = r.get("history") or {}
            native = hist.get("balance_native") or (_wei_to_native(r["balance_wei"]) if r.get("balance_wei") else None)
            nets.append({
                "network": net, "found": True, "balance": f"{native} (native)" if native is not None else None, "tx_count": r.get("nonce"),
                "has_code": r.get("has_code"), "recent_transactions": hist.get("recent_transactions", []),
                "counterparties": hist.get("counterparties", []), "history_truncated": hist.get("history_truncated"),
                "has_more": bool(hist.get("next_cursor")), "is_page": bool(payload.get("page")),
                "history_error": r.get("history_error") or (None if hist else "no transaction-history explorer configured for this network"),
                "explorer_flags": hist.get("explorer_flags"),
            })
        for net, err in (payload.get("errors") or {}).items():
            nets.append({"network": net, "found": None, "error": err, "recent_transactions": [], "counterparties": []})
    return {"networks": nets, "note": payload.get("note")}


def _address_observation(session: Session, run: ToolRun) -> Observation | None:
    return session.scalar(
        select(Observation).where(
            Observation.id.in_(run.observation_ids or [""]), Observation.observation_type == ObservationType.BLOCKCHAIN_ADDRESS.value
        )
    )


def _wallet(session: Session, entity_id: str) -> tuple[Entity, str, str]:
    ent = session.get(Entity, ent_svc.resolve_id(session, entity_id))
    if ent is None or ent.entity_type != EntityType.WALLET.value:
        raise LeadError("wallet entity not found", 404)
    net, addr = ent.meta.get("network"), ent.meta.get("address")
    if not net or not addr:
        raise LeadError("entity has no network/address metadata", 409)
    return ent, net, addr


def lookup_onchain(session: Session, *, inv_id: str, entity_id: str, refresh: bool, actor: str) -> dict:
    inv = _inv(session, inv_id)
    ent, net, addr = _wallet(session, entity_id)
    return _lookup(session, inv, ent, net, addr, refresh=refresh, actor=actor, max_age=True)


def _latest_run(session: Session, provider: str, target: str) -> ToolRun | None:
    """Most recent successful stored run for this exact target, however old (graph expansion reuses what is stored)."""
    return session.scalar(
        select(ToolRun)
        .where(ToolRun.provider_name == provider, ToolRun.input_key == target, ToolRun.status == ToolRunStatus.SUCCEEDED.value)
        .order_by(ToolRun.finished_at.desc())
        .limit(1)
    )


def _lookup(session: Session, inv: Investigation | None, ent: Entity, net: str, addr: str, *, refresh: bool, actor: str, max_age: bool) -> dict:
    """Run (or reuse) the on-chain lookup. `max_age=True` applies the reuse window; False reuses any stored run."""
    name = _provider_for(net)
    ref = _address_ref(net, addr, addr, None)
    impl = get_provider(name)
    if not impl.supports(ref):
        raise LeadError(f"{name} does not support this address", 409)
    audit(session, actor, "lead.onchain.approve", "entity", ent.id, investigation=inv.id if inv else None, provider=name, network=net, refresh=refresh)
    if refresh:
        run = None
    elif max_age:
        run = disc._find_reusable_run(session, name, ref.canonical_id)
    else:
        run = _latest_run(session, name, ref.canonical_id)
    reused = run is not None
    outcome = None
    if run is None:
        outcome = _execute(session, name, ProviderInput(reference=ref, payload={}), inv=inv, actor=actor)
        run = outcome.tool_run
    elif inv is not None:
        ingest_svc._add_item(session, inv.id, "tool_run", run.id, "reused")
    result = {**_outcome_dict(run, outcome), "provider": name, "reused_prior_run": reused, "entity_id": ent.id, "network": net, "address": addr}
    obs = _address_observation(session, run) if run.status == ToolRunStatus.SUCCEEDED.value else None
    if obs is not None:
        result.update({"observation_id": obs.id, "lookup_run_id": run.id, "view": onchain_view(obs.normalized_payload, net), "retrieved_at": obs.retrieved_at.isoformat()})
    return result


# ----------------------------------------------------------------------------------------------- load more


def _next_cursor(payload: dict, net: str) -> dict | None:
    """Provider-issued cursor of the NEXT history page, read from the stored observation (never taken from the client)."""
    if net in ("bitcoin", "solana"):
        return payload.get("next_cursor") or None
    for n, r in sorted((payload.get("per_network") or {}).items()):
        cur = (r.get("history") or {}).get("next_cursor")
        if cur:
            return {"network": n, "params": cur}
    return None


def load_more(session: Session, *, inv_id: str, entity_id: str, lookup_run_id: str, actor: str) -> dict:
    """Fetch the next page of a wallet's transaction history as its own, separately stored and audited provider run.

    * The cursor comes from the stored observation of `lookup_run_id` (the latest page the analyst has seen).
    * Each page is its own tool run + source, so earlier pages are never superseded. A page that was already
      loaded is reused (no second node query), so the action is idempotent.
    """
    inv = _inv(session, inv_id)
    ent, net, addr = _wallet(session, entity_id)
    prev = _run(session, lookup_run_id, _provider_for(net))
    _check_run_belongs(prev, net, addr)
    prev_obs = _address_observation(session, prev)
    if prev_obs is None:
        raise LeadError("that lookup produced no address observation", 409)
    cursor = _next_cursor(prev_obs.normalized_payload, net)
    if cursor is None:
        raise LeadError("there is no further history page for this lookup", 409)
    name = _provider_for(net)
    ref = _address_ref(net, addr, addr, None)
    data = ProviderInput(reference=ref, payload={"cursor": cursor, "run_meta": {"history_page_after": lookup_run_id, "page_cursor": cursor}})
    audit(session, actor, "lead.onchain.load_more", "entity", ent.id, investigation=inv.id, provider=name, network=net, after_run=lookup_run_id)
    run = _latest_run(session, name, data.run_key)
    reused = run is not None
    outcome = None
    if run is None:
        outcome = _execute(session, name, data, inv=inv, actor=actor)
        run = outcome.tool_run
    else:
        ingest_svc._add_item(session, inv.id, "tool_run", run.id, "reused")
    result = {**_outcome_dict(run, outcome), "provider": name, "reused_prior_run": reused, "entity_id": ent.id, "network": net, "address": addr}
    obs = _address_observation(session, run) if run.status == ToolRunStatus.SUCCEEDED.value else None
    if obs is not None:
        result.update({"observation_id": obs.id, "lookup_run_id": run.id, "view": onchain_view(obs.normalized_payload, net), "retrieved_at": obs.retrieved_at.isoformat()})
    return result


# ----------------------------------------------------------------------------------------------- expand


@dataclass
class _Cp:
    network: str
    address: str
    direction: str
    tx_count: int
    sample_txs: list[str]


def _valid_cp(network: str, address: str) -> bool:
    if network == "bitcoin":
        return is_bitcoin_address(address)
    if network == "solana":
        return is_solana_address(address)
    return is_evm_address(address)


def _link_counterparty(
    session: Session, *, inv: Investigation | None, ent: Entity, obs: Observation, cp: _Cp, actor: str, audit_action: str
) -> dict:
    """Counterparty wallet + WALLET_TRANSACTED_WITH backed by the cited on-chain observation. Idempotent."""
    cref = _address_ref(cp.network, cp.address, cp.address, None)
    cent, c_created = ent_svc.get_or_create_entity(session, cref, obs.observed_at)
    rel, rel_created = rel_svc.get_or_create_relationship(
        session, ent_svc.resolve_id(session, ent.id), ent_svc.resolve_id(session, cent.id),
        RelationshipType.WALLET_TRANSACTED_WITH.value, "analyst-selected counterparty from on-chain history",
    )
    _ev, ev_created = rel_svc.add_evidence(
        session, rel, obs, evidence_type=EvidenceType.DETERMINISTIC_BLOCKCHAIN.value,
        explanation=(
            f"{cp.address} appears as '{cp.direction}' in {cp.tx_count} of the retrieved transaction(s) "
            f"(e.g. {', '.join(cp.sample_txs[:2]) or 'n/a'}). This is a transaction-level fact only."
        ),
        basis="on-chain history from the configured node/explorer; a shared transaction does not imply shared ownership",
    )
    rel_svc.recompute(session, rel)
    if inv is not None:
        ingest_svc._add_item(session, inv.id, "entity", cent.id, "manual")
    if c_created or rel_created or ev_created:
        audit(
            session, actor, audit_action, "entity", cent.id, investigation=inv.id if inv else None, from_wallet=ent.id,
            network=cp.network, direction=cp.direction, relationship=rel.id,
        )
    return {
        "entity_id": cent.id, "address": cp.address, "network": cp.network, "created": c_created, "relationship_id": rel.id,
        "relationship_status": rel.status, "already_present": not (c_created or rel_created or ev_created),
    }


def _check_run_belongs(run: ToolRun, net: str, addr: str) -> None:
    """A cited lookup/page run must be about THIS wallet (input_key is `<canonical id>` or `<canonical id>#page:<digest>`)."""
    if (run.input_key or "").split("#")[0] != _address_ref(net, addr, addr, None).canonical_id:
        raise LeadError("that lookup run belongs to a different address", 409)


def _available(session: Session, net: str, addr: str, run_id: str) -> tuple[Observation, dict[tuple[str, str, str], _Cp]]:
    run = _run(session, run_id, _provider_for(net))
    _check_run_belongs(run, net, addr)
    obs = _address_observation(session, run)
    if obs is None:
        raise LeadError("that lookup produced no address observation", 409)
    available: dict[tuple[str, str, str], _Cp] = {}
    for v in onchain_view(obs.normalized_payload, net)["networks"]:
        for c in v.get("counterparties", []):
            a = c["address"].lower() if v["network"] in EVM_NETWORKS else c["address"]
            available[(v["network"], a, c["direction"])] = _Cp(v["network"], c["address"], c["direction"], c["tx_count"], c.get("sample_txs", []))
    return obs, available


def expand(session: Session, *, inv_id: str, entity_id: str, lookup_run_id: str, counterparties: list[dict], actor: str) -> dict:
    """Analyst picks counterparties; each becomes a wallet + WALLET_TRANSACTED_WITH (an on-chain fact, never ownership).

    A counterparty may cite its own history-page run (`lookup_run_id`, set by "load more"); otherwise the request's run is cited."""
    inv = _inv(session, inv_id)
    if not counterparties:
        raise LeadError("select at least one counterparty")
    if len(counterparties) > MAX_EXPAND:
        raise LeadError(f"at most {MAX_EXPAND} counterparties per expansion")
    ent, net, addr = _wallet(session, entity_id)
    cache: dict[str, tuple[Observation, dict[tuple[str, str, str], _Cp]]] = {}
    created, skipped = [], []
    for want in counterparties:
        run_id = str(want.get("lookup_run_id") or lookup_run_id)
        if run_id not in cache:
            cache[run_id] = _available(session, net, addr, run_id)
        obs, available = cache[run_id]
        cnet, caddr, cdir = str(want.get("network")), str(want.get("address")), str(want.get("direction"))
        key = (cnet, caddr.lower() if cnet in EVM_NETWORKS else caddr, cdir)
        cp = available.get(key)
        if cp is None:
            skipped.append({"address": caddr, "reason": "not a counterparty in the cited lookup"})
            continue
        if not _valid_cp(cnet, cp.address):
            skipped.append({"address": caddr, "reason": "not a valid address for the network"})
            continue
        created.append(_link_counterparty(session, inv=inv, ent=ent, obs=obs, cp=cp, actor=actor, audit_action="lead.expand"))
    return {"added": created, "skipped": skipped, "note": "Counterparties are transaction facts, not identity or ownership."}


def expand_wallet(session: Session, *, entity_id: str, actor: str) -> dict:
    """Graph "expand neighbours" for a wallet: ensure its on-chain history is stored, then link its top counterparties.

    * A stored lookup is reused (no node query); only a wallet with no stored lookup is queried.
    * One step only: counterparties are linked to THIS wallet and are not expanded further.
    * At most MAX_EXPAND distinct counterparty addresses (most frequent first); the rest are reported, not linked.
    * Idempotent: repeating it adds no entities, relationships or evidence rows.
    * Relationships are WALLET_TRANSACTED_WITH (a transaction fact), never ownership.
    """
    ent, net, addr = _wallet(session, entity_id)
    looked = _lookup(session, None, ent, net, addr, refresh=False, actor=actor, max_age=False)
    summary = {k: looked.get(k) for k in ("tool_run_id", "status", "error", "unavailable", "reused_prior_run", "retrieved_at", "lookup_run_id")}
    if "view" not in looked:
        return {"entity_id": ent.id, "lookup": summary, "added": [], "skipped_over_limit": 0, "linked_new": 0,
                "note": "No on-chain data is available for this wallet, so nothing was added to the graph."}
    obs = _address_observation(session, _run(session, looked["lookup_run_id"], _provider_for(net)))
    assert obs is not None
    best: dict[tuple[str, str], _Cp] = {}
    for v in looked["view"]["networks"]:
        for c in v.get("counterparties", []):
            cnet = v["network"]
            if not _valid_cp(cnet, c["address"]):
                continue
            key = (cnet, c["address"].lower() if cnet in EVM_NETWORKS else c["address"])
            cp = _Cp(cnet, c["address"], c["direction"], c["tx_count"], c.get("sample_txs", []))
            if key not in best or cp.tx_count > best[key].tx_count:
                best[key] = cp
    ranked = sorted(best.values(), key=lambda c: (-c.tx_count, c.address))
    chosen, rest = ranked[:MAX_EXPAND], ranked[MAX_EXPAND:]
    added = [_link_counterparty(session, inv=None, ent=ent, obs=obs, cp=cp, actor=actor, audit_action="graph.expand_wallet") for cp in chosen]
    audit(session, actor, "graph.expand_wallet.summary", "entity", ent.id, linked=len(added), over_limit=len(rest), reused=looked["reused_prior_run"])
    return {
        "entity_id": ent.id, "lookup": summary, "added": added, "skipped_over_limit": len(rest),
        "linked_new": sum(1 for a in added if not a["already_present"]),
        "note": "Counterparties are transaction facts (addresses seen in the same transactions), not identity or ownership.",
    }


# ----------------------------------------------------------------------------------------------- state


def state(session: Session, inv_id: str) -> dict:
    inv = _inv(session, inv_id)
    runs = session.scalars(
        select(ToolRun).where(ToolRun.provider_name.in_(LEAD_PROVIDERS)).order_by(ToolRun.started_at.desc()).limit(500)
    ).all()
    mine = [r for r in runs if (r.parameters or {}).get("investigation_id") == inv.id]
    summary = []
    for r in mine:
        p = r.parameters or {}
        summary.append({
            "tool_run_id": r.id, "provider": r.provider_name, "status": r.status, "error": r.error, "started_at": r.started_at.isoformat(),
            "query": p.get("query"), "url": p.get("url"), "backend": p.get("backend"), "unofficial_backend": p.get("unofficial_backend"),
            "target": r.input_key,
        })
    ids = [i.item_id for i in session.scalars(select(InvestigationItem).where(InvestigationItem.investigation_id == inv.id, InvestigationItem.item_type == "entity"))]
    wallets = session.scalars(select(Entity).where(Entity.id.in_(ids or [""]), Entity.entity_type == EntityType.WALLET.value)).all()
    rels = session.scalars(
        select(Relationship).where(
            Relationship.relationship_type.in_([RelationshipType.PAGE_REFERENCES_ENTITY.value, RelationshipType.WALLET_TRANSACTED_WITH.value]),
            Relationship.target_entity_id.in_([w.id for w in wallets] or [""]),
        )
    ).all()
    table = mention_repeatability(session, inv)
    return {
        "investigation": {"id": inv.id, "name": inv.name, "subject": (inv.scope or {}).get("subject")},
        "runs": summary,
        "wallets": [
            {"entity_id": w.id, "network": w.meta.get("network"), "address": w.meta.get("address"), "display_name": w.display_name,
             "repeatability": _rep_for(table, str(w.meta.get("network")), str(w.meta.get("address")))}
            for w in wallets
        ],
        "relationships": [
            {"id": r.id, "type": r.relationship_type, "status": r.status, "source": r.source_entity_id, "target": r.target_entity_id} for r in rels
        ],
        "generated_at": utcnow().isoformat(),
    }


def run_detail(session: Session, run_id: str) -> dict:
    run = _run(session, run_id, LEAD_PROVIDERS)
    doc = run_doc(session, run)
    out = {**_outcome_dict(run), "started_at": run.started_at.isoformat()}
    if run.provider_name == "web_search":
        out.update({k: (doc or {}).get(k) for k in ("query", "backend_used", "fallback", "unofficial_backend", "results", "note")})
    elif run.provider_name == "web_fetch":
        out["page"] = _page_view(doc)
    else:
        net = (run.input_refs or {}).get("target", "").split(":")[1] if (run.input_refs or {}).get("target", "").startswith("addr:") else None
        obs = _address_observation(session, run)
        if obs is not None and net:
            out["view"] = onchain_view(obs.normalized_payload, net)
    return out
