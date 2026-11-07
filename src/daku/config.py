"""Configuration and secret loading.

Resolution order (highest first):

1. Environment variables prefixed with ``DAKU_`` (for example ``DAKU_PORT``).
2. ``config.json`` in the config directory.
3. Built-in defaults.

Secrets live in ``secrets.json`` next to the config file and are read with owner-only
permissions. They are never written to logs or returned by the HTTP API.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "qbt_pass",
        "aria2_secret",
        "jellyfin_api_key",
        "telegram_bot_token",
        "tvdb_api_key",
        "omdb_api_key",
    }
)

SECRETS_FILE_MODE = 0o600
CONFIG_FILE_NAME = "config.json"
SECRETS_FILE_NAME = "secrets.json"

# The provider names Torrentio knows, offered in the UI. This is *not* a default
# selection: an absent ``providers=`` part makes Torrentio query every provider.
DEFAULT_PROVIDERS: tuple[str, ...] = (
    "yts",
    "eztv",
    "rarbg",
    "1337x",
    "ext",
    "thepiratebay",
    "kickasstorrents",
    "torrentgalaxy",
    "magnetdl",
)
# Shipped Torrentio prefix: all providers, no upstream quality filter. The local
# filter in ``ranking`` stays authoritative.
TORRENTIO_DEFAULT = "sort=seeders"


def _home() -> Path:
    return Path.home()


def xdg_config_home(env: Mapping[str, str]) -> Path:
    raw = env.get("XDG_CONFIG_HOME")
    return Path(raw).expanduser() if raw else _home() / ".config"


def xdg_data_home(env: Mapping[str, str]) -> Path:
    raw = env.get("XDG_DATA_HOME")
    return Path(raw).expanduser() if raw else _home() / ".local" / "share"


def default_config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("DAKU_CONFIG_DIR")
    if raw:
        return Path(raw).expanduser()
    return xdg_config_home(env) / "daku"


def default_data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("DAKU_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return xdg_data_home(env) / "daku"


class Settings(BaseModel):
    """Fully resolved runtime configuration."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    host: str = "0.0.0.0"
    port: int = Field(default=8420, ge=0, le=65535)
    config_dir: Path
    data_dir: Path

    downloads_dir: Path = Field(default_factory=lambda: _home() / "Downloads" / "Daku")
    movies_dir: Path = Field(default_factory=lambda: _home() / "Media" / "Movies")
    tv_dir: Path = Field(default_factory=lambda: _home() / "Media" / "TV")
    anime_dir: Path = Field(default_factory=lambda: _home() / "Media" / "Anime")
    organize_mode: Literal["hardlink", "copy", "move"] = "hardlink"

    qbt_url: str = "http://127.0.0.1:8080"
    qbt_user: str = "admin"
    aria2_rpc: str = "http://127.0.0.1:6800/jsonrpc"
    aria2_bin: str = ""
    jellyfin_url: str = "http://127.0.0.1:8096"
    tvdb_api_key: str = ""

    # Playback. On by default; the Settings page persists a per-install override and the
    # UI only shows the launch affordance when enabled.
    mpv_enabled: bool = True
    mpv_bin: str = "mpv"

    # Worker loop. Disable in tests so a TestClient does not poll localhost.
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=2.0, gt=0)

    # Optional libtorrent metadata probe. Disabled means the Files view reports 503.
    magnet_probe: bool = True

    telegram_api_base: str = "https://api.telegram.org"
    telegram_archive_channel: str = ""
    telegram_allowlist: list[int] = Field(default_factory=list)

    torrentio_config: str = TORRENTIO_DEFAULT
    # Empty means "every provider": no ``providers=`` part is sent to Torrentio.
    providers: list[str] = Field(default_factory=list)
    sort: Literal["seeders", "quality", "size"] = "seeders"
    limit: int = Field(default=20, ge=1, le=200)
    quality_filter: list[str] = Field(default_factory=lambda: ["cam", "scr", "unknown"])
    metadata_sources: list[str] = Field(default_factory=lambda: ["cinemeta", "tvdb", "anilist"])

    # Secrets — loaded from secrets.json or the environment.
    qbt_pass: str = ""
    aria2_secret: str = ""
    jellyfin_api_key: str = ""
    telegram_bot_token: str = ""
    omdb_api_key: str = ""

    @field_validator(
        "config_dir",
        "data_dir",
        "downloads_dir",
        "movies_dir",
        "tv_dir",
        "anime_dir",
        mode="after",
    )
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("quality_filter", "providers", mode="before")
    @classmethod
    def _split_quality_filter(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip().lower() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip().lower() for part in value if str(part).strip()]
        return value

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "daku.db"

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILE_NAME

    @property
    def secrets_file(self) -> Path:
        return self.config_dir / SECRETS_FILE_NAME

    def ensure_dirs(self) -> None:
        """Create the config directory, data directory and log directory."""

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def secret_values(self) -> list[str]:
        return [str(getattr(self, name)) for name in sorted(SECRET_FIELDS) if getattr(self, name)]

    def public_dict(self) -> dict[str, Any]:
        """Return config with every secret replaced by ``***``."""

        data = self.model_dump(mode="json")
        for name in SECRET_FIELDS:
            if data.get(name):
                data[name] = "***"
        return data

    def public_repr(self) -> str:
        return f"Settings({json.dumps(self.public_dict(), sort_keys=True)})"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"cannot read {path.name}: expected a JSON object")
    return raw


