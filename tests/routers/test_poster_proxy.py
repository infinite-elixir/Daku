from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from daku.routers import posters

POSTER_URL = "https://images.metahub.space/poster/tt1234567.jpg"
PUBLIC_IP = {"93.184.216.34"}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(_host: str, _port: int = 443) -> set[str]:
        return PUBLIC_IP

    monkeypatch.setattr(posters, "_resolve_host", resolve)


def _proxy(client: TestClient, url: str) -> httpx.Response:
    return client.get("/api/posters/proxy", params={"url": url})


def test_allowlisted_host_returns_image_and_cache_header(client: TestClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(POSTER_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nfake",
                headers={"Content-Type": "image/png"},
            )
        )
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == posters.CACHE_CONTROL
    assert response.content.startswith(b"\x89PNG")


def test_redirect_to_a_private_address_is_rejected_with_400(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(POSTER_URL).mock(
            return_value=httpx.Response(
                302,
                headers={"Location": "https://127.0.0.1/secret.png"},
            )
        )
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 400
    assert response.json()["code"] == "POSTER_HOST_NOT_ALLOWED"


def test_allowlisted_host_resolving_to_private_address_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(posters, "_resolve_host", lambda _host, _port=443: {"10.0.0.7"})

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(POSTER_URL).mock(return_value=httpx.Response(200, content=b"x"))
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 400
    assert response.json()["code"] == "POSTER_HOST_NOT_ALLOWED"
    assert route.call_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://images.metahub.space/poster.jpg",
        "https://example.com/poster.jpg",
        "https://images.metahub.space:8443/poster.jpg",
        "https://user:pass@images.metahub.space/poster.jpg",
    ],
)
def test_non_allowlisted_or_unsafe_urls_are_rejected(client: TestClient, url: str) -> None:
    response = _proxy(client, url)

    assert response.status_code == 400
    assert response.json()["code"] == "POSTER_HOST_NOT_ALLOWED"


def test_wrong_content_type_is_rejected(client: TestClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(POSTER_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"<html>nope</html>",
                headers={"Content-Type": "text/html"},
            )
        )
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 502
    assert response.json()["code"] == "POSTER_NOT_IMAGE"


def test_oversize_body_is_rejected_without_buffering_it_all(client: TestClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(POSTER_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"x" * (posters.MAX_BYTES + 1),
                headers={"Content-Type": "image/jpeg"},
            )
        )
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 413
    assert response.json()["code"] == "POSTER_TOO_LARGE"


def test_upstream_failure_is_a_stable_502(client: TestClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(POSTER_URL).mock(return_value=httpx.Response(503))
        response = _proxy(client, POSTER_URL)

    assert response.status_code == 502
    assert response.json()["code"] == "POSTER_FETCH_FAILED"


def test_proxy_never_writes_to_disk(client: TestClient, settings: Any) -> None:
    before = sorted(path.relative_to(settings.data_dir) for path in settings.data_dir.rglob("*"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get(POSTER_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nfake",
                headers={"Content-Type": "image/png"},
            )
        )
        assert _proxy(client, POSTER_URL).status_code == 200

    after = sorted(path.relative_to(settings.data_dir) for path in settings.data_dir.rglob("*"))
    assert before == after
