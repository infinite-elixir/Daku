"""Pure episode-to-file matching for season packs and multi-file torrents.

The matcher never touches a database or the network. Its input is the file list
returned by the metadata probe (T-030) and an episode identity; its output is a
ranked list of :class:`FileMatch` rows plus the single confident winner, if any.

Scoring is deliberately simple and explainable. A file that names the wrong
``SxxEyy`` is rejected outright; it is never outranked by a title overlap. Samples,
subtitles, trailers and archives are rejected before scoring.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol

FLOOR = 50
AMBIGUITY_GAP = 10

KNOWN_SEASONS = "single"

_BIDI_RE = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_BRACKET_RE = re.compile(r"[\[\(\{]([^\]\)\}]*)[\]\)\}]")
_SXXEYY_RE = re.compile(r"(?<![a-z0-9])s(?:eason)?\s*0*(\d{1,2})\s*e\s*0*(\d{1,3})(?![a-z0-9])")
_XXYY_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})\s*x\s*0*(\d{1,3})(?![a-z0-9])")
_SEASON_RE = re.compile(r"(?<![a-z0-9])(?:s|season)\s*0*(\d{1,2})(?![a-z0-9])")
_EPISODE_RE = re.compile(r"(?<![a-z0-9])e(?:p(?:isode)?)?\s*0*(\d{1,3})(?![a-z0-9])")
_RANGE_RE = re.compile(r"(?<![a-z0-9])0*(\d{1,3})\s*-\s*0*(\d{1,3})(?![a-z0-9])")
_NUMBER_RE = re.compile(r"(?<![a-z0-9])0*(\d{1,4})(?![a-z0-9])")

_BAD_TOKENS = frozenset(
    {
        "sample",
        "samples",
        "trailer",
        "trailers",
        "featurette",
        "featurettes",
        "ncop",
        "nced",
        "preview",
        "par2",
        "rar",
        "zip",
        "7z",
        "nfo",
    }
)
_BAD_EXTENSIONS = frozenset(
    {".srt", ".ass", ".ssa", ".sup", ".sub", ".vtt", ".par2", ".rar", ".zip", ".7z", ".nfo"}
)
_RESOLUTIONS = frozenset({480, 576, 720, 1080, 1440, 2160, 4320})
_CODEC_NUMBERS = frozenset({264, 265, 266, 10, 12})
_STOPWORDS = frozenset({"the", "a", "an", "and", "of", "to", "in", "on", "for", "with", "part"})


class TorrentFileLike(Protocol):
    index: int
    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class EpisodeTarget:
    """The parts of an ``Episode`` row the matcher needs."""

    season: int
    episode: int
    absolute: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class FileMatch:
    score: int
    file: Any
    reason: str

    @property
    def index(self) -> int:
        return int(self.file.index)

    @property
    def path(self) -> str:
        return str(self.file.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "size_bytes": int(getattr(self.file, "size_bytes", 0) or 0),
            "score": self.score,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MatchOutcome:
    ranked: list[FileMatch] = field(default_factory=list)
    match: FileMatch | None = None
    ambiguous: bool = False
    reason: str = "no match"
    candidates: list[FileMatch] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if self.match is not None:
            return {
                "file_index": self.match.index,
                "path": self.match.path,
                "confidence": self.match.score,
                "reason": self.match.reason,
            }
        if self.ambiguous:
            return {
                "ambiguous": True,
                "reason": self.reason,
                "candidates": [item.to_dict() for item in self.candidates],
            }
        return {"reason": self.reason, "candidates": []}


def normalize_path(path: str) -> str:
    """Normalise a torrent path for comparison and token parsing."""

    text = unicodedata.normalize("NFKC", str(path)).lower()
    text = _BIDI_RE.sub("", text)

    def _brackets(match: re.Match[str]) -> str:
        inner = match.group(1)
        # Keep a bracket that itself carries the episode token. Release groups
        # and quality tags are noise; ``[S02E05]`` is not.
        if _SXXEYY_RE.search(inner) or _XXYY_RE.search(inner):
            return f" {inner} "
        return " "

    text = _BRACKET_RE.sub(_brackets, text)
    text = re.sub(r"[_./\\]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text) if token]


def _extension(path: str) -> str:
    return PurePosixPath(str(path)).suffix.lower()


def _is_non_content(path: str, text: str) -> str | None:
    if _extension(path) in _BAD_EXTENSIONS:
        return "subtitle or archive"
    tokens = set(_tokens(text))
    if tokens & _BAD_TOKENS:
        return "sample, trailer or extra"
    return None


def _title_overlap(text: str, episode: EpisodeTarget, title_name: str | None) -> int:
    words = [
        token
        for token in _tokens(normalize_path(episode.name or ""))
        + _tokens(normalize_path(title_name or ""))
        if len(token) >= 3 and token not in _STOPWORDS
    ]
    if not words:
        return 0
    present = set(_tokens(text))
    return min(10, 5 * sum(1 for word in set(words) if word in present))


def _first_season(text: str) -> int | None:
    for match in _SEASON_RE.finditer(text):
        # ``s02e05`` is handled by the exact parser; this is for standalone
        # season folders and multi-season pack names.
        return int(match.group(1))
    return None


def season_episode_from_path(path: str) -> tuple[int, int] | None:
    """Return an explicit ``SxxEyy``/``2x05`` pair from a path, if present."""

    text = normalize_path(path)
    exact = _SXXEYY_RE.search(text)
    if exact:
        return int(exact.group(1)), int(exact.group(2))
    xx = _XXYY_RE.search(text)
    if xx:
        return int(xx.group(1)), int(xx.group(2))
    return None


def _known_seasons(files: list[Any], release_filename: str | None) -> set[int]:
    seasons: set[int] = set()
    for item in files:
        text = normalize_path(str(getattr(item, "path", "")))
        exact = _SXXEYY_RE.search(text)
        if exact:
            seasons.add(int(exact.group(1)))
            continue
        xx = _XXYY_RE.search(text)
        if xx:
            seasons.add(int(xx.group(1)))
            continue
        season = _first_season(text)
        if season is not None:
            seasons.add(season)
    if release_filename:
        release_text = normalize_path(release_filename)
        exact = _SXXEYY_RE.search(release_text)
        if exact:
            seasons.add(int(exact.group(1)))
        else:
            season = _first_season(release_text)
            if season is not None:
                seasons.add(season)
    return seasons


def _is_single_season_pack(files: list[Any], release_filename: str | None) -> bool:
    return len(_known_seasons(files, release_filename)) <= 1


def _episode_reason(
    text: str,
    episode: EpisodeTarget,
) -> tuple[int, str] | None:
    """Return the strongest episode token found in ``text``, if it matches."""

    exact = _SXXEYY_RE.search(text)
    if exact:
        season, number = int(exact.group(1)), int(exact.group(2))
        if season != episode.season or number != episode.episode:
            return None
        return 100, f"S{season:02d}E{number:02d}"

    xx = _XXYY_RE.search(text)
    if xx:
        season, number = int(xx.group(1)), int(xx.group(2))
        if season != episode.season or number != episode.episode:
            return None
        return 90, f"{season}x{number:02d}"

    tagged = _EPISODE_RE.search(text)
    if tagged:
        number = int(tagged.group(1))
        if number != episode.episode:
            return None
        return 80, f"episode {number:02d}"

    best: tuple[int, str] | None = None

    for match in _RANGE_RE.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        endpoints = {start, end}
        absolute = episode.absolute
        if episode.episode in endpoints:
            best = (60, f"range {start:02d}-{end:02d}")
        elif absolute is not None and absolute in endpoints:
            best = (58, f"absolute range {start:02d}-{end:02d}")

    if best is not None:
        return best

    for match in _NUMBER_RE.finditer(text):
        number = int(match.group(1))
        if number in _RESOLUTIONS or number in _CODEC_NUMBERS or number >= 1900:
            continue
        if episode.absolute is not None and number == episode.absolute:
            return 85, f"absolute {number}"
        if number == episode.episode:
            return 75, f"episode {number:02d}"

    return None


def match_episode(
    files: list[Any],
    episode: EpisodeTarget,
    *,
    title_kind: str = "series",
    release_filename: str | None = None,
    title_name: str | None = None,
    single_season: bool | None = None,
) -> MatchOutcome:
    """Rank ``files`` for one episode and decide whether the winner is confident."""

    is_anime = title_kind == "anime"
    known_seasons = _known_seasons(files, release_filename)
    single = single_season if single_season is not None else len(known_seasons) <= 1
    missing_season_allowed = single and (not known_seasons or episode.season in known_seasons)

    ranked: list[FileMatch] = []
    for item in files:
        path = str(getattr(item, "path", ""))
        text = normalize_path(path)
        if not text:
            continue
        rejected = _is_non_content(path, text)
        if rejected is not None:
            continue

        reason = _episode_reason(text, episode)
        if reason is None:
            continue
        score, label = reason

        file_season = _first_season(text)
        if file_season is not None and file_season != episode.season:
            continue
        if file_season is None and not missing_season_allowed:
            continue
        if file_season is None:
            score -= 5

        if not is_anime and episode.absolute is not None and label.startswith("absolute"):
            # Absolute numbering is an anime convention. A real S/E token above
            # already won; a bare number stays usable but at lower confidence.
            score -= 10

        overlap = _title_overlap(text, episode, title_name)
        score += overlap
        if overlap:
            label = f"{label} + title"

        ranked.append(FileMatch(score=score, file=item, reason=label))

    ranked.sort(key=lambda match: (-match.score, match.path))

    if not ranked or ranked[0].score < FLOOR:
        return MatchOutcome(
            ranked=ranked,
            reason="no file matched this episode",
            candidates=ranked[:10],
        )

    top = ranked[0]
    close = [match for match in ranked[1:] if top.score - match.score <= AMBIGUITY_GAP]
    if close:
        return MatchOutcome(
            ranked=ranked,
            ambiguous=True,
            reason="more than one file matches this episode",
            candidates=[top, *close][:10],
        )

    return MatchOutcome(ranked=ranked, match=top, reason=top.reason, candidates=[top])


# Backwards-friendly alias for callers that just want a ranked list.
def rank_files(
    files: list[Any],
    episode: EpisodeTarget,
    **kwargs: Any,
) -> list[FileMatch]:
    return match_episode(files, episode, **kwargs).ranked


__all__ = [
    "AMBIGUITY_GAP",
    "FLOOR",
    "EpisodeTarget",
    "FileMatch",
    "MatchOutcome",
    "match_episode",
    "normalize_path",
    "rank_files",
    "season_episode_from_path",
]
