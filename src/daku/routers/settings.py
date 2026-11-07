"""Persisted, non-secret settings: release defaults and the player toggle."""

from __future__ import annotations

import re
import shutil
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from daku.config import DEFAULT_PROVIDERS
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
    sources: dict[str, list[str]] | None = None
    min_quality: str | None = Field(default=None)
    sort: Literal["seeders", "quality", "size"] | None = None
    limit: int | None = Field(default=None, ge=1, le=200)
    anime_sources: list[str] | None = None


class PlayerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpv_enabled: bool | None = None


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: ReleasePatch | None = None
    player: PlayerPatch | None = None


def _invalid(message: str, detail: Any = None) -> AppError:
    return AppError(code="INVALID_SETTING", message=message, status_code=422, detail=detail)


def _resolved(session: Session, request: Request) -> dict[str, Any]:
    runtime = request.app.state.settings
    defaults = {
        kind: settings_repo.effective_release_defaults(session, runtime, kind) for kind in KINDS
    }
    # The known provider catalogue, not a selection. An empty stored list means
    # "all providers" and is returned as-is under ``providers``.
    config_providers = list(dict.fromkeys([*DEFAULT_PROVIDERS, *runtime.providers]))
    first = defaults["movie"]
    stored_mpv = settings_repo.get_setting(session, "mpv_enabled", None)
    mpv_enabled = stored_mpv if isinstance(stored_mpv, bool) else runtime.mpv_enabled
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
            "by_kind": {kind: settings_repo.get_sources(session, kind) for kind in KINDS},
            # Legacy alias kept for older SPA builds.
            "anime_sources": settings_repo.get_sources(session, "anime"),
        },
        "player": {
            "mpv_enabled": mpv_enabled,
            "mpv_bin": runtime.mpv_bin,
            "mpv_path": shutil.which(runtime.mpv_bin),
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
    if set(body) - {"release", "player"}:
        raise AppError(code="UNKNOWN_SETTING", message="Unknown setting", status_code=422)
    if "release" not in body and "player" not in body:
        raise AppError(code="UNKNOWN_SETTING", message="No settings were supplied", status_code=422)
    if "release" in body:
        release_payload = body["release"]
        if not isinstance(release_payload, dict):
            raise _invalid("Release settings must be an object")
        _apply_release(session, release_payload)
    if "player" in body:
        player_payload = body["player"]
        if not isinstance(player_payload, dict):
            raise _invalid("Player settings must be an object")
        unknown_player = set(player_payload) - {"mpv_enabled"}
        if unknown_player:
            raise AppError(
                code="UNKNOWN_SETTING", message="Unknown player setting", status_code=422
            )
        try:
            player = PlayerPatch.model_validate(player_payload)
        except ValidationError as exc:
            raise _invalid("Invalid player setting") from exc
        if player.mpv_enabled is not None:
            settings_repo.set_setting(session, "mpv_enabled", player.mpv_enabled)
            # Apply immediately: the player and doctor read the runtime settings object.
            request.app.state.settings.mpv_enabled = player.mpv_enabled

    payload = _resolved(session, request)
    bus: EventBus | None = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        bus.publish(
            "settings.updated",
            {"release": payload["release"], "player": payload["player"]},
        )
    return payload


def _apply_release(session: Session, body: dict[str, Any]) -> None:
    unknown_release = set(body) - {
        "providers",
        "sources",
        "min_quality",
        "sort",
        "limit",
        "anime_sources",
    }
    if unknown_release:
        raise AppError(code="UNKNOWN_SETTING", message="Unknown release setting", status_code=422)
    try:
        release = ReleasePatch.model_validate(body)
    except ValidationError as exc:
        raise _invalid("Invalid release setting") from exc
    if release.anime_sources is not None:
        sources = settings_repo.normalize_sources("anime", release.anime_sources)
        settings_repo.set_setting(session, "sources_anime", sources)
    if release.sources is not None:
        unknown = set(release.sources) - set(KINDS)
        if unknown:
            raise AppError(
                code="UNKNOWN_SETTING",
                message=f"Unknown source kind: {sorted(unknown)[0]}",
                status_code=422,
            )
        for kind, values in release.sources.items():
            normalised_sources = settings_repo.normalize_sources(kind, values)
            settings_repo.set_setting(session, f"sources_{kind}", normalised_sources)
    if release.providers is not None:
        unknown = set(release.providers) - set(KINDS)
        if unknown:
            raise AppError(
                code="UNKNOWN_SETTING",
                message=f"Unknown provider kind: {sorted(unknown)[0]}",
                status_code=422,
            )
        for kind, providers in release.providers.items():
            if len(providers) > 20:
                raise _invalid("Each kind must have 0-20 providers", {"kind": kind})
            normalised: list[str] = []
            for raw in providers:
                value = raw.strip().lower()
                if not value or not PROVIDER_RE.fullmatch(value):
                    raise _invalid(f"Invalid provider name: {raw!r}", {"kind": kind})
                if value not in normalised:
                    normalised.append(value)
            # An empty list is valid and means "all providers".
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
