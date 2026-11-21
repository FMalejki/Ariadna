"""Discovery engine: loops, depth, policy, rate limits, retries, dead-letter and knowledge reuse. Synthetic data only.

A scripted in-memory provider (`fake_osint`) stands in for an external tool so nothing touches the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.domain import EntityType, EvidenceType
from app.models import DiscoveryTask, Entity, Investigation, InvestigationItem, Observation, Provider, ToolRun
from app.normalize import Reference
from app.providers import registry
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    ProviderError,
    ProviderUnavailable,
    RawResult,
    SourceRef,
    NormalizedObservation,
)
from app.providers.registry import sync_providers
from app.services import discovery as disc


class FakeOsint(IntelligenceProvider):
    """Scripted provider: `graph[handle]` is the list of handles whose profile URL appears in that profile's text."""

    name = "fake_osint"
    version = "test-1"
    kind = "cli"
    category = "blockchain_verification"  # auto-approved category, so tasks start queued
    description = "test double"
    capabilities = ["test"]
    evidence_class = EvidenceType.OFFICIAL_PLATFORM_DATA

    def __init__(self, graph: dict[str, list[str]], mode: str = "ok"):
        self.graph, self.mode, self.calls = graph, mode, []

    def supports(self, ref: Reference) -> bool:
        return ref.entity_type == EntityType.SOCIAL_ACCOUNT.value and ref.canonical_id.startswith("x:")

    def execute(self, data, ctx):
        handle = data.reference.canonical_id.split(":", 1)[1]
        self.calls.append(handle)
        if self.mode == "fail":
            raise ProviderError("boom")
        if self.mode == "unavailable":
            raise ProviderUnavailable("not installed")
        return RawResult(raw=json.dumps({"handle": handle}).encode(), parsed={"handle": handle})

    def normalize_result(self, raw, data, ctx):
        handle = raw.parsed["handle"]
        ref = data.reference
        mentions = self.graph.get(handle, [])
        bio = " ".join(f"https://x.com/{m}" for m in mentions)
        return [
            NormalizedObservation(
                source=SourceRef("social_account", ref.canonical_id, ref.url, ref.canonical_id, ref.display_name),
                subject=ref, observation_type="social_profile", observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                payload={"handle": handle, "bio": bio}, text_fields=[bio],
            )
        ]

    def health_check(self, ctx):
        return HealthStatus("healthy")


def _setup(session, graph, mode="ok") -> FakeOsint:
    fake = FakeOsint(graph, mode)
    registry.reset_registry(extra=[fake])
    sync_providers(session)
    session.commit()
    return fake


def _scope(depth=5):
    return {"max_depth": depth, "allowed_providers": ["fake_osint"]}


def _start(session, seed, depth=5, name="inv"):
    inv, ref, tasks = disc.start_investigation(session, name=name, seed=seed, actor="tester", scope=_scope(depth))
    session.flush()
    return inv


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def _tasks(session, inv):
    return session.scalars(select(DiscoveryTask).where(DiscoveryTask.investigation_id == inv.id)).all()


def test_follow_ups_are_created_and_cycles_terminate(session):
    fake = _setup(session, {"alice": ["bob"], "bob": ["alice", "carol"], "carol": ["alice"]})
    inv = _start(session, "https://x.com/alice")
    disc.run_pending(session, investigation_id=inv.id)
    # each handle fetched exactly once even though a -> b -> a is a cycle
    assert sorted(fake.calls) == ["alice", "bob", "carol"]
    keys = [t.dedup_key for t in _tasks(session, inv)]
    assert len(keys) == len(set(keys))
    assert all(t.status == "completed" for t in _tasks(session, inv))


def test_depth_limit_is_enforced_and_capped_by_server_setting(session):
    # settings.max_discovery_depth is 2 (conftest); the investigation asks for 10 but cannot exceed 2
    fake = _setup(session, {"a1": ["a2"], "a2": ["a3"], "a3": ["a4"], "a4": ["a5"]})
    inv = _start(session, "https://x.com/a1", depth=10)
    assert disc.effective_max_depth(inv) == 2
    disc.run_pending(session, investigation_id=inv.id)
    assert fake.calls == ["a1", "a2", "a3"]  # depth 0,1,2 only; a4 would be depth 3
    assert max(t.depth for t in _tasks(session, inv)) == 2


