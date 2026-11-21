"""On-chain history helpers + the Bitcoin provider (Esplora-compatible REST).

STATUS: IMPLEMENTED against the response schemas observed on the public endpoints named below; unit-tested with
mocked responses, and exercised once against live endpoints (result recorded in docs/LLM_WIKI/sessions/).

What this module records are ON-CHAIN FACTS about an address: balance, recent transactions and the other
addresses that appear in those transactions ("counterparties"). A counterparty is NOT "the same owner", NOT
"related" in any identity sense - exchanges, contracts and payment processors are counterparties of millions.
No ownership heuristic of any kind is applied (in particular, no Bitcoin "common input ownership").

Endpoints / docs
* Bitcoin: Esplora REST (mempool.space, blockstream.info and compatible instances) -
  GET /address/:a, GET /address/:a/txs, GET /tx/:txid.   https://github.com/Blockstream/esplora/blob/master/API.md
* EVM history: Blockscout REST v2 (optional, keyless) -
  GET /v2/addresses/:a, GET /v2/addresses/:a/transactions.   https://docs.blockscout.com/devs/apis/rest
* Solana: JSON-RPC getSignaturesForAddress + getTransaction (jsonParsed).   https://solana.com/docs/rpc
"""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain import EntityType, EvidenceType, ObservationType, SourceType
from app.normalize import (
    Reference,
    _address_ref,
    is_bitcoin_address,
    is_bitcoin_txid,
    is_solana_address,
)
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    NormalizedObservation,
    ProviderContext,
    ProviderError,
    ProviderInput,
    ProviderUnavailable,
    RawResult,
    SourceRef,
)

NOTE_ADDRESS = (
    "On-chain facts about this address only. Counterparties are other addresses seen in the same transactions; "
    "exchanges, contracts and services are counterparties of many unrelated parties. No ownership is implied."
)


