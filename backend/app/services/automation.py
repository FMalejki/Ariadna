"""Lead automation: stages A-E run as a persistent, abortable, fully logged job (executed in chunks by the worker).

STATUS: IMPLEMENTED (unit/API-tested with mocked HTTP; real Sherlock and live nodes are not exercised by the tests).

  A  Sherlock: login -> profile URLs (username-string matches only; may feed stage B's fetch queue). Standalone.
  B  web search (approved query plan) -> fetch pages -> address candidates (no graph writes except the page observation)
  C  track ONLY addresses whose context (+-120 chars, same page) contains the full name or the login (repeatability.context_mentions)
  D  on-chain lookup of the wallets tracked in THIS run
  E  expand ONE step from those wallets (WALLET_TRANSACTED_WITH, <= MAX_EXPAND per wallet, idempotent)

Dependencies: C needs B, D needs C, E needs D. Hard limits (queries, pages, addresses, seconds) always apply; hitting one ends the
run as `stopped_by_limit` with a stored reason. Every step is logged (what ran, outcome, error, policy skip).

What this module never does: create an ownership relationship, treat a mention as ownership, follow counterparties beyond one
step, fetch no-scrape/robots-disallowed pages, or silently replace an unavailable source with another.
The analyst starting a run with stage A ticked is the approval for Sherlock (audited as `automation.start`); it is not a DiscoveryTask.
"""

from __future__ import annotations

import copy
import time
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repeatability as rep
from app.audit import audit
from app.config import get_settings
from app.db import utcnow
from app.domain import ObservationType, ToolRunStatus
from app.models import AutomationRun, Investigation, Observation, ToolRun
from app.normalize import EVM_NETWORKS, InvalidReference, classify_username
from app.providers.base import ProviderInput
from app.providers.webfetch import is_blocked_domain
from app.services import leads
from app.services.leads import LeadError, MAX_EXPAND

STAGES = ("A", "B", "C", "D", "E")
STAGE_INFO = {
    "A": "Sherlock: login -> profile URLs (username-string matches, not identity)",
    "B": "Web search -> fetch pages -> address candidates",
    "C": "Track addresses with the full name or login in their context (+-120 chars, same page)",
    "D": "On-chain lookup of the wallets tracked in this run",
    "E": "Expand one step from those wallets (<= 20 counterparties each)",
}
DEPENDS = {"C": "B", "D": "C", "E": "D"}
DEFAULT_LIMITS = {"max_queries": 6, "max_pages": 15, "max_addresses": 30, "max_seconds": 600}
HARD_CAPS = {"max_queries": 20, "max_pages": 50, "max_addresses": 100, "max_seconds": 1800}
CHUNK_SECONDS = 20  # time budget of one worker advance; the run continues on the next tick
FINAL = ("completed", "stopped_by_limit", "aborted", "failed")
LOG_CAP = 600
POLICY_MARKERS = ("no-scrape list", "robots.txt", "SSRF", "redirect to a disallowed target", "access refused by the site")
CONTEXT_NOTE = "A mention on a page is not ownership; the rule only decides which candidates deserve a closer look."


class _Limit(Exception):
    """A hard limit was reached; the run ends as stopped_by_limit with this reason."""


# ----------------------------------------------------------------------------------------------- validation


def validate_stages(stages: list[str]) -> list[str]:
    chosen = {str(s).upper() for s in stages or []}
    unknown = chosen - set(STAGES)
    if unknown:
        raise LeadError(f"unknown stage(s): {', '.join(sorted(unknown))}")
    if not chosen:
        raise LeadError("select at least one stage")
    for stage, needs in DEPENDS.items():
        if stage in chosen and needs not in chosen:
            raise LeadError(f"stage {stage} requires stage {needs}")
    return [s for s in STAGES if s in chosen]


def resolve_limits(limits: dict | None) -> dict:
    out = dict(DEFAULT_LIMITS)
    for key, val in (limits or {}).items():
        if val is None:
            continue
        if key not in DEFAULT_LIMITS:
            raise LeadError(f"unknown limit: {key}")
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise LeadError(f"{key} must be a positive integer")
        if val > HARD_CAPS[key]:
            raise LeadError(f"{key} may not exceed {HARD_CAPS[key]}")
        out[key] = val
    return out


