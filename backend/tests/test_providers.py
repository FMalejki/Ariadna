"""Providers: mocked EVM/Solana JSON-RPC, Sherlock CSV parsing, provider failure handling. No network access.

STATUS note: the RPC providers are verified here ONLY against mocked JSON-RPC responses (synthetic data)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import Entity, EntityMerge, Observation, Relationship, ToolRun
from app.normalize import classify_seed, classify_url
from app.providers.base import ProviderContext, ProviderError, ProviderInput, ProviderUnavailable
from app.providers.blockchain import TRANSFER_TOPIC, EvmRpcProvider, SolanaRpcProvider
from app.providers.registry import sync_providers
from app.providers.sherlock import parse_sherlock_csv
from app.services import discovery as disc
from app.services import runner
from tests.conftest import EVM_A, EVM_B, EVM_TOKEN, EVM_TX, SOL_ADDR


def _evm_handler(found: bool = True, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code)
        body = json.loads(request.content)
        m = body["method"]
        if m == "eth_getTransactionByHash":
            result = {"hash": EVM_TX, "from": EVM_A, "to": EVM_B, "blockNumber": "0x10"} if found else None
        elif m == "eth_getTransactionReceipt":
            result = {"status": "0x1", "gasUsed": "0x5208", "logs": [{"address": EVM_TOKEN, "topics": [TRANSFER_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "22" * 20]}]}
        elif m == "eth_getBlockByNumber":
            result = {"timestamp": "0x65920080"}
        elif m == "eth_chainId":
            result = "0x1"
        else:
            result = None
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return handler


def _ctx(handler, **settings_kw) -> ProviderContext:
    s = Settings(evm_rpc_urls={"ethereum": "http://rpc.test"}, solana_rpc_url="http://sol.test", **settings_kw)
    return ProviderContext(settings=s, http=httpx.Client(transport=httpx.MockTransport(handler)))


def _row(session, name):
    return next(p for p in sync_providers(session) if p.name == name)


def _run(session, name, seed, ctx):
    return runner.run_provider(session, _row(session, name), ProviderInput(reference=classify_seed(seed)), ctx=ctx)


class TestEvm:
    def test_verified_tx_resolves_network_and_records_deterministic_evidence(self, session):
        out = _run(session, "evm_rpc", EVM_TX, _ctx(_evm_handler()))
        assert out.error is None, out.error
        obs = session.scalars(select(Observation).where(Observation.observation_type == "blockchain_transaction")).all()
        assert len(obs) == 1 and obs[0].evidence_type == "deterministic_blockchain_verification"
        assert obs[0].normalized_payload["verified"] is True
        assert obs[0].normalized_payload["from"] == EVM_A
        # sender relationship exists, is evidenced by the RPC observation, and is a *wallet executed tx* fact only
        rel = session.scalar(select(Relationship).where(Relationship.relationship_type == "WALLET_EXECUTED_TRANSACTION"))
        assert rel is not None and rel.status == "verified"
        assert session.scalar(select(Relationship).where(Relationship.relationship_type == "TRANSACTION_TRANSFERRED_TOKEN")) is not None

    def test_unknown_network_merge_is_deterministic_and_reversible(self, session):
        from app.services import entities as ent_svc

        disc.start_investigation(session, name="n", seed=EVM_TX, actor="t")  # creates tx:evm:<hash>
        assert session.scalar(select(Entity).where(Entity.canonical_identifier == f"tx:evm:{EVM_TX}")) is not None
        _run(session, "evm_rpc", EVM_TX, _ctx(_evm_handler()))
        merge = session.scalar(select(EntityMerge))
        assert merge is not None and merge.method == "deterministic_network_resolution"
        loser = session.get(Entity, merge.loser_id)
        assert loser.status == "merged" and loser.merged_into_id == merge.winner_id
        assert ent_svc.resolve_id(session, loser.id) == merge.winner_id
        ent_svc.reverse_merge(session, merge.id, "tester")
        session.refresh(loser)
        assert loser.status == "active" and loser.merged_into_id is None

    def test_tx_not_found_is_recorded_as_failed_lookup_not_verified(self, session):
        out = _run(session, "evm_rpc", EVM_TX, _ctx(_evm_handler(found=False)))
        assert out.error is None
        o = session.scalar(select(Observation))
        assert o.observation_type == "blockchain_lookup_failed" and o.normalized_payload["verified"] is False
        assert session.scalar(select(Relationship)) is None  # nothing invented

    def test_unconfigured_rpc_is_reported_unavailable_not_faked(self, session):
        ctx = ProviderContext(settings=Settings(evm_rpc_urls={}), http=httpx.Client(transport=httpx.MockTransport(_evm_handler())))
        out = _run(session, "evm_rpc", EVM_TX, ctx)
        assert out.unavailable and out.error
        run = session.scalar(select(ToolRun))
        assert run.status == "unavailable"
        assert session.scalar(select(Observation)) is None

    def test_http_failure_is_retryable_provider_error(self, session):
        out = _run(session, "evm_rpc", EVM_TX, _ctx(_evm_handler(status_code=503)))
        assert out.error and out.retryable and not out.unavailable
        assert session.scalar(select(ToolRun)).status == "failed"

    def test_timeout_marks_tool_run_timed_out(self, session):
        def boom(request):
            raise httpx.ReadTimeout("slow", request=request)

        out = _run(session, "evm_rpc", EVM_TX, _ctx(boom))
        assert out.retryable
        assert session.scalar(select(ToolRun)).status == "failed"  # RPC layer maps timeouts to ProviderError

    def test_provider_never_attributes_ownership(self, session):
        _run(session, "evm_rpc", EVM_TX, _ctx(_evm_handler()))
        forbidden = {"OWNS", "CONTROLS", "BELONGS_TO"}
        assert not any(any(f in r.relationship_type for f in forbidden) for r in session.scalars(select(Relationship)))


class TestSolana:
    def test_account_lookup_mocked(self, session):
        def handler(request):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": {"lamports": 5, "executable": False, "owner": "11111111111111111111111111111111"}}})

        out = _run(session, "solana_rpc", SOL_ADDR, _ctx(handler))
        assert out.error is None
        o = session.scalar(select(Observation))
        assert o.observation_type == "blockchain_address" and o.normalized_payload["lamports"] == 5

    def test_unconfigured_is_unavailable(self, session):
        ctx = ProviderContext(settings=Settings(solana_rpc_url=None), http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
        out = _run(session, "solana_rpc", SOL_ADDR, ctx)
        assert out.unavailable


class TestSherlock:
    HEADER = "username,name,url_main,url_user,exists,http_status,response_time_s\n"

    def test_claimed_hits_become_proposed_matches_never_aliases(self, session):
        csv_text = self.HEADER + (
            "alice,GitHub,https://github.com,https://github.com/alice,Claimed,200,0.2\n"
            "alice,Reddit,https://reddit.com,https://reddit.com/user/alice,Available,404,0.1\n"
        )
        subject = classify_url("https://x.com/alice")
        ns = parse_sherlock_csv(csv_text, subject, "t")
        assert ns.payload["claimed_count"] == 1
        assert ns.links and all(l.relationship_type == "USERNAME_MATCHES_PROFILE" and l.match_candidate_rule for l in ns.links)
        assert ns.payload["note"].startswith("A claimed result is a username-string match only")

    def test_bad_header_rejected(self):
        with pytest.raises(ProviderError):
            parse_sherlock_csv("a,b,c\n1,2,3\n", classify_url("https://x.com/alice"), "t")

    def test_ingest_creates_pending_candidate_not_merge(self, session):
        from app.models import EntityAlias, MatchCandidate
        from app.services import ingest as ingest_svc

        csv_text = self.HEADER + "alice,GitHub,https://github.com,https://github.com/alice,Claimed,200,0.2\n"
        ns = parse_sherlock_csv(csv_text, classify_url("https://x.com/alice"), "t")
        ingest_svc.ingest(session, [ns], provider=_row(session, "sherlock"), tool_run_id=None, raw_artifact_id=None, evidence_default="tool_generated_discovery")
        rel = session.scalar(select(Relationship).where(Relationship.relationship_type == "USERNAME_MATCHES_PROFILE"))
        assert rel is not None and rel.status in ("proposed", "supported")
        assert rel.status != "verified"
        cand = session.scalar(select(MatchCandidate))
        assert cand is not None and cand.status == "pending"
        assert session.scalar(select(EntityAlias)) is None and session.scalar(select(EntityMerge)) is None

    def test_missing_binary_is_unavailable(self, session):
        ctx = ProviderContext(settings=Settings(sherlock_binary="definitely-not-installed-sherlock"))
        out = _run(session, "sherlock", "https://x.com/alice", ctx)
        assert out.unavailable
        assert session.scalar(select(Observation)) is None


def test_placeholders_never_run_or_enable(session):
    for name in ("maigret", "spiderfoot", "maltego"):
        row = _row(session, name)
        assert row.implementation_status == "placeholder" and row.enabled is False
    assert get_settings() is not None


def test_former_placeholder_is_enabled_once_implemented_but_operator_disable_sticks(session):
    from app.models import Provider
    from app.providers.registry import sync_providers

    row = session.scalar(select(Provider).where(Provider.name == "web_search"))
    row.implementation_status, row.enabled = "placeholder", False  # state left behind by an older release
    session.flush()
    assert next(p for p in sync_providers(session) if p.name == "web_search").enabled is True
    row.enabled = False  # operator decision afterwards must survive further syncs
    session.flush()
    assert next(p for p in sync_providers(session) if p.name == "web_search").enabled is False
