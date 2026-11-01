"""Conservative anime classification backed by AniBridge mappings."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import AnimeMapping
from daku.services.metadata.models import TitleCandidate

Confidence = Literal["mapped", "exact", "alias", "none"]
IMDB_MAPPING_PROVIDERS = ("imdb_show", "imdb_movie", "imdb", "imdb_id")
ANIME_FORMATS = frozenset({"TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT"})
# Words that appear in almost every entry of a long franchise. They must not make
# "JoJo's Bizarre Adventure: Stone Ocean" look like a Steel Ball Run cour.
_FRANCHISE_STOPWORDS = frozenset(
    {
        "a",
        "adventure",
        "an",
        "and",
        "bizarre",
        "bouken",
        "jojo",
        "jojos",
        "kimyou",
        "na",
        "no",
        "of",
        "part",
        "the",
    }
)

logger = logging.getLogger("daku.metadata.classification")


@dataclass(frozen=True, slots=True)
class AnimeSignal:
    is_anime: bool
    confidence: Confidence
    anilist_id: int | None
    reason: str


def _normalise_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def mapping_dataset_present(session: Session) -> bool:
    """True when any mapping has been ingested. Avoids guessing without data."""

    return session.scalar(select(AnimeMapping.src_id).limit(1)) is not None


def mapped_anilist_ids(session: Session, imdb_ids: list[str]) -> dict[str, int]:
    """Return mapped AniList ids for IMDb ids with one database query."""

    ids = list(dict.fromkeys(value for value in imdb_ids if value))
    if not ids:
        return {}
    statement = (
        select(AnimeMapping.src_id, AnimeMapping.tgt_id)
        .where(
            AnimeMapping.src_provider.in_(IMDB_MAPPING_PROVIDERS),
            AnimeMapping.src_id.in_(ids),
            AnimeMapping.tgt_provider == "anilist",
        )
        .distinct()
        .order_by(AnimeMapping.src_id, AnimeMapping.tgt_id)
    )
    result: dict[str, int] = {}
    for source_id, target_id in session.execute(statement):
        try:
            result.setdefault(str(source_id), int(target_id))
        except (TypeError, ValueError):
            continue
    return result


def classify_mapped(session: Session, imdb_id: str) -> AnimeSignal:
    anilist_id = mapped_anilist_ids(session, [imdb_id]).get(imdb_id)
    if anilist_id is None:
        return AnimeSignal(False, "none", None, "No AniBridge anime mapping was found")
    return AnimeSignal(True, "mapped", anilist_id, "Detected via AniBridge mapping")


def anilist_ids_with_mapping(session: Session, anilist_ids: Iterable[int]) -> set[int]:
    """AniList ids that AniBridge maps to a provider season.

    Used as a tie-break: when two cours of a franchise both match, the one with a
    season mapping is the one release names and library seasons already use.
    """

    ids = [str(value) for value in dict.fromkeys(anilist_ids)]
    if not ids:
        return set()
    statement = (
        select(AnimeMapping.tgt_id)
        .where(
            AnimeMapping.tgt_provider == "anilist",
            AnimeMapping.tgt_id.in_(ids),
            AnimeMapping.src_provider.in_(("tvdb_show", "imdb_show", "tmdb_show")),
        )
        .distinct()
    )
    mapped: set[int] = set()
    for (value,) in session.execute(statement):
        try:
            mapped.add(int(value))
        except (TypeError, ValueError):
            continue
    return mapped


def exact_signal(
    *,
    title: str,
    year: int | None,
    candidate_title: str,
    candidate_year: int | None,
    synonyms: list[str] | None,
    format: str | None,
    country_of_origin: str | None,
    anilist_id: int | None,
) -> AnimeSignal:
    names = {_normalise_title(candidate_title)}
    names.update(_normalise_title(value) for value in synonyms or [])
    if (
        anilist_id is not None
        and _normalise_title(title) in names
        and year is not None
        and candidate_year is not None
        and abs(year - candidate_year) <= 1
        and (format or "").upper() in ANIME_FORMATS
        and (country_of_origin or "").upper() == "JP"
    ):
        return AnimeSignal(True, "exact", anilist_id, "AniList exact match")
    return AnimeSignal(False, "none", None, "No conservative anime match was found")


def _significant_tokens(value: str) -> tuple[str, ...]:
    tokens = {
        part
        for part in re.findall(r"[a-z0-9]+", value.casefold())
        if len(part) > 1 and part not in _FRANCHISE_STOPWORDS
    }
    return tuple(sorted(tokens))


def _alias_signal(
    *,
    title: str,
    year: int | None,
    candidate_title: str,
    candidate_year: int | None,
    synonyms: list[str] | None,
    format: str | None,
    country_of_origin: str | None,
    anilist_id: int | None,
) -> AnimeSignal:
    """Accept a candidate when its distinctive words are a subset of one AniList name.

    Exact equality cannot match ``Steel Ball Run: JoJo's Bizarre Adventure`` to
    ``STEEL BALL RUN ... 1st STAGE``. This is still conservative: the year must be
    within one, the entry must be Japanese anime, and the candidate needs at least
    two distinctive words after franchise boilerplate is removed.
    """

    if (
        anilist_id is None
        or year is None
        or candidate_year is None
        or abs(year - candidate_year) > 1
        or (format or "").upper() not in ANIME_FORMATS
        or (country_of_origin or "").upper() != "JP"
    ):
        return AnimeSignal(False, "none", None, "No conservative anime match was found")

    wanted = _significant_tokens(title)
    if len(wanted) < 2:
        return AnimeSignal(False, "none", None, "No conservative anime match was found")

    wanted_set = set(wanted)
    names = [candidate_title, *(synonyms or [])]
    if any(wanted_set <= set(_significant_tokens(name)) for name in names if name.strip()):
        return AnimeSignal(True, "alias", anilist_id, "AniList alias match")
    return AnimeSignal(False, "none", None, "No conservative anime match was found")


def match_exact(
    candidate: TitleCandidate,
    media: Iterable[TitleCandidate],
    preferred_ids: Iterable[int] | None = None,
) -> AnimeSignal:
    """Return the best conservative AniList match for one IMDb candidate.

    Exact normalized titles win. If none match, a distinctive alias match may be
    accepted for split-cour entries. Ties prefer an AniBridge season mapping, then
    the earliest ``seasonYear``, then the lowest AniList id, so a search result
    order cannot change the chosen identity.
    """

    if candidate.kind not in {"series", "movie"}:
        return AnimeSignal(False, "none", None, "No conservative anime match was found")

    preferred = set(preferred_ids or ())
    exact: list[tuple[int, int, int, AnimeSignal]] = []
    alias: list[tuple[int, int, int, AnimeSignal]] = []
    for item in media:
        try:
            anilist_id = int(item.source_id)
        except (TypeError, ValueError):
            continue
        signal = exact_signal(
            title=candidate.title,
            year=candidate.year,
            candidate_title=item.title,
            candidate_year=item.year,
            synonyms=list(item.synonyms),
            format=item.format,
            country_of_origin=item.country_of_origin,
            anilist_id=anilist_id,
        )
        if not signal.is_anime:
            signal = _alias_signal(
                title=candidate.title,
                year=candidate.year,
                candidate_title=item.title,
                candidate_year=item.year,
                synonyms=list(item.synonyms),
                format=item.format,
                country_of_origin=item.country_of_origin,
                anilist_id=anilist_id,
            )
        if not signal.is_anime:
            continue
        season_year = item.year if item.year is not None else 9999
        bucket = exact if signal.confidence == "exact" else alias
        bucket.append(
            (
                0 if anilist_id in preferred else 1,
                season_year,
                anilist_id,
                signal,
            )
        )

    for bucket in (exact, alias):
        if bucket:
            bucket.sort(key=lambda row: (row[0], row[1], row[2]))
            return bucket[0][3]
    return AnimeSignal(False, "none", None, "No conservative anime match was found")


def enrich_exact(
    candidates: Iterable[TitleCandidate],
    media: Iterable[TitleCandidate],
    preferred_ids: Iterable[int] | None = None,
) -> int:
    """Apply AniList exact-match signals in place. Returns how many were classified."""

    media_list = list(media)
    preferred = set(preferred_ids or ())
    changed = 0
    for candidate in candidates:
        if candidate.anime or candidate.kind not in {"series", "movie"}:
            continue
        signal = match_exact(candidate, media_list, preferred)
        if not signal.is_anime or signal.anilist_id is None:
            continue
        candidate.anime = True
        candidate.anilist_id = signal.anilist_id
        candidate.classification_reason = signal.reason
        changed += 1
        logger.info(
            "title classified",
            extra={
                "source_id": candidate.source_id,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "anilist_id": signal.anilist_id,
            },
        )
    return changed
