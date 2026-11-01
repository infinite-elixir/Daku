"""AnimeTosho JSON feed adapter for anime releases.

The feed is a public, read-only index of anime torrents. Daku reads metadata only and
builds its own magnet from the validated info hash; it never downloads a ``.torrent``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, quote_plus

import httpx

from daku.services.metadata.aliases import derive_styles, normalise_query
from daku.services.torrents.base import Release
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.ranking import detect_quality, extract_hints, parse_size_bytes
from daku.services.torrents.torrentio import sanitize_filename

FEED_BASE = "https://feed.animetosho.net/"
LEGACY_FEED_BASE = "https://feed.animetosho.xyz/"
FEED_USER_AGENT = "daku/1.0 (+self-hosted; contact in README)"
MAX_QUERY_CHARS = 120
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_QUERIES = 8
MAX_CANDIDATES = 40
SEARCH_DEADLINE_SECONDS = 8.0
MATCH_STOPWORDS = frozenset(
    {"the", "of", "a", "an", "and", "part", "season", "movie", "ova", "ona"}
)
PUBLIC_TRACKERS = (
    "http://nyaa.tracker.wf:7777/announce",
    "http://anidex.moe:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.tamersunion.org:443/announce",
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_HASH_RE = re.compile(r"^(?:[0-9a-f]{40}|[a-z2-7]{32})$", re.IGNORECASE)


class AnimeToshoError(Exception):
    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(slots=True)
class AnimeToshoSearch:
    releases: list[Release]
    stale: bool
    cached_at: str | None
    url: str


def build_search_url(query: str) -> str:
    cleaned = _CONTROL_RE.sub(" ", query).strip()[:MAX_QUERY_CHARS]
    return f"{FEED_BASE}json?q={quote_plus(cleaned)}"


def build_magnet(info_hash: str, title: str) -> str:
    encoded_title = quote(title, safe="")
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={encoded_title}" + "".join(
        f"&tr={quote(tracker, safe='')}" for tracker in PUBLIC_TRACKERS
    )


def _hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not _HASH_RE.fullmatch(candidate):
        return None
    if len(candidate) == 32:
        try:
            return base64.b32decode(candidate.upper()).hex()
        except (binascii.Error, ValueError):
            return None
    return candidate


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return 0


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _timestamp(value: object) -> str | None:
    seconds = _optional_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = html.unescape(value).strip()
    return cleaned or None


def parse_feed(payload: str | bytes) -> list[Release]:
    try:
        results = json.loads(payload)
    except (TypeError, ValueError):
        return []
    if not isinstance(results, list):
        return []

    releases: list[Release] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        info_hash = _hash(result.get("info_hash"))
        if info_hash is None:
            continue
        title = _text(result.get("title")) or _text(result.get("torrent_name"))
        if title is None:
            continue
        filename = sanitize_filename(title)
        if filename is None:
            continue

        metadata = {
            **extract_hints(filename),
            "category": _text(result.get("article_title")),
            "status": _text(result.get("status")),
            "num_files": _optional_int(result.get("num_files")),
            "published_at": _timestamp(result.get("timestamp")),
        }
        releases.append(
            Release(
                info_hash=info_hash,
                filename=filename,
                quality=detect_quality(filename),
                size_bytes=parse_size_bytes(result.get("total_size")),
                seeders=_int(result.get("seeders")),
                provider="animetosho",
                magnet=build_magnet(info_hash, title),
                metadata=metadata,
            )
        )
    return releases


def _alias_styles(aliases: list[str]) -> list[list[str]]:
    return [derive_styles(alias) for alias in aliases if alias.strip()]


def _dedupe_queries(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _CONTROL_RE.sub(" ", value).strip()
        key = normalise_query(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def build_bare_queries(aliases: list[str]) -> list[str]:
    """Every alias once before spending the rest of the budget on extra styles."""

    styles = _alias_styles(aliases)
    ordered = [group[0] for group in styles if group]
    for group in styles:
        ordered.extend(group[1:])
    return _dedupe_queries(ordered)[:MAX_QUERIES]


def _season_numbers(season: int | None, provider_seasons: list[int] | None) -> list[int]:
    values = list(dict.fromkeys(value for value in (provider_seasons or []) if value is not None))
    if not values and season is not None:
        values = [season]
    return values


def build_episode_queries(
    aliases: list[str],
    *,
    season: int | None = None,
    episode: int | None = None,
    provider_seasons: list[int] | None = None,
) -> list[str]:
    """Suffix spellings, provider-season ``SxxEyy`` first, then episode-only forms."""

    if episode is None:
        return []
    styles = _alias_styles(aliases)
    ordered: list[str] = []
    seasons = _season_numbers(season, provider_seasons)
    for group in styles:
        primary = group[0] if group else None
        if primary is None:
            continue
        for provider_season in seasons:
            ordered.append(f"{primary} S{provider_season:02d}E{episode:02d}")
    for group in styles:
        primary = group[0] if group else None
        if primary is None:
            continue
        ordered.append(f"{primary} E{episode:02d}")
        ordered.append(f"{primary} Episode {episode}")
        ordered.append(f"{primary} {episode:02d}")
    for group in styles:
        for extra in group[1:]:
            ordered.append(f"{extra} {episode:02d}")
    return _dedupe_queries(ordered)


def build_query_plan(
    aliases: list[str],
    *,
    season: int | None = None,
    episode: int | None = None,
    provider_seasons: list[int] | None = None,
) -> list[str]:
    """The bounded plan: bare names first, then explicit episode spellings."""

    plan = build_bare_queries(aliases) + build_episode_queries(
        aliases,
        season=season,
        episode=episode,
        provider_seasons=provider_seasons,
    )
    return _dedupe_queries(plan)[:MAX_QUERIES]


_throttle_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
_last_request = 0.0


def _throttle_lock() -> asyncio.Lock:
    """One throttle per event loop: tests create a loop per TestClient."""

    loop = asyncio.get_running_loop()
    lock = _throttle_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _throttle_locks[loop] = lock
    return lock


async def fetch_releases(
    client: httpx.AsyncClient,
    cache: PayloadCache,
    query: str,
) -> AnimeToshoSearch:
    global _last_request
    url = build_search_url(query)
    # Entries written before the feed moved to ``.net`` are still valid.
    legacy_url = url.replace(FEED_BASE, LEGACY_FEED_BASE, 1)
    fresh = cache.get_fresh(url) or cache.get_fresh(legacy_url)
    if fresh is not None:
        return AnimeToshoSearch(parse_feed(fresh.payload), False, fresh.cached_at, url)
    async with _throttle_lock():
        wait = 1.0 - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        try:
            response = await client.get(url, headers={"User-Agent": FEED_USER_AGENT})
            if response.status_code in (429, 503):
                raise AnimeToshoError(url, f"HTTP {response.status_code}")
            if len(response.content) > MAX_PAYLOAD_BYTES:
                raise AnimeToshoError(url, "response too large")
            response.raise_for_status()
            entry = cache.set(url, response.text)
            return AnimeToshoSearch(parse_feed(response.text), False, entry.cached_at, url)
        except (httpx.HTTPError, AnimeToshoError) as exc:
            stale = cache.get_stale(url) or cache.get_stale(legacy_url)
            if stale is not None:
                return AnimeToshoSearch(parse_feed(stale.payload), True, stale.cached_at, url)
            if isinstance(exc, AnimeToshoError):
                raise
            raise AnimeToshoError(url, str(exc)) from exc


def _match_tokens(value: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", value.casefold())
    tokens = set(raw)
    # ``jojos`` / ``jojo's`` / ``jojo s`` all describe the same word.
    for index, token in enumerate(raw):
        if token == "s" and index > 0:
            tokens.add(raw[index - 1] + "s")
        if token.endswith("s") and len(token) > 2:
            tokens.add(token[:-1])
    return tokens


def _significant_match_tokens(value: str) -> set[str]:
    return {
        token for token in _match_tokens(value) if len(token) > 1 and token not in MATCH_STOPWORDS
    }


def _looks_like_acronym(value: str) -> bool:
    letters = re.sub(r"[^A-Za-z0-9]", "", value)
    return len(letters) >= 3 and letters.isupper()


def _alias_matches(alias: str, haystack: set[str]) -> bool:
    wanted = _significant_match_tokens(alias)
    if len(wanted) >= 2:
        return wanted <= haystack
    if len(wanted) == 1:
        token = next(iter(wanted))
        # A single common word is not a title match. An acronym is.
        return token in haystack and _looks_like_acronym(alias)
    return False


def _alias_acronym(alias: str) -> str | None:
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9]+", alias)
        if len(word) > 1 and word.casefold() not in MATCH_STOPWORDS
    ]
    if len(words) < 3:
        return None
    letters = "".join(word[0] for word in words).upper()
    return letters if 3 <= len(letters) <= 8 else None


def matches_query(title: str, filename: str, synonyms: list[str] | None = None) -> bool:
    """Accept only when one whole alias is covered by the release filename."""

    haystack = _match_tokens(filename)
    names = [title, *(synonyms or [])]
    for name in names:
        if not name.strip():
            continue
        if _alias_matches(name, haystack):
            return True
        acronym = _alias_acronym(name)
        if acronym is not None and acronym.casefold() in haystack:
            return True
    return False


@dataclass(frozen=True, slots=True)
class ReleaseEpisode:
    season: int | None
    episode: int | None
    absolute: int | None
    is_pack: bool
    notation: str
    end: int | None = None


_RESOLUTIONS = frozenset({480, 576, 720, 1080, 1440, 2160, 4320})
_CODEC_NUMBERS = frozenset({8, 10, 12, 264, 265, 266})
_SXXEYY_RE = re.compile(
    r"(?<![a-z0-9])s(?:eason)?\s*0*(\d{1,2})\s*e\s*0*(\d{1,3})(?!\d)", re.IGNORECASE
)
_XXYY_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})\s*x\s*0*(\d{1,3})(?!\d)", re.IGNORECASE)
_EXPLICIT_RANGE_RE = re.compile(
    r"(?<![a-z0-9])s(?:eason)?\s*0*(\d{1,2})\s*e\s*0*(\d{1,3})\s*[-~\u2013\u2014]\s*"
    r"(?:s(?:eason)?\s*0*(\d{1,2})\s*)?e?\s*0*(\d{1,3})(?!\d)",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*[-~\u2013\u2014]\s*(\d{1,3})(?![\d.])")
_SEASON_RE = re.compile(r"(?<![a-z0-9])s(?:eason)?\s*0*(\d{1,2})(?![a-z0-9])", re.IGNORECASE)
_EPISODE_RE = re.compile(
    r"(?<![a-z0-9])(?:e|ep|episode)[ ._-]*0*(\d{1,3})(?:v\d+)?(?![a-z0-9])", re.IGNORECASE
)
_CJK_RE = re.compile(r"第\s*0*(\d{1,3})\s*[話话]")
_BRACKET_RE = re.compile(r"[\[\(]\s*0*(\d{1,3})(?:v\d+)?\s*[\]\)]")
_DASH_RE = re.compile(r"(?<!\d)[-\u2013\u2014]\s*0*(\d{1,3})(?:v\d+)?(?:\D|$)")
_NUMBER_RE = re.compile(r"(?<![a-z0-9])0*(\d{1,4})(?:v\d+)?(?![a-z0-9])")
# Explicit "this is the whole thing" markers. A release that also carries an
# episode notation is still treated as that episode; markers only decide the
# fallback after every episode pattern has failed.
_PACK_MARKER_RE = re.compile(
    r"(?i)(?:\[\s*batch\s*\]|\(\s*batch\s*\)|\bbatch\b|\bcomplete\b|全集|合集)"
)
_SEASON_BEFORE_RANGE_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})\s*[-\u2013\u2014~]\s*$")


def _plausible_range(start: int, end: int) -> bool:
    if start < 1 or end < 1 or end < start:
        return False
    if start >= 1900 or end >= 1900:
        return False
    if start in _RESOLUTIONS or end in _RESOLUTIONS:
        return False
    return end - start <= 199


def _find_range(text: str) -> re.Match[str] | None:
    """First plausible episode range, allowing overlap with a bogus match.

    ``Edgerunners 2 - 01 ~ 10`` contains ``2 - 01`` first, which is invalid. The
    scan has to resume one character later to see ``01 ~ 10``.
    """

    position = 0
    while True:
        match = _RANGE_RE.search(text, position)
        if match is None:
            return None
        start, end = int(match.group(1)), int(match.group(2))
        if _plausible_range(start, end):
            return match
        position = match.start() + 1


def _range_season(text: str, start: int) -> int | None:
    """Season for a range, from ``S02`` or a leading ``2 - 01 ~ 10`` number."""

    before = _SEASON_BEFORE_RANGE_RE.search(text[:start])
    if before is not None:
        value = int(before.group(1))
        if 1 <= value <= 99 and value not in _RESOLUTIONS and value not in _CODEC_NUMBERS:
            return value
    return _standalone_season(text)


def _standalone_season(text: str) -> int | None:
    match = _SEASON_RE.search(text)
    return int(match.group(1)) if match else None


def parse_release_episode(filename: str) -> ReleaseEpisode:
    """Parse the first clear episode/pack notation in a release filename."""

    text = filename.casefold()

    explicit_range = _EXPLICIT_RANGE_RE.search(text)
    if explicit_range:
        season = int(explicit_range.group(1))
        start = int(explicit_range.group(2))
        end = int(explicit_range.group(4))
        if _plausible_range(start, end):
            return ReleaseEpisode(
                season=season,
                episode=start,
                absolute=None,
                is_pack=True,
                notation=f"S{season:02d}E{start:02d}-E{end:02d}",
                end=end,
            )

    range_match = _find_range(text)
    if range_match is not None:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return ReleaseEpisode(
            season=_range_season(text, range_match.start()),
            episode=start,
            absolute=None,
            is_pack=True,
            notation=f"{start:02d}-{end:02d}",
            end=end,
        )

    exact = _SXXEYY_RE.search(text)
    if exact:
        season, number = int(exact.group(1)), int(exact.group(2))
        return ReleaseEpisode(
            season=season,
            episode=number,
            absolute=None,
            is_pack=False,
            notation=f"S{season:02d}E{number:02d}",
        )

    cross = _XXYY_RE.search(text)
    if cross:
        season, number = int(cross.group(1)), int(cross.group(2))
        return ReleaseEpisode(
            season=season,
            episode=number,
            absolute=None,
            is_pack=False,
            notation=f"{season}x{number:02d}",
        )

    cjk = _CJK_RE.search(text)
    if cjk:
        number = int(cjk.group(1))
        return ReleaseEpisode(
            season=_standalone_season(text),
            episode=number,
            absolute=None,
            is_pack=False,
            notation=f"第{number}話",
        )

    tagged = _EPISODE_RE.search(text)
    if tagged:
        number = int(tagged.group(1))
        return ReleaseEpisode(
            season=_standalone_season(text),
            episode=number,
            absolute=None,
            is_pack=False,
            notation=f"episode {number:02d}",
        )

    bracket = _BRACKET_RE.search(text)
    if bracket:
        number = int(bracket.group(1))
        return ReleaseEpisode(
            season=_standalone_season(text),
            episode=number,
            absolute=number,
            is_pack=False,
            notation=f"[{number:02d}]",
        )

    dash = _DASH_RE.search(text)
    if dash:
        number = int(dash.group(1))
        return ReleaseEpisode(
            season=_standalone_season(text),
            episode=number,
            absolute=number,
            is_pack=False,
            notation=f"- {number:02d}",
        )

    # A season marker or a batch marker with no episode notation is a pack, not
    # an episode. Guessing the last stray number (a season, a year, an audio
    # channel) is what used to scatter "Judas (Season 2)" onto random episodes.
    pack_marker = _PACK_MARKER_RE.search(text) is not None
    standalone = _standalone_season(text)
    if pack_marker or standalone is not None:
        return ReleaseEpisode(
            season=standalone,
            episode=None,
            absolute=None,
            is_pack=True,
            notation=f"S{standalone:02d} pack" if standalone is not None else "batch",
        )

    candidates: list[int] = []
    for match in _NUMBER_RE.finditer(text):
        number = int(match.group(1))
        if number in _RESOLUTIONS or number in _CODEC_NUMBERS or number >= 1900:
            continue
        if number >= 1:
            candidates.append(number)
    if candidates:
        number = candidates[-1]
        return ReleaseEpisode(
            season=_standalone_season(text),
            episode=number,
            absolute=number,
            is_pack=False,
            notation=f"{number:02d}",
        )

    return ReleaseEpisode(None, None, None, False, "unknown")


def matches_episode(
    filename: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    absolute: int | None = None,
    pack: bool = False,
    provider_seasons: list[int] | None = None,
    title_matched: bool = True,
    season_fallback: bool = False,
) -> bool:
    """Match a single episode exactly, tolerating season remaps but not batches."""

    if episode is None or pack:
        return True

    parsed = parse_release_episode(filename)
    if parsed.is_pack or parsed.episode is None:
        return False
    if parsed.season == 0 and season != 0:
        return False

    release_episode = parsed.episode
    if release_episode != episode:
        # An absolute-only number may stand in for the requested episode, but a
        # conflicting explicit season on another cour always wins.
        return absolute is not None and parsed.season is None and parsed.absolute == absolute

    if season == 0:
        return parsed.season == 0

    if parsed.season is None:
        return title_matched

    if parsed.season == season:
        return True
    if provider_seasons and parsed.season in provider_seasons:
        return True
    if season_fallback and title_matched:
        # Single-season library entries often lack a provider-season map (for
        # example a split cour published as ``S06`` inside a long franchise).
        return True

    # Explicit absolute fallback, but never against a different explicit cour.
    if absolute is not None and parsed.absolute == absolute:
        return False
    return False


def pack_covers_episode(
    filename: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    provider_seasons: list[int] | None = None,
    title_matched: bool = True,
    season_fallback: bool = False,
) -> bool:
    """Is this a pack that *contains* the requested episode?

    A pack is never an exact episode match (that is ``matches_episode``), but it
    is a legitimate source for the episode: the download flow selects the single
    file out of the pack. The caller labels these rows as packs.
    """

    if episode is None:
        return False

    parsed = parse_release_episode(filename)
    if not parsed.is_pack:
        return False

    if season == 0:
        return parsed.season == 0

    allowed = {value for value in [season, *(provider_seasons or [])] if value is not None}
    if parsed.season is not None:
        return parsed.season in allowed or (season_fallback and title_matched)

    # No season marker: a numeric range has to contain the episode.
    if parsed.episode is not None and parsed.end is not None:
        return parsed.episode <= episode <= parsed.end

    # A bare batch ("COMPLETE", "全集合集") is a whole-series pack.
    return title_matched


def pack_label(filename: str) -> str:
    """Short human label for a pack row, derived from the parsed notation."""

    parsed = parse_release_episode(filename)
    if parsed.end is not None and parsed.episode is not None:
        return f"E{parsed.episode:02d}-E{parsed.end:02d}"
    if parsed.season is not None:
        return f"S{parsed.season:02d}"
    return "Complete"
