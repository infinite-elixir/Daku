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

Confidence = Literal["mapped", "exact", "none"]
IMDB_MAPPING_PROVIDERS = ("imdb_show", "imdb_movie", "imdb", "imdb_id")
ANIME_FORMATS = frozenset({"TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT"})

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


def match_exact(candidate: TitleCandidate, media: Iterable[TitleCandidate]) -> AnimeSignal:
    """Return the first conservative AniList exact match for one IMDb candidate."""

    if candidate.kind not in {"series", "movie"}:
        return AnimeSignal(False, "none", None, "No conservative anime match was found")
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
        if signal.is_anime:
            return signal
    return AnimeSignal(False, "none", None, "No conservative anime match was found")


def enrich_exact(
    candidates: Iterable[TitleCandidate],
    media: Iterable[TitleCandidate],
) -> int:
    """Apply AniList exact-match signals in place. Returns how many were classified."""

    media_list = list(media)
    changed = 0
    for candidate in candidates:
        if candidate.anime or candidate.kind not in {"series", "movie"}:
            continue
        signal = match_exact(candidate, media_list)
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