def match_term(context: str, *, name: str | None, login: str | None) -> str | None:
    """Stage-C rule: which term (full name / login) appears in this address context, or None. A bare first name or surname,
    or a login shorter than 3 characters, never matches (see repeatability.context_mentions)."""
    if name and rep.context_mentions(context, name=name):
        return "full name"
    if login and rep.context_mentions(context, login=login):
        return "login"
    return None


# ----------------------------------------------------------------------------------------------- start / abort


def _actor(run: AutomationRun) -> str:
    return f"automation/{run.created_by}"[:100]


def _log(run: AutomationRun, stage: str, action: str, outcome: str, target: str = "", detail: str = "") -> None:
    entry = {"at": utcnow().isoformat(), "stage": stage, "action": action, "outcome": outcome, "target": target[:300], "detail": detail[:600]}
    log = [*run.log, entry]
    if len(log) > LOG_CAP:
        log = [*log[:50], {**entry, "stage": "-", "action": "log_truncated", "outcome": "info", "target": "", "detail": "older entries dropped"}, *log[-(LOG_CAP - 51):]]
    run.log = log


def start_run(
    session: Session, *, actor: str, stages: list[str], limits: dict | None = None, full_name: str | None = None,
    login: str | None = None, queries: list[str] | None = None, investigation_id: str | None = None,
) -> AutomationRun:
    chosen = validate_stages(stages)
    lim = resolve_limits(limits)
    if investigation_id:
        inv = session.get(Investigation, investigation_id)
        if inv is None:
            raise LeadError("investigation not found", 404)
        name, lg = leads._terms(inv)
    else:
        name = leads._clean_subject(full_name) if (full_name or "").strip() else None
        lg = None
        if (login or "").strip():
            try:
                lg = classify_username(login).attrs["handle"]  # type: ignore[union-attr]
            except InvalidReference as exc:
                raise LeadError(str(exc)) from exc
        inv = None
    if not (name or lg):
        raise LeadError("provide a full name or a login")
    if "A" in chosen and not lg:
        raise LeadError("stage A (Sherlock) needs a login")
    plan: list[str] = []
    if "B" in chosen:
        plan = [q.strip() for q in (queries or []) if q and q.strip()][: HARD_CAPS["max_queries"]]
        if not plan:  # generated plan: the first `max_queries` proposals (an analyst-supplied plan longer than the limit stops by limit)
            plan = [q["query"] for q in leads.propose_queries(name or lg or "")][: lim["max_queries"]]
    if inv is None:
        scope = {"max_depth": 0, "allowed_providers": None, "allowed_domains": None, "workflow": "lead_automation"}
        if name:
            scope["subject"] = name
        if lg:
            scope["login"] = lg
        inv = Investigation(name=f"Automation: {name or lg}"[:200], description="", scope=scope, created_by=actor)
        session.add(inv)
        session.flush()
        audit(session, actor, "lead.start", "investigation", inv.id, subject=name, login=lg, workflow="lead_automation")
    order = [p for s in chosen for p in {"A": ["A"], "B": ["B_search", "B_fetch"], "C": ["C"], "D": ["D"], "E": ["E"]}[s]]
    run = AutomationRun(
        investigation_id=inv.id, status="queued", created_by=actor,
        config={"stages": chosen, "limits": lim, "subject": name, "login": lg, "queries": plan, "phases": order},
        progress={
            "phase": order[0], "qi": 0, "fi": 0, "ti": 0, "di": 0, "ei": 0, "search_urls": [], "profile_urls": [], "seen_urls": [],
            "candidates": {}, "tracked": [], "looked": {}, "c_summary_logged": False,
        },
        counters={"queries": 0, "pages": 0, "addresses": 0, "lookups": 0, "expanded": 0, "skipped": 0, "failed": 0},
        log=[],
    )
    session.add(run)
    session.flush()
    audit(
        session, actor, "automation.start", "automation_run", run.id, investigation=inv.id, stages=chosen, limits=lim, subject=name, login=lg,
        sherlock_approved_by_start="A" in chosen,
    )
    _log(run, "-", "start", "info", detail=f"stages {''.join(chosen)}; limits {lim}")
    if name and len(name.split()) < 2:
        _log(run, "C", "rule", "info", detail="the full name is a single word, so only the login can trigger tracking")
    return run


