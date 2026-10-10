"""Build the Windows app: stage the shared code and page, then run PyInstaller.

    pip install -r windows/requirements.txt pyinstaller
    python windows/build.py            # makes windows/dist/LocalEventScout.exe

It also works on Linux/macOS for checking the build (the result runs with --no-tray).
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
INTEGRATION = REPO / "custom_components" / "local_event_scout"
STAGE = HERE / "_staging"


def stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    shutil.copytree(INTEGRATION / "scout", STAGE / "scout", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(HERE / "event_scout_app" / "web", STAGE / "web")
    shutil.copy2(INTEGRATION / "frontend" / "event-scout-panel.js", STAGE / "web" / "event-scout-panel.js")
    sys.path.insert(0, str(HERE))
    from event_scout_app.icon import icon_image  # noqa: PLC0415

    icon_image(256).save(STAGE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def build() -> None:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", "LocalEventScout",
        "--icon", str(STAGE / "icon.ico"),
        "--paths", str(STAGE),
        "--paths", str(HERE),
        "--add-data", f"{STAGE / 'web'}{os.pathsep}web",
        "--collect-submodules", "scout",
        "--collect-data", "tzdata",
        "--hidden-import", "tzlocal",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]
    if sys.platform == "win32":
        args += ["--windowed", "--hidden-import", "pystray._win32", "--hidden-import", "keyring.backends.Windows"]
    args.append(str(HERE / "run.py"))
    subprocess.run(args, check=True)


if __name__ == "__main__":
    stage()
    build()
    print("Built", HERE / "dist" / ("LocalEventScout.exe" if sys.platform == "win32" else "LocalEventScout"))
