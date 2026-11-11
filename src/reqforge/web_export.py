from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
WEB_DIR = PKG_DIR / "web"

SKIP_DIRS = {"__pycache__", "web"}
SKIP_FILES = {"server.py", "web_export.py", "cli.py"}


def export_static_web_bundle(out_dir: str | Path) -> list[Path]:
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for fname in ("index.html", "styles.css", "app.js"):
        src = WEB_DIR / fname
        target = dest / fname
        shutil.copyfile(src, target)
        written.append(target)

    zip_path = dest / "reqforge_core.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for py_file in sorted(PKG_DIR.rglob("*.py")):
            rel = py_file.relative_to(PKG_DIR)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if rel.name in SKIP_FILES and len(rel.parts) == 1:
                continue
            zf.write(py_file, arcname=str(Path("reqforge") / rel).replace("\\", "/"))
    written.append(zip_path)
    return written
