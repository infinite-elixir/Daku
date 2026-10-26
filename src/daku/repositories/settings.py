"""Key/value settings repository.

Values are stored as JSON so lists and numbers round-trip. Secrets are never stored
here — they live in ``secrets.json`` with ``0600`` permissions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.config import Settings
from daku.models import Setting

DEFAULT_SETTING_KEYS: tuple[str, ...] = (
    "host",
    "port",
    "config_dir",
    "data_dir",
    "downloads_dir",
    "movies_dir",
    "tv_dir",
    "anime_dir",
    "organize_mode",
    "qbt_url",
    "qbt_user",
    "aria2_rpc",
    "jellyfin_url",
    "telegram_api_base",
    "telegram_archive_channel",
    "telegram_allowlist",
    "torrentio_config",
    "providers",
    "sort",
    "limit",
    "quality_filter",
    "providers_movie",
    "providers_series",
    "providers_anime",
    "anime_sources",
    "default_min_quality",
    "default_sort",
    "default_limit",
)


@dataclass(frozen=True, slots=True)
class ReleaseDefaults:
    providers: list[str]
    min_quality: str | None
    sort: str
    limit: int
    source: str


def default_settings_map(settings: Settings) -> dict[str, Any]:
    values = {key: getattr(settings, key) for key in DEFAULT_SETTING_KEYS if hasattr(settings, key)}
    values.update(
        {
            "providers_movie": list(settings.providers),
            "providers_series": list(settings.providers),
            "providers_anime": list(settings.providers),
            "anime_sources": ["torrentio", "nyaa"],
            "default_min_quality": None,
            "default_sort": settings.sort,
            "default_limit": settings.limit,
        }
    )
    return values


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def seed_default_settings(session: Session, settings: Settings) -> int:
    """Insert missing defaults without overwriting user values. Returns rows added."""

    existing = set(session.scalars(select(Setting.key)))
    added = 0
    for key, value in default_settings_map(settings).items():
        if key in existing:
            continue
        session.add(Setting(key=key, value=_encode(value)))
        added += 1
    if added:
        session.commit()
    return added


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    if row is None:
        return default
    return _decode(row.value)


def set_setting(session: Session, key: str, value: Any) -> Setting:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=_encode(value))
        session.add(row)
    else:
        row.value = _encode(value)
    session.commit()
    session.refresh(row)
    return row


def all_settings(session: Session) -> dict[str, Any]:
    return {row.key: _decode(row.value) for row in session.scalars(select(Setting))}


def delete_setting(session: Session, key: str) -> bool:
    row = session.get(Setting, key)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def effective_release_defaults(session: Session, settings: Settings, kind: str) -> ReleaseDefaults:
    """Resolve one kind's release defaults from DB, then runtime config."""

    provider_key = f"providers_{kind}"
    stored = get_setting(session, provider_key, None)
    providers = stored if isinstance(stored, list) else None
    min_quality = get_setting(session, "default_min_quality", None)
    sort = get_setting(session, "default_sort", None)
    limit = get_setting(session, "default_limit", None)
    if not isinstance(providers, list):
        providers = list(settings.providers)
        source = "config"
    else:
        source = "settings"
    if not providers:
        for part in settings.torrentio_config.split("|"):
            if part.lower().startswith("providers="):
                providers = [
                    item.strip().lower()
                    for item in part.split("=", 1)[1].split(",")
                    if item.strip()
                ]
                source = "config"
                break
    if not isinstance(sort, str):
        sort = settings.sort
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = settings.limit
    if not isinstance(min_quality, str) and min_quality is not None:
        min_quality = None
    return ReleaseDefaults(
        providers=[str(value) for value in providers],
        min_quality=min_quality,
        sort=sort,
        limit=limit,
        source=source,
    )
