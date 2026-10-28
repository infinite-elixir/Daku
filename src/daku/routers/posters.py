"""Same-origin poster proxy used only for canvas pixel reads.

The browser can load a poster as an ``<img>`` from any host, but reading its pixels
taints the canvas unless the host sends permissive CORS headers. Many poster hosts do
not, so the dither view falls back to this read-only proxy. It is deliberately narrow:

* HTTPS only, and only hosts Daku's metadata adapters return.
* Every host is resolved before the request; private, loopback, link-local and other
  non-global addresses are rejected to avoid SSRF.
* Redirects are followed manually so each hop is validated the same way.
* At most 8 MB is buffered in memory. Nothing is written to disk.

All image processing happens in the browser. This endpoint never generates an image.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Annotated
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, Query, Request, Response

from daku.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["posters"])

MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
CACHE_CONTROL = "public, max-age=3600"

# Poster hosts returned by the metadata adapters: Cinemeta (Metahub), AniList, IMDb and
# TheTVDB. Keep this list short; it is an allowlist, not a general-purpose fetcher.
ALLOWED_POSTER_HOSTS: frozenset[str] = frozenset(
    {
        "images.metahub.space",
        "s4.anilist.co",
        "m.media-amazon.com",
        "artworks.thetvdb.com",
        "artwork.tvdb.com",
    }
)

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_IMAGE_ACCEPT = "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"


def _invalid(message: str = "This is not an allowed poster URL") -> AppError:
    return AppError(code="POSTER_HOST_NOT_ALLOWED", message=message, status_code=400)


def _host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_POSTER_HOSTS)


def _parse_target(raw: str) -> tuple[str, str, int]:
    try:
        parts = urlsplit(raw.strip())
    except ValueError as exc:
        raise _invalid("This is not a valid poster URL") from exc
    if parts.scheme.lower() != "https":
        raise _invalid("Poster URLs must use HTTPS")
    if parts.username or parts.password:
        raise _invalid("Poster URLs cannot contain credentials")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or not _host_allowed(host):
        raise _invalid()
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise _invalid("This is not a valid poster URL") from exc
    if port != 443:
        raise _invalid("Poster URLs must use the standard HTTPS port")
    return parts.geturl(), host, port


def _resolve_host(host: str, port: int = 443) -> set[str]:
    """Resolve a hostname synchronously. Kept small so tests can replace it."""

    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: set[str] = set()
    for info in infos:
        address = info[4][0]
        if isinstance(address, str):
            addresses.add(address)
    return addresses


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _assert_public_host(host: str, port: int) -> None:
    try:
        addresses = await asyncio.to_thread(_resolve_host, host, port)
    except OSError as exc:
        raise AppError(
            code="POSTER_FETCH_FAILED",
            message="The poster host could not be resolved",
            status_code=502,
        ) from exc
    if not addresses or not all(_is_public(address) for address in addresses):
        raise _invalid("Poster URLs must resolve to a public address")


def _too_large() -> AppError:
    return AppError(
        code="POSTER_TOO_LARGE",
        message="That poster is too large to proxy (8 MB limit)",
        status_code=413,
    )


def _fetch_failed() -> AppError:
    return AppError(
        code="POSTER_FETCH_FAILED",
        message="The poster could not be fetched",
        status_code=502,
    )


async def _read_image(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    target, host, port = _parse_target(url)

    for _hop in range(MAX_REDIRECTS + 1):
        await _assert_public_host(host, port)
        try:
            async with client.stream(
                "GET",
                target,
                headers={"Accept": _IMAGE_ACCEPT},
                follow_redirects=False,
            ) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise _fetch_failed()
                    next_url = urljoin(target, location)
                    target, host, port = _parse_target(next_url)
                    continue

                if response.status_code != 200:
                    raise _fetch_failed()

                content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                if not content_type.lower().startswith("image/"):
                    raise AppError(
                        code="POSTER_NOT_IMAGE",
                        message="The poster URL did not return an image",
                        status_code=502,
                    )

                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > MAX_BYTES:
                            raise _too_large()
                    except ValueError:
                        pass

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise _too_large()
                    chunks.append(chunk)
                if total == 0:
                    raise _fetch_failed()
                logger.info("poster proxy fetched", extra={"host": host})
                return b"".join(chunks), content_type
        except httpx.HTTPError as exc:
            raise _fetch_failed() from exc

    raise AppError(
        code="POSTER_REDIRECT_LIMIT",
        message="The poster URL redirected too many times",
        status_code=502,
    )


@router.get("/posters/proxy")
async def poster_proxy(
    request: Request,
    url: Annotated[str, Query(min_length=1)],
) -> Response:
    """Fetch one allowlisted HTTPS image so the browser can read its pixels."""

    client: httpx.AsyncClient = request.app.state.http_client
    body, media_type = await _read_image(client, url)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Cache-Control": CACHE_CONTROL},
    )
