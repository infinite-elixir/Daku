"""Shared public-tracker list and local magnet construction.

Release sources return an info hash, never a trusted remote magnet. Daku builds the
magnet itself so a payload can never smuggle in an unexpected tracker.
"""

from __future__ import annotations

from urllib.parse import quote

PUBLIC_TRACKERS = (
    "http://nyaa.tracker.wf:7777/announce",
    "http://anidex.moe:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.tamersunion.org:443/announce",
)


def build_magnet(info_hash: str, name: str) -> str:
    """``magnet:?xt=urn:btih:<hash>&dn=<name>&tr=<tracker>...``."""

    encoded_title = quote(name, safe="")
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={encoded_title}" + "".join(
        f"&tr={quote(tracker, safe='')}" for tracker in PUBLIC_TRACKERS
    )
