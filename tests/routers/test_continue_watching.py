from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from daku.models import Episode, MediaFile, Title
from daku.repositories import media_files as media_files_repo
from daku.utils import new_id, utc_now_iso


def _session(client: TestClient) -> Session:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory()


def _title(
    session: Session,
    *,
    title: str = "Example",
    kind: str = "movie",
    watch_status: str | None = None,
) -> Title:
    row = Title(
        id=new_id(),
        kind=kind,
        title=title,
        metadata_source="cinemeta",
        added_at=utc_now_iso(),
        watch_status=watch_status,
    )
    session.add(row)
    session.commit()
    return row


def _episode(session: Session, title: Title, season: int, episode: int) -> Episode:
    row = Episode(id=new_id(), title_id=title.id, season=season, episode=episode)
    session.add(row)
    session.commit()
    return row


def _file(
    session: Session,
    title: Title,
    path: Path,
    *,
    episode: Episode | None = None,
    is_primary: bool = True,
    size: int = 16,
) -> MediaFile:
    return media_files_repo.create_media_file(
        session,
        title_id=title.id,
        episode_id=episode.id if episode else None,
        src_path=str(path),
        size_bytes=size,
        is_primary=is_primary,
    )


def _play(
    session: Session,
    row: MediaFile,
    position_s: float,
    duration_s: float | None,
    at: str,
) -> None:
    row.playback_position_s = position_s
    row.playback_duration_s = duration_s
    row.playback_updated_at = at
    session.commit()


def _mark_watched(session: Session, episode: Episode, at: str = "2026-11-01T09:00:00Z") -> None:
    episode.watched = True
    episode.watched_at = at
    session.commit()


def _created(session: Session, row: MediaFile, at: str) -> None:
    row.created_at = at
    session.commit()


def _real_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * 16)
    return path


def _items(client: TestClient) -> list[dict[str, object]]:
    response = client.get("/api/continue-watching")
    assert response.status_code == 200
    return response.json()["items"]