def test_scope_depth_smaller_than_server_cap_wins(session):
    fake = _setup(session, {"a1": ["a2"], "a2": ["a3"]})
    inv = _start(session, "https://x.com/a1", depth=1)
    disc.run_pending(session, investigation_id=inv.id)
    assert fake.calls == ["a1", "a2"]


def test_unknown_category_requires_approval_and_decision_is_audited(session):
    registry.reset_registry()
    sync_providers(session)
    inv, _, tasks = disc.start_investigation(session, name="p", seed="https://x.com/alice", actor="tester")
    session.flush()
    sher = next(t for t in tasks if t.provider_name == "sherlock")
    assert sher.status == "pending_approval"
    # a pending task is never executed by the loop
    assert disc.run_pending(session, investigation_id=inv.id) == []
    disc.decide_task(session, sher, True, "reviewer", "ok for test")
    assert sher.status == "queued" and sher.decided_by == "reviewer"
    with pytest.raises(ValueError):
        disc.decide_task(session, sher, False, "reviewer")  # no longer pending


def test_rejected_task_never_runs(session):
    registry.reset_registry()
    sync_providers(session)
    inv, _, tasks = disc.start_investigation(session, name="p", seed="https://x.com/alice", actor="tester")
    session.flush()
    sher = next(t for t in tasks if t.provider_name == "sherlock")
    disc.decide_task(session, sher, False, "reviewer", "not in scope")
    assert sher.status == "rejected"
    assert disc.run_pending(session, investigation_id=inv.id) == []
    assert _count(session, ToolRun) == 0


def test_provider_not_in_scope_allowlist_is_not_routed(session):
    _setup(session, {})
    inv, _, tasks = disc.start_investigation(
        session, name="s", seed="https://x.com/alice", actor="tester", scope={"max_depth": 1, "allowed_providers": ["evm_rpc"]}
    )
    assert tasks == []


def test_disabled_provider_is_not_routed(session):
    _setup(session, {})
    row = session.scalar(select(Provider).where(Provider.name == "fake_osint"))
    row.enabled = False
    session.flush()
    inv, _, tasks = disc.start_investigation(session, name="s", seed="https://x.com/alice", actor="tester", scope=_scope())
    assert tasks == []


def test_provider_failure_retries_then_dead_letters(session):
    fake = _setup(session, {}, mode="fail")
    inv = _start(session, "https://x.com/alice")
    task = _tasks(session, inv)[0]
    for attempt in (1, 2):
        disc.execute_task(session, task)
        assert task.status == "failed" and task.attempts == attempt
        assert task.next_attempt_at is not None  # backoff scheduled
        task.next_attempt_at = None  # pretend the backoff elapsed
    disc.execute_task(session, task)
    assert task.status == "dead" and task.attempts == 3
    assert len(fake.calls) == 3
    runs = session.scalars(select(ToolRun)).all()
    assert [r.status for r in runs] == ["failed"] * 3 and all(r.error == "boom" for r in runs)
    assert _count(session, Observation) == 0  # failure produced no knowledge


def test_failed_task_waits_for_backoff(session):
    fake = _setup(session, {}, mode="fail")
    inv = _start(session, "https://x.com/alice")
    disc.run_pending(session, investigation_id=inv.id)
    # one attempt only: the retry is in the future so the loop terminates instead of spinning
    assert fake.calls == ["alice"]
    assert _tasks(session, inv)[0].status == "failed"


def test_unavailable_provider_is_skipped_not_retried(session):
    fake = _setup(session, {}, mode="unavailable")
    inv = _start(session, "https://x.com/alice")
    disc.run_pending(session, investigation_id=inv.id)
    t = _tasks(session, inv)[0]
    assert t.status == "skipped" and "unavailable" in t.status_reason
    assert fake.calls == ["alice"]
    assert session.scalar(select(ToolRun)).status == "unavailable"


