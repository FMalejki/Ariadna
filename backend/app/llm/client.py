"""LLM client abstraction. `none` (default) disables LLM features honestly instead of faking output."""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import Settings, get_settings


class LLMDisabled(RuntimeError):
    """No LLM is configured. Callers surface this to the user; there is no silent fallback."""


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str) -> str: ...


class NullClient:
    provider = "none"
    model = "none"

    def complete(self, system: str, user: str) -> str:
        raise LLMDisabled("LLM is not configured (OSINT_LLM_PROVIDER=none)")


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint (self-hosted or hosted)."""

    provider = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float = 60.0, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._http = http

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        client = self._http or httpx.Client(timeout=self._timeout)
        try:
            resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc


def get_client(settings: Settings | None = None) -> LLMClient:
    s = settings or get_settings()
    if s.llm_provider == "openai_compatible" and s.llm_base_url and s.llm_model:
        return OpenAICompatibleClient(s.llm_base_url, s.llm_model, s.llm_api_key)
    return NullClient()
