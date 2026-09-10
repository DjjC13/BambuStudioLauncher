#!/usr/bin/env python3
"""
Builds BambuStudioLauncher.exe with PyInstaller.

    pip install pyinstaller
    python build_exe.py

Produces a single self-contained exe in dist/ with no console window and the
bamboo icon baked in. Users need nothing installed - not even Python.

Notes on the flags:

* ``--onefile`` unpacks to a temp directory at run time, which is why the app
  reads its icons from ``bambu_core.RESOURCE_DIR`` and writes settings to
  ``bambu_core.USER_DIR`` (%LOCALAPPDATA%) instead of next to the exe.
* ``--windowed`` suppresses the console window; this is a GUI app.
* PIL is excluded deliberately. It is only needed by make_icon.py at authoring
  time and would add megabytes to the download for nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "BambuStudioLauncher"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed.  Run:  pip install pyinstaller",
              file=sys.stderr)
        return 1

    for stale in ("build", "dist", NAME + ".spec"):
        p = HERE / stale
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    # ";" is the Windows separator for --add-data (":" on POSIX).
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", NAME,
        "--icon", str(HERE / "icon.ico"),
        "--add-data", "{};.".format(HERE / "icon.ico"),
        "--add-data", "{};.".format(HERE / "icon_64.png"),
        "--exclude-module", "PIL",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
        "--noconfirm",
        str(HERE / "bambu_launcher.py"),
    ]
    print(" ".join(cmd), "\n")
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        return result.returncode

    exe = HERE / "dist" / (NAME + ".exe")
    if not exe.exists():
        print("Build reported success but {} is missing.".format(exe), file=sys.stderr)
        return 1
    print("\nBuilt {}  ({:,} bytes)".format(exe, exe.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
