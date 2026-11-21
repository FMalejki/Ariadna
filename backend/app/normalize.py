"""Deterministic reference parsing: URLs, social identifiers, blockchain addresses / hashes.

Rules (spec 3.1.D): never use an LLM where deterministic validation exists. Every function here is pure
and unit-tested. Bump PARSER_VERSION when output for the same input can change - it is stored on every
observation so historical results stay interpretable.

Canonical identifier formats (globally unique in `entities.canonical_identifier`):
  x:<handle>                      X/Twitter account (lowercase)
  x:post:<id>                     X/Twitter post
  telegram:<name>                 public Telegram channel/user name (lowercase)
  telegram:<name>/<msgid>         public Telegram post
  github:<user> | instagram:<user> | tiktok:<user> | reddit:<user> | medium:<user> | warpcast:<user>
  youtube:<@handle|channel-id>
  website:<host>/<path>[?query]   normalized web page (no scheme, no www, no tracking params)
  domain:<host>                   host without leading www.
  addr:<network>:<address>        blockchain address (wallet/token/contract share the identifier space,
                                  because on one network an address is exactly one on-chain object)
  tx:<network>:<hash>             blockchain transaction
Network "evm" means "an EVM chain, not yet determined" and is deliberately NOT merged with a concrete
EVM network (the same string on different networks must not silently resolve to one entity).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.domain import EntityType, SourceType

PARSER_VERSION = "1.1.0"  # 1.1.0: Bitcoin addresses (base58check + bech32/bech32m with checksums)


class InvalidReference(ValueError):
    pass


# --------------------------------------------------------------------------- base58 / chains

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}


def base58_decode(value: str) -> bytes:
    if not value:
        raise InvalidReference("empty base58 string")
    num = 0
    for ch in value:
        try:
            num = num * 58 + _B58_INDEX[ch]
        except KeyError as exc:
            raise InvalidReference(f"invalid base58 character {ch!r}") from exc
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(value) - len(value.lstrip("1"))
    return b"\x00" * pad + raw


def is_solana_address(value: str) -> bool:
    if not 32 <= len(value) <= 44:
        return False
    try:
        return len(base58_decode(value)) == 32
    except InvalidReference:
        return False


def is_solana_signature(value: str) -> bool:
    if not 86 <= len(value) <= 88:
        return False
    try:
        return len(base58_decode(value)) == 64
    except InvalidReference:
        return False


def is_bitcoin_base58_address(value: str) -> bool:
    """Legacy P2PKH (1...) / P2SH (3...) address: 25 decoded bytes, version 0x00/0x05, valid double-SHA256 checksum."""
    if not 26 <= len(value) <= 35 or value[0] not in "13":
        return False
    try:
        raw = base58_decode(value)
    except InvalidReference:
        return False
    if len(raw) != 25 or raw[0] not in (0x00, 0x05):
        return False
    return hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]


_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def is_bitcoin_bech32_address(value: str) -> bool:
    """SegWit address (bc1...). Mixed case is invalid; checksum is bech32 (witness v0) or bech32m (v1+)."""
    if value != value.lower() and value != value.upper():
        return False
    v = value.lower()
    if not v.startswith("bc1") or not 14 <= len(v) <= 74:
        return False
    data_part = v[3:]
    if any(c not in _BECH32_CHARSET for c in data_part):
        return False
    data = [_BECH32_CHARSET.index(c) for c in data_part]
    hrp_exp = [ord(c) >> 5 for c in "bc"] + [0] + [ord(c) & 31 for c in "bc"]
    const = _bech32_polymod(hrp_exp + data)
    witness_version = data[0]
    if witness_version == 0:
        return const == _BECH32_CONST and _program_len(data) in (20, 32)
    if witness_version > 16:
        return False
    return const == _BECH32M_CONST and 2 <= _program_len(data) <= 40


def _program_len(data: list[int]) -> int:
    """Length in bytes of the witness program carried by 5-bit `data` (version + program + 6-symbol checksum)."""
    payload_symbols = len(data) - 1 - 6
    return (payload_symbols * 5) // 8


def is_bitcoin_address(value: str) -> bool:
    return is_bitcoin_base58_address(value) or is_bitcoin_bech32_address(value)


def normalize_bitcoin_address(value: str) -> str:
    """bech32 is case-insensitive (canonical: lowercase); base58check is case-sensitive."""
    return value.lower() if value[:3].lower() == "bc1" else value


_BTC_TXID = re.compile(r"^[0-9a-fA-F]{64}$")


def is_bitcoin_txid(value: str) -> bool:
    return bool(_BTC_TXID.match(value))


_EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_EVM_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


def is_evm_address(value: str) -> bool:
    return bool(_EVM_ADDR.match(value))


def is_evm_tx_hash(value: str) -> bool:
    return bool(_EVM_HASH.match(value))


EVM_NETWORKS = {"evm", "ethereum", "base", "bsc", "polygon", "arbitrum", "optimism", "avalanche"}

# explorer host -> network
EXPLORER_HOSTS: dict[str, str] = {
    "etherscan.io": "ethereum",
    "basescan.org": "base",
    "bscscan.com": "bsc",
    "polygonscan.com": "polygon",
    "arbiscan.io": "arbitrum",
    "optimistic.etherscan.io": "optimism",
    "snowtrace.io": "avalanche",
    "solscan.io": "solana",
    "explorer.solana.com": "solana",
    "solana.fm": "solana",
    "mempool.space": "bitcoin",
    "blockstream.info": "bitcoin",
}


def address_identifier(network: str, address: str) -> str:
    if network == "bitcoin":
        return f"addr:bitcoin:{normalize_bitcoin_address(address)}"
    return f"addr:{network}:{address.lower() if network in EVM_NETWORKS else address}"


def tx_identifier(network: str, tx_hash: str) -> str:
    return f"tx:{network}:{tx_hash.lower() if network in EVM_NETWORKS or network == 'bitcoin' else tx_hash}"


# --------------------------------------------------------------------------- URL normalization

_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "ref_url",
    "s", "t", "si", "feature", "_hsenc", "_hsmi", "spm", "cmpid",
}
_SHORTENERS = {"t.co", "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "is.gd", "cutt.ly", "rb.gy", "lnkd.in"}
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]\}]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)]}>'\""


@dataclass(frozen=True)
class NormalizedUrl:
    canonical_url: str  # https://host/path?query
    key: str  # host/path?query (scheme-less, www-less)
    host: str
    domain: str
    path: str
    query: str
    is_shortener: bool


def normalize_url(raw: str) -> NormalizedUrl:
    raw = raw.strip().rstrip(_TRAILING_PUNCT)
    if not raw:
        raise InvalidReference("empty URL")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidReference(f"unsupported URL scheme {parts.scheme!r}")
    if parts.username or parts.password:
        raise InvalidReference("URL contains credentials; refusing to store")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidReference("URL has no host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidReference("invalid internationalized host") from exc
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise InvalidReference("private/loopback address rejected")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")) or "." not in host:
        raise InvalidReference(f"non-public host {host!r} rejected")
    domain = host[4:] if host.startswith("www.") else host
    port = parts.port
    if port in (80, 443, None):
        port_s = ""
    else:
        port_s = f":{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query_items = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    query = urlencode(query_items)
    netloc = domain + port_s
    key = netloc + path + (f"?{query}" if query else "")
    canonical = f"https://{key}"
    return NormalizedUrl(canonical, key, host, domain, path, query, domain in _SHORTENERS)


# --------------------------------------------------------------------------- Reference model


@dataclass(frozen=True)
class Reference:
    """A deterministically parsed pointer to something. Not yet an entity - a candidate."""

    entity_type: str  # EntityType value
    canonical_id: str
    display_name: str
    source_type: str  # SourceType value used if the analyst/discovery registers it as a source
    raw: str
    platform: str | None = None
    network: str | None = None
    url: str | None = None
    attrs: dict = field(default_factory=dict)
    method: str = "deterministic_parser"

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity_type, self.canonical_id)


@dataclass(frozen=True)
class SkippedReference:
    raw: str
    reason: str


_X_RESERVED = {
    "i", "home", "intent", "share", "search", "hashtag", "explore", "settings", "messages", "notifications",
    "login", "signup", "tos", "privacy", "compose", "about", "download", "jobs", "who_to_follow", "lists",
}
_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"}
_X_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_TG_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_TG_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_GH_RESERVED = {
    "orgs", "topics", "features", "marketplace", "sponsors", "settings", "login", "about", "pricing",
    "explore", "notifications", "pulls", "issues", "search", "collections", "events", "enterprise",
}
_SIMPLE_HANDLE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
_PROFILE_HOSTS = {
    "github.com": "github",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "warpcast.com": "warpcast",
    "medium.com": "medium",
    "reddit.com": "reddit",
}


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _from_social_url(u: NormalizedUrl, raw: str) -> Reference | SkippedReference | None:
    host = u.domain
    segs = _segments(u.path)
    if host in _X_HOSTS:
        if not segs or segs[0].lower() in _X_RESERVED:
            return SkippedReference(raw, "x.com non-profile path")
        handle = segs[0]
        if not _X_HANDLE.match(handle):
            return SkippedReference(raw, "invalid X handle")
        h = handle.lower()
        if len(segs) >= 3 and segs[1].lower() in ("status", "statuses") and segs[2].isdigit():
            return Reference(
                EntityType.POST.value, f"x:post:{segs[2]}", f"X post {segs[2]}", SourceType.SOCIAL_POST.value,
                raw, platform="x", url=f"https://x.com/{h}/status/{segs[2]}",
                attrs={"author_handle": h, "post_id": segs[2]},
            )
        return Reference(
            EntityType.SOCIAL_ACCOUNT.value, f"x:{h}", f"@{handle}", SourceType.SOCIAL_ACCOUNT.value, raw,
            platform="x", url=f"https://x.com/{h}", attrs={"handle": h},
        )
    if host in _TG_HOSTS:
        if segs and segs[0] == "s":  # web preview form t.me/s/<name>
            segs = segs[1:]
        if not segs:
            return SkippedReference(raw, "telegram link without channel")
        first = segs[0]
        if first.startswith("+") or first.lower() in ("joinchat", "addstickers", "share", "proxy", "socks", "login"):
            return SkippedReference(raw, "private invite / non-public telegram link; not registered")
        if not _TG_NAME.match(first):
            return SkippedReference(raw, "invalid telegram name")
        n = first.lower()
        if len(segs) >= 2 and segs[1].isdigit():
            return Reference(
                EntityType.POST.value, f"telegram:{n}/{segs[1]}", f"t.me/{n}/{segs[1]}", SourceType.SOCIAL_POST.value,
                raw, platform="telegram", url=f"https://t.me/{n}/{segs[1]}",
                attrs={"channel": n, "post_id": segs[1]},
            )
        return Reference(
            EntityType.CHANNEL.value, f"telegram:{n}", f"t.me/{n}", SourceType.CHANNEL.value, raw,
            platform="telegram", url=f"https://t.me/{n}", attrs={"name": n},
        )
    if host in ("youtube.com", "m.youtube.com"):
        if segs and segs[0].startswith("@") and _SIMPLE_HANDLE.match(segs[0][1:] or "!"):
            n = segs[0].lower()
            return Reference(
                EntityType.CHANNEL.value, f"youtube:{n}", f"YouTube {segs[0]}", SourceType.CHANNEL.value, raw,
                platform="youtube", url=f"https://www.youtube.com/{n}", attrs={"handle": n},
            )
        if len(segs) >= 2 and segs[0] == "channel" and re.match(r"^UC[\w-]{20,24}$", segs[1]):
            return Reference(
                EntityType.CHANNEL.value, f"youtube:{segs[1]}", f"YouTube {segs[1]}", SourceType.CHANNEL.value, raw,
                platform="youtube", url=f"https://www.youtube.com/channel/{segs[1]}", attrs={"channel_id": segs[1]},
            )
        return None
    if host in _PROFILE_HOSTS:
        platform = _PROFILE_HOSTS[host]
        name = None
        if platform == "github" and len(segs) == 1 and segs[0].lower() not in _GH_RESERVED:
            name = segs[0]
        elif platform in ("instagram", "warpcast") and len(segs) == 1 and segs[0].lower() not in {"p", "reel", "explore", "accounts", "~"}:
            name = segs[0]
        elif platform == "tiktok" and len(segs) == 1 and segs[0].startswith("@"):
            name = segs[0][1:]
        elif platform == "medium" and len(segs) >= 1 and segs[0].startswith("@"):
            name = segs[0][1:]
        elif platform == "reddit" and len(segs) >= 2 and segs[0].lower() in ("user", "u"):
            name = segs[1]
        if name and _SIMPLE_HANDLE.match(name):
            n = name.lower()
            return Reference(
                EntityType.PUBLIC_PROFILE.value, f"{platform}:{n}", f"{platform}/{name}",
                SourceType.PUBLIC_PROFILE.value, raw, platform=platform, url=u.canonical_url, attrs={"handle": n},
            )
        return None
    return None


def _from_explorer_url(u: NormalizedUrl, raw: str) -> Reference | None:
    network = EXPLORER_HOSTS.get(u.domain)
    if network is None:
        return None
    segs = _segments(u.path)
    if len(segs) < 2:
        return None
    kind, value = segs[0].lower(), segs[1]
    if kind == "tx":
        if network == "solana" and is_solana_signature(value):
            return _tx_ref(network, value, raw, u.canonical_url)
        if network == "bitcoin" and is_bitcoin_txid(value):
            return _tx_ref(network, value, raw, u.canonical_url)
        if network not in ("solana", "bitcoin") and is_evm_tx_hash(value):
            return _tx_ref(network, value, raw, u.canonical_url)
        return None
    if kind in ("address", "account", "token"):
        if network == "solana":
            valid = is_solana_address(value)
        elif network == "bitcoin":
            valid = is_bitcoin_address(value)
        else:
            valid = is_evm_address(value)
        if not valid:
            return None
        address_kind = "token" if kind == "token" else "unknown"
        return _address_ref(network, value, raw, u.canonical_url, address_kind)
    return None


def _address_ref(network: str, address: str, raw: str, url: str | None, address_kind: str = "unknown") -> Reference:
    ident = address_identifier(network, address)
    etype = EntityType.TOKEN.value if address_kind == "token" else EntityType.WALLET.value
    shown = (
        normalize_bitcoin_address(address) if network == "bitcoin"
        else address.lower() if network in EVM_NETWORKS else address
    )
    return Reference(
        etype, ident, f"{shown[:6]}…{shown[-4:]} ({network})", SourceType.BLOCKCHAIN_ADDRESS.value, raw,
        network=network, url=url,
        attrs={"address": shown, "address_kind": address_kind, "network_determined": network != "evm"},
    )


def _tx_ref(network: str, tx_hash: str, raw: str, url: str | None = None) -> Reference:
    h = tx_hash.lower() if network in EVM_NETWORKS or network == "bitcoin" else tx_hash
    return Reference(
        EntityType.TRANSACTION.value, tx_identifier(network, h), f"tx {h[:8]}…{h[-6:]} ({network})",
        SourceType.BLOCKCHAIN_TRANSACTION.value, raw, network=network, url=url,
        attrs={"hash": h, "network_determined": network != "evm"},
    )


def classify_url(raw: str) -> Reference | SkippedReference:
    """Classify one URL into the most specific deterministic reference it denotes."""
    try:
        u = normalize_url(raw)
    except InvalidReference as exc:
        return SkippedReference(raw, str(exc))
    if u.is_shortener:
        return SkippedReference(raw, "URL shortener: destination unknown without fetching; not registered")
    social = _from_social_url(u, raw)
    if social is not None:
        return social
    chain = _from_explorer_url(u, raw)
    if chain is not None:
        return chain
    return Reference(
        EntityType.WEBSITE.value, f"website:{u.key}", u.key, SourceType.WEBSITE.value, raw, url=u.canonical_url,
        attrs={"host": u.domain, "path": u.path},
    )


def domain_reference(host: str) -> Reference:
    d = host.lower()
    d = d[4:] if d.startswith("www.") else d
    return Reference(
        EntityType.DOMAIN.value, f"domain:{d}", d, SourceType.WEBSITE.value, d, url=f"https://{d}", attrs={"host": d}
    )


# --------------------------------------------------------------------------- text extraction

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MENTION_RE = re.compile(r"(?<![\w@/.])@([A-Za-z0-9_]{1,15})\b")
_EVM_ADDR_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z])0x[0-9a-fA-F]{40}(?![0-9a-zA-Z])")
_EVM_HASH_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z])0x[0-9a-fA-F]{64}(?![0-9a-zA-Z])")
_B58_RUN = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,88}(?![1-9A-HJ-NP-Za-km-z])")
_BTC_B58_RUN = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[13][1-9A-HJ-NP-Za-km-z]{25,34}(?![1-9A-HJ-NP-Za-km-z])")
_BTC_BECH32_RUN = re.compile(r"(?<![0-9A-Za-z])(?:bc1|BC1)[0-9A-Za-z]{11,71}(?![0-9A-Za-z])")


def extract_references(
    text: str, platform_hint: str | None = None
) -> tuple[list[Reference], list[SkippedReference]]:
    """Extract deterministic references from free text.

    * Email addresses are ignored on purpose (private contact detail - spec 1.3).
    * `@mentions` become accounts only when `platform_hint == "x"` (ambiguous otherwise).
    * Bare base58 / 0x strings are validated structurally (length + decoded byte length).
    * Output is de-duplicated and ordered by first appearance.
    """
    found: dict[tuple[str, str], Reference] = {}
    skipped: list[SkippedReference] = []

    def add(ref: Reference | SkippedReference | None) -> None:
        if ref is None:
            return
        if isinstance(ref, SkippedReference):
            skipped.append(ref)
        else:
            found.setdefault(ref.key, ref)

    remainder = text
    for m in _URL_RE.finditer(text):
        add(classify_url(m.group(0)))
    remainder = _URL_RE.sub(" ", remainder)
    remainder = _EMAIL_RE.sub(" ", remainder)

    for m in _EVM_HASH_IN_TEXT.finditer(remainder):
        add(_tx_ref("evm", m.group(0), m.group(0)))
    remainder_no_hash = _EVM_HASH_IN_TEXT.sub(" ", remainder)
    for m in _EVM_ADDR_IN_TEXT.finditer(remainder_no_hash):
        add(_address_ref("evm", m.group(0), m.group(0), None))
    for m in _B58_RUN.finditer(remainder_no_hash):
        token = m.group(0)
        if is_solana_signature(token):
            add(_tx_ref("solana", token, token))
        elif is_solana_address(token):
            add(_address_ref("solana", token, token, None))
    # Bitcoin: checksum validation makes false positives on prose/ids practically impossible.
    for m in _BTC_B58_RUN.finditer(remainder_no_hash):
        if is_bitcoin_base58_address(m.group(0)):
            add(_address_ref("bitcoin", m.group(0), m.group(0), None))
    for m in _BTC_BECH32_RUN.finditer(remainder_no_hash):
        if is_bitcoin_bech32_address(m.group(0)):
            add(_address_ref("bitcoin", m.group(0), m.group(0), None))
    if platform_hint == "x":
        for m in _MENTION_RE.finditer(remainder):
            h = m.group(1)
            add(
                Reference(
                    EntityType.SOCIAL_ACCOUNT.value, f"x:{h.lower()}", f"@{h}", SourceType.SOCIAL_ACCOUNT.value,
                    f"@{h}", platform="x", url=f"https://x.com/{h.lower()}", attrs={"handle": h.lower()},
                )
            )
    return list(found.values()), skipped


@dataclass(frozen=True)
class AddressSpan:
    network: str  # "evm" (chain undetermined) | "solana" | "bitcoin"
    address: str  # canonical form (EVM lowercased, bech32 lowercased)
    raw: str
    start: int
    end: int


def _looks_random(token: str) -> bool:
    """Guard for base58 Solana candidates in free text: real keys are random, so they mix character classes."""
    classes = sum(
        (any(c.isupper() for c in token), any(c.islower() for c in token), any(c.isdigit() for c in token))
    )
    return classes >= 2


def find_address_spans(text: str) -> list[AddressSpan]:
    """Locate validated blockchain addresses (EVM, Solana, Bitcoin) in free text, with positions.

    Deterministic and structural only: EVM = 0x + 40 hex (not a 64-hex hash); Solana = base58 that decodes to
    32 bytes and is not a signature; Bitcoin = base58check or bech32/bech32m with a valid checksum. Positions
    refer to `text` (URLs are NOT stripped, so explorer links such as .../address/<addr> are found too).
    A match is a mention in text - it says nothing about who controls the address.
    """
    spans: list[AddressSpan] = []
    for m in _EVM_ADDR_IN_TEXT.finditer(text):
        spans.append(AddressSpan("evm", m.group(0).lower(), m.group(0), m.start(), m.end()))
    for m in _B58_RUN.finditer(text):
        tok = m.group(0)
        if is_solana_address(tok) and _looks_random(tok):
            spans.append(AddressSpan("solana", tok, tok, m.start(), m.end()))
    for m in _BTC_B58_RUN.finditer(text):
        if is_bitcoin_base58_address(m.group(0)):
            spans.append(AddressSpan("bitcoin", m.group(0), m.group(0), m.start(), m.end()))
    for m in _BTC_BECH32_RUN.finditer(text):
        if is_bitcoin_bech32_address(m.group(0)):
            spans.append(AddressSpan("bitcoin", normalize_bitcoin_address(m.group(0)), m.group(0), m.start(), m.end()))
    spans.sort(key=lambda sp: sp.start)
    return spans


def classify_seed(raw: str, platform_hint: str | None = None) -> Reference:
    """Classify an analyst-supplied seed (URL, address, tx hash, @handle). Raises InvalidReference."""
    raw = raw.strip()
    if not raw:
        raise InvalidReference("empty seed")
    if "://" in raw or re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", raw, re.IGNORECASE):
        ref = classify_url(raw)
        if isinstance(ref, SkippedReference):
            raise InvalidReference(ref.reason)
        return ref
    if is_evm_tx_hash(raw):
        return _tx_ref("evm", raw, raw)
    if is_evm_address(raw):
        return _address_ref("evm", raw, raw, None)
    if is_bitcoin_address(raw):
        return _address_ref("bitcoin", raw, raw, None)
    if is_solana_signature(raw):
        return _tx_ref("solana", raw, raw)
    if is_solana_address(raw):
        return _address_ref("solana", raw, raw, None)
    m = re.match(r"^@?([A-Za-z0-9_]{1,15})$", raw)
    if m and platform_hint == "x":
        h = m.group(1)
        return Reference(
            EntityType.SOCIAL_ACCOUNT.value, f"x:{h.lower()}", f"@{h}", SourceType.SOCIAL_ACCOUNT.value, raw,
            platform="x", url=f"https://x.com/{h.lower()}", attrs={"handle": h.lower()},
        )
    raise InvalidReference(f"cannot classify {raw!r} deterministically")