def _coerce_env_value(field: str, raw: str) -> Any:
    if field in ("telegram_allowlist", "quality_filter", "metadata_sources", "providers"):
        stripped = raw.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"DAKU_{field.upper()} must be JSON or comma-separated") from exc
            if not isinstance(parsed, list):
                raise ValueError(f"DAKU_{field.upper()} must be a list")
            return parsed
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if field in ("port", "limit"):
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"DAKU_{field.upper()} must be an integer") from exc
    return raw


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Resolve settings from environment, config file and defaults."""

    env = dict(os.environ if env is None else env)

    config_dir = default_config_dir(env)
    data_dir = default_data_dir(env)
    defaults = Settings(config_dir=config_dir, data_dir=data_dir).model_dump(mode="python")

    file_values = _read_json_object(config_dir / CONFIG_FILE_NAME)
    merged: dict[str, Any] = {**defaults}
    for key, value in file_values.items():
        if key in defaults and key not in SECRET_FIELDS:
            merged[key] = value

    secrets_values = _read_json_object(config_dir / SECRETS_FILE_NAME)
    for key, value in secrets_values.items():
        if key in SECRET_FIELDS:
            merged[key] = value

    # config_dir and data_dir are resolved before the file is read so the file cannot
    # relocate its own lookup root.
    merged["config_dir"] = config_dir
    merged["data_dir"] = data_dir

    for key in defaults:
        env_key = f"DAKU_{key.upper()}"
        if env_key in env:
            merged[key] = _coerce_env_value(key, env[env_key])

    return Settings.model_validate(merged)


def ensure_secrets_file(settings: Settings) -> Path:
    """Create ``secrets.json`` with ``0600`` permissions if it is missing."""

    settings.config_dir.mkdir(parents=True, exist_ok=True)
    path = settings.secrets_file
    if not path.exists():
        payload = {name: "" for name in sorted(SECRET_FIELDS)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRETS_FILE_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    else:
        os.chmod(path, SECRETS_FILE_MODE)
    return path


def load_secrets(settings: Settings) -> dict[str, str]:
    """Read secrets for callers that need the raw mapping (never log the result)."""

    raw = _read_json_object(settings.secrets_file)
    return {key: str(value) for key, value in raw.items() if key in SECRET_FIELDS}
