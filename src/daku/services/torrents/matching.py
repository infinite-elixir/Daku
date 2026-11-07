"""Release matching helpers shared by every keyword source.

Nothing here is anime-specific: the title, episode and pack matchers operate on a
release filename and the requested title/episode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MATCH_STOPWORDS = frozenset(
    {"the", "of", "a", "an", "and", "part", "season", "movie", "ova", "ona"}
)


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
