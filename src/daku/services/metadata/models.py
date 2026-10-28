"""Internal metadata models shared by every adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TitleCandidate:
    source: str
    source_id: str
    kind: str
    title: str
    year: int | None = None
    poster_url: str | None = None
    raw_type: str | None = None
    id_mal: int | None = None
    format: str | None = None
    episode_count: int | None = None
    anime: bool = False
    anilist_id: int | None = None
    classification_reason: str | None = None
    country_of_origin: str | None = None
    synonyms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "source_id": self.source_id,
            "kind": self.kind,
            "title": self.title,
            "year": self.year,
            "poster_url": self.poster_url,
        }
        if self.kind == "anime":
            payload["id_mal"] = self.id_mal
            payload["format"] = self.format
            payload["episode_count"] = self.episode_count
        if self.anime:
            payload["anime"] = True
            payload["anilist_id"] = self.anilist_id
            payload["classification_reason"] = self.classification_reason
        return payload


@dataclass(slots=True)
class EpisodeRef:
    season: int
    episode: int
    title: str | None = None
    imdb_id: str | None = None
    air_date: str | None = None

    @property
    def label(self) -> str:
        return f"S{self.season:02d}E{self.episode:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "episode": self.episode,
            "label": self.label,
            "title": self.title,
            "air_date": self.air_date,
            "imdb_id": self.imdb_id,
        }


@dataclass(slots=True)
class TitleDetail:
    source: str
    source_id: str
    kind: str
    title: str
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    episodes: list[EpisodeRef] = field(default_factory=list)
    raw_type: str | None = None
    id_mal: int | None = None
    format: str | None = None
    episode_count: int | None = None
    banner_url: str | None = None
    studios: list[str] = field(default_factory=list)
    status: str | None = None
    season: str | None = None
    season_year: int | None = None
    synonyms: list[str] = field(default_factory=list)
    relations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # AniList streaming episode labels, used as a last-resort name source (T-036).
    streaming_episodes: list[dict[str, Any]] = field(default_factory=list)
    # Display-only enrichment (T-024). Ratings are never persisted.
    imdb_rating: float | None = None
    rt_rating: int | None = None
    metacritic_rating: int | None = None
    runtime_minutes: int | None = None
    genres: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["episodes"] = [episode.to_dict() for episode in self.episodes]
        if self.kind != "anime":
            for key in (
                "id_mal",
                "format",
                "episode_count",
                "studios",
                "status",
                "season",
                "season_year",
                "synonyms",
                "relations",
                "streaming_episodes",
            ):
                payload.pop(key, None)
        return payload
