"""URL normalization, identifier validation, reference extraction (deterministic layer). Synthetic data only."""

from __future__ import annotations

import pytest

from app.normalize import (
    InvalidReference,
    SkippedReference,
    classify_seed,
    classify_url,
    extract_references,
    is_evm_address,
    is_evm_tx_hash,
    is_solana_address,
    is_solana_signature,
    normalize_url,
)
from tests.conftest import EVM_A, EVM_TX, SOL_ADDR


class TestUrlNormalization:
    def test_strips_tracking_params_www_fragment_and_trailing_slash(self):
        a = normalize_url("https://www.Example.com/path/?utm_source=x&fbclid=abc&b=2&a=1#frag")
        b = normalize_url("example.com/path?a=1&b=2")
        assert a.canonical_url == b.canonical_url == "https://example.com/path?a=1&b=2"

    def test_rejects_loopback_private_and_credentials(self):
        for bad in ("http://127.0.0.1/x", "http://localhost/x", "http://10.0.0.5/x", "https://user:pw@example.com/", "ftp://example.com/x"):
            with pytest.raises(InvalidReference):
                normalize_url(bad)

    def test_flags_shorteners(self):
        assert normalize_url("https://bit.ly/abc").is_shortener


class TestSocialUrls:
    def test_twitter_and_x_share_one_canonical_id(self):
        a = classify_url("https://twitter.com/SomeUser")
        b = classify_url("https://x.com/someuser/")
        assert a.canonical_id == b.canonical_id == "x:someuser"
        assert a.entity_type == "social_account"

    def test_x_post(self):
        r = classify_url("https://x.com/someuser/status/1234567890")
        assert r.entity_type == "post"
        assert r.canonical_id == "x:post:1234567890"

    def test_telegram_public_channel_vs_private_invite(self):
        assert classify_url("https://t.me/examplechannel").canonical_id == "telegram:examplechannel"
        assert isinstance(classify_url("https://t.me/+AbCdEf123"), SkippedReference)

    def test_shortener_is_skipped_not_followed(self):
        assert isinstance(classify_url("https://t.co/abc"), SkippedReference)


class TestIdentifierValidation:
    def test_evm(self):
        assert is_evm_address(EVM_A)
        assert not is_evm_address("0x1234")
        assert is_evm_tx_hash(EVM_TX)
        assert not is_evm_tx_hash(EVM_A)

    def test_solana(self):
        assert is_solana_address(SOL_ADDR)
        assert not is_solana_address("0OIl" * 8)  # characters outside the base58 alphabet
        assert not is_solana_address("short")
        assert not is_solana_signature(SOL_ADDR)

    def test_evm_ids_are_case_insensitive(self):
        upper = "0x" + "AB" * 20
        a = classify_seed(upper)
        b = classify_seed(upper.lower())
        assert a.canonical_id == b.canonical_id


class TestSeedClassification:
    def test_wallet_tx_domain_url(self):
        assert classify_seed(EVM_A).entity_type == "wallet"
        assert classify_seed(EVM_TX).entity_type == "transaction"
        assert classify_seed("https://example.com/about").entity_type == "website"

    def test_handle_needs_platform_hint(self):
        with pytest.raises(InvalidReference):
            classify_seed("@someuser")
        assert classify_seed("@someuser", "x").canonical_id == "x:someuser"

    def test_garbage_rejected(self):
        with pytest.raises(InvalidReference):
            classify_seed("not a thing")
        with pytest.raises(InvalidReference):
            classify_seed("")


class TestExtraction:
    def test_extracts_addresses_urls_and_ignores_emails(self):
        text = f"buy at {EVM_A} see https://x.com/someuser/status/99 contact me@example.com tx {EVM_TX}"
        refs, _ = extract_references(text)
        ids = {r.canonical_id for r in refs}
        assert f"addr:evm:{EVM_A}" in ids
        assert "x:post:99" in ids
        assert f"tx:evm:{EVM_TX}" in ids
        assert not any("example.com" in i and "me@" in i for i in ids)

    def test_mentions_only_with_platform_hint(self):
        refs, _ = extract_references("thanks @alice")
        assert refs == []
        refs, _ = extract_references("thanks @alice", "x")
        assert [r.canonical_id for r in refs] == ["x:alice"]

    def test_deduplicates(self):
        refs, _ = extract_references(f"{EVM_A} {EVM_A.upper().replace('0X', '0x')}")
        assert len(refs) == 1


# --------------------------------------------------------------------------- Bitcoin (BIP173/BIP350 vectors)

BTC_P2PKH = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
BTC_BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BTC_TAPROOT = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"


def test_bitcoin_valid_addresses():
    from app.normalize import is_bitcoin_address

    for a in (BTC_P2PKH, BTC_P2SH, BTC_BECH32, BTC_BECH32.upper(), BTC_TAPROOT):
        assert is_bitcoin_address(a), a


def test_bitcoin_invalid_addresses():
    from app.normalize import is_bitcoin_address

    assert not is_bitcoin_address(BTC_P2PKH[:-1] + "b")  # base58check checksum
    assert not is_bitcoin_address(BTC_BECH32[:-1] + "p")  # bech32 checksum
    assert not is_bitcoin_address("bc1Qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")  # mixed case
    assert not is_bitcoin_address("hello world")
    assert not is_bitcoin_address("")


def test_bitcoin_bech32_is_case_normalized():
    ref = classify_seed(BTC_BECH32.upper())
    assert ref.canonical_id == f"addr:bitcoin:{BTC_BECH32}"


def test_bitcoin_seed_and_explorer_url():
    assert classify_seed(BTC_P2PKH).canonical_id == f"addr:bitcoin:{BTC_P2PKH}"
    ref = classify_seed(f"https://mempool.space/address/{BTC_P2SH}")
    assert ref.canonical_id == f"addr:bitcoin:{BTC_P2SH}" and ref.network == "bitcoin"
    txid = "a" * 64
    tx = classify_seed(f"https://blockstream.info/tx/{txid}")
    assert tx.canonical_id == f"tx:bitcoin:{txid}"


def test_bitcoin_extracted_from_text_without_false_positives():
    refs, _ = extract_references(
        f"Wpłata na {BTC_P2PKH}, alternatywnie {BTC_BECH32}. Zamówienie 1234567890123456789012345678 oraz słowo Bitcoin."
    )
    ids = {r.canonical_id for r in refs}
    assert ids == {f"addr:bitcoin:{BTC_P2PKH}", f"addr:bitcoin:{BTC_BECH32}"}
