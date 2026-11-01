from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from daku.repositories import titles as titles_repo
from daku.services.metadata.aliases import (
    effective_aliases,
    store_generated_aliases,
)
from daku.services.metadata.overrides import delete_field_override, update_identity


def test_manual_alias_override_wins_and_clearing_restores_generated(
    db: tuple[object, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles_repo.create_title(
            session, kind="anime", title="Steel Ball Run", anilist_id=190327
        )
        store_generated_aliases(session, title, ["Steel Ball Run", "JoJo's Bizarre Adventure"])
        session.commit()

        assert effective_aliases(session, title) == [
            "Steel Ball Run",
            "JoJo's Bizarre Adventure",
        ]

        updated, _needs_refresh = update_identity(
            session, title, {"aliases": ["SBR", "Steel Ball Run"]}
        )
        assert effective_aliases(session, updated) == ["SBR", "Steel Ball Run"]

        assert delete_field_override(session, title.id, "aliases") is True
        session.refresh(updated)
        assert effective_aliases(session, updated) == [
            "Steel Ball Run",
            "JoJo's Bizarre Adventure",
        ]
