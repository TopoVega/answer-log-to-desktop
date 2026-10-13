"""Frozen-app entry point for PyInstaller.

A windowed build must not print to a console, but startup failures would
otherwise vanish completely, so they go to a log file next to the exe.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> int:
    try:
        from workbench.app import main as app_main

        return app_main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException:
        err = traceback.format_exc()
        try:
            (_log_dir() / "AgentWorkbench.startup.log").write_text(err, encoding="utf-8")
        except OSError:
            pass
        print(err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
