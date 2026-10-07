#!/usr/bin/env python3
"""Frozen entry point for CRM Builder (PyInstaller builds).

Handles what a double-clicked, windowed executable has to handle: Finder's
-psn argument, a missing stdout, and start-up failures that would otherwise
be silent.

  CRM Builder                 open the window
  CRM Builder FILE.crm        open the window with that CRM
  CRM Builder selftest        run the self-test, write crm-builder-selftest.txt
                              beside the app, exit 0 (passed) or 1 (problems)
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import traceback

CRASH_LOG = "crm-builder-crash.log"
SELFTEST_REPORT = "crm-builder-selftest.txt"


def _app_dir():
    """Beside the exe / beside the .app bundle (never inside it) / this folder.
    crmbuilder.paths.app_dir() is the one definition; the copy below it is only
    for the case where the package itself is what failed to load."""
    try:
        from crmbuilder.paths import app_dir
        return app_dir()
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        parts = exe.split(os.sep)
        for i, p in enumerate(parts):
            if p.endswith(".app"):
                return os.sep.join(parts[:i]) or os.sep
        return os.path.dirname(exe)
    return os.path.dirname(os.path.abspath(__file__))


def _fix_streams():
    """A windowed build has no console: sys.stdout and sys.stderr are None."""
    class _Null:
        def write(self, *_a):
            return 0

        def flush(self):
            pass

        def isatty(self):
            return False

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _Null())
        else:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def _write_beside(name, text, mode):
    """Write a file beside the app; when that folder cannot be written to (the
    app was put in Program Files, or macOS is running it from a read-only
    copy), in the same ~/.crm-builder folder the settings fall back to.
    Returns the path written, or None."""
    fallback = os.path.join(os.path.expanduser("~"), ".crm-builder")
    for folder in (_app_dir(), fallback):
        try:
            if folder == fallback:
                os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            with open(path, mode, encoding="utf-8") as fh:
                fh.write(text)
            return path
        except Exception:
            continue
    return None


def _crash(exc, dialog=True):
    body = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = _write_beside(CRASH_LOG, body + "\n" + "-" * 60 + "\n", "a")
    if dialog:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            where = f"Details written to:\n{path}" if path else "(The details could not be written to a file.)"
            messagebox.showerror("CRM Builder could not start",
                                 f"{type(exc).__name__}: {exc}\n\n{where}")
            root.destroy()
        except Exception:
            pass
    print(body, file=sys.stderr)
    return body


def _selftest_could_not_run(body):
    """The self-test itself failed to load or crashed outright. The build script
    reads the report file, so say so there (no dialog: nobody may be watching)."""
    _write_beside(SELFTEST_REPORT, "CRM Builder self-test\n\n  FAIL  The self-test could not run:\n\n"
                  + body + "\nPROBLEMS FOUND: 1\n", "w")


def main():
    multiprocessing.freeze_support()
    _fix_streams()
    # Finder passes -psn_0_12345 to a bundle; it is not a file to open.
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    selftest = bool(args) and args[0].lower() in ("selftest", "--selftest", "-t")
    try:
        if selftest:
            from crmbuilder.selftest import main as selftest_main
            return selftest_main(args[1:])
        from crmbuilder.ui.app import run
        return run(args)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        body = _crash(exc, dialog=not selftest)
        if selftest:
            _selftest_could_not_run(body)
        return 1


if __name__ == "__main__":
    sys.exit(main())
