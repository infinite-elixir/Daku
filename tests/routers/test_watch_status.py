from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from daku.app import create_app
from daku.config import Settings
from daku.repositories import episodes as episodes_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata.models import EpisodeRef


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def _session(client: TestClient) -> Session:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory()


def _make_title(
    client: TestClient,
    *,
    kind: str = "series",
    name: str = "Show",
    episodes: list[tuple[int, int]] | None = None,
    wanted: bool = False,
) -> dict[str, Any]:
    with _session(client) as session:
        title = titles_repo.create_title(session, kind=kind, title=name, wanted=wanted)
        if episodes is not None:
            episodes_repo.sync_episodes(
                session,
                title_id=title.id,
                episodes=[EpisodeRef(season=season, episode=number) for season, number in episodes],
            )
        return {"id": title.id}


def _episodes(client: TestClient, title_id: str) -> dict[str, str]:
    detail = client.get(f"/api/titles/{title_id}").json()
    return {episode["label"]: episode["id"] for episode in detail["episodes"]}


def _toggle(client: TestClient, title_id: str, episode_id: str, watched: bool) -> dict[str, Any]:
    response = client.patch(
        f"/api/titles/{title_id}/episodes/{episode_id}", json={"watched": watched}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_movie_statuses_survive_restart(settings: Settings) -> None:
    with TestClient(create_app(settings, configure_logs=False)) as first:
        title_id = _make_title(first, kind="movie", name="Movie")["id"]
        for status in ("planning", "watching", "dropped", "on_hold", "completed"):
            response = first.patch(f"/api/titles/{title_id}", json={"watch_status": status})
            assert response.status_code == 200, response.text
            assert response.json()["watch_status"] == status
            assert response.json()["status_changed_at"] is not None

        assert first.get(f"/api/titles/{title_id}").json()["watch_status"] == "completed"

    with TestClient(create_app(settings, configure_logs=False)) as second:
        payload = second.get(f"/api/titles/{title_id}").json()
        assert payload["watch_status"] == "completed"
        assert payload["watched_episode_count"] == 0
        assert payload["watchable_episode_count"] == 0
        assert payload["next_episode"] is None


def test_episode_progress_moves_planning_to_watching_then_completed(client: TestClient) -> None:
    title_id = _make_title(client, episodes=[(0, 1), (1, 1), (1, 2), (1, 3)])["id"]
    ids = _episodes(client, title_id)

    assert (
        client.patch(f"/api/titles/{title_id}", json={"watch_status": "planning"}).json()[
            "watch_status"
        ]
        == "planning"
    )

    payload = _toggle(client, title_id, ids["S01E01"], True)
    assert payload["watch_status"] == "watching"
    assert payload["watched_episode_count"] == 1
    assert payload["watchable_episode_count"] == 3
    assert payload["next_episode"] == {"season": 1, "episode": 2, "name": None}

    _toggle(client, title_id, ids["S01E02"], True)
    payload = _toggle(client, title_id, ids["S01E03"], True)
    assert payload["watch_status"] == "completed"
    assert payload["watched_episode_count"] == 3
    assert payload["next_episode"] is None
    by_label = {episode["label"]: episode for episode in payload["episodes"]}
    assert by_label["S00E01"]["watched"] is False


def test_dropped_and_on_hold_are_sticky_for_episode_toggles(client: TestClient) -> None:
    title_id = _make_title(client, episodes=[(1, 1), (1, 2)])["id"]
    ids = _episodes(client, title_id)

    for sticky in ("dropped", "on_hold"):
        client.patch(f"/api/titles/{title_id}", json={"watch_status": sticky})
        payload = _toggle(client, title_id, ids["S01E01"], True)
        assert payload["watch_status"] == sticky
        payload = _toggle(client, title_id, ids["S01E02"], True)
        assert payload["watch_status"] == sticky


def test_explicit_status_is_not_overwritten_by_automatic_transitions(
    client: TestClient,
) -> None:
    title_id = _make_title(client, episodes=[(1, 1), (1, 2)])["id"]
    ids = _episodes(client, title_id)

    client.patch(f"/api/titles/{title_id}", json={"watch_status": "dropped"})
    _toggle(client, title_id, ids["S01E01"], True)
    _toggle(client, title_id, ids["S01E02"], True)
    assert client.get(f"/api/titles/{title_id}").json()["watch_status"] == "dropped"

    client.patch(f"/api/titles/{title_id}", json={"watch_status": "completed"})
    payload = _toggle(client, title_id, ids["S01E01"], False)
    assert payload["watch_status"] == "completed"
    assert payload["watched_episode_count"] == 1


def test_completed_bulk_marks_non_specials_and_zero_episode_series_is_allowed(
    client: TestClient,
) -> None:
    title_id = _make_title(client, episodes=[(0, 1), (0, 2), (1, 1), (1, 2)])["id"]

    payload = client.patch(f"/api/titles/{title_id}", json={"watch_status": "completed"}).json()

    by_label = {episode["label"]: episode for episode in payload["episodes"]}
    assert by_label["S00E01"]["watched"] is False
    assert by_label["S00E02"]["watched"] is False
    assert by_label["S01E01"]["watched"] is True
    assert by_label["S01E02"]["watched"] is True
    assert payload["watched_episode_count"] == 2
    assert payload["watchable_episode_count"] == 2

    empty_id = _make_title(client, name="Seen elsewhere", episodes=[])["id"]
    empty = client.patch(f"/api/titles/{empty_id}", json={"watch_status": "completed"})
    assert empty.status_code == 200
    assert empty.json()["watch_status"] == "completed"


def test_leaving_completed_keeps_progress_unless_clear_progress_is_true(
    client: TestClient,
) -> None:
    title_id = _make_title(client, episodes=[(1, 1), (1, 2)])["id"]
    client.patch(f"/api/titles/{title_id}", json={"watch_status": "completed"})

    kept = client.patch(f"/api/titles/{title_id}", json={"watch_status": "watching"}).json()
    assert kept["watched_episode_count"] == 2

    client.patch(f"/api/titles/{title_id}", json={"watch_status": "completed"})
    cleared = client.patch(
        f"/api/titles/{title_id}",
        json={"watch_status": "planning", "clear_progress": True},
    ).json()
    assert cleared["watched_episode_count"] == 0
    assert all(episode["watched"] is False for episode in cleared["episodes"])


def test_cross_title_episode_toggle_is_404(client: TestClient) -> None:
    first = _make_title(client, name="First", episodes=[(1, 1)])["id"]
    second = _make_title(client, name="Second", episodes=[(1, 1)])["id"]
    other_episode = _episodes(client, second)["S01E01"]

    response = client.patch(f"/api/titles/{first}/episodes/{other_episode}", json={"watched": True})
    assert response.status_code == 404
    assert response.json()["code"] == "EPISODE_NOT_FOUND"


def test_status_filters_compose_with_kind_wanted_and_availability(client: TestClient) -> None:
    planned = _make_title(client, kind="series", name="Planned", episodes=[(1, 1)])["id"]
    watching = _make_title(client, kind="movie", name="Watching", wanted=True)["id"]
    completed = _make_title(client, kind="movie", name="Completed")["id"]
    untracked = _make_title(client, kind="movie", name="Untracked")["id"]
    client.patch(f"/api/titles/{planned}", json={"watch_status": "planning"})
    client.patch(f"/api/titles/{watching}", json={"watch_status": "watching"})
    client.patch(f"/api/titles/{completed}", json={"watch_status": "completed"})

    def ids(params: dict[str, Any]) -> set[str]:
        response = client.get("/api/titles", params=params)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert ids({"status": "planning"}) == {planned}
    assert ids({"status": "planning,watching"}) == {planned, watching}
    assert ids({"status": ["planning", "watching"]}) == {planned, watching}
    assert ids({"status": "untracked"}) == {untracked}
    assert ids({"status": "planning", "kind": "series"}) == {planned}
    assert ids({"status": "planning", "kind": "movie"}) == set()
    assert ids({"status": "watching", "wanted": "true"}) == {watching}
    assert ids({"status": "completed", "availability": "none"}) == {completed}
    assert ids({"watched": "true"}) == {completed}
    assert ids({"watched": "false"}) == {planned, watching, untracked}
    assert client.get("/api/titles", params={"status": "nope"}).status_code == 422


def test_recently_watched_sort_uses_status_changed_at(client: TestClient) -> None:
    first = _make_title(client, kind="movie", name="Older")["id"]
    second = _make_title(client, kind="movie", name="Newer")["id"]
    client.patch(f"/api/titles/{first}", json={"watch_status": "completed"})
    client.patch(f"/api/titles/{second}", json={"watch_status": "watching"})

    response = client.get("/api/titles", params={"sort": "status_changed_at"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items[:2]] == [second, first]


def test_watch_changes_publish_watch_updated_events(client: TestClient) -> None:
    title_id = _make_title(client, episodes=[(1, 1)])["id"]
    episode_id = _episodes(client, title_id)["S01E01"]
    bus = client.app.state.event_bus  # type: ignore[attr-defined]
    subscription = bus.subscribe()
    try:
        client.patch(f"/api/titles/{title_id}", json={"watch_status": "planning"})
        status_event = subscription.get_nowait()
        assert status_event.type == "watch.updated"
        assert status_event.data["title_id"] == title_id
        assert status_event.data["source"] == "title"

        client.patch(f"/api/titles/{title_id}/episodes/{episode_id}", json={"watched": True})
        episode_event = subscription.get_nowait()
        assert episode_event.type == "watch.updated"
        assert episode_event.data["episode_id"] == episode_id
        assert episode_event.data["watched"] is True
        assert episode_event.data["source"] == "episode"
    finally:
        subscription.close()