def test_lists_an_in_progress_movie(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, title="Big Buck Bunny")
        row = _file(session, title, _real_file(tmp_path, "bbb.mkv"))
        _play(session, row, 120.0, 600.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["title_id"] == title.id
    assert items[0]["title"] == "Big Buck Bunny"
    assert items[0]["kind"] == "movie"
    assert items[0]["up_next"] is False
    assert items[0]["file"]["id"] == row.id
    assert items[0]["file"]["playback"]["position_s"] == 120.0


def test_finished_movie_is_absent(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session)
        row = _file(session, title, _real_file(tmp_path, "done.mkv"))
        _play(session, row, 600.0, 600.0, "2026-11-01T10:00:00Z")

    assert _items(client) == []


def test_unknown_duration_is_skipped(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session)
        row = _file(session, title, _real_file(tmp_path, "nodur.mkv"))
        _play(session, row, 45.0, None, "2026-11-01T10:00:00Z")

    assert _items(client) == []


def test_finished_episode_points_at_the_next_downloaded_episode(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, title="Show", kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        first = _file(session, title, _real_file(tmp_path, "s01e01.mkv"), episode=ep1)
        second = _file(session, title, _real_file(tmp_path, "s01e02.mkv"), episode=ep2)
        _play(session, first, 1000.0, 1000.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["up_next"] is True
    assert items[0]["file"]["id"] == second.id
    assert items[0]["file"]["episode_label"] == "S01E02"
    assert items[0]["file"]["playback"] is None


def test_in_progress_episode_wins_over_a_downloaded_next_episode(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        first = _file(session, title, _real_file(tmp_path, "a.mkv"), episode=ep1)
        _file(session, title, _real_file(tmp_path, "b.mkv"), episode=ep2)
        _play(session, first, 400.0, 1000.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["up_next"] is False
    assert items[0]["file"]["id"] == first.id
    assert items[0]["file"]["episode_label"] == "S01E01"


def test_finished_episode_without_a_next_file_is_absent(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        row = _file(session, title, _real_file(tmp_path, "only.mkv"), episode=ep1)
        _play(session, row, 1000.0, 1000.0, "2026-11-01T10:00:00Z")

    assert _items(client) == []


def test_next_episode_orders_by_season_then_episode(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        ep3 = _episode(session, title, 2, 1)
        first = _file(session, title, _real_file(tmp_path, "s01e01.mkv"), episode=ep1)
        _file(session, title, _real_file(tmp_path, "s02e01.mkv"), episode=ep3)
        second = _file(session, title, _real_file(tmp_path, "s01e02.mkv"), episode=ep2)
        _play(session, first, 1000.0, 1000.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert items[0]["file"]["id"] == second.id
    assert items[0]["file"]["season"] == 1


def test_one_card_per_title_uses_the_most_recent_file(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        first = _file(session, title, _real_file(tmp_path, "one.mkv"), episode=ep1)
        second = _file(session, title, _real_file(tmp_path, "two.mkv"), episode=ep2)
        _play(session, first, 400.0, 1000.0, "2026-11-01T09:00:00Z")
        _play(session, second, 100.0, 1000.0, "2026-11-01T11:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["file"]["id"] == second.id


def test_specials_are_ignored(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        special = _episode(session, title, 0, 1)
        row = _file(session, title, _real_file(tmp_path, "special.mkv"), episode=special)
        _play(session, row, 100.0, 1000.0, "2026-11-01T10:00:00Z")

    assert _items(client) == []


def test_hidden_watch_statuses_are_excluded(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        for status in ("dropped", "on_hold"):
            title = _title(session, title=status, kind="movie", watch_status=status)
            row = _file(session, title, _real_file(tmp_path, f"{status}.mkv"))
            _play(session, row, 100.0, 1000.0, "2026-11-01T10:00:00Z")

    assert _items(client) == []


def test_missing_files_are_excluded(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session)
        path = _real_file(tmp_path, "gone.mkv")
        row = _file(session, title, path)
        _play(session, row, 100.0, 1000.0, "2026-11-01T10:00:00Z")
        path.unlink()

    assert _items(client) == []


def test_results_are_capped_and_newest_first(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        media_ids: list[str] = []
        for index in range(13):
            title = _title(session, title=f"Movie {index}")
            row = _file(session, title, _real_file(tmp_path, f"m{index}.mkv"))
            _play(session, row, 100.0, 1000.0, f"2026-11-01T10:{index:02d}:00Z")
            media_ids.append(row.id)

    items = _items(client)

    assert len(items) == 12
    assert [item["file"]["id"] for item in items] == list(reversed(media_ids))[:12]


def test_a_freshly_downloaded_movie_appears_before_it_is_played(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, title="Fresh Movie")
        row = _file(session, title, _real_file(tmp_path, "fresh.mkv"))

    items = _items(client)

    assert len(items) == 1
    assert items[0]["title_id"] == title.id
    assert items[0]["file"]["id"] == row.id
    assert items[0]["is_new"] is True
    assert items[0]["up_next"] is False
    assert items[0]["played_at"] is None


def test_a_fresh_series_uses_the_earliest_unwatched_downloaded_episode(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, title="Fresh Show", kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        ep3 = _episode(session, title, 1, 3)
        _mark_watched(session, ep1)
        _file(session, title, _real_file(tmp_path, "s01e01.mkv"), episode=ep1)
        expected = _file(session, title, _real_file(tmp_path, "s01e02.mkv"), episode=ep2)
        _file(session, title, _real_file(tmp_path, "s01e03.mkv"), episode=ep3)

    items = _items(client)

    assert len(items) == 1
    assert items[0]["is_new"] is True
    assert items[0]["file"]["id"] == expected.id
    assert items[0]["file"]["episode_label"] == "S01E02"


def test_fresh_downloads_do_not_duplicate_a_title_with_progress(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        first = _file(session, title, _real_file(tmp_path, "one.mkv"), episode=ep1)
        _file(session, title, _real_file(tmp_path, "two.mkv"), episode=ep2)
        _play(session, first, 400.0, 1000.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["file"]["id"] == first.id
    assert items[0]["is_new"] is False


def test_manual_watched_episodes_are_skipped_when_choosing_up_next(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        ep3 = _episode(session, title, 1, 3)
        first = _file(session, title, _real_file(tmp_path, "s01e01.mkv"), episode=ep1)
        _file(session, title, _real_file(tmp_path, "s01e02.mkv"), episode=ep2)
        expected = _file(session, title, _real_file(tmp_path, "s01e03.mkv"), episode=ep3)
        _mark_watched(session, ep2)
        _play(session, first, 1000.0, 1000.0, "2026-11-01T10:00:00Z")

    items = _items(client)

    assert len(items) == 1
    assert items[0]["up_next"] is True
    assert items[0]["file"]["id"] == expected.id
    assert items[0]["file"]["episode_label"] == "S01E03"


def test_a_series_with_every_download_watched_is_absent(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="series")
        ep1 = _episode(session, title, 1, 1)
        ep2 = _episode(session, title, 1, 2)
        _file(session, title, _real_file(tmp_path, "s01e01.mkv"), episode=ep1)
        _file(session, title, _real_file(tmp_path, "s01e02.mkv"), episode=ep2)
        _mark_watched(session, ep1)
        _mark_watched(session, ep2)

    assert _items(client) == []


def test_fresh_downloads_are_ordered_newest_first(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        older = _title(session, title="Older")
        newer = _title(session, title="Newer")
        older_file = _file(session, older, _real_file(tmp_path, "older.mkv"))
        newer_file = _file(session, newer, _real_file(tmp_path, "newer.mkv"))
        _created(session, older_file, "2026-11-01T10:00:00Z")
        _created(session, newer_file, "2026-11-01T12:00:00Z")

    items = _items(client)

    assert [item["file"]["id"] for item in items] == [newer_file.id, older_file.id]
