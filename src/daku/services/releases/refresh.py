"""Best-effort seeder re-check for saved releases.

A refresh is one Torrentio request per ``(title, episode)`` group, served from the
shared payload cache, so twenty saved releases on the same title cost one upstream
request. Nothing here may destroy state on a network failure: a fetch error leaves
``seeders`` and ``miss_count`` untouched.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.orm import Session

from daku.models import Episode, Release, Title
from daku.repositories import releases as releases_repo
from daku.services.releases.metadata import resolved_from
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.torrentio import TorrentioError, fetch_releases, with_qualityfilter

if TYPE_CHECKING:
    from daku.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_INTERVAL_SECONDS = 600.0


class RefreshThrottle:
    """At most one refresh attempt per release per interval.

    The attempt is recorded before the request, so a repeated failure is throttled too.
    The map is in-process: a restart simply allows one fresh attempt.
    """

    def __init__(
        self,
        interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._last_attempt: dict[str, float] = {}

    def due(self, release_id: str) -> bool:
        last = self._last_attempt.get(release_id)
        return last is None or (self._clock() - last) >= self.interval_seconds

    def mark(self, release_id: str) -> None:
        self._last_attempt[release_id] = self._clock()

    def clear(self) -> None:
        self._last_attempt.clear()


@dataclass(slots=True)
class RefreshReport:
    attempted: int = 0
    checked: int = 0
    found: int = 0
    missed: int = 0
    stale: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "checked": self.checked,
            "found": self.found,
            "missed": self.missed,
            "stale": self.stale,
            "reason": self.reason,
        }


def _media_key(session: Session, release: Release) -> tuple[str, str, str] | None:
    """Return ``(group_key, media_type, media_id)`` for a release, or ``None``."""

    title = session.get(Title, release.title_id)
    if title is None or not title.imdb_id:
        return None
    kind = "movie" if title.kind == "movie" else "series"
    if kind == "movie":
        return f"movie:{title.imdb_id}", "movie", title.imdb_id
    if release.episode_id is None:
        return None
    episode = session.get(Episode, release.episode_id)
    if episode is None:
        return None
    media_id = f"{title.imdb_id}:{episode.season}:{episode.episode}"
    return f"series:{media_id}", "series", media_id


def _is_manual(release: Release) -> bool:
    return resolved_from(release) == "manual"


async def refresh_releases(
    session: Session,
    client: httpx.AsyncClient,
    cache: PayloadCache,
    settings: Settings,
    releases: Iterable[Release],
    *,
    throttle: RefreshThrottle,
    force: bool = False,
) -> RefreshReport:
    """Re-check seeders for the given saved releases.

    ``force`` bypasses the throttle (the explicit refresh endpoint). Network failures
    are reported, never raised.
    """

    report = RefreshReport()
    groups: dict[str, list[Release]] = {}
    targets: dict[str, tuple[str, str]] = {}

    for release in releases:
        if not release.info_hash:
            continue
        if not force and not throttle.due(release.id):
            continue
        throttle.mark(release.id)
        report.attempted += 1

        key = _media_key(session, release)
        if key is None:
            continue
        group_key, media_type, media_id = key
        groups.setdefault(group_key, []).append(release)
        targets[group_key] = (media_type, media_id)

    if not groups:
        return report

    # Ask for the full stream list: a saved cam/scr release must still be findable.
    config = with_qualityfilter(settings.torrentio_config, [])

    for group_key, group in groups.items():
        media_type, media_id = targets[group_key]
        try:
            outcome = await fetch_releases(client, cache, config, media_type, media_id)
        except TorrentioError as exc:
            report.reason = str(exc)
            logger.info("release refresh unavailable", extra={"media_id": media_id})
            continue

        report.stale = report.stale or outcome.stale
        by_hash = {item.info_hash: item for item in outcome.releases}
        for release in group:
            match = by_hash.get((release.info_hash or "").lower())
            if match is not None:
                releases_repo.record_seen(
                    session,
                    release.id,
                    seeders=match.seeders,
                    size_bytes=match.size_bytes,
                )
                report.found += 1
                report.checked += 1
                continue
            if _is_manual(release):
                # A hand-pasted magnet need not be indexed by Torrentio. Absence is not
                # evidence of death, so leave the miss counter alone.
                continue
            releases_repo.record_miss(session, release.id)
            report.missed += 1
            report.checked += 1

    return report
