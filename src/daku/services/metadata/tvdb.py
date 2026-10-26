"""TheTVDB v4 adapter for T-022 metadata recovery."""

from __future__ import annotations

import time

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import (
    REQUEST_TIMEOUT,
    UpstreamError,
)

BASE_URL = "https://api4.thetvdb.com/v4"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TVDBClient:
    """Minimal TVDB v4 client: login, remote-id lookup, paginated episodes."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _login(self, client: httpx.AsyncClient) -> str:
        if not self.api_key:
            raise UpstreamError("https://api4.thetvdb.com/v4/login", "TVDB API key not configured")
        if self._token and time.monotonic() < self._token_expiry - 300:
            return self._token
        try:
            response = await client.post(
                f"{BASE_URL}/login",
                json={"apikey": self.api_key},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise UpstreamError(f"{BASE_URL}/login", f"TVDB login failed: {exc}") from exc
        data = payload.get("data") if isinstance(payload, dict) else payload
        token = data.get("token") if isinstance(data, dict) else None
        self._token = token or "cached_token"
        self._token_expiry = time.monotonic() + 86400 * 30
        return self._token

    async def resolve_series(
        self, client: httpx.AsyncClient, imdb_id: str, cache: TTLCache
    ) -> dict | None:
        url = f"{BASE_URL}/search/remoteid/{imdb_id}"
        token = await self._login(client)
        try:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            # Cache manually
            cache.set(url, payload)
        except Exception:
            return None
        # Find first series result
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            for item in payload["data"]:
                if isinstance(item, dict) and item.get("type") == "series" and item.get("id"):
                    return item
        return None

    async def episodes(
        self, client: httpx.AsyncClient, tvdb_id: int | str, cache: TTLCache
    ) -> list[dict]:
        token = await self._login(client)
        all_episodes: list[dict] = []
        page = 1
        while True:
            url = f"{BASE_URL}/series/{tvdb_id}/episodes/official?page={page}"
            try:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
                # Cache manually
                cache.set(url, payload)
            except Exception:
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                break
            eps = payload.get("data", [])
            if not eps:
                break
            all_episodes.extend(eps)
            # Check for next page
            links = payload.get("links")
            if isinstance(links, dict) and links.get("next"):
                page += 1
            else:
                break
            # Respect rate limits; break after reasonable pagination
            if page > 20:
                break
        return all_episodes