def test_rate_limit_defers_instead_of_dropping(session):
    fake = _setup(session, {"alice": ["bob"]})
    session.scalar(select(Provider).where(Provider.name == "fake_osint")).rate_limit_per_minute = 1
    inv = _start(session, "https://x.com/alice")
    done = disc.run_pending(session, investigation_id=inv.id)
    assert fake.calls == ["alice"]
    by = {t.target_identifier: t for t in _tasks(session, inv)}
    assert by["x:alice"].status == "completed"
    deferred = by["x:bob"]
    assert deferred.status == "queued" and deferred.next_attempt_at is not None
    assert deferred.attempts == 0 and "deferred" in deferred.status_reason
    assert len(done) == 2  # alice + the deferred attempt; loop did not spin


def test_second_investigation_reuses_knowledge_and_adds_only_new(session):
    fake = _setup(session, {"alice": ["bob"], "bob": ["carol"], "carol": ["dave"], "dave": []})
    inv1 = _start(session, "https://x.com/alice", depth=2)
    disc.run_pending(session, investigation_id=inv1.id)
    assert fake.calls == ["alice", "bob", "carol"]
    obs_before = _count(session, Observation)
    entities_before = _count(session, Entity)
    runs_before = _count(session, ToolRun)

    # overlapping investigation: starts at bob, which investigation 1 already covered
    inv2 = _start(session, "https://x.com/bob", depth=2, name="second")
    disc.run_pending(session, investigation_id=inv2.id)
    by = {t.target_identifier: t for t in _tasks(session, inv2)}
    assert by["x:bob"].status == "reused" and by["x:carol"].status == "reused"
    assert by["x:bob"].result_summary["provider_executed"] is False
    assert by["x:dave"].status == "completed"  # genuinely new -> the only provider call
    assert fake.calls == ["alice", "bob", "carol", "dave"]
    assert _count(session, ToolRun) == runs_before + 1
    assert _count(session, Observation) == obs_before + 1
    assert _count(session, Entity) == entities_before  # dave already existed as a mentioned entity

    # evidence chain is visible from the second investigation: reused observations are linked to it
    reused_obs = session.scalars(
        select(InvestigationItem).where(
            InvestigationItem.investigation_id == inv2.id, InvestigationItem.item_type == "observation", InvestigationItem.origin == "reused"
        )
    ).all()
    assert len(reused_obs) >= 2
    first_run = session.scalar(select(ToolRun).where(ToolRun.input_key == "x:bob"))
    assert by["x:bob"].result_summary["reused_tool_run_id"] == first_run.id


def test_full_repeat_of_same_seed_creates_nothing_new(session):
    fake = _setup(session, {"alice": ["bob"], "bob": []})
    inv1 = _start(session, "https://x.com/alice", depth=1)
    disc.run_pending(session, investigation_id=inv1.id)
    counts = (_count(session, Observation), _count(session, Entity), _count(session, ToolRun))
    inv2 = _start(session, "https://x.com/alice", depth=1, name="again")
    disc.run_pending(session, investigation_id=inv2.id)
    assert counts == (_count(session, Observation), _count(session, Entity), _count(session, ToolRun))
    assert fake.calls == ["alice", "bob"]
    assert {t.status for t in _tasks(session, inv2)} == {"reused"}


def test_invalid_seed_is_rejected(session):
    with pytest.raises(disc.InvalidReference):
        disc.start_investigation(session, name="x", seed="not a thing", actor="tester")


def test_non_followup_links_do_not_spawn_tasks(session):
    """Sherlock-style hits (Link.follow_up=False) must never fan out into more lookups."""
    from app.providers.sherlock import parse_sherlock_csv
    from app.normalize import classify_seed

    csv_text = (
        "username,name,url_main,url_user,exists,http_status,response_time_s\n"
        "alice,GitHub,https://github.com/,https://github.com/alice,Claimed,200,0.2\n"
    )
    ns = parse_sherlock_csv(csv_text, classify_seed("https://x.com/alice"), "t")
    assert ns.links and all(l.follow_up is False for l in ns.links)
