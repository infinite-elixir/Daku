# PyInstaller onedir bundle for Daku.
#
# Build with:
#   uv sync --extra package
#   python scripts/build_bundle.py
#
# The SPA is included from src/daku/webdist. PyInstaller places package data under
# ``daku/webdist`` so app.py's ``Path(__file__).parent / "webdist"`` keeps working.
from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - injected by PyInstaller

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("pydantic")
    + [
        "anyio._backends._asyncio",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.pysqlite",
    ]
)

datas = [
    (os.path.join(root, "src", "daku", "webdist"), "daku/webdist"),
]

a = Analysis(  # noqa: F821
    [os.path.join(root, "src", "daku", "__main__.py")],
    pathex=[os.path.join(root, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="daku",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="daku",
)
