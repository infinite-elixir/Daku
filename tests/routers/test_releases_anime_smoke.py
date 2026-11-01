from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import seasons as seasons_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata.aliases import store_generated_aliases
from daku.services.torrents import sources as sources_module
from daku.services.torrents.animetosho import (
    MAX_QUERIES,
    AnimeToshoSearch,
    build_bare_queries,
    build_episode_queries,
    build_query_plan,
)
from daku.services.torrents.base import Release


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    plain = settings.model_copy(update={"torrentio_config": "", "providers": []})
    with TestClient(create_app(plain, configure_logs=False)) as test_client:
        yield test_client


def _release(number: int, *, filename: str = "Steel Ball Run - 07 [1080p]") -> Release:
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


async def test_bare_aliases_are_searched_before_episode_styles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch(client: Any, cache: Any, query: str) -> AnimeToshoSearch:
        calls.append(query)
        return _Result([])  # type: ignore[return-value]

    monkeypatch.setattr(sources_module, "fetch_animetosho", fake_fetch)
    aliases = [f"Alias {index}" for index in range(1, 7)]

    outcome = await sources_module.search_all_sources(
        httpx.AsyncClient(),
        None,
        kind="anime",
        title="Alias 1",
        imdb_id=None,
        season=1,
        episode=7,
        pack=False,
        sources=["animetosho"],
        synonyms=aliases[1:],
        provider_seasons=[6],
    )

    assert outcome.aliases == 6
    assert outcome.queries <= MAX_QUERIES
    assert calls[:6] == build_bare_queries(aliases)
    assert len(calls) <= MAX_QUERIES


async def test_search_stops_after_forty_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch(client: Any, cache: Any, query: str) -> AnimeToshoSearch:
        calls.append(query)
        return _Result([_release(index) for index in range(50)])  # type: ignore[return-value]

    monkeypatch.setattr(sources_module, "fetch_animetosho", fake_fetch)

    outcome = await sources_module.search_all_sources(
        httpx.AsyncClient(),
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

    # Concurrency may have started the acronym spelling, but no suffix query is issued.
    assert calls == build_bare_queries(["Steel Ball Run"])
    assert outcome.queries == len(calls)
    assert len(outcome.releases) >= 50


async def test_partial_failure_keeps_results_and_marks_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch(client: Any, cache: Any, query: str) -> AnimeToshoSearch:
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("upstream down")
        return _Result([_release(1)])  # type: ignore[return-value]

    monkeypatch.setattr(sources_module, "fetch_animetosho", fake_fetch)

    outcome = await sources_module.search_all_sources(
        httpx.AsyncClient(),
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

    assert outcome.sources == ["animetosho"]
    assert outcome.sources_degraded == ["animetosho"]
    assert outcome.releases


def test_query_plan_is_bounded_and_deduped() -> None:
    aliases = ["Steel Ball Run", "Steel Ball Run", "SBR"]
    plan = build_query_plan(aliases, season=1, episode=7, provider_seasons=[6])

    assert len(plan) == len(dict.fromkeys(plan))
    assert len(plan) <= MAX_QUERIES
    assert plan[0] == "Steel Ball Run"
    assert "Steel Ball Run S06E07" in plan
    assert len(build_bare_queries(aliases)) == 2
    assert len(build_episode_queries(aliases, season=1, episode=7, provider_seasons=[6])) > 0


def _add_steel_ball_run(client: TestClient, aliases: list[str]) -> str:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = titles_repo.create_title(
            session,
            kind="anime",
            title="Steel Ball Run",
            year=2026,
            anilist_id=190327,
        )
        store_generated_aliases(session, row, aliases)
        seasons_repo.upsert_seasons(
            session,
            row.id,
            [
                {
                    "library_season": 1,
                    "source": "anilist",
                    "source_id": "190327",
                    "provider_season": 6,
                }
            ],
        )
        return row.id


def _feed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows


def test_steel_ball_run_provider_season_matches_episode_seven(client: TestClient) -> None:
    title_id = _add_steel_ball_run(client, ["Steel Ball Run", "JoJo's Bizarre Adventure"])
    payload = _feed(
        [
            {
                "info_hash": "a" * 40,
                "title": "[Judas] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]",
                "total_size": 1_000,
                "seeders": 50,
            },
            {
                "info_hash": "b" * 40,
                "title": "[Judas] JoJo's Bizarre Adventure - Steel Ball Run S06E08 [1080p]",
                "total_size": 1_000,
                "seeders": 49,
            },
        ]
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="feed.animetosho.net").mock(
            return_value=httpx.Response(200, text=json.dumps(payload))
        )
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 7},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["info_hash"] for row in body["releases"]] == ["a" * 40]
    assert body["search"]["queries"] <= MAX_QUERIES
    assert body["search"]["aliases"] == 2
    assert set(body["search"]) == {"queries", "aliases", "degraded"}

    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="feed.animetosho.net").mock(
            return_value=httpx.Response(200, text=json.dumps(payload))
        )
        wrong = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 8},
        )

    assert wrong.status_code == 200, wrong.text
    assert [row["info_hash"] for row in wrong.json()["releases"]] == ["b" * 40]


def test_covered_season_pack_is_returned_and_flagged(client: TestClient) -> None:
    title_id = _add_steel_ball_run(client, ["Steel Ball Run", "JoJo's Bizarre Adventure"])
    payload = _feed(
        [
            {
                "info_hash": "a" * 40,
                "title": "[Judas] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]",
                "total_size": 1_000,
                "seeders": 50,
            },
            {
                "info_hash": "c" * 40,
                "title": "[Judas] JoJo's Bizarre Adventure - Steel Ball Run (Season 6) (Batch)",
                "total_size": 10_000,
                "seeders": 40,
            },
            {
                "info_hash": "d" * 40,
                "title": "[Erai-raws] JoJo's Bizarre Adventure - Steel Ball Run - 01 ~ 12 [1080p]",
                "total_size": 10_000,
                "seeders": 30,
            },
        ]
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="feed.animetosho.net").mock(
            return_value=httpx.Response(200, text=json.dumps(payload))
        )
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 7},
        )

    assert response.status_code == 200, response.text
    rows = {row["info_hash"]: row for row in response.json()["releases"]}
    assert set(rows) == {"a" * 40, "c" * 40, "d" * 40}
    assert rows["a" * 40]["is_pack"] is False
    assert rows["a" * 40]["pack_label"] is None
    assert rows["c" * 40]["is_pack"] is True
    assert rows["c" * 40]["pack_label"] == "S06"
    assert rows["d" * 40]["is_pack"] is True
    assert rows["d" * 40]["pack_label"] == "E01-E12"
