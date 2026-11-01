from __future__ import annotations

from typing import Any

import httpx
import pytest

from daku.services.metadata.aliases import build_generated_aliases, derive_styles
from daku.services.metadata.models import TitleDetail
from daku.services.torrents import sources as sources_module
from daku.services.torrents.animetosho import (
    MAX_QUERIES,
    AnimeToshoSearch,
    build_bare_queries,
    build_episode_queries,
    build_query_plan,
    build_search_url,
    matches_query,
)
from daku.services.torrents.base import Release


def _release(number: int, filename: str = "Steel Ball Run - 07 [1080p]") -> Release:
    return Release(
        info_hash=f"{number:040x}",
        filename=filename,
        quality="1080p",
        size_bytes=100,
        seeders=1,
        provider="animetosho",
        magnet=f"magnet:?xt=urn:btih:{number:040x}",
        metadata={},
    )


class _Result:
    stale = False
    cached_at = None

    def __init__(self, releases: list[Release]) -> None:
        self.releases = releases


def test_alias_styles_are_deterministic_and_deduped() -> None:
    styles = derive_styles("Steel Ball Run JoJo's Bizarre Adventure")

    assert styles[0] == "Steel Ball Run"
    assert "Steel Ball Run JoJo's Bizarre Adventure" in styles
    assert "SBR" in styles
    assert len(styles) == len({value.casefold() for value in styles})


def test_romanized_base_name_and_distinctive_subtitle_are_both_searched() -> None:
    styles = derive_styles("JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE")

    assert styles[0] == "JoJo no Kimyou na Bouken"
    assert "Steel Ball Run" in styles
    assert all("1st STAGE" not in value for value in styles[:4])


def test_stage_qualifier_does_not_consume_the_query_budget() -> None:
    styles = derive_styles("STEEL BALL RUN JoJo's Bizarre Adventure 1st STAGE")

    assert styles[0] == "STEEL BALL RUN"
    assert "STEEL BALL RUN JoJo's Bizarre Adventure" in styles


def test_punctuation_spaced_and_compact_forms_both_exist() -> None:
    styles = derive_styles("JoJo's Bizarre Adventure")

    assert "JoJo s Bizarre Adventure" in styles
    assert "JoJos Bizarre Adventure" in styles
    assert "JBA" in styles


def test_trailing_year_is_dropped() -> None:
    assert derive_styles("Zom 100 (2023)")[0] == "Zom 100"
    assert derive_styles("Zom 100 2023")[0] == "Zom 100"


def test_native_alias_is_sent_as_written() -> None:
    assert derive_styles("進撃の巨人") == ["進撃の巨人"]


def test_alias_generation_uses_every_anilist_spelling() -> None:
    detail = TitleDetail(
        source="anilist",
        source_id="1",
        kind="anime",
        title="Shingeki no Kyojin",
        aliases=["Attack on Titan"],
        synonyms=["AoT", "進撃の巨人"],
    )

    aliases = build_generated_aliases("Shingeki no Kyojin", [detail], ["SnK"])

    assert aliases == ["Shingeki no Kyojin", "Attack on Titan", "AoT", "進撃の巨人", "SnK"]


def test_query_plan_keeps_bare_aliases_first_and_caps_at_eight() -> None:
    aliases = [f"Alias {index}" for index in range(1, 11)]

    plan = build_query_plan(aliases, season=1, episode=7, provider_seasons=[6])

    assert len(plan) == MAX_QUERIES
    assert plan[: len(build_bare_queries(aliases))] == build_bare_queries(aliases)


def test_query_plan_uses_provider_season_notation() -> None:
    queries = build_episode_queries(["Steel Ball Run"], season=1, episode=7, provider_seasons=[6])

    assert queries[0] == "Steel Ball Run S06E07"
    assert "Steel Ball Run E07" in queries
    assert "Steel Ball Run Episode 7" in queries


def test_query_budget_and_control_characters() -> None:
    assert build_search_url("a" * 200).split("q=", 1)[1] == "a" * 120
    assert build_search_url("bad\x00query\n") == ("https://feed.animetosho.net/json?q=bad+query")


def test_coverage_matching_requires_whole_alias() -> None:
    filename = "[Judas] JoJo's Bizarre Adventure - Steel Ball Run (SBR) - 07 [1080p]"

    assert matches_query("Steel Ball Run", filename)
    assert matches_query("SBR", filename)
    assert not matches_query("Run", filename)
    assert not matches_query("Steel", filename)
    assert not matches_query("Different Show", filename)


@pytest.mark.asyncio
async def test_derived_alias_styles_also_match_release_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(client: Any, cache: Any, query: str) -> AnimeToshoSearch:
        return _Result(  # type: ignore[return-value]
            [
                _release(
                    7,
                    "[Erai-raws] JoJo no Kimyou na Bouken: Steel Ball Run - 07 [1080p]",
                )
            ]
        )

    monkeypatch.setattr(sources_module, "fetch_animetosho", fake_fetch)

    async with httpx.AsyncClient() as client:
        outcome = await sources_module.search_all_sources(
            client,
            None,
            kind="anime",
            title="JoJo's Bizarre Adventure Part 7: Steel Ball Run",
            imdb_id=None,
            season=1,
            episode=7,
            pack=False,
            sources=["animetosho"],
            season_fallback=True,
        )

    assert {release.filename for release in outcome.releases} == {
        "[Erai-raws] JoJo no Kimyou na Bouken: Steel Ball Run - 07 [1080p]"
    }


@pytest.mark.asyncio
async def test_no_episode_suffix_when_bare_names_deliver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch(client: Any, cache: Any, query: str) -> AnimeToshoSearch:
        calls.append(query)
        return _Result([_release(index) for index in range(20)])  # type: ignore[return-value]

    monkeypatch.setattr(sources_module, "fetch_animetosho", fake_fetch)

    async with httpx.AsyncClient() as client:
        outcome = await sources_module.search_all_sources(
            client,
            None,
            kind="anime",
            title="Steel Ball Run",
            imdb_id=None,
            season=1,
            episode=7,
            pack=False,
            sources=["animetosho"],
            provider_seasons=[6],
        )

    assert outcome.queries == len(calls)
    assert calls == build_bare_queries(["Steel Ball Run"])
