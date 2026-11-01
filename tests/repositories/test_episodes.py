from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daku.models import Title
from daku.repositories import episodes as episodes_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata.models import EpisodeRef


def _series_with_episodes(
    factory: sessionmaker[Session],
) -> tuple[str, list[str]]:
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Ordering")
        episodes_repo.sync_episodes(
            session,
            title_id=title.id,
            episodes=[
                EpisodeRef(season=0, episode=1, title="Special"),
                EpisodeRef(season=1, episode=2, title="Second"),
                EpisodeRef(season=1, episode=1, title="First"),
                EpisodeRef(season=2, episode=1, title="Next season"),
            ],
        )
        return title.id, [row.id for row in episodes_repo.list_episodes(session, title.id)]


def test_progress_counts_exclude_specials_and_next_episode_is_ordered(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    title_id, episode_ids = _series_with_episodes(factory)

    with factory() as session:
        title = session.get(Title, title_id)
        assert title is not None
        assert episodes_repo.progress_for(session, title_id) == {
            "watched_episode_count": 0,
            "watchable_episode_count": 3,
            "next_episode": {"season": 1, "episode": 1, "name": "First"},
        }

        first = episodes_repo.get_episode(session, episode_ids[1])
        assert first is not None
        episodes_repo.set_episode_watched(session, title=title, episode=first, watched=True)

        progress = episodes_repo.progress_for(session, title_id)
        assert progress["watched_episode_count"] == 1
        assert progress["watchable_episode_count"] == 3
        assert progress["next_episode"] == {
            "season": 1,
            "episode": 2,
            "name": "Second",
        }


def test_marking_a_special_never_moves_title_status(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    title_id, episode_ids = _series_with_episodes(factory)

    with factory() as session:
        title = session.get(Title, title_id)
        special = episodes_repo.get_episode(session, episode_ids[0])
        assert title is not None and special is not None
        episodes_repo.set_episode_watched(session, title=title, episode=special, watched=True)

        assert title.watch_status is None
        progress = episodes_repo.progress_for(session, title_id)
        assert progress["watched_episode_count"] == 0
        assert progress["next_episode"]["season"] == 1
