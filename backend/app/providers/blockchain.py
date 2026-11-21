"""Blockchain verification providers (spec section 8). Provider-backed; we are NOT building an explorer.

STATUS: IMPLEMENTED against the standard JSON-RPC methods below; requires the operator to configure RPC URLs
(OSINT_EVM_RPC_URLS / OSINT_SOLANA_RPC_URL). Nothing is queried and nothing is fabricated when unconfigured:
the provider reports "unavailable". Verified only against mocked JSON-RPC responses in tests/ - NOT yet
exercised against live nodes (see IMPLEMENTATION_STATUS.md).

EVM methods: eth_getTransactionByHash, eth_getTransactionReceipt, eth_getBlockByNumber, eth_getCode,
eth_getTransactionCount, eth_chainId.   Docs: https://ethereum.org/en/developers/docs/apis/json-rpc/
Solana methods: getTransaction (encoding=jsonParsed, maxSupportedTransactionVersion=0), getAccountInfo,
getHealth.   Docs: https://solana.com/docs/rpc

What is and is NOT claimed:
* A found transaction proves that the transaction exists, who signed/sent it, and (EVM) which ERC-20-style
  Transfer logs it emitted / (Solana) which token balances changed. It does NOT prove that any social account
  controls the sender, and swap semantics are NOT inferred (no DEX decoding) - we only record transfers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain import EntityType, EvidenceType, ObservationType, RelationshipType, SourceType
from app.normalize import (
    EVM_NETWORKS,
    Reference,
    _address_ref,
    _tx_ref,
    is_evm_address,
    is_evm_tx_hash,
    is_solana_address,
    is_solana_signature,
)
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    Link,
    NetworkResolution,
    NormalizedObservation,
    ProviderContext,
    ProviderError,
    ProviderInput,
    ProviderUnavailable,
    RawResult,
    SourceRef,
)

import json

from app.providers.onchain import blockscout_history, page_source_key, solana_history  # noqa: E402

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # keccak256("Transfer(address,address,uint256)")


def _hex_int(value: str | None) -> int | None:
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else None


def _topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def rpc_call(client: httpx.Client, url: str, method: str, params: list) -> Any:
    try:
        resp = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    except httpx.TimeoutException as exc:
        raise ProviderError(f"RPC timeout calling {method}") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"RPC transport error calling {method}: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise ProviderError(f"RPC {method} returned HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ProviderError(f"RPC {method} returned non-JSON") from exc
    if body.get("error"):
        raise ProviderError(f"RPC {method} error: {str(body['error'])[:200]}")
    return body.get("result")


# ----------------------------------------------------------------------------------------------- EVM


class EvmRpcProvider(IntelligenceProvider):
    name = "evm_rpc"
    version = "1.0.0"
    kind = "blockchain_rpc"
    category = "blockchain_verification"
    description = "Verify EVM transactions and inspect addresses through configured JSON-RPC endpoints."
    capabilities = ["evm:transaction_lookup", "evm:address_lookup"]
    evidence_class = EvidenceType.DETERMINISTIC_BLOCKCHAIN
    docs_url = "https://ethereum.org/en/developers/docs/apis/json-rpc/"

    def supports(self, ref: Reference) -> bool:
        if ref.network not in EVM_NETWORKS:
            return False
        if ref.entity_type == EntityType.TRANSACTION.value:
            return is_evm_tx_hash(ref.attrs.get("hash", ""))
        if ref.entity_type in (EntityType.WALLET.value, EntityType.TOKEN.value, EntityType.CONTRACT.value):
            return is_evm_address(ref.attrs.get("address", ""))
        return False

    def _networks_for(self, ref: Reference, ctx: ProviderContext) -> dict[str, str]:
        urls = ctx.settings.evm_rpc_urls
        if ref.network == "evm":
            return dict(urls)
        if ref.network in urls:
            return {ref.network: urls[ref.network]}
        return {}

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        ref = data.reference
        assert ref is not None
        nets = self._networks_for(ref, ctx)
        cursor = data.payload.get("cursor") if data.payload else None
        if cursor is not None:  # "load more": one network, one history page, nothing else is re-queried
            if ref.entity_type == EntityType.TRANSACTION.value or not isinstance(cursor, dict) or not isinstance(cursor.get("params"), dict):
                raise ProviderError("invalid EVM history cursor")
            net = str(cursor.get("network"))
            explorer = ctx.settings.evm_explorer_apis.get(net)
            if net not in nets or not explorer:
                raise ProviderUnavailable(f"no history explorer configured for network {net!r} (set OSINT_EVM_EXPLORER_APIS)")
            nets = {net: nets[net]}
        if not nets:
            raise ProviderUnavailable(f"no EVM RPC configured for network {ref.network!r} (set OSINT_EVM_RPC_URLS)")
        client = ctx.client()
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for net, url in nets.items():
            try:
                if cursor is not None:
                    hist = blockscout_history(client, ctx.settings.evm_explorer_apis[net], ref.attrs["address"], ctx.settings.onchain_history_limit, cursor["params"])
                    results[net] = {"history": hist}
                elif ref.entity_type == EntityType.TRANSACTION.value:
                    results[net] = self._lookup_tx(client, url, ref.attrs["hash"])
                else:
                    results[net] = self._lookup_address(client, url, ref.attrs["address"])
                    explorer = ctx.settings.evm_explorer_apis.get(net)
                    if explorer:  # optional, keyless Blockscout-compatible REST for history; failures are reported, not hidden
                        try:
                            results[net]["history"] = blockscout_history(client, explorer, ref.attrs["address"], ctx.settings.onchain_history_limit)
                        except ProviderError as exc:
                            results[net]["history_error"] = str(exc)
            except ProviderError as exc:
                errors[net] = str(exc)
        if not results:
            raise ProviderError("all configured EVM RPC endpoints failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
        doc = {"queried_networks": sorted(nets), "results": results, "errors": errors}
        if cursor is not None:
            doc["page"], doc["cursor"] = True, cursor
        return RawResult(
            raw=json.dumps(doc, sort_keys=True).encode(), parsed=doc,
            log=f"queried {len(nets)} network(s); errors={len(errors)}", parameters={"networks": sorted(nets)},
        )

    @staticmethod
    def _lookup_tx(client: httpx.Client, url: str, tx_hash: str) -> dict:
        tx = rpc_call(client, url, "eth_getTransactionByHash", [tx_hash])
        if tx is None:
            return {"found": False}
        receipt = rpc_call(client, url, "eth_getTransactionReceipt", [tx_hash])
        block_time = None
        if tx.get("blockNumber"):
            block = rpc_call(client, url, "eth_getBlockByNumber", [tx["blockNumber"], False])
            block_time = _hex_int((block or {}).get("timestamp"))
        return {"found": True, "tx": tx, "receipt": receipt, "block_timestamp": block_time}

    @staticmethod
    def _lookup_address(client: httpx.Client, url: str, address: str) -> dict:
        code = rpc_call(client, url, "eth_getCode", [address, "latest"]) or "0x"
        nonce = rpc_call(client, url, "eth_getTransactionCount", [address, "latest"])
        balance = _hex_int(rpc_call(client, url, "eth_getBalance", [address, "latest"]))
        return {
            "has_code": code not in ("0x", "0x0"), "code_size_bytes": (len(code) - 2) // 2, "nonce": _hex_int(nonce),
            "balance_wei": str(balance) if balance is not None else None,
        }

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        ref = data.reference
        assert ref is not None
        now = ctx.now or datetime.now(UTC)
        doc = raw.parsed
        if ref.entity_type == EntityType.TRANSACTION.value:
            return self._normalize_tx(ref, doc, now)
        return self._normalize_address(ref, doc, now)

    def _normalize_tx(self, ref: Reference, doc: dict, now: datetime) -> list[NormalizedObservation]:
        found = {n: r for n, r in doc["results"].items() if r.get("found")}
        h = ref.attrs["hash"]
        if not found:
            payload = {"verified": False, "reason": "transaction not found on queried networks", "queried": doc["queried_networks"], "errors": doc["errors"]}
            return [
                NormalizedObservation(
                    source=SourceRef(SourceType.BLOCKCHAIN_TRANSACTION.value, ref.canonical_id, None, ref.canonical_id, ref.display_name, "rpc"),
                    subject=ref, observation_type=ObservationType.BLOCKCHAIN_LOOKUP_FAILED.value, observed_at=now, payload=payload,
                    external_id=ref.canonical_id,
                )
            ]
        out = []
        for net, r in sorted(found.items()):
            tx, receipt = r["tx"], r.get("receipt") or {}
            concrete = _tx_ref(net, h, ref.raw)
            links: list[Link] = []
            sender = tx.get("from")
            if sender and is_evm_address(sender):
                links.append(
                    Link(
                        _address_ref(net, sender, sender, None), RelationshipType.WALLET_EXECUTED_TRANSACTION.value, direction="in",
                        explanation=f"RPC reports {sender.lower()} as the sender of {h} on {net}", basis=f"eth_getTransactionByHash on {net}",
                        follow_up=True,
                    )
                )
            seen_tokens: set[str] = set()
            for log in receipt.get("logs", []):
                topics = log.get("topics") or []
                if topics[:1] == [TRANSFER_TOPIC] and len(topics) == 3 and is_evm_address(log.get("address", "")):
                    token = log["address"].lower()
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    links.append(
                        Link(
                            _address_ref(net, token, token, None, "token"), RelationshipType.TRANSACTION_TRANSFERRED_TOKEN.value,
                            explanation=f"Receipt log from {token} matches ERC-20 Transfer(address,address,uint256) topic",
                            basis=f"eth_getTransactionReceipt logs on {net}", follow_up=False,
                        )
                    )
            block_ts = r.get("block_timestamp")
            payload = {
                "verified": True, "network": net, "hash": h, "from": sender, "to": tx.get("to"),
                "block_number": _hex_int(tx.get("blockNumber")), "block_timestamp": block_ts,
                "status": _hex_int(receipt.get("status")), "gas_used": _hex_int(receipt.get("gasUsed")),
                "transfer_logs": sorted(seen_tokens), "interpretation": "existence + sender + ERC-20 transfer logs only; swaps/ownership are not inferred",
            }
            out.append(
                NormalizedObservation(
                    source=SourceRef(SourceType.BLOCKCHAIN_TRANSACTION.value, concrete.canonical_id, None, concrete.canonical_id, concrete.display_name, "rpc"),
                    subject=concrete, observation_type=ObservationType.BLOCKCHAIN_TRANSACTION.value,
                    observed_at=datetime.fromtimestamp(block_ts, UTC) if block_ts else now, payload=payload,
                    external_id=concrete.canonical_id, links=links,
                    network_resolutions=[NetworkResolution(ref.canonical_id, concrete, f"RPC for {net} returned this transaction hash")] if ref.network == "evm" else [],
                )
            )
        return out

    def _normalize_address(self, ref: Reference, doc: dict, now: datetime) -> list[NormalizedObservation]:
        payload = {"address": ref.attrs["address"], "per_network": doc["results"], "errors": doc["errors"],
                   "note": "Activity counts are on-chain facts about the address only; no ownership is implied."}
        if doc.get("page"):
            payload["page"] = True
        return [
            NormalizedObservation(
                source=SourceRef(SourceType.BLOCKCHAIN_ADDRESS.value, page_source_key(ref, doc.get("cursor")), None, ref.canonical_id, ref.display_name, "rpc"),
                subject=ref, observation_type=ObservationType.BLOCKCHAIN_ADDRESS.value, observed_at=now, payload=payload,
                external_id=ref.canonical_id,
            )
        ]

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        urls = ctx.settings.evm_rpc_urls
        if not urls:
            return HealthStatus("unavailable", "no EVM RPC URLs configured")
        client = ctx.client()
        bad = []
        for net, url in urls.items():
            try:
                rpc_call(client, url, "eth_chainId", [])
            except ProviderError as exc:
                bad.append(f"{net}: {exc}")
        if not bad:
            return HealthStatus("healthy", f"{len(urls)} network(s) reachable")
        return HealthStatus("degraded" if len(bad) < len(urls) else "unavailable", "; ".join(bad))


# ----------------------------------------------------------------------------------------------- Solana


class SolanaRpcProvider(IntelligenceProvider):
    name = "solana_rpc"
    version = "1.0.0"
    kind = "blockchain_rpc"
    category = "blockchain_verification"
    description = "Verify Solana transactions and inspect accounts through a configured JSON-RPC endpoint."
    capabilities = ["solana:transaction_lookup", "solana:address_lookup"]
    evidence_class = EvidenceType.DETERMINISTIC_BLOCKCHAIN
    docs_url = "https://solana.com/docs/rpc"

    def supports(self, ref: Reference) -> bool:
        if ref.network != "solana":
            return False
        if ref.entity_type == EntityType.TRANSACTION.value:
            return is_solana_signature(ref.attrs.get("hash", ""))
        if ref.entity_type in (EntityType.WALLET.value, EntityType.TOKEN.value, EntityType.CONTRACT.value):
            return is_solana_address(ref.attrs.get("address", ""))
        return False

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        ref = data.reference
        assert ref is not None
        url = ctx.settings.solana_rpc_url
        if not url:
            raise ProviderUnavailable("no Solana RPC configured (set OSINT_SOLANA_RPC_URL)")
        client = ctx.client()
        if ref.entity_type == EntityType.TRANSACTION.value:
            result = rpc_call(client, url, "getTransaction", [ref.attrs["hash"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])
            doc = {"kind": "transaction", "found": result is not None, "result": result}
        else:
            cursor = data.payload.get("cursor") if data.payload else None
            if cursor is not None:  # "load more": signatures older than the last one already shown; no account re-read
                doc = {"kind": "account", "found": True, "result": None, "page": True, "cursor": cursor}
                doc["history"] = solana_history(rpc_call, client, url, ref.attrs["address"], ctx.settings.onchain_history_limit, str((cursor or {}).get("before", "")))
            else:
                result = rpc_call(client, url, "getAccountInfo", [ref.attrs["address"], {"encoding": "base64", "commitment": "finalized"}])
                doc = {"kind": "account", "found": (result or {}).get("value") is not None, "result": result}
                try:
                    doc["history"] = solana_history(rpc_call, client, url, ref.attrs["address"], ctx.settings.onchain_history_limit)
                except ProviderError as exc:
                    doc["history_error"] = str(exc)
        return RawResult(raw=json.dumps(doc, sort_keys=True).encode(), parsed=doc, log=f"found={doc['found']}", parameters={"commitment": "finalized"})

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        ref = data.reference
        assert ref is not None
        now = ctx.now or datetime.now(UTC)
        doc = raw.parsed
        if doc["kind"] == "account":
            value = (doc["result"] or {}).get("value")
            payload = {"found": doc["found"], "lamports": (value or {}).get("lamports"), "executable": (value or {}).get("executable"),
                       "owner_program": (value or {}).get("owner"), "note": "on-chain account facts only; no ownership by any person/account is implied"}
            if doc.get("history"):
                payload.update({"network": "solana", "recent_transactions": doc["history"]["recent_transactions"],
                                "counterparties": doc["history"]["counterparties"], "history_truncated": doc["history"]["history_truncated"],
                                "next_cursor": doc["history"].get("next_cursor")})
            if doc.get("page"):
                payload = {k: v for k, v in payload.items() if k not in ("lamports", "executable", "owner_program")}
                payload["found"], payload["page"] = True, True
            if doc.get("history_error"):
                payload["history_error"] = doc["history_error"]
            return [NormalizedObservation(
                source=SourceRef(SourceType.BLOCKCHAIN_ADDRESS.value, page_source_key(ref, doc.get("cursor")), None, ref.canonical_id, ref.display_name, "rpc"),
                subject=ref, observation_type=ObservationType.BLOCKCHAIN_ADDRESS.value, observed_at=now, payload=payload, external_id=ref.canonical_id,
            )]
        src = SourceRef(SourceType.BLOCKCHAIN_TRANSACTION.value, ref.canonical_id, None, ref.canonical_id, ref.display_name, "rpc")
        if not doc["found"]:
            return [NormalizedObservation(
                source=src, subject=ref, observation_type=ObservationType.BLOCKCHAIN_LOOKUP_FAILED.value, observed_at=now,
                payload={"verified": False, "reason": "transaction not found at finalized commitment"}, external_id=ref.canonical_id,
            )]
        r = doc["result"]
        meta = r.get("meta") or {}
        keys = (r.get("transaction") or {}).get("message", {}).get("accountKeys", [])
        signers = [k["pubkey"] for k in keys if isinstance(k, dict) and k.get("signer")]
        links: list[Link] = []
        if signers and is_solana_address(signers[0]):
            links.append(Link(
                _address_ref("solana", signers[0], signers[0], None), RelationshipType.WALLET_EXECUTED_TRANSACTION.value, direction="in",
                explanation=f"RPC lists {signers[0]} as fee payer / first signer of the transaction", basis="getTransaction accountKeys[0] signer", follow_up=True,
            ))
        mints: set[str] = set()
        for bal in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
            m = bal.get("mint")
            if m and is_solana_address(m):
                mints.add(m)
        for m in sorted(mints):
            links.append(Link(
                _address_ref("solana", m, m, None, "token"), RelationshipType.TRANSACTION_TRANSFERRED_TOKEN.value,
                explanation=f"Token balances for mint {m} appear in the transaction's pre/post token balances",
                basis="getTransaction meta.pre/postTokenBalances", follow_up=False,
            ))
        block_time = r.get("blockTime")
        payload = {"verified": True, "slot": r.get("slot"), "block_time": block_time, "err": meta.get("err"), "fee": meta.get("fee"),
                   "signers": signers, "token_mints": sorted(mints), "interpretation": "existence + signer + token balance participants only; swaps/ownership are not inferred"}
        return [NormalizedObservation(
            source=src, subject=ref, observation_type=ObservationType.BLOCKCHAIN_TRANSACTION.value,
            observed_at=datetime.fromtimestamp(block_time, UTC) if block_time else now, payload=payload, external_id=ref.canonical_id, links=links,
        )]

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        url = ctx.settings.solana_rpc_url
        if not url:
            return HealthStatus("unavailable", "no Solana RPC URL configured")
        try:
            res = rpc_call(ctx.client(), url, "getHealth", [])
        except ProviderError as exc:
            return HealthStatus("unavailable", str(exc))
        return HealthStatus("healthy" if res == "ok" else "degraded", f"getHealth -> {res}")
