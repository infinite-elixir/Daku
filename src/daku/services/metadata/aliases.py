"""Release-search aliases and their deterministic spellings (T-053).

AniList knows several spellings for one entry: the English, romaji and native title,
plus an open-ended ``synonyms`` list. Release groups use all of them, and some rewrite
punctuation or use an acronym. This module turns that inventory into a small, stable
set of query spellings. It never guesses a translation or a fan title.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from daku.models import Title
from daku.services.metadata.models import TitleDetail

# Words that are part of a long franchise name rather than the cour identity.
_FRANCHISE_WORDS = frozenset(
    {
        "adventure",
        "bizarre",
        "bouken",
        "jojo",
        "jojos",
        "kimyou",
        "na",
        "no",
        "part",
    }
)
_YEAR_SUFFIX_RE = re.compile(r"\s*(?:\(\s*(?:19|20)\d{2}\s*\)|(?:19|20)\d{2})\s*$")
_PUNCTUATION_RE = re.compile(r"[:'&.!]")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_ORDINAL = r"(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|final)"
_TRAILING_STAGE_RE = re.compile(
    rf"(?i)(?:\s*[-:\u2013\u2014]\s*|\s+){_ORDINAL}"
    rf"(?:\s*[&,\uff06\-\u2013\u2014]\s*{_ORDINAL})*\s+(?:stage|cour)\b.*$"
)
_TRAILING_SEASON_RE = re.compile(
    r"(?i)(?:\s*[-:\u2013\u2014]\s*|\s+)(?:stage|cour|season)\s*\d+\s*$"
)
_TRAILING_PART_RE = re.compile(r"(?i)(?:\s*[-:\u2013\u2014]\s*|\s+)part\s*\d+\s*$")


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip()).strip()


def clean_alias(value: str) -> str | None:
    """Trim, collapse whitespace and drop a trailing release year."""

    cleaned = _clean(value)
    if not cleaned:
        return None
    cleaned = _YEAR_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned or None


def _strip_trailing_markers(alias: str) -> str:
    """Drop stage/cour/season/part markers that narrow an upstream feed query."""

    value = _clean(alias)
    for _ in range(3):
        previous = value
        for pattern in (_TRAILING_STAGE_RE, _TRAILING_SEASON_RE, _TRAILING_PART_RE):
            stripped = _clean(pattern.sub("", value))
            if stripped and stripped != value:
                value = stripped
        if value == previous:
            break
    return value


def _leading_segment(alias: str) -> str | None:
    """The franchise/base name before a colon or spaced dash, when it is a name."""

    parts = re.split(r"\s*:\s*|\s+[-\u2013\u2014]\s+", alias, maxsplit=1)
    if len(parts) < 2:
        return None
    segment = _clean(parts[0])
    if not segment or segment == alias or len(segment.split()) < 2:
        return None
    return segment


def _punctuation_styles(alias: str) -> list[str]:
    """Keep a spaced form and a compact form for punctuation providers rewrite."""

    if not _PUNCTUATION_RE.search(alias):
        return []
    spaced = _clean(_PUNCTUATION_RE.sub(" ", alias))
    compact = _clean(_PUNCTUATION_RE.sub("", alias))
    return [value for value in (spaced, compact) if value and value != alias]


def _trim_franchise(alias: str) -> str | None:
    """Drop a franchise-only run at either edge, if a distinctive name remains."""

    words = alias.split()
    if len(words) < 4:
        return None

    def franchise_word(item: str) -> bool:
        token = re.sub(r"[^a-z0-9]", "", item.casefold())
        return not token or len(token) == 1 or token in _FRANCHISE_WORDS

    def significant(items: list[str]) -> int:
        return sum(
            1
            for item in items
            if len(re.sub(r"[^A-Za-z0-9]", "", item)) > 1
            and item.casefold() not in _FRANCHISE_WORDS
        )

    candidates: list[str] = []
    for start in range(1, len(words)):
        if all(franchise_word(word) for word in words[:start]):
            remainder = words[start:]
            if significant(remainder) >= 2:
                candidates.append(_clean(" ".join(remainder)))
        if all(franchise_word(word) for word in words[-start:]):
            remainder = words[: len(words) - start]
            if significant(remainder) >= 2:
                candidates.append(_clean(" ".join(remainder)))
    return min(candidates, key=len) if candidates else None


def _acronym(alias: str) -> str | None:
    words = [word for word in _WORD_RE.findall(alias) if len(word) > 1]
    if len(words) < 3:
        return None
    letters = "".join(word[0] for word in words)
    if len(letters) < 3 or len(letters) > 8:
        return None
    return letters.upper()


def derive_styles(alias: str) -> list[str]:
    """Return the deterministic spellings for one alias, broadest first."""

    cleaned = clean_alias(alias)
    if cleaned is None:
        return []

    base = _strip_trailing_markers(cleaned)
    # Native/CJK names are indexed as written; dropping an ASCII qualifier is
    # still useful, but rewriting the native text itself only loses recall.
    if _NON_ASCII_RE.search(cleaned):
        return [base]

    styles: list[str] = []
    leading = _leading_segment(base)
    if leading:
        styles.append(leading)
    trimmed = _trim_franchise(base)
    if trimmed:
        styles.append(trimmed)
    styles.append(base)
    styles.extend(_punctuation_styles(base))
    if cleaned != base:
        styles.append(cleaned)
        styles.extend(_punctuation_styles(cleaned))

    for value in list(styles):
        trimmed_value = _trim_franchise(value)
        if trimmed_value:
            styles.append(trimmed_value)
        leading_value = _leading_segment(value)
        if leading_value:
            styles.append(leading_value)

    for value in list(styles):
        acronym = _acronym(value)
        if acronym:
            styles.append(acronym)

    result: list[str] = []
    seen: set[str] = set()
    for value in styles:
        # Keep the spaced and compact punctuation forms even though the query plan
        # later folds them into one upstream request.
        key = _SPACE_RE.sub(" ", value.casefold()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def normalise_query(value: str) -> str:
    """Key used to dedupe queries: casefold, collapse whitespace, strip punctuation."""

    folded = value.casefold()
    folded = folded.replace("'", "").replace("\u2019", "")
    folded = _QUERY_PUNCTUATION_RE.sub(" ", folded)
    return _SPACE_RE.sub(" ", folded).strip()


def dedupe_aliases(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_alias(str(value))
        if cleaned is None:
            continue
        key = normalise_query(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def build_generated_aliases(
    title: str,
    details: Iterable[TitleDetail | None] = (),
    extras: Iterable[str] = (),
) -> list[str]:
    """Build the inventory from the stored title and every known AniList spelling."""

    values: list[str] = [title]
    for detail in details:
        if detail is None:
            continue
        values.append(detail.title)
        values.extend(detail.aliases)
        values.extend(detail.synonyms)
    values.extend(extras)
    return dedupe_aliases(value for value in values if value)


def _load_json(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed: Any = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item.strip()]


def generated_aliases(title: Title) -> list[str]:
    return _load_json(title.aliases_json)


def store_generated_aliases(session: Any, title: Title, aliases: list[str]) -> None:
    """Persist the generated inventory, independent of a manual override."""

    title.aliases_json = json.dumps(dedupe_aliases(aliases), ensure_ascii=False)
    session.flush()


def effective_aliases(session: Any, title: Title) -> list[str]:
    """Manual ``aliases`` override wins; otherwise return the generated inventory."""

    from daku.services.metadata.overrides import load_field_overrides

    override = load_field_overrides(session, title.id).get("aliases")
    if isinstance(override, list):
        values = dedupe_aliases(str(item) for item in override if isinstance(item, str))
        if values:
            return values
    values = generated_aliases(title)
    if not values:
        values = dedupe_aliases([title.title])
    return values


__all__ = [
    "build_generated_aliases",
    "clean_alias",
    "dedupe_aliases",
    "derive_styles",
    "effective_aliases",
    "generated_aliases",
    "normalise_query",
    "store_generated_aliases",
]
