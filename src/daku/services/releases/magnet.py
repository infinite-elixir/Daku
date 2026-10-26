"""Manual magnet parsing.

A pasted magnet is normalised into the same shape a Torrentio result has, with one
difference: the seeder count is unknown (``None``) until the first refresh.

Only ``xt=urn:btih:`` matters. The hash may be 40 hex characters or 32 base32
characters; both are converted to the canonical lowercase 40-character hex form.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from daku.services.torrents.ranking import detect_quality, extract_hints
from daku.services.torrents.torrentio import sanitize_filename

BTIH_PREFIX = "urn:btih:"
HASH_HEX_LENGTH = 40
HASH_BASE32_LENGTH = 32
HASH_BYTES = 20
HEX_RE = re.compile(r"^[0-9a-f]+$")


class MagnetError(ValueError):
    """The URI cannot be used as a torrent magnet."""


@dataclass(slots=True)
class ParsedMagnet:
    """Normalised magnet, shaped like :class:`daku.services.torrents.base.Release`."""

    info_hash: str
    filename: str | None
    trackers: list[str]
    magnet: str
    quality: str
    metadata: dict[str, Any]
    provider: str = "manual"
    size_bytes: int | None = None
    seeders: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "info_hash": self.info_hash,
            "filename": self.filename,
            "trackers": list(self.trackers),
            "magnet": self.magnet,
            "quality": self.quality,
            "size_bytes": self.size_bytes,
            "seeders": self.seeders,
            "provider": self.provider,
            "metadata_json": dict(self.metadata),
        }


def decode_info_hash(raw: str) -> str:
    """Convert a ``btih`` value to 40 lowercase hex characters."""

    candidate = raw.strip()
    if len(candidate) == HASH_HEX_LENGTH:
        if not HEX_RE.fullmatch(candidate.lower()):
            raise MagnetError("magnet btih is not hexadecimal")
        return candidate.lower()
    if len(candidate) == HASH_BASE32_LENGTH:
        try:
            decoded = base64.b32decode(candidate.upper())
        except (binascii.Error, ValueError) as exc:
            raise MagnetError("magnet btih is not valid base32") from exc
        if len(decoded) != HASH_BYTES:
            raise MagnetError("magnet btih does not decode to 20 bytes")
        return decoded.hex()
    raise MagnetError("magnet btih must be 40 hex characters or 32 base32 characters")


def build_magnet(
    info_hash: str,
    filename: str | None = None,
    trackers: list[str] | None = None,
) -> str:
    """Build the canonical stored magnet, preserving every tracker in order."""

    magnet = f"magnet:?xt=urn:btih:{info_hash.lower()}"
    if filename:
        magnet += f"&dn={quote(filename, safe='')}"
    for tracker in trackers or []:
        magnet += f"&tr={quote(tracker, safe='')}"
    return magnet


def parse_magnet(uri: str) -> ParsedMagnet:
    """Parse a manual magnet URI or raise :class:`MagnetError`."""

    if not isinstance(uri, str) or not uri.strip():
        raise MagnetError("magnet URI is empty")

    parts = urlsplit(uri.strip())
    if parts.scheme.lower() != "magnet":
        raise MagnetError("only magnet: URIs are accepted")

    params = parse_qs(parts.query, keep_blank_values=False)

    info_hash: str | None = None
    seen_btih = False
    for xt in params.get("xt", []):
        if not xt.lower().startswith(BTIH_PREFIX):
            continue
        seen_btih = True
        try:
            info_hash = decode_info_hash(xt[len(BTIH_PREFIX) :])
        except MagnetError:
            # A later xt may still carry a valid hash.
            continue
        break

    if info_hash is None:
        if seen_btih:
            raise MagnetError("magnet btih hash is malformed")
        raise MagnetError("magnet URI has no btih info hash")

    trackers: list[str] = []
    for tracker in params.get("tr", []):
        cleaned = tracker.strip()
        if cleaned and cleaned not in trackers:
            trackers.append(cleaned)

    raw_name = params.get("dn", [None])[0]
    filename = sanitize_filename(raw_name) if raw_name else None

    return ParsedMagnet(
        info_hash=info_hash,
        filename=filename,
        trackers=trackers,
        magnet=build_magnet(info_hash, filename, trackers),
        quality=detect_quality(filename) if filename else "unknown",
        metadata=extract_hints(filename or ""),
    )