def abort_run(session: Session, run_id: str, actor: str) -> AutomationRun:
    run = session.get(AutomationRun, run_id)
    if run is None:
        raise LeadError("automation run not found", 404)
    if run.status in FINAL:
        raise LeadError(f"run is already {run.status}", 409)
    audit(session, actor, "automation.abort", "automation_run", run.id, investigation=run.investigation_id, status=run.status)
    run.abort_requested = True
    _log(run, "-", "abort", "info", detail=f"abort requested by {actor}")
    if run.status == "queued":
        _finish(session, run, "aborted", f"aborted by {actor} before it started")
    return run


def _finish(session: Session, run: AutomationRun, status: str, reason: str | None) -> None:
    run.status, run.stop_reason, run.finished_at = status, reason, utcnow()
    _log(run, "-", "finish", status, detail=reason or "")
    audit(session, _actor(run), "automation.finish", "automation_run", run.id, investigation=run.investigation_id, status=status, reason=reason, counters=run.counters)


# ----------------------------------------------------------------------------------------------- executor


def _next_phase(run: AutomationRun, p: dict) -> None:
    order = run.config["phases"]
    i = order.index(p["phase"])
    p["phase"] = order[i + 1] if i + 1 < len(order) else "done"


def _cand_key(network: str, address: str) -> str:
    return f"{network}|{address.lower() if network == 'evm' else address}"


