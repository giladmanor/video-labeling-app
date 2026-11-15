"""xAI (Grok) API client and API key storage in the macOS Keychain."""

from __future__ import annotations

import os
import time

import httpx
import keyring
from keyring.errors import PasswordDeleteError

KEYCHAIN_SERVICE = "video-label"
KEYCHAIN_ACCOUNT = "xai-api-key"


def get_key() -> str | None:
    """The XAI_API_KEY variable wins over the Keychain (useful for development)."""
    return os.environ.get("XAI_API_KEY") or keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def set_key(key: str) -> None:
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key.strip())


def delete_key() -> None:
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except PasswordDeleteError:
        pass


class GrokError(RuntimeError):
    pass


class GrokClient:
    """Minimal client for the OpenAI-compatible xAI chat API."""

    def __init__(self, key: str, base_url: str, model: str, timeout_s: float = 180.0):
        self.model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_s,
        )

    def _request(self, method: str, path: str, **kw) -> dict:
        delay = 2.0
        for attempt in range(4):
            try:
                r = self._http.request(method, path, **kw)
            except httpx.TransportError as e:
                err = f"network error: {e}"
            else:
                if r.status_code < 400:
                    return r.json()
                err = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code not in (408, 429) and r.status_code < 500:
                    raise GrokError(err)
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
        raise GrokError(err)

    def chat(self, messages: list[dict]) -> tuple[str, dict]:
        """Return (answer text, token usage)."""
        data = self._request(
            "POST", "/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": 0},
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise GrokError(f"unexpected answer: {str(data)[:300]}") from e
        return text or "", data.get("usage") or {}

    def models(self) -> list[dict]:
        """Models the key can use, with a flag for image input when the API reports it."""
        try:
            data = self._request("GET", "/language-models")
            return [
                {"id": m["id"], "image_input": "image" in (m.get("input_modalities") or [])}
                for m in data.get("models", [])
            ]
        except GrokError:
            data = self._request("GET", "/models")
            return [{"id": m["id"], "image_input": None} for m in data.get("data", [])]

    def close(self) -> None:
        self._http.close()
