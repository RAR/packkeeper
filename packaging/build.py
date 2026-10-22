"""Build the desktop app and the command-line tool with PyInstaller.

    python packaging/build.py            # both, into dist/

GUI: "EG4 BMS Tool" (a .app on macOS, a windowed .exe on Windows, one file on
Linux). With no arguments it opens the window; with arguments it runs the CLI
(on Windows a windowed .exe has no console, so use the CLI build there).
CLI: "eg4tool", a small console binary without Qt.
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
    gui = ["--name", "EG4 BMS Tool", "--windowed", "--collect-submodules", "eg4tool"]
    # Only the Qt modules the app uses.
    for mod in ("QtNetwork", "QtQml", "QtQuick", "QtPdf", "QtWebEngineCore", "QtMultimedia", "Qt3DCore"):
        gui += ["--exclude-module", f"PySide6.{mod}"]
    if sys.platform == "darwin":
        gui += ["--osx-bundle-identifier", "io.github.rar.eg4bmstool"]
    else:
        gui += ["--onefile"]
    run(*gui)
    run("--name", "eg4tool", "--console", "--onefile", "--collect-submodules", "eg4tool",
        "--exclude-module", "PySide6", "--exclude-module", "eg4tool.gui")
    print(f"built for {sys.platform} {platform.machine()} in {ROOT / 'dist'}")


if __name__ == "__main__":
    main()
