#!/usr/bin/env python3
"""CRM Builder - a CRM you design yourself.

  python crm_builder.py                 open the window
  python crm_builder.py FILE.crm        open the window with that CRM
  python crm_builder.py selftest        check that everything works; writes
                                        crm-builder-selftest.txt beside this file
  python crm_builder.py selftest --no-gui    the same without opening a window
  python crm_builder.py --version

run.bat (Windows) and run.command (macOS) do this for you, setting Python up
the first time. build-exe.bat / build-app.command make a standalone app.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str]) -> int:
    first = argv[0].lower() if argv else ""
    if first in ("selftest", "--selftest", "-t"):
        # imported here so the self-test also runs on a Python without tkinter
        from crmbuilder.selftest import main as selftest_main
        return selftest_main(argv[1:])
    if first in ("-h", "--help", "/?"):
        print(__doc__.strip())
        return 0
    if first in ("--version", "-v"):
        from crmbuilder import APP_NAME, __version__
        print(f"{APP_NAME} {__version__}")
        return 0
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        print(f"CRM Builder needs Python with Tk (tkinter), and this Python has none ({exc}).\n"
              "Use run.bat / run.command, which set up a suitable Python, or install one:\n"
              "  Windows: python.org installer (tick 'tcl/tk')   macOS: brew install python python-tk",
              file=sys.stderr)
        return 1
    try:
        from crmbuilder.ui.app import run
        return run(argv)
    except Exception as exc:  # noqa: BLE001
        # run.bat starts this with pythonw, which has no console: without this a
        # start-up failure would show nothing at all. Same crash log and dialog
        # as the built app.
        from crm_builder_app import _crash
        _crash(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
