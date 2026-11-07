"""Release-source registry.

Adding a source is one :class:`SourceSpec` entry plus one adapter module. The router
and the aggregator resolve names here instead of branching on hardcoded strings.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS: frozenset[str] = frozenset({"movie", "series", "anime"})


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    kinds: frozenset[str]
    requires_imdb: bool
    keyword: bool
    # Registered but not yet selectable: the adapter may still be landing.
    available: bool = True


SOURCES: dict[str, SourceSpec] = {
    "torrentio": SourceSpec(
        name="torrentio",
        kinds=KINDS,
        requires_imdb=True,
        keyword=False,
    ),
    "animetosho": SourceSpec(
        name="animetosho",
        kinds=frozenset({"anime"}),
        requires_imdb=False,
        keyword=True,
    ),
    "torrents-csv": SourceSpec(
        name="torrents-csv",
        kinds=frozenset({"movie", "series"}),
        requires_imdb=False,
        keyword=True,
    ),
}


def available_sources(kind: str) -> tuple[str, ...]:
    """Names a user may select for ``kind``, in registry order."""

    return tuple(spec.name for spec in SOURCES.values() if spec.available and kind in spec.kinds)


def is_known_source(name: str, kind: str) -> bool:
    spec = SOURCES.get(name)
    return spec is not None and spec.available and kind in spec.kinds


def get_source(name: str) -> SourceSpec | None:
    return SOURCES.get(name)
