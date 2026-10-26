from __future__ import annotations

import json
from pathlib import Path

import pytest

from daku.__main__ import main


def test_db_cli_migrate_and_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DAKU_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("DAKU_DATA_DIR", str(tmp_path / "data"))

    assert main(["db", "init"]) == 0
    assert main(["db", "migrate"]) == 0
    assert (tmp_path / "data" / "daku.db").exists()

    assert main(["db", "dump"]) == 0
    output = capsys.readouterr().out
    dump = json.loads(output[output.index("{") :])
    assert {"titles", "releases", "downloads", "settings", "events"} <= set(dump)
