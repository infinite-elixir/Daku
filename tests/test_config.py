from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

from daku.config import (
    DEFAULT_PROVIDERS,
    TORRENTIO_DEFAULT,
    Settings,
    ensure_secrets_file,
    load_settings,
)
from daku.logging_setup import JsonFormatter, SecretRedactor


def test_defaults_with_xdg_paths(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "dat"),
    }

    settings = load_settings(env)

    assert settings.host == "0.0.0.0"
    assert settings.port == 8420
    assert settings.config_dir == tmp_path / "cfg" / "daku"
    assert settings.data_dir == tmp_path / "dat" / "daku"
    assert settings.organize_mode == "hardlink"


def test_release_discovery_defaults_match_tsearch(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "dat"),
    }

    settings = load_settings(env)

    assert list(DEFAULT_PROVIDERS) == [
        "yts",
        "eztv",
        "1337x",
        "thepiratebay",
        "torrentgalaxy",
    ]
    assert settings.providers == list(DEFAULT_PROVIDERS)
    assert settings.quality_filter == ["cam", "scr", "unknown"]
    assert settings.torrentio_config == TORRENTIO_DEFAULT
    assert TORRENTIO_DEFAULT == (
        "providers=yts,eztv,1337x,thepiratebay,torrentgalaxy"
        "|qualityfilter=scr,cam,unknown|sort=seeders"
    )


def test_providers_accept_comma_string(tmp_path: Path) -> None:
    settings = Settings(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "dat",
        providers="YTS, eztv ,,",
    )

    assert settings.providers == ["yts", "eztv"]


def test_config_file_beats_default(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "dat"),
    }
    config_dir = tmp_path / "cfg" / "daku"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"port": 9000, "host": "127.0.0.1"}), encoding="utf-8"
    )

    settings = load_settings(env)

    assert settings.port == 9000
    assert settings.host == "127.0.0.1"


def test_environment_beats_config_file(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "dat"),
        "DAKU_PORT": "9100",
        "DAKU_HOST": "192.168.1.10",
        "DAKU_TELEGRAM_ALLOWLIST": "111, 222",
    }
    config_dir = tmp_path / "cfg" / "daku"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"port": 9000}), encoding="utf-8")

    settings = load_settings(env)

    assert settings.port == 9100
    assert settings.host == "192.168.1.10"
    assert settings.telegram_allowlist == [111, 222]


def test_secrets_file_is_owner_only_and_loaded(tmp_path: Path) -> None:
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "dat")
    path = ensure_secrets_file(settings)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert json.loads(path.read_text()) == {
        "aria2_secret": "",
        "jellyfin_api_key": "",
        "omdb_api_key": "",
        "qbt_pass": "",
        "telegram_bot_token": "",
        "tvdb_api_key": "",
    }

    path.write_text(json.dumps({"telegram_bot_token": "123:super-secret"}), encoding="utf-8")
    env = {
        "HOME": str(tmp_path / "home"),
        "DAKU_CONFIG_DIR": str(settings.config_dir),
        "DAKU_DATA_DIR": str(settings.data_dir),
    }

    loaded = load_settings(env)

    assert loaded.telegram_bot_token == "123:super-secret"
    assert "super-secret" not in loaded.public_repr()
    assert loaded.public_dict()["telegram_bot_token"] == "***"


def test_environment_beats_secrets_file(tmp_path: Path) -> None:
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "dat")
    settings.config_dir.mkdir(parents=True)
    ensure_secrets_file(settings)
    settings.secrets_file.write_text(json.dumps({"qbt_pass": "file-pass"}), encoding="utf-8")

    loaded = load_settings(
        {
            "HOME": str(tmp_path / "home"),
            "DAKU_CONFIG_DIR": str(settings.config_dir),
            "DAKU_DATA_DIR": str(settings.data_dir),
            "DAKU_QBT_PASS": "env-pass",
        }
    )

    assert loaded.qbt_pass == "env-pass"


def test_log_formatter_redacts_secrets() -> None:
    formatter = JsonFormatter(SecretRedactor(["123:super-secret"]))
    record = logging.LogRecord(
        name="daku.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token is %s",
        args=("123:super-secret",),
        exc_info=None,
    )

    line = formatter.format(record)
    payload = json.loads(line)

    assert payload["message"] == "token is ***"
    assert "super-secret" not in line
    assert payload["level"] == "info"


def test_dirs_are_created(settings: Settings) -> None:
    settings.ensure_dirs()

    assert settings.data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert settings.config_dir.is_dir()
