# PyInstaller entry point: no arguments opens the app, arguments run the CLI.
import sys

from eg4tool.cli import main

sys.exit(main())
