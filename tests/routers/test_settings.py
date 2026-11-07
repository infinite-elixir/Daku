from __future__ import annotations

from fastapi.testclient import TestClient


def test_settings_exposes_only_release_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"release", "sources", "player"}
    assert payload["release"]["providers"]["movie"] == []
    assert payload["release"]["config_providers"]["movie"] == [
        "yts",
        "eztv",
        "rarbg",
        "1337x",
        "ext",
        "thepiratebay",
        "kickasstorrents",
        "torrentgalaxy",
        "magnetdl",
    ]
    assert payload["sources"]["by_kind"]["movie"] == ["torrentio", "torrents-csv"]
    assert payload["player"]["mpv_enabled"] is True
    assert payload["player"]["mpv_bin"] == "mpv"
    assert "qbt_pass" not in response.text
    assert "config_dir" not in response.text


def test_settings_exposes_the_mpv_binary(client: TestClient, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/mpv")
    client.app.state.settings.mpv_enabled = True

    payload = client.get("/api/settings").json()["player"]

    assert payload == {
        "mpv_enabled": True,
        "mpv_bin": "mpv",
        "mpv_path": "/usr/bin/mpv",
    }


def test_player_toggle_is_persisted_and_applied_immediately(client: TestClient) -> None:
    response = client.patch("/api/settings", json={"player": {"mpv_enabled": False}})

    assert response.status_code == 200
    assert response.json()["player"]["mpv_enabled"] is False
    assert client.app.state.settings.mpv_enabled is False
    assert client.get("/api/settings").json()["player"]["mpv_enabled"] is False

    enabled = client.patch(
        "/api/settings", json={"release": {"limit": 50}, "player": {"mpv_enabled": True}}
    )
    assert enabled.status_code == 200
    assert enabled.json()["player"]["mpv_enabled"] is True
    assert enabled.json()["release"]["limit"] == 50
    assert client.app.state.settings.mpv_enabled is True


def test_player_toggle_survives_a_restart(client: TestClient, settings) -> None:
    from daku.app import create_app

    response = client.patch("/api/settings", json={"player": {"mpv_enabled": False}})
    assert response.status_code == 200

    with TestClient(create_app(settings, configure_logs=False)) as restarted:
        assert restarted.get("/api/settings").json()["player"]["mpv_enabled"] is False


def test_player_patch_is_validated(client: TestClient) -> None:
    unknown = client.patch("/api/settings", json={"player": {"mpv_bin": "vlc"}})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "UNKNOWN_SETTING"

    junk = client.patch("/api/settings", json={"player": {"mpv_enabled": "maybe"}})
    assert junk.status_code == 422

    empty = client.patch("/api/settings", json={})
    assert empty.status_code == 422
    assert empty.json()["code"] == "UNKNOWN_SETTING"


def test_settings_patch_is_persisted_and_validated(client: TestClient) -> None:
    response = client.patch(
        "/api/settings",
        json={"release": {"providers": {"anime": [" YTS ", "yts"]}, "limit": 50}},
    )
    assert response.status_code == 200
    assert response.json()["release"]["providers"]["anime"] == ["yts"]
    assert response.json()["release"]["limit"] == 50
    assert client.get("/api/settings").json()["release"]["providers"]["anime"] == ["yts"]

    invalid_mode = client.patch(
        "/api/settings", json={"release": {"providers": {"anime": ["bad name!"]}}}
    )
    assert invalid_mode.status_code == 422
    assert invalid_mode.json()["code"] == "INVALID_SETTING"

    empty = client.patch("/api/settings", json={"release": {"providers": {"movie": []}}})
    assert empty.status_code == 200
    assert empty.json()["release"]["providers"]["movie"] == []
    unknown = client.patch("/api/settings", json={"release": {"wat": 1}})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "UNKNOWN_SETTING"


def test_anime_sources_accept_animetosho_and_legacy_nyaa(client: TestClient) -> None:
    response = client.patch(
        "/api/settings", json={"release": {"anime_sources": ["torrentio", "animetosho"]}}
    )
    assert response.status_code == 200
    assert response.json()["sources"]["anime_sources"] == ["torrentio", "animetosho"]

    legacy = client.patch("/api/settings", json={"release": {"anime_sources": ["nyaa"]}})
    assert legacy.status_code == 200
    assert legacy.json()["sources"]["anime_sources"] == ["animetosho"]

    empty = client.patch("/api/settings", json={"release": {"anime_sources": []}})
    assert empty.status_code == 422
    assert empty.json()["code"] == "INVALID_SOURCES"

    junk = client.patch("/api/settings", json={"release": {"anime_sources": ["nyaa", "junk"]}})
    assert junk.status_code == 422
    assert junk.json()["code"] == "INVALID_SOURCES"


def test_sources_are_persisted_per_kind(client: TestClient) -> None:
    response = client.patch(
        "/api/settings",
        json={"release": {"sources": {"movie": ["torrentio"], "series": ["torrents-csv"]}}},
    )
    assert response.status_code == 200
    assert response.json()["sources"]["by_kind"]["movie"] == ["torrentio"]
    assert response.json()["sources"]["by_kind"]["series"] == ["torrents-csv"]

    empty = client.patch("/api/settings", json={"release": {"sources": {"movie": []}}})
    assert empty.status_code == 422
    assert empty.json()["code"] == "INVALID_SOURCES"

    unknown = client.patch(
        "/api/settings", json={"release": {"sources": {"movie": ["animetosho"]}}}
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "INVALID_SOURCES"
