"""Build the desktop app and the command-line tool with PyInstaller.

    python packaging/build.py            # both, into dist/

GUI: "PackKeeper" (a .app on macOS, a windowed .exe on Windows, one file on
Linux). With no arguments it opens the window; with arguments it runs the CLI
(on Windows a windowed .exe has no console, so use the CLI build there).
CLI: "packkeeper" ("packkeeper-cli.exe" on Windows), a small console binary
without Qt, built into dist/cli/.
"""

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "packaging" / "entry.py"


def run(*args: str) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--paths", str(ROOT / "src"), *args, str(ENTRY)]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    gui = ["--name", "PackKeeper", "--windowed", "--collect-submodules", "packkeeper"]
    # Only the Qt modules the app uses.
    for mod in ("QtNetwork", "QtQml", "QtQuick", "QtPdf", "QtWebEngineCore", "QtMultimedia", "Qt3DCore"):
        gui += ["--exclude-module", f"PySide6.{mod}"]
    if sys.platform == "darwin":
        gui += ["--osx-bundle-identifier", "io.github.rar.packkeeper"]
    else:
        gui += ["--onefile"]
    run(*gui)
    # "PackKeeper" and "packkeeper" are the same name on case-insensitive file
    # systems (Windows, macOS), so the CLI builds in folders of its own. On
    # Windows the two .exe files would still clash in the zip.
    cli = "packkeeper-cli" if sys.platform == "win32" else "packkeeper"
    own = ["--distpath", str(ROOT / "dist" / "cli"), "--workpath", str(ROOT / "build" / "cli"),
           "--specpath", str(ROOT / "build" / "cli")]
    run("--name", cli, "--console", "--onefile", "--collect-submodules", "packkeeper",
        "--exclude-module", "PySide6", "--exclude-module", "packkeeper.gui", *own)
    print(f"built for {sys.platform} {platform.machine()} in {ROOT / 'dist'}")


if __name__ == "__main__":
    main()