def get_json(client: httpx.Client, url: str, *, what: str) -> Any:
    """GET + JSON with honest error mapping (retryable ProviderError; never fabricated data)."""
    try:
        resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{what}: timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"{what}: transport error {type(exc).__name__}") from exc
    if resp.status_code == 429:
        raise ProviderError(f"{what}: rate limited by the endpoint (HTTP 429)")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ProviderError(f"{what}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError(f"{what}: non-JSON response") from exc


class CounterpartyCollector:
    """Aggregate (counterparty, direction, tx) observations into a bounded, ordered list."""

    def __init__(self, own: str):
        self.own = own
        self._items: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def add(self, address: str | None, direction: str, tx_hash: str, *, is_contract: bool | None = None) -> None:
        if not address or address == self.own:
            return
        item = self._items.setdefault(
            (address, direction), {"address": address, "direction": direction, "tx_count": 0, "sample_txs": [], "is_contract": None}
        )
        item["tx_count"] += 1
        if tx_hash not in item["sample_txs"] and len(item["sample_txs"]) < 3:
            item["sample_txs"].append(tx_hash)
        if is_contract is not None:
            item["is_contract"] = is_contract

    def result(self) -> list[dict]:
        return sorted(self._items.values(), key=lambda i: (-i["tx_count"], i["address"]))


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


# ----------------------------------------------------------------------------------------------- Bitcoin


def sats_to_btc(sats: int) -> str:
    sign = "-" if sats < 0 else ""
    sats = abs(sats)
    return f"{sign}{sats // 100_000_000}.{sats % 100_000_000:08d}"


def summarize_btc_txs(address: str, txs: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """Per-transaction net effect for `address` plus counterparties. Facts only; inputs are NOT clustered."""
    recent: list[dict] = []
    cps = CounterpartyCollector(address)
    for tx in txs[:limit]:
        vin = tx.get("vin") or []
        vout = tx.get("vout") or []
        spent = sum((i.get("prevout") or {}).get("value", 0) for i in vin if (i.get("prevout") or {}).get("scriptpubkey_address") == address)
        received = sum(o.get("value", 0) for o in vout if o.get("scriptpubkey_address") == address)
        net = received - spent
        in_inputs = spent > 0
        status = tx.get("status") or {}
        txid = tx.get("txid", "")
        if in_inputs:
            for o in vout:
                cps.add(o.get("scriptpubkey_address"), "sent_to", txid)
        else:
            for i in vin:
                cps.add((i.get("prevout") or {}).get("scriptpubkey_address"), "received_from", txid)
        recent.append(
            {
                "hash": txid, "time": _iso(status.get("block_time")), "confirmed": bool(status.get("confirmed")),
                "block_height": status.get("block_height"), "net_sats": net, "net_btc": sats_to_btc(net),
                "direction": "received" if net > 0 else "sent" if net < 0 else "neutral",
            }
        )
    return recent, cps.result()


class BitcoinEsploraProvider(IntelligenceProvider):
    name = "bitcoin_esplora"
    version = "1.0.0"
    kind = "blockchain_rpc"
    category = "blockchain_verification"
    description = "Look up Bitcoin addresses/transactions through a configured Esplora-compatible REST API."
    capabilities = ["bitcoin:address_lookup", "bitcoin:transaction_lookup", "bitcoin:address_history"]
    evidence_class = EvidenceType.DETERMINISTIC_BLOCKCHAIN
    docs_url = "https://github.com/Blockstream/esplora/blob/master/API.md"

    def supports(self, ref: Reference) -> bool:
        if ref.network != "bitcoin":
            return False
        if ref.entity_type == EntityType.TRANSACTION.value:
            return is_bitcoin_txid(ref.attrs.get("hash", ""))
        if ref.entity_type in (EntityType.WALLET.value, EntityType.TOKEN.value, EntityType.CONTRACT.value):
            return is_bitcoin_address(ref.attrs.get("address", ""))
        return False

    def _base(self, ctx: ProviderContext) -> str:
        base = ctx.settings.btc_esplora_url
        if not base:
            raise ProviderUnavailable("no Bitcoin Esplora API configured (set OSINT_BTC_ESPLORA_URL, e.g. https://mempool.space/api)")
        return base.rstrip("/")

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        ref = data.reference
        assert ref is not None
        base = self._base(ctx)
        client = ctx.client()
        if ref.entity_type == EntityType.TRANSACTION.value:
            tx = get_json(client, f"{base}/tx/{ref.attrs['hash']}", what="esplora tx")
            doc: dict = {"kind": "transaction", "found": tx is not None, "tx": tx}
        else:
            addr = ref.attrs["address"]
            stats = get_json(client, f"{base}/address/{addr}", what="esplora address")
            txs = get_json(client, f"{base}/address/{addr}/txs", what="esplora address txs") if stats is not None else []
            doc = {"kind": "address", "found": stats is not None, "stats": stats, "txs": (txs or [])[: ctx.settings.onchain_history_limit]}
        return RawResult(
            raw=json.dumps(doc, sort_keys=True).encode(), parsed=doc, log=f"found={doc['found']}",
            parameters={"esplora_base": base, "history_limit": ctx.settings.onchain_history_limit},
        )

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        ref = data.reference
        assert ref is not None
        now = ctx.now or datetime.now(UTC)
        doc = raw.parsed
        src = SourceRef(
            SourceType.BLOCKCHAIN_TRANSACTION.value if doc["kind"] == "transaction" else SourceType.BLOCKCHAIN_ADDRESS.value,
            ref.canonical_id, None, ref.canonical_id, ref.display_name, "esplora",
        )
        if doc["kind"] == "transaction":
            if not doc["found"]:
                return [NormalizedObservation(
                    source=src, subject=ref, observation_type=ObservationType.BLOCKCHAIN_LOOKUP_FAILED.value, observed_at=now,
                    payload={"verified": False, "reason": "transaction not found by the configured Esplora API"}, external_id=ref.canonical_id,
                )]
            tx = doc["tx"]
            status = tx.get("status") or {}
            payload = {
                "verified": True, "network": "bitcoin", "hash": tx.get("txid"), "confirmed": bool(status.get("confirmed")),
                "block_height": status.get("block_height"), "block_time": _iso(status.get("block_time")), "fee_sats": tx.get("fee"),
                "input_count": len(tx.get("vin") or []), "output_count": len(tx.get("vout") or []),
                "outputs": [{"address": o.get("scriptpubkey_address"), "sats": o.get("value")} for o in (tx.get("vout") or [])][:50],
                "interpretation": "existence + outputs only; inputs are not attributed to a single owner",
            }
            bt = status.get("block_time")
            return [NormalizedObservation(
                source=src, subject=ref, observation_type=ObservationType.BLOCKCHAIN_TRANSACTION.value,
                observed_at=datetime.fromtimestamp(bt, UTC) if bt else now, payload=payload, external_id=ref.canonical_id,
            )]
        if not doc["found"]:
            payload = {"network": "bitcoin", "found": False, "note": NOTE_ADDRESS}
        else:
            addr = ref.attrs["address"]
            chain, mem = doc["stats"].get("chain_stats", {}), doc["stats"].get("mempool_stats", {})
            confirmed = chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)
            unconfirmed = mem.get("funded_txo_sum", 0) - mem.get("spent_txo_sum", 0)
            recent, cps = summarize_btc_txs(addr, doc["txs"], ctx.settings.onchain_history_limit)
            total = chain.get("tx_count", 0) + mem.get("tx_count", 0)
            payload = {
                "network": "bitcoin", "found": True, "address": addr, "source": "esplora",
                "balance": {"confirmed_sats": confirmed, "unconfirmed_delta_sats": unconfirmed, "btc": sats_to_btc(confirmed)},
                "tx_count": total, "recent_transactions": recent, "counterparties": cps,
                "history_truncated": total > len(recent), "note": NOTE_ADDRESS,
            }
        return [NormalizedObservation(
            source=src, subject=ref, observation_type=ObservationType.BLOCKCHAIN_ADDRESS.value, observed_at=now, payload=payload,
            external_id=ref.canonical_id,
        )]

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        try:
            base = self._base(ctx)
        except ProviderUnavailable as exc:
            return HealthStatus("unavailable", str(exc))
        try:
            resp = ctx.client().get(f"{base}/blocks/tip/height")
        except httpx.HTTPError as exc:
            return HealthStatus("unavailable", f"{type(exc).__name__}")
        if resp.status_code != 200:
            return HealthStatus("unavailable", f"HTTP {resp.status_code}")
        return HealthStatus("healthy", f"tip height {resp.text.strip()[:12]}")


# ----------------------------------------------------------------------------------------------- EVM (Blockscout)


def blockscout_history(client: httpx.Client, base: str, address: str, limit: int) -> dict:
    """Balance + recent transactions + counterparties from a Blockscout-compatible REST API."""
    base = base.rstrip("/")
    info = get_json(client, f"{base}/v2/addresses/{address}", what="blockscout address")
    if info is None:
        return {"found": False, "recent_transactions": [], "counterparties": []}
    txs = get_json(client, f"{base}/v2/addresses/{address}/transactions", what="blockscout transactions") or {}
    own = address.lower()
    cps = CounterpartyCollector(own)
    recent: list[dict] = []
    for tx in (txs.get("items") or [])[:limit]:
        frm, to = tx.get("from") or {}, tx.get("to") or {}
        f, t = (frm.get("hash") or "").lower(), (to.get("hash") or "").lower()
        h = tx.get("hash", "")
        if f == own:
            cps.add(t, "sent_to", h, is_contract=to.get("is_contract"))
            direction = "sent"
        elif t == own:
            cps.add(f, "received_from", h, is_contract=frm.get("is_contract"))
            direction = "received"
        else:
            direction = "other"
        recent.append(
            {"hash": h, "time": tx.get("timestamp"), "direction": direction, "value_wei": tx.get("value"),
             "status": tx.get("result") or tx.get("status"), "block_number": tx.get("block_number")}
        )
    wei = info.get("coin_balance")
    return {
        "found": True, "source": "blockscout", "balance_wei": wei, "balance_native": _wei_to_native(wei),
        "is_contract": info.get("is_contract"), "ens_domain_name": info.get("ens_domain_name"),
        "explorer_flags": {"is_scam": info.get("is_scam"), "reputation": info.get("reputation")},
        "recent_transactions": recent, "counterparties": cps.result(),
        "history_truncated": bool(txs.get("next_page_params")) or len(txs.get("items") or []) > limit,
    }


def _wei_to_native(wei: str | int | None) -> str | None:
    if wei is None:
        return None
    n = int(wei)
    return f"{n // 10**18}.{n % 10**18:018d}".rstrip("0").rstrip(".") or "0"


# ----------------------------------------------------------------------------------------------- Solana

_SOLANA_INFRA = {
    "11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Vote111111111111111111111111111111111111111", "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
}


def solana_history(rpc_call, client: httpx.Client, url: str, address: str, limit: int) -> dict:
    """Recent signatures + same-transaction participants whose balance changed. `rpc_call` is injected to avoid a cycle."""
    sigs = rpc_call(client, url, "getSignaturesForAddress", [address, {"limit": limit}]) or []
    if not isinstance(sigs, list):
        raise ProviderError("unexpected getSignaturesForAddress response shape (not a list)")
    cps = CounterpartyCollector(address)
    recent: list[dict] = []
    errors: list[str] = []
    for s in sigs[:limit]:
        sig = s.get("signature", "")
        recent.append({"hash": sig, "time": _iso(s.get("blockTime")), "slot": s.get("slot"), "failed": s.get("err") is not None})
        if s.get("err") is not None:
            continue
        try:
            tx = rpc_call(client, url, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])
        except ProviderError as exc:
            errors.append(f"{sig[:8]}…: {exc}")
            continue
        if not tx or not isinstance(tx, dict):
            continue
        meta = tx.get("meta") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k for k in (tx.get("transaction") or {}).get("message", {}).get("accountKeys", [])]
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        for i, key in enumerate(keys):
            if i < len(pre) and i < len(post) and post[i] != pre[i] and key not in _SOLANA_INFRA and is_solana_address(key or ""):
                cps.add(key, "same_transaction_balance_change", sig)
        owners_pre = {(b.get("accountIndex")): b for b in (meta.get("preTokenBalances") or [])}
        for b in meta.get("postTokenBalances") or []:
            before = owners_pre.get(b.get("accountIndex"))
            if (before or {}).get("uiTokenAmount", {}).get("amount") != (b.get("uiTokenAmount") or {}).get("amount"):
                owner = b.get("owner")
                if owner and owner not in _SOLANA_INFRA and is_solana_address(owner):
                    cps.add(owner, "same_transaction_token_balance_change", sig)
    return {"recent_transactions": recent, "counterparties": cps.result(), "history_truncated": len(sigs) >= limit, "errors": errors}


__all__ = [
    "BitcoinEsploraProvider", "blockscout_history", "solana_history", "summarize_btc_txs", "get_json", "NOTE_ADDRESS",
    "CounterpartyCollector", "_address_ref",
]
