from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / ".pyinstaller-cache")
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def main() -> None:
    for folder in (DIST, BUILD):
        if folder.exists():
            shutil.rmtree(folder)
    system = platform.system()
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--name", "直播守候",
        "--icon",
        str(ROOT / "assets" / ("streamkeeper.ico" if system == "Windows" else "streamkeeper.icns")),
        "--collect-all", "imageio_ffmpeg",
        "--hidden-import", "httpx",
        "--hidden-import", "httpcore",
        "--add-data", f"assets/app-icon-concept-v2.png{';' if system == 'Windows' else ':'}assets",
        "--add-data", f"third_party/upstream/LICENSE{';' if system == 'Windows' else ':'}licenses/upstream",
        "run.py",
    ]
    if system == "Darwin":
        command += ["--osx-bundle-identifier", "com.streamkeeper.recorder"]
    run(command)

    if system == "Darwin":
        app = DIST / "直播守候.app"
        dmg = DIST / "StreamKeeper-macOS.dmg"
        if app.exists() and shutil.which("hdiutil"):
            try:
                run(["hdiutil", "create", "-volname", "直播守候", "-srcfolder", str(app), "-ov", "-format", "UDZO", str(dmg)])
            except subprocess.CalledProcessError:
                print("DMG creation was skipped; the .app bundle is ready in dist/.")
    elif system == "Windows":
        folder = DIST / "直播守候"
        archive = shutil.make_archive(str(DIST / "StreamKeeper-Windows"), "zip", folder)
        print("Created", archive)


if __name__ == "__main__":
    main()
