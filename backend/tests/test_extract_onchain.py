"""Address extraction with context (+ redaction), positioned spans, and on-chain history helpers. Synthetic data only; no network.

Bitcoin addresses used below are the public BIP-173/BIP-350 / Bitcoin-wiki *test vectors*, not any person's wallet."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.extract import extract_address_candidates, redact_contact_data
from app.normalize import _address_ref, find_address_spans
from app.providers.base import ProviderContext, ProviderError, ProviderInput
from app.providers.onchain import (
    BitcoinEsploraProvider,
    blockscout_history,
    sats_to_btc,
    solana_history,
    summarize_btc_txs,
)
from tests.conftest import EVM_A, EVM_B, SOL_ADDR

BTC_A = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"  # BIP-173 vector
BTC_B = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"  # public test vector
BTC_C = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"  # public test vector
SOL_RANDOM = "C8H4v4c2eA6njjgzvWSrCpLdYg3hWSygoVsi4RkUrzjV"  # random 32 bytes, base58


# ------------------------------------------------------------------------------------------ spans / extractor
class TestSpansAndExtractor:
    TEXT = (
        f"Sprzedam laptopa. Kontakt: jan.kowalski@example.com, tel +48 600 123 456. "
        f"Płatność SOL: {SOL_RANDOM} albo ETH {EVM_A} albo BTC {BTC_A}."
    )

    def test_spans_cover_all_three_networks_with_positions(self):
        spans = find_address_spans(self.TEXT)
        assert {s.network for s in spans} == {"solana", "evm", "bitcoin"}
        for s in spans:
            assert self.TEXT[s.start : s.end] == s.raw

    def test_plain_long_words_and_invalid_checksums_are_not_addresses(self):
        text = "ThisIsJustALongWordWithoutDigitsOrSpacesAtAllReallyLongText 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdQ"
        assert find_address_spans(text) == []

    def test_candidates_have_context_and_contact_data_is_redacted(self):
        cands = {c.network: c for c in extract_address_candidates(self.TEXT)}
        assert set(cands) == {"solana", "evm", "bitcoin"}
        joined = " ".join(" ".join(c.contexts) for c in cands.values())
        assert "jan.kowalski@example.com" not in joined and "600 123 456" not in joined
        assert "[email removed]" in joined and "[phone removed]" in joined
        assert SOL_RANDOM in cands["solana"].contexts[0]
        assert cands["evm"].address == EVM_A.lower() or cands["evm"].address == EVM_A

    def test_repeated_address_is_grouped_with_occurrence_count(self):
        text = f"{EVM_A} first. Later again {EVM_A} and once more {EVM_A}"
        (c,) = extract_address_candidates(text)
        assert c.occurrences == 3 and 1 <= len(c.contexts) <= 2

    def test_redact_contact_data(self):
        out = redact_contact_data("mail me a@b.co or call (555) 123-4567 or +48 600-123-456")
        assert "a@b.co" not in out and "123-4567" not in out and "600-123-456" not in out

    def test_candidate_cap(self):
        many = " ".join("0x" + f"{i:040x}" for i in range(1, 80))
        assert len(extract_address_candidates(many)) == 50


# ------------------------------------------------------------------------------------------ bitcoin
def _btc_tx(txid, ins, outs, height=800_000, t=1_700_000_000):
    return {
        "txid": txid,
        "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in ins],
        "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outs],
        "status": {"confirmed": True, "block_height": height, "block_time": t},
    }


class TestBitcoin:
    def test_sats_to_btc(self):
        assert sats_to_btc(150_000_000) == "1.50000000" and sats_to_btc(-1) == "-0.00000001"

    def test_summarize_received_and_sent_counterparties(self):
        txs = [
            _btc_tx("t1", [(BTC_B, 60_000)], [(BTC_A, 50_000), (BTC_B, 9_000)]),  # BTC_A received from BTC_B
            _btc_tx("t2", [(BTC_A, 50_000)], [(BTC_C, 40_000), (BTC_A, 9_000)]),  # BTC_A sent to BTC_C
        ]
        recent, cps = summarize_btc_txs(BTC_A, txs, 10)
        assert [r["direction"] for r in recent] == ["received", "sent"]
        assert recent[0]["net_sats"] == 50_000 and recent[1]["net_sats"] == -41_000
        by = {(c["address"], c["direction"]): c for c in cps}
        assert by[(BTC_B, "received_from")]["tx_count"] == 1 and by[(BTC_C, "sent_to")]["tx_count"] == 1
        assert all(c["address"] != BTC_A for c in cps)  # own change output never a counterparty

    def test_no_common_input_ownership_heuristic(self):
        # two inputs from different addresses in one tx: BOTH are reported as separate counterparties, never merged.
        tx = _btc_tx("t3", [(BTC_B, 10_000), (BTC_C, 10_000)], [(BTC_A, 19_000)])
        _, cps = summarize_btc_txs(BTC_A, [tx], 10)
        assert {c["address"] for c in cps} == {BTC_B, BTC_C}

    def _ctx(self, handler, **kw):
        s = Settings(btc_esplora_url="https://esplora.example.org/api", **kw)
        return ProviderContext(settings=s, http=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_provider_unavailable_without_config(self):
        from app.providers.base import ProviderUnavailable

        ctx = ProviderContext(settings=Settings(btc_esplora_url=None), http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
        with pytest.raises(ProviderUnavailable):
            BitcoinEsploraProvider().execute(ProviderInput(reference=_address_ref("bitcoin", BTC_A, BTC_A, None)), ctx)

    def test_provider_address_lookup_mocked(self):
        stats = {"chain_stats": {"funded_txo_sum": 100_000, "spent_txo_sum": 40_000, "tx_count": 12}, "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0}}
        txs = [_btc_tx("t1", [(BTC_B, 60_000)], [(BTC_A, 50_000)])]

        def handler(req: httpx.Request):
            if req.url.path.endswith(f"/address/{BTC_A}"):
                return httpx.Response(200, json=stats)
            if req.url.path.endswith(f"/address/{BTC_A}/txs"):
                return httpx.Response(200, json=txs)
            return httpx.Response(404)

        prov, ref, ctx = BitcoinEsploraProvider(), _address_ref("bitcoin", BTC_A, BTC_A, None), self._ctx(handler)
        data = ProviderInput(reference=ref)
        raw = prov.execute(data, ctx)
        (obs,) = prov.normalize_result(raw, data, ctx)
        p = obs.payload
        assert p["balance"]["confirmed_sats"] == 60_000 and p["balance"]["btc"] == "0.00060000"
        assert p["tx_count"] == 12 and p["history_truncated"] is True
        assert p["counterparties"][0]["address"] == BTC_B
        assert "No ownership" in p["note"] or "no ownership" in p["note"].lower()

    def test_provider_address_not_found_is_honest(self):
        prov, ref = BitcoinEsploraProvider(), _address_ref("bitcoin", BTC_A, BTC_A, None)
        ctx = self._ctx(lambda r: httpx.Response(404))
        data = ProviderInput(reference=ref)
        (obs,) = prov.normalize_result(prov.execute(data, ctx), data, ctx)
        assert obs.payload["found"] is False

    def test_rate_limit_is_retryable_error_not_empty_data(self):
        prov, ref = BitcoinEsploraProvider(), _address_ref("bitcoin", BTC_A, BTC_A, None)
        with pytest.raises(ProviderError):
            prov.execute(ProviderInput(reference=ref), self._ctx(lambda r: httpx.Response(429)))


# ------------------------------------------------------------------------------------------ EVM (blockscout) & Solana
class TestHistory:
    def test_blockscout_history(self):
        info = {"coin_balance": "1500000000000000000", "is_contract": False, "ens_domain_name": None, "is_scam": False, "reputation": "ok"}
        txs = {"items": [
            {"hash": "0xa1", "from": {"hash": EVM_A, "is_contract": False}, "to": {"hash": EVM_B, "is_contract": True}, "value": "5", "timestamp": "2026-01-01T00:00:00Z", "result": "success", "block_number": 1},
            {"hash": "0xa2", "from": {"hash": EVM_B, "is_contract": True}, "to": {"hash": EVM_A, "is_contract": False}, "value": "7", "timestamp": "2026-01-02T00:00:00Z", "result": "success", "block_number": 2},
        ], "next_page_params": {"x": 1}}

        def handler(req):
            return httpx.Response(200, json=txs if req.url.path.endswith("/transactions") else info)

        out = blockscout_history(httpx.Client(transport=httpx.MockTransport(handler)), "https://scout.example.org/api", EVM_A, 10)
        assert out["balance_native"] == "1.5" and out["history_truncated"] is True
        assert [t["direction"] for t in out["recent_transactions"]] == ["sent", "received"]
        assert {(c["direction"], c["is_contract"]) for c in out["counterparties"]} == {("sent_to", True), ("received_from", True)}

    def test_blockscout_unknown_address(self):
        out = blockscout_history(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))), "https://scout.example.org/api", EVM_A, 5)
        assert out["found"] is False and out["counterparties"] == []

    def test_blockscout_error_raises(self):
        with pytest.raises(ProviderError):
            blockscout_history(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))), "https://scout.example.org/api", EVM_A, 5)

    def test_solana_history_counterparties_from_balance_changes(self):
        def rpc(client, url, method, params):
            if method == "getSignaturesForAddress":
                return [{"signature": "sigOK", "slot": 5, "blockTime": 1_700_000_000, "err": None}, {"signature": "sigERR", "slot": 4, "blockTime": 1_699_999_999, "err": {"x": 1}}]
            assert method == "getTransaction" and params[0] == "sigOK"
            return {
                "meta": {"preBalances": [10, 20, 30], "postBalances": [9, 21, 30], "preTokenBalances": [], "postTokenBalances": []},
                "transaction": {"message": {"accountKeys": [{"pubkey": SOL_ADDR}, {"pubkey": SOL_RANDOM}, {"pubkey": "11111111111111111111111111111111"}]}},
            }

        out = solana_history(rpc, None, "x", SOL_ADDR, 10)
        assert [t["failed"] for t in out["recent_transactions"]] == [False, True]
        assert [c["address"] for c in out["counterparties"]] == [SOL_RANDOM]  # system program excluded, own address excluded

    def test_solana_history_bad_shape_is_an_error_not_a_crash(self):
        with pytest.raises(ProviderError):
            solana_history(lambda *a: {"value": 1}, None, "x", SOL_ADDR, 10)


# ------------------------------------------------------------------------------------------ history pagination cursors
class TestPagination:
    def test_blockscout_cursor_is_sent_back_urlencoded_and_skips_the_balance_lookup(self):
        seen = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, json={"items": [], "next_page_params": None})

        out = blockscout_history(httpx.Client(transport=httpx.MockTransport(handler)), "https://scout.example.org/api", EVM_A, 10, {"block_number": 5, "hash": "0xab", "index": None})
        assert len(seen) == 1 and "block_number=5" in seen[0] and "hash=0xab" in seen[0] and "index=" in seen[0]
        assert out["found"] is True and "balance_wei" not in out and out["next_cursor"] is None

    def test_blockscout_next_cursor_only_when_the_page_was_consumed(self):
        items = [{"hash": f"0x{i}", "from": {"hash": EVM_A}, "to": {"hash": EVM_B}} for i in range(3)]
        h = lambda r: httpx.Response(200, json={"items": items, "next_page_params": {"p": 1}} if r.url.path.endswith("/transactions") else {"coin_balance": "0"})  # noqa: E731
        c = httpx.Client(transport=httpx.MockTransport(h))
        assert blockscout_history(c, "https://scout.example.org/api", EVM_A, 10)["next_cursor"] == {"p": 1}
        # limit smaller than the page: the unseen rest of the page would be skipped, so no cursor is offered
        assert blockscout_history(c, "https://scout.example.org/api", EVM_A, 2)["next_cursor"] is None

    def test_blockscout_rejects_a_malformed_cursor(self):
        c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        for bad in ({}, "x", {f"k{i}": 1 for i in range(13)}):
            with pytest.raises(ProviderError):
                blockscout_history(c, "https://scout.example.org/api", EVM_A, 5, bad)

    def test_solana_before_cursor_and_next_cursor(self):
        sig = "5" * 88
        calls = []

        def rpc(client, url, method, params):
            calls.append((method, params))
            if method == "getSignaturesForAddress":
                return [{"signature": sig, "slot": 1, "blockTime": 1, "err": {"x": 1}}]
            raise AssertionError(method)

        out = solana_history(rpc, None, "x", SOL_ADDR, 1, before="6" * 88)
        assert calls[0][1][1] == {"limit": 1, "before": "6" * 88} and out["next_cursor"] == {"before": sig}
        assert solana_history(rpc, None, "x", SOL_ADDR, 5)["next_cursor"] is None  # fewer than `limit` -> end of history
        with pytest.raises(ProviderError):
            solana_history(rpc, None, "x", SOL_ADDR, 1, before="not-a-signature")

    def test_bitcoin_page_uses_chain_cursor_and_unconfirmed_tx_cannot_anchor_a_page(self):
        t1 = "a" * 64
        confirmed = _btc_tx(t1, [(BTC_B, 60_000)], [(BTC_A, 50_000)])
        confirmed["status"] = {"confirmed": True, "block_height": 1, "block_time": 1_700_000_000}
        from app.providers.onchain import btc_next_cursor

        assert btc_next_cursor([confirmed], 10) == {"after": t1}
        unconfirmed = {**confirmed, "status": {"confirmed": False}}
        assert btc_next_cursor([unconfirmed], 10) is None and btc_next_cursor([], 10) is None

        paths = []

        def handler(req):
            paths.append(req.url.path)
            return httpx.Response(200, json=[confirmed])

        ctx = ProviderContext(settings=Settings(btc_esplora_url="https://esplora.example.org/api", onchain_history_limit=1),
                              http=httpx.Client(transport=httpx.MockTransport(handler)))
        prov, ref = BitcoinEsploraProvider(), _address_ref("bitcoin", BTC_A, BTC_A, None)
        data = ProviderInput(reference=ref, payload={"cursor": {"after": t1}})
        raw = prov.execute(data, ctx)
        (obs,) = prov.normalize_result(raw, data, ctx)
        assert paths == [f"/api/address/{BTC_A}/txs/chain/{t1}"] and obs.payload["page"] is True and obs.payload["next_cursor"] == {"after": t1}
        assert obs.source.canonical_key.startswith(ref.canonical_id + "#page:")
        with pytest.raises(ProviderError):
            prov.execute(ProviderInput(reference=ref, payload={"cursor": {"after": "../x"}}), ctx)
