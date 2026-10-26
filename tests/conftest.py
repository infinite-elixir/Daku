from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import zstandard
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daku.app import create_app
from daku.config import Settings
from daku.db import init_db

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], object]:
    def _load(name: str) -> object:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        downloads_dir=tmp_path / "downloads",
        movies_dir=tmp_path / "media" / "Movies",
        tv_dir=tmp_path / "media" / "TV",
        anime_dir=tmp_path / "media" / "Anime",
        worker_enabled=False,
    )


@pytest.fixture
def app_client(settings: Settings) -> Iterator[tuple[TestClient, Settings]]:
    application = create_app(settings, configure_logs=False)
    with TestClient(application) as client:
        yield client, settings


@pytest.fixture
def db(settings: Settings) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    engine, factory = init_db(settings)
    try:
        yield engine, factory
    finally:
        engine.dispose()


@pytest.fixture
def mapping_payload() -> dict[str, Any]:
    return {
        "$meta": {
            "schema_version": "3.0.3",
            "generated_on": "2026-10-23T06:49:48Z",
        },
        "imdb_show:tt1234567:s1": {
            "anilist:154587": {"1-12": "1-12", "13-": "13-"},
        },
        "imdb_show:tt1234567:s2": {
            "anilist:154587": {"1-12": "29-40"},
            "anilist:170068": {"1-12": "1-12"},
        },
        "imdb_movie:tt9999999": {
            "anilist:20954": {"1": "1"},
        },
        "anilist:154587": {
            "mal:52991": {"1-28": "1-28"},
        },
        "tmdb_show:500:s1": {
            "anilist:1003": {"1-12": "1-6,8-13", "13-": "14-|2"},
        },
        "tvdb_show:600:s1": {
            "anilist:2000": {"1-12": "1-2|-3"},
        },
    }


@pytest.fixture
def compress() -> Callable[[Any], bytes]:
    def _compress(payload: Any) -> bytes:
        raw = json.dumps(payload).encode("utf-8")
        return zstandard.ZstdCompressor().compress(raw)

    return _compress


@pytest.fixture
def client(app_client: tuple[TestClient, Settings]) -> TestClient:
    return app_client[0]
