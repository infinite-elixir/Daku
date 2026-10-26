from __future__ import annotations

from typing import Any

from daku.repositories import downloads as downloads_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.queue import queue_direct, queue_release

HASH = "c" * 40


def test_same_info_hash_twice_creates_one_row(db: tuple[Any, Any]) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="Dune")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=HASH,
            magnet=f"magnet:?xt=urn:btih:{HASH}",
            size_bytes=1000,
        )

        first = queue_release(session, release, save_path="/downloads")
        second = queue_release(session, release, save_path="/downloads")

        assert first.created is True
        assert second.created is False
        assert first.download.id == second.download.id
        assert len(downloads_repo.list_downloads(session)) == 1


def test_same_hash_from_another_release_links_the_existing_download(db: tuple[Any, Any]) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="The Wire")
        episode_one = episodes_repo.create_episode(session, title_id=title.id, season=1, episode=1)
        episode_two = episodes_repo.create_episode(session, title_id=title.id, season=1, episode=2)
        first_release = releases_repo.create_release(
            session,
            title_id=title.id,
            episode_id=episode_one.id,
            info_hash=HASH,
            magnet="magnet:?xt=urn:btih:a",
        )
        second_release = releases_repo.create_release(
            session,
            title_id=title.id,
            episode_id=episode_two.id,
            info_hash=HASH,
            magnet="magnet:?xt=urn:btih:a",
        )

        first = queue_release(session, first_release, save_path="/downloads")
        second = queue_release(session, second_release, save_path="/downloads")

        assert second.created is False
        assert second.download.id == first.download.id
        assert second.download.release_id == second_release.id
        assert len(downloads_repo.list_downloads(session)) == 1


def test_direct_download_row_keeps_its_url_for_retry(db: tuple[Any, Any]) -> None:
    _engine, factory = db
    with factory() as session:
        row = queue_direct(
            session,
            url="https://example.test/file.mkv",
            gid="gid-1",
            save_path="/downloads",
            total_bytes=1234,
        )

        assert row.engine == "aria2"
        assert row.external_id == "gid-1"
        assert row.status == "active"
        assert row.url == "https://example.test/file.mkv"
        assert row.total_bytes == 1234