def _phase_a(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    login = run.config["login"]
    try:
        out = leads._execute(session, "sherlock", ProviderInput(reference=classify_username(login), payload={}), inv=inv, actor=_actor(run))
    except (LeadError, InvalidReference) as exc:
        _log(run, "A", "sherlock", "failed", f"username:{login}", str(exc))
        c["failed"] += 1
        return _next_phase(run, p)
    tr = out.tool_run
    if tr.status == ToolRunStatus.SUCCEEDED.value:
        obs = session.scalar(
            select(Observation).where(Observation.id.in_(tr.observation_ids or [""]), Observation.observation_type == ObservationType.USERNAME_HIT.value)
        )
        claimed = (obs.normalized_payload.get("claimed") if obs else None) or []
        urls, seen_keys = [], set()
        for x in claimed:
            key = rep.page_key(x.get("url"))
            if key and key not in seen_keys:
                seen_keys.add(key)
                urls.append(x["url"])
        if "B" in run.config["stages"]:
            p["profile_urls"] = urls[:100]
        _log(run, "A", "sherlock", "ok", f"username:{login}", f"{len(urls)} page(s) exist for this username string (not identity evidence)")
    elif tr.status == ToolRunStatus.UNAVAILABLE.value:
        _log(run, "A", "sherlock", "unavailable", f"username:{login}", tr.error or "sherlock is unavailable")
        c["skipped"] += 1
    else:
        _log(run, "A", "sherlock", "failed", f"username:{login}", tr.error or tr.status)
        c["failed"] += 1
    _next_phase(run, p)


def _phase_b_search(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    if p["qi"] >= len(run.config["queries"]):
        return _next_phase(run, p)
    if c["queries"] >= run.config["limits"]["max_queries"]:
        raise _Limit(f"query limit reached ({run.config['limits']['max_queries']}); {len(run.config['queries']) - p['qi']} planned queries were not run")
    q = run.config["queries"][p["qi"]]
    p["qi"] += 1
    c["queries"] += 1
    try:
        r = leads.search(session, inv_id=inv.id, query=q, backend=None, max_results=None, actor=_actor(run))
    except LeadError as exc:
        _log(run, "B", "search", "failed", q, str(exc))
        c["failed"] += 1
        return
    if r["status"] != ToolRunStatus.SUCCEEDED.value:
        _log(run, "B", "search", "unavailable" if r["unavailable"] else "failed", q, r.get("error") or r["status"])
        c["skipped" if r["unavailable"] else "failed"] += 1
        return
    seen = set(p["seen_urls"])
    added = 0
    for res in r["results"]:
        url, key = res.get("url"), rep.page_key(res.get("url"))
        if url and key and key not in seen and url.lower().startswith(("http://", "https://")):
            seen.add(key)
            p["search_urls"].append(url)
            added += 1
    p["seen_urls"] = sorted(seen)
    backend = r.get("backend_used") or "?"
    _log(run, "B", "search", "ok", q, f"{len(r['results'])} result(s), {added} new URL(s); backend {backend}" + (" (UNOFFICIAL ddgs)" if r.get("unofficial_backend") else ""))


def _phase_b_fetch(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    queue = [*p["search_urls"], *[u for u in p["profile_urls"] if rep.page_key(u) not in set(p["seen_urls"])]]
    if p["fi"] >= len(queue):
        return _next_phase(run, p)
    url = queue[p["fi"]]
    p["fi"] += 1
    host = urlsplit(url).hostname or ""
    blocked = is_blocked_domain(host, get_settings().fetch_blocked_domains)
    if blocked:
        _log(run, "B", "fetch", "skipped_policy", url, f"{blocked} is on the no-scrape list")
        c["skipped"] += 1
        return
    if c["pages"] >= run.config["limits"]["max_pages"]:
        raise _Limit(f"page limit reached ({run.config['limits']['max_pages']}); {len(queue) - p['fi'] + 1} URL(s) were not fetched")
    c["pages"] += 1
    try:
        r = leads.fetch(session, inv_id=inv.id, url=url, actor=_actor(run))
    except LeadError as exc:
        _log(run, "B", "fetch", "failed", url, str(exc))
        c["failed"] += 1
        return
    if r["status"] != ToolRunStatus.SUCCEEDED.value or not r.get("page"):
        err = r.get("error") or r["status"]
        policy = r["unavailable"] and any(m in err for m in POLICY_MARKERS)
        outcome = "skipped_policy" if policy else ("unavailable" if r["unavailable"] else "failed")
        _log(run, "B", "fetch", outcome, url, err)
        c["skipped" if r["unavailable"] else "failed"] += 1
        return
    name, login = run.config["subject"], run.config["login"]
    cands = r["page"].get("address_candidates") or []
    hits = 0
    for cand in cands:
        entry = p["candidates"].setdefault(_cand_key(cand["network"], cand["address"]), {
            "network": cand["network"], "address": cand["address"], "pages": 0, "match": None,
        })
        entry["pages"] += 1
        if entry["match"] is None:
            for ctx in cand.get("contexts") or []:
                term = match_term(ctx, name=name, login=login)
                if term:
                    entry["match"] = {"term": term, "fetch_run_id": r["tool_run_id"], "url": r["url"], "context": ctx[:300]}
                    hits += 1
                    break
    _log(run, "B", "fetch", "ok", url, f"{len(cands)} address candidate(s), {hits} with the name/login in their context")


def _phase_c(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    matched = sorted((e for e in p["candidates"].values() if e["match"]), key=lambda e: (e["network"], e["address"].lower()))
    if p["ti"] == 0 and not p["c_summary_logged"]:
        p["c_summary_logged"] = True
        rest = len(p["candidates"]) - len(matched)
        _log(
            run, "C", "rule", "info",
            detail=f"{len(matched)} address(es) have the name/login in their context and will be tracked; {rest} stay candidates. {CONTEXT_NOTE}",
        )
    if p["ti"] >= len(matched):
        return _next_phase(run, p)
    cand = matched[p["ti"]]
    p["ti"] += 1
    if c["addresses"] >= run.config["limits"]["max_addresses"]:
        raise _Limit(f"address limit reached ({run.config['limits']['max_addresses']}); {len(matched) - p['ti'] + 1} matching address(es) were not tracked")
    m = cand["match"]
    rationale = (
        f"Automatic rule (run {run.id[:8]}): the {m['term']} appears within 120 characters of this address on {m['url']}. "
        f"Context: {m['context']} A mention is not ownership."
    )[:1900]
    target = f"{cand['network']}:{cand['address']}"
    try:
        t = leads.track_address(
            session, inv_id=inv.id, fetch_run_id=m["fetch_run_id"], network=cand["network"], address=cand["address"],
            rationale=rationale, actor=_actor(run), automatic=True,
        )
    except LeadError as exc:
        _log(run, "C", "track", "failed", target, str(exc))
        c["failed"] += 1
        return
    c["addresses"] += 1
    if not any(x["entity_id"] == t["wallet_entity_id"] for x in p["tracked"]):
        p["tracked"].append({"entity_id": t["wallet_entity_id"], "network": cand["network"], "address": cand["address"]})
    audit(
        session, _actor(run), "automation.track", "entity", t["wallet_entity_id"], run=run.id, investigation=inv.id,
        network=cand["network"], address=cand["address"], matched=m["term"], page=m["url"], rationale=rationale,
    )
    _log(run, "C", "track", "ok", target, f"{m['term']} found near the address on {m['url']}; a page mention was recorded (not ownership)")


def _phase_d(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    if p["di"] >= len(p["tracked"]):
        return _next_phase(run, p)
    w = p["tracked"][p["di"]]
    p["di"] += 1
    target = f"{w['network']}:{w['address']}"
    try:
        r = leads.lookup_onchain(session, inv_id=inv.id, entity_id=w["entity_id"], refresh=False, actor=_actor(run))
    except LeadError as exc:
        _log(run, "D", "onchain", "failed", target, str(exc))
        c["failed"] += 1
        return
    c["lookups"] += 1
    if "view" in r:
        p["looked"][w["entity_id"]] = r["lookup_run_id"]
        txs = sum(len(v.get("recent_transactions") or []) for v in r["view"]["networks"])
        _log(run, "D", "onchain", "ok", target, f"{txs} recent transaction(s)" + ("; stored lookup reused" if r.get("reused_prior_run") else ""))
    elif r.get("unavailable"):
        _log(run, "D", "onchain", "unavailable", target, r.get("error") or "provider unavailable")
        c["skipped"] += 1
    else:
        _log(run, "D", "onchain", "failed", target, r.get("error") or r.get("status") or "no data")
        c["failed"] += 1


def _top_counterparties(view: dict, own_address: str) -> tuple[list[dict], int]:
    best: dict[tuple[str, str], dict] = {}
    for v in view["networks"]:
        for cp in v.get("counterparties", []):
            addr = cp["address"]
            if addr.lower() == own_address.lower() or not leads._valid_cp(v["network"], addr):
                continue
            key = (v["network"], addr.lower() if v["network"] in EVM_NETWORKS else addr)
            if key not in best or cp["tx_count"] > best[key]["tx_count"]:
                best[key] = {"network": v["network"], "address": addr, "direction": cp["direction"], "tx_count": cp["tx_count"]}
    ranked = sorted(best.values(), key=lambda x: (-x["tx_count"], x["address"]))
    return ranked[:MAX_EXPAND], len(ranked) - MAX_EXPAND if len(ranked) > MAX_EXPAND else 0


def _phase_e(session: Session, run: AutomationRun, inv: Investigation, p: dict, c: dict) -> None:
    wallets = [w for w in p["tracked"] if w["entity_id"] in p["looked"]]
    if p["ei"] >= len(wallets):
        return _next_phase(run, p)
    w = wallets[p["ei"]]
    p["ei"] += 1
    target = f"{w['network']}:{w['address']}"
    try:
        r = leads.lookup_onchain(session, inv_id=inv.id, entity_id=w["entity_id"], refresh=False, actor=_actor(run))
        if "view" not in r:
            _log(run, "E", "expand", "skipped", target, "no stored on-chain data for this wallet")
            c["skipped"] += 1
            return
        cps, over = _top_counterparties(r["view"], w["address"])
        if not cps:
            _log(run, "E", "expand", "ok", target, "no counterparties to link")
            return
        out = leads.expand(
            session, inv_id=inv.id, entity_id=w["entity_id"], lookup_run_id=r["lookup_run_id"],
            counterparties=[{k: x[k] for k in ("network", "address", "direction")} for x in cps], actor=_actor(run),
        )
    except LeadError as exc:
        _log(run, "E", "expand", "failed", target, str(exc))
        c["failed"] += 1
        return
    new = sum(1 for a in out["added"] if not a["already_present"])
    c["expanded"] += new
    c["skipped"] += len(out["skipped"])
    _log(
        run, "E", "expand", "ok", target,
        f"{new} new counterparty link(s), {len(out['added']) - new} already present, {over} over the {MAX_EXPAND} limit (transaction facts, not ownership)",
    )


_PHASES = {"A": _phase_a, "B_search": _phase_b_search, "B_fetch": _phase_b_fetch, "C": _phase_c, "D": _phase_d, "E": _phase_e}


def _step(session: Session, run: AutomationRun) -> bool:
    """Run ONE bounded step (at most one provider call). Returns False when the run is finished."""
    inv = session.get(Investigation, run.investigation_id)
    if inv is None:
        _finish(session, run, "failed", "investigation no longer exists")
        return False
    if run.abort_requested:
        _finish(session, run, "aborted", "aborted by the analyst")
        return False
    started = run.started_at or utcnow()
    if utcnow() - started > timedelta(seconds=run.config["limits"]["max_seconds"]):
        _finish(session, run, "stopped_by_limit", f"time limit reached ({run.config['limits']['max_seconds']} s)")
        return False
    p, c = copy.deepcopy(run.progress), dict(run.counters)
    if p["phase"] == "done":
        _finish(session, run, "completed", None)
        return False
    try:
        with session.begin_nested():
            _PHASES[p["phase"]](session, run, inv, p, c)
    except _Limit as exc:
        run.progress, run.counters = p, c
        _finish(session, run, "stopped_by_limit", str(exc))
        return False
    except Exception as exc:  # recorded, never swallowed silently
        session.refresh(run)
        _finish(session, run, "failed", f"unexpected {type(exc).__name__}: {exc}"[:500])
        return False
    run.progress, run.counters = p, c
    session.flush()
    return True


def advance_run(session: Session, run: AutomationRun, *, budget_seconds: float = CHUNK_SECONDS) -> AutomationRun:
    """Advance one run for at most `budget_seconds` (the worker calls this each tick; the run resumes next tick)."""
    if run.status in FINAL:
        return run
    if run.status == "queued":
        run.status, run.started_at = "running", utcnow()
        _log(run, "-", "running", "info", detail="the worker picked the run up")
    deadline = time.monotonic() + budget_seconds
    while time.monotonic() < deadline:
        if not _step(session, run):
            break
    run.updated_at = utcnow()
    session.flush()
    return run


def advance_due_runs(session: Session, *, max_runs: int = 3) -> int:
    runs = session.scalars(
        select(AutomationRun).where(AutomationRun.status.in_(("queued", "running"))).order_by(AutomationRun.created_at).limit(max_runs)
    ).all()
    for run in runs:
        advance_run(session, run)
        session.commit()
    return len(runs)


# ----------------------------------------------------------------------------------------------- read side


def _summary(run: AutomationRun) -> dict:
    return {
        "id": run.id, "investigation_id": run.investigation_id, "status": run.status, "stages": run.config.get("stages"),
        "subject": run.config.get("subject"), "login": run.config.get("login"), "stop_reason": run.stop_reason,
        "counters": run.counters, "limits": run.config.get("limits"), "created_by": run.created_by,
        "created_at": run.created_at.isoformat(), "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None, "abort_requested": run.abort_requested,
    }


def run_state(session: Session, run_id: str) -> dict:
    run = session.get(AutomationRun, run_id)
    if run is None:
        raise LeadError("automation run not found", 404)
    p = run.progress or {}
    tracked = {t["entity_id"] for t in p.get("tracked", [])}
    return {
        **_summary(run), "phase": p.get("phase"), "queries": run.config.get("queries"), "log": run.log,
        "profile_urls": p.get("profile_urls", []), "tracked": p.get("tracked", []),
        "candidates": [
            {"network": e["network"], "address": e["address"], "pages": e["pages"], "matched_term": (e["match"] or {}).get("term"),
             "page": (e["match"] or {}).get("url"), "tracked": any(t["address"].lower() == e["address"].lower() for t in p.get("tracked", []))}
            for e in p.get("candidates", {}).values()
        ],
        "note": CONTEXT_NOTE, "tracked_count": len(tracked),
    }


def list_runs(session: Session, *, investigation_id: str | None = None, limit: int = 20) -> list[dict]:
    q = select(AutomationRun).order_by(AutomationRun.created_at.desc()).limit(min(max(limit, 1), 100))
    if investigation_id:
        q = q.where(AutomationRun.investigation_id == investigation_id)
    return [_summary(r) for r in session.scalars(q)]
