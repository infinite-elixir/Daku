"""Quality detection, size parsing, release hints and ranking.

Torrentio gives us displays strings, not structured fields. Everything here is parsed
from the stream title/filename and is deliberately tolerant: a missing hint stays
``None`` rather than being guessed from the provider name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from daku.services.torrents.base import Release

UNKNOWN_QUALITY = "unknown"
UNKNOWN_RANK = 1

QUALITY_RANK: dict[str, int] = {
    "2160p": 60,
    "1440p": 55,
    "1080p": 50,
    "720p": 40,
    "576p": 35,
    "480p": 30,
    "ts": 4,
    "scr": 5,
    "cam": 3,
    UNKNOWN_QUALITY: UNKNOWN_RANK,
}

QUALITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cam", re.compile(r"camrip|hdcam|(^|[^a-z])cam([^a-z]|$)", re.IGNORECASE)),
    ("scr", re.compile(r"dvdscr|screener|(^|[^a-z])scr([^a-z]|$)", re.IGNORECASE)),
    ("ts", re.compile(r"hdts|telesync|(^|[^a-z])ts([^a-z]|$)", re.IGNORECASE)),
    ("2160p", re.compile(r"2160p|(^|[^a-z])4k([^a-z]|$)|uhd", re.IGNORECASE)),
    ("1440p", re.compile(r"1440p", re.IGNORECASE)),
    ("1080p", re.compile(r"1080", re.IGNORECASE)),
    ("720p", re.compile(r"720", re.IGNORECASE)),
    ("576p", re.compile(r"576", re.IGNORECASE)),
    ("480p", re.compile(r"480", re.IGNORECASE)),
)

SIZE_RE = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[KMGTP]?I?B)?",
    re.IGNORECASE,
)
SIZE_FACTORS: dict[str, int] = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
    "P": 1024**5,
    "PB": 1024**5,
    "PIB": 1024**5,
}

SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("REMUX", re.compile(r"remux", re.IGNORECASE)),
    ("BluRay", re.compile(r"blu-?ray|bdrip|brrip|bdremux", re.IGNORECASE)),
    ("WEB-DL", re.compile(r"web-?dl|webdl", re.IGNORECASE)),
    ("WEBRip", re.compile(r"web-?rip|webrip", re.IGNORECASE)),
    ("HDTV", re.compile(r"hdtv", re.IGNORECASE)),
)

VIDEO_CODEC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AV1", re.compile(r"(^|[^a-z0-9])av1([^a-z0-9]|$)", re.IGNORECASE)),
    ("x265", re.compile(r"x265|h\.?265|hevc", re.IGNORECASE)),
    ("x264", re.compile(r"x264|h\.?264|(^|[^a-z0-9])avc([^a-z0-9]|$)", re.IGNORECASE)),
)

AUDIO_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Atmos", re.compile(r"(^|[^a-z0-9])atmos([^a-z0-9]|$)", re.IGNORECASE)),
    ("TrueHD", re.compile(r"true-?hd", re.IGNORECASE)),
    ("DDP", re.compile(r"(^|[^a-z0-9])(ddp|dd\+|eac3)([^a-z0-9]|$)", re.IGNORECASE)),
    ("DTS", re.compile(r"(^|[^a-z0-9])dts([^a-z0-9]|$)", re.IGNORECASE)),
    ("AC3", re.compile(r"(^|[^a-z0-9])(ac-?3|eac3)([^a-z0-9]|$)", re.IGNORECASE)),
    ("AAC", re.compile(r"(^|[^a-z0-9])aac([^a-z0-9]|$)", re.IGNORECASE)),
)

LANGUAGE_ALIASES: dict[str, str] = {
    "multi": "multi",
    "dual": "dual",
    "en": "en",
    "eng": "en",
    "english": "en",
    "fr": "fr",
    "fre": "fr",
    "fra": "fr",
    "french": "fr",
    "de": "de",
    "ger": "de",
    "deu": "de",
    "german": "de",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "it": "it",
    "ita": "it",
    "italian": "it",
    "pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "ja": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "zh": "zh",
    "chi": "zh",
    "zho": "zh",
    "chinese": "zh",
    "hi": "hi",
    "hin": "hi",
    "hindi": "hi",
    "ar": "ar",
    "ara": "ar",
    "arabic": "ar",
    "nl": "nl",
    "dut": "nl",
    "nld": "nl",
    "sv": "sv",
    "swe": "sv",
    "no": "no",
    "nor": "no",
    "da": "da",
    "dan": "da",
    "fi": "fi",
    "fin": "fi",
    "pl": "pl",
    "pol": "pl",
    "tr": "tr",
    "tur": "tr",
    "cs": "cs",
    "cze": "cs",
    "hu": "hu",
    "hun": "hu",
    "el": "el",
    "gre": "el",
}
LANGUAGE_RE = re.compile(
    r"(?<![a-z])("
    + "|".join(sorted((re.escape(alias) for alias in LANGUAGE_ALIASES), key=len, reverse=True))
    + r")(?![a-z])",
    re.IGNORECASE,
)

EDITION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Director's Cut", re.compile(r"director'?s?[ ._-]*cut", re.IGNORECASE)),
    ("Special Edition", re.compile(r"special[ ._-]*edition", re.IGNORECASE)),
    ("Final Cut", re.compile(r"final[ ._-]*cut", re.IGNORECASE)),
    ("Extended", re.compile(r"(^|[^a-z])extended([^a-z]|$)", re.IGNORECASE)),
    ("Unrated", re.compile(r"(^|[^a-z])unrated([^a-z]|$)", re.IGNORECASE)),
    ("Uncut", re.compile(r"(^|[^a-z])uncut([^a-z]|$)", re.IGNORECASE)),
    ("Theatrical", re.compile(r"(^|[^a-z])theatrical([^a-z]|$)", re.IGNORECASE)),
    ("Remastered", re.compile(r"(^|[^a-z])remastered([^a-z]|$)", re.IGNORECASE)),
    ("IMAX", re.compile(r"(^|[^a-z])imax([^a-z]|$)", re.IGNORECASE)),
    ("Criterion", re.compile(r"(^|[^a-z])criterion([^a-z]|$)", re.IGNORECASE)),
)

VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|m4v|avi|mov|wmv|flv|webm|ts|m2ts)$", re.IGNORECASE)
RELEASE_GROUP_RE = re.compile(r"-([A-Za-z0-9][A-Za-z0-9@._]*)$")
CODEC_GROUPS = frozenset(
    {
        "x264",
        "x265",
        "h264",
        "h265",
        "hevc",
        "av1",
        "aac",
        "ac3",
        "dts",
        "truehd",
        "ddp",
        "dl",
        "rip",
        "web",
        "webrip",
        "hdtv",
        "bluray",
        "remux",
        "proper",
        "repack",
    }
)

SORT_MODES = ("seeders", "quality", "size")


def detect_quality(text: str | None) -> str:
    """Return one of the supported quality labels, case-insensitively."""

    if not text:
        return UNKNOWN_QUALITY
    lowered = text.lower()
    for label, pattern in QUALITY_PATTERNS:
        if pattern.search(lowered):
            return label
    return UNKNOWN_QUALITY


def quality_rank(quality: str | None) -> int:
    if not quality:
        return UNKNOWN_RANK
    return QUALITY_RANK.get(quality.lower(), UNKNOWN_RANK)


def parse_size_bytes(value: str | int | float | None) -> int | None:
    """Parse ``209.37 MB``, ``1.5GiB`` or a bare byte count.

    Missing or unparseable values return ``None`` — never ``0``, which would rank as a
    real zero-sized release.
    """

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = value.strip()
    if not text:
        return None
    match = SIZE_RE.search(text)
    if match is None:
        return None
    number = match.group("number").replace(",", ".")
    unit = (match.group("unit") or "").upper()
    factor = SIZE_FACTORS.get(unit)
    if factor is None:
        return None
    try:
        amount = float(number)
    except ValueError:
        return None
    return int(amount * factor)


def detect_source(text: str | None) -> str | None:
    if not text:
        return None
    for label, pattern in SOURCE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def detect_video_codec(text: str | None) -> str | None:
    if not text:
        return None
    for label, pattern in VIDEO_CODEC_PATTERNS:
        if pattern.search(text):
            return label
    return None


def detect_audio_hint(text: str | None) -> str | None:
    if not text:
        return None
    for label, pattern in AUDIO_PATTERNS:
        if pattern.search(text):
            return label
    return None


def detect_languages(text: str | None) -> list[str] | None:
    if not text:
        return None
    found: list[str] = []
    for match in LANGUAGE_RE.finditer(text):
        canonical = LANGUAGE_ALIASES[match.group(1).lower()]
        if canonical not in found:
            found.append(canonical)
    return found or None


def detect_release_group(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = VIDEO_EXT_RE.sub("", filename.strip())
    match = RELEASE_GROUP_RE.search(stem)
    if match is None:
        return None
    group = match.group(1).strip(".-_")
    if not group or group.lower() in CODEC_GROUPS:
        return None
    return group


def detect_edition(text: str | None) -> str | None:
    if not text:
        return None
    for label, pattern in EDITION_PATTERNS:
        if pattern.search(text):
            return label
    return None


def extract_hints(filename: str, title: str | None = None) -> dict[str, Any]:
    """Resolved, nullable hints stored as ``releases.metadata_json``."""

    text = filename if not title else f"{filename} {title}"
    return {
        "source": detect_source(text),
        "video_codec": detect_video_codec(text),
        "audio_hint": detect_audio_hint(text),
        "languages": detect_languages(text),
        "release_group": detect_release_group(filename),
        "edition": detect_edition(text),
    }


def filter_quality(releases: Iterable[Release], blocked: Sequence[str]) -> list[Release]:
    """Drop releases whose quality label is in ``blocked`` (case-insensitive)."""

    bad = {label.strip().lower() for label in blocked if label and label.strip()}
    if not bad:
        return list(releases)
    return [release for release in releases if release.quality.lower() not in bad]


MIN_QUALITIES: tuple[str, ...] = ("480p", "576p", "720p", "1080p", "1440p", "2160p")
QUALITY_DISPLAY_ORDER: tuple[str, ...] = (
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "576p",
    "480p",
)


def apply_min_quality(releases: Iterable[Release], min_quality: str | None) -> list[Release]:
    """Keep releases at or above ``min_quality``; ``unknown`` is excluded when set."""

    if not min_quality:
        return list(releases)
    floor = quality_rank(min_quality)
    return [
        release
        for release in releases
        if release.quality.lower() != UNKNOWN_QUALITY and quality_rank(release.quality) >= floor
    ]


def dedupe_releases(releases: Iterable[Release]) -> tuple[list[Release], int]:
    """Collapse rows sharing an info-hash, keeping the highest seeder count.

    Providers overlap heavily; the same torrent often appears twice with different
    provider labels. The first position is preserved so sorting stays deterministic.
    """

    kept: dict[str, Release] = {}
    order: list[str] = []
    duplicates = 0
    for release in releases:
        key = release.info_hash.strip().lower()
        if not key:
            duplicates += 1
            continue
        current = kept.get(key)
        if current is None:
            kept[key] = release
            order.append(key)
            continue
        duplicates += 1
        if release.seeders > current.seeders:
            kept[key] = release
    return [kept[key] for key in order], duplicates


def quality_counts(releases: Iterable[Release]) -> dict[str, int]:
    """Count filtered rows per display quality, highest quality first."""

    counts: dict[str, int] = {}
    unknown = 0
    for release in releases:
        quality = release.quality.lower() or UNKNOWN_QUALITY
        if quality in QUALITY_DISPLAY_ORDER:
            counts[quality] = counts.get(quality, 0) + 1
        else:
            unknown += 1
    ordered = {quality: counts[quality] for quality in QUALITY_DISPLAY_ORDER if quality in counts}
    if unknown:
        ordered[UNKNOWN_QUALITY] = unknown
    return ordered


def sort_releases(releases: Iterable[Release], mode: str = "seeders") -> list[Release]:
    """Sort releases, preserving input order for exact ties.

    Unknown sort modes fall back to ``seeders``.
    """

    rows = list(releases)
    if mode == "quality":
        key = lambda release: (-quality_rank(release.quality), -release.seeders)  # noqa: E731
    elif mode == "size":
        key = lambda release: (  # noqa: E731
            -(release.size_bytes if release.size_bytes is not None else 0),
            -release.seeders,
        )
    else:
        key = lambda release: (-release.seeders, -quality_rank(release.quality))  # noqa: E731
    return sorted(rows, key=key)
