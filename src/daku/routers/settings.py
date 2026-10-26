"""Persisted, non-secret release settings."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.errors import AppError
from daku.events import EventBus
from daku.repositories import settings as settings_repo
from daku.services.torrents.ranking import MIN_QUALITIES

router = APIRouter(prefix="/api/settings", tags=["settings"])
KINDS = ("movie", "series", "anime")
PROVIDER_RE = re.compile(r"^[a-z0-9._-]+$")
SORTS = ("seeders", "quality", "size")


class ReleasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, list[str]] | None = None
    min_quality: str | None = Field(default=None)
    sort: Literal["seeders", "quality", "size"] | None = None
    limit: int | None = Field(default=None, ge=1, le=200)
    anime_sources: list[str] | None = None


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: ReleasePatch | None = None


def _invalid(message: str, detail: Any = None) -> AppError:
    return AppError(code="INVALID_SETTING", message=message, status_code=422, detail=detail)


def _resolved(session: Session, request: Request) -> dict[str, Any]:
    runtime = request.app.state.settings
    defaults = {
        kind: settings_repo.effective_release_defaults(session, runtime, kind) for kind in KINDS
    }
    config_providers = list(runtime.providers)
    if not config_providers:
        for part in runtime.torrentio_config.split("|"):
            if part.lower().startswith("providers="):
                config_providers = [
                    item.strip().lower()
                    for item in part.split("=", 1)[1].split(",")
                    if item.strip()
                ]
                break
    first = defaults["movie"]
    return {
        "release": {
            "providers": {kind: defaults[kind].providers for kind in KINDS},
            "config_providers": {kind: config_providers for kind in KINDS},
            "min_quality": first.min_quality,
            "sort": first.sort,
            "limit": first.limit,
        },
        "sources": {
            "database": True,
            "config_keys": ["providers", "sort", "limit"],
            "anime_sources": settings_repo.get_setting(
                session, "anime_sources", ["torrentio", "nyaa"]
            ),
        },
    }


@router.get("")
def get_settings(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> dict[str, Any]:
    return _resolved(session, request)


@router.patch("")
def patch_settings(
    body: dict[str, Any],
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    if set(body) - {"release"}:
        raise AppError(code="UNKNOWN_SETTING", message="Unknown setting", status_code=422)
    if not isinstance(body.get("release"), dict):
        raise AppError(code="UNKNOWN_SETTING", message="No settings were supplied", status_code=422)
    unknown_release = set(body["release"]) - {
        "providers",
        "min_quality",
        "sort",
        "limit",
        "anime_sources",
    }
    if unknown_release:
        raise AppError(code="UNKNOWN_SETTING", message="Unknown release setting", status_code=422)
    release = ReleasePatch.model_validate(body["release"])
    if release.anime_sources is not None:
        sources = list(dict.fromkeys(value.strip().lower() for value in release.anime_sources))
        if not sources or any(value not in {"torrentio", "nyaa"} for value in sources):
            raise _invalid(
                "Anime sources must include Torrentio or Nyaa", {"anime_sources": sources}
            )
        settings_repo.set_setting(session, "anime_sources", sources)
    if release.providers is not None:
        unknown = set(release.providers) - set(KINDS)
        if unknown:
            raise AppError(
                code="UNKNOWN_SETTING",
                message=f"Unknown provider kind: {sorted(unknown)[0]}",
                status_code=422,
            )
        for kind, providers in release.providers.items():
            if not providers or len(providers) > 20:
                raise _invalid("Each kind must have 1-20 providers", {"kind": kind})
            normalised: list[str] = []
            for raw in providers:
                value = raw.strip().lower()
                if not value or not PROVIDER_RE.fullmatch(value):
                    raise _invalid(f"Invalid provider name: {raw!r}", {"kind": kind})
                if value not in normalised:
                    normalised.append(value)
            if not normalised:
                raise _invalid("Each kind must have at least one provider", {"kind": kind})
            settings_repo.set_setting(session, f"providers_{kind}", normalised)
    if release.min_quality is not None:
        if release.min_quality not in MIN_QUALITIES:
            raise _invalid("Invalid minimum quality", {"min_quality": release.min_quality})
        settings_repo.set_setting(session, "default_min_quality", release.min_quality)
    elif "min_quality" in release.model_fields_set:
        settings_repo.set_setting(session, "default_min_quality", None)
    if release.sort is not None:
        if release.sort not in SORTS:
            raise _invalid("Invalid sort order", {"sort": release.sort})
        settings_repo.set_setting(session, "default_sort", release.sort)
    if release.limit is not None:
        settings_repo.set_setting(session, "default_limit", release.limit)

    payload = _resolved(session, request)
    bus: EventBus | None = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        bus.publish("settings.updated", {"release": payload["release"]})
    return payload
