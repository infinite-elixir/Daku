#!/usr/bin/env python3
"""Build a self-contained Daku bundle and zip it.

The bundle is a PyInstaller ``--onedir`` directory containing the Python runtime, the
backend and the built SPA. It is for users who do not want to install Python or Node.

    uv sync --extra package
    python scripts/build_bundle.py

    # reuse an existing src/daku/webdist build
    python scripts/build_bundle.py --skip-web

Artifacts land in ``dist/daku/`` and ``dist/daku-<version>-<platform>-<arch>.zip``.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "daku.spec"
DIST = ROOT / "dist"
BUNDLE = DIST / "daku"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def build_web() -> None:
    pnpm = shutil.which("pnpm")
    npm = shutil.which("npm")
    if pnpm is not None:
        run([pnpm, "-C", "web", "install", "--frozen-lockfile"])
        run([pnpm, "-C", "web", "build"])
    elif npm is not None:
        run([npm, "--prefix", "web", "install"])
        run([npm, "--prefix", "web", "run", "build"])
    else:
        raise SystemExit("pnpm/npm not found; install Node or pass --skip-web")


def read_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def add_launchers() -> None:
    cmd = BUNDLE / "start-daku.cmd"
    cmd.write_text(
        '@echo off\r\ncd /d "%~dp0"\r\n"%~dp0daku.exe" %*\r\n',
        encoding="utf-8",
    )
    sh = BUNDLE / "start-daku.sh"
    sh.write_text(
        '#!/usr/bin/env sh\nexec "$(dirname "$0")/daku" "$@"\n',
        encoding="utf-8",
    )
    sh.chmod(0o755)
    (BUNDLE / "README.txt").write_text(
        "Daku portable bundle\n"
        "====================\n\n"
        "Run start-daku.cmd on Windows or ./start-daku.sh on Linux/macOS.\n"
        "Open http://localhost:8420.\n\n"
        "Config and data live under your user profile by default:\n"
        "  config: ~/.config/daku          (Windows: %USERPROFILE%\\.config\\daku)\n"
        "  data:   ~/.local/share/daku     (Windows: %USERPROFILE%\\.local\\share\\daku)\n\n"
        "Set DAKU_CONFIG_DIR and DAKU_DATA_DIR to keep them next to this folder.\n"
        "Run 'daku doctor' from a terminal to check integrations.\n\n"
        "WARNING: no authentication. LAN use only.\n",
        encoding="utf-8",
    )


def zip_bundle(version: str) -> Path:
    arch = platform.machine() or "unknown"
    artifact = DIST / f"daku-{version}-{sys.platform}-{arch}.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(BUNDLE.rglob("*")):
            if path.is_file():
                archive.write(path, Path("daku") / path.relative_to(BUNDLE))
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web", action="store_true", help="reuse src/daku/webdist")
    parser.add_argument("--skip-zip", action="store_true", help="leave the bundle unzipped")
    args = parser.parse_args()

    if not args.skip_web:
        build_web()

    if not (ROOT / "src" / "daku" / "webdist" / "index.html").is_file():
        raise SystemExit("src/daku/webdist/index.html is missing; build the web UI first")

    DIST.mkdir(exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            str(SPEC),
        ]
    )

    add_launchers()
    version = read_version()
    print(f"\nBundle: {BUNDLE}")
    if not args.skip_zip:
        artifact = zip_bundle(version)
        print(f"Archive: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
