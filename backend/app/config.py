"""Environment-based configuration. No secrets are hardcoded; see .env.example."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OSINT_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./osint.db"
    artifact_dir: str = "./artifacts"
    artifact_retention_days: int | None = None

    # Auth. If jwt_secret is unset an ephemeral secret is generated at startup (tokens die on restart).
    jwt_secret: str | None = None
    jwt_ttl_minutes: int = 480
    admin_username: str = "admin"
    admin_password: str | None = None  # admin user is only bootstrapped when this is set
    cors_origins: list[str] = ["http://localhost:3000"]

    # Discovery policy
    max_discovery_depth: int = 2
    reuse_window_hours: int = 24 * 7
    stale_after_days: int = 30
    auto_approve_categories: list[str] = ["blockchain_verification"]
    default_rate_limit_per_minute: int = 20
    default_quota_per_day: int = 500
    task_max_attempts: int = 3
    task_retry_backoff_seconds: int = 60

    # Graph bounds
    graph_max_nodes: int = 200
    graph_max_path_depth: int = 6

    # Providers
    sherlock_binary: str = "sherlock"
    sherlock_timeout_seconds: int = 120
    evm_rpc_urls: dict[str, str] = {}  # network name -> JSON-RPC URL, e.g. {"ethereum": "https://..."}
    solana_rpc_url: str | None = None
    provider_http_timeout_seconds: float = 20.0
    btc_esplora_url: str | None = None  # Esplora-compatible REST base, e.g. https://mempool.space/api
    evm_explorer_apis: dict[str, str] = {}  # network -> Blockscout-compatible base, e.g. {"ethereum": "https://eth.blockscout.com/api"}
    onchain_history_limit: int = 10  # max recent transactions fetched per address lookup

    # Web search (lead discovery). Tavily is the primary (official, keyed) backend; ddgs is an UNOFFICIAL
    # keyless fallback that scrapes DuckDuckGo and may violate its terms - the UI/docs label it as such.
    tavily_api_key: str | None = None
    tavily_base_url: str = "https://api.tavily.com"
    search_default_backend: str = "auto"  # auto (tavily, then ddgs) | tavily | ddgs
    search_max_results: int = 10

    # Page fetch (analyst-selected URLs only)
    fetch_max_bytes: int = 2_000_000
    fetch_timeout_seconds: float = 15.0
    fetch_max_redirects: int = 3
    fetch_user_agent: str = "OSINTMemoryBot/0.1 (analyst-initiated fetch; honors robots.txt)"
    fetch_blocked_domains: list[str] = [  # platforms we do not scrape (spec 4.2); use authorized import instead
        "x.com", "twitter.com", "t.me", "telegram.me", "telegram.org", "instagram.com", "facebook.com",
        "tiktok.com", "linkedin.com",
    ]

    # LLM (optional). "none" disables all LLM features honestly.
    llm_provider: str = "none"  # none | openai_compatible
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    def public_view(self) -> dict:
        """Settings safe to expose through the API (no secrets)."""
        return {
            "environment": self.environment,
            "max_discovery_depth": self.max_discovery_depth,
            "reuse_window_hours": self.reuse_window_hours,
            "stale_after_days": self.stale_after_days,
            "auto_approve_categories": self.auto_approve_categories,
            "default_rate_limit_per_minute": self.default_rate_limit_per_minute,
            "default_quota_per_day": self.default_quota_per_day,
            "task_max_attempts": self.task_max_attempts,
            "artifact_retention_days": self.artifact_retention_days,
            "graph_max_nodes": self.graph_max_nodes,
            "evm_networks_configured": sorted(self.evm_rpc_urls),
            "solana_rpc_configured": bool(self.solana_rpc_url),
            "btc_esplora_configured": bool(self.btc_esplora_url),
            "evm_explorer_networks": sorted(self.evm_explorer_apis),
            "tavily_configured": bool(self.tavily_api_key),
            "search_default_backend": self.search_default_backend,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_configured": self.llm_provider != "none" and bool(self.llm_base_url and self.llm_model),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
