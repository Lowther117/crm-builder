"""Where things live: the app folder, the settings file, the default save folder."""
from __future__ import annotations

import json
import os
import sys

SETTINGS_NAME = "crm-builder-settings.json"


def app_dir() -> str:
    """Folder that holds the app: beside the exe, beside the .app bundle (never
    inside it - writing there breaks the signature), or the repo root from source."""
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        parts = exe.split(os.sep)
        for i, p in enumerate(parts):
            if p.endswith(".app"):
                return os.sep.join(parts[:i]) or os.sep
        return os.path.dirname(exe)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(folder: str) -> bool:
    try:
        probe = os.path.join(folder, ".crm-builder-write-test")
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def settings_path() -> str:
    folder = app_dir()
    if not _writable(folder):  # e.g. the app was dropped into /Applications
        folder = os.path.join(os.path.expanduser("~"), ".crm-builder")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass                # nowhere to keep settings: the app still runs
    return os.path.join(folder, SETTINGS_NAME)


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        path = settings_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


def downloads_dir() -> str:
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD),
                            ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
            fid = GUID(0x374DE290, 0x123F, 0x4565,
                       (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0 and out.value:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if os.path.isdir(path):
                    return path
        except Exception:
            pass
    path = os.path.join(home, "Downloads")
    return path if os.path.isdir(path) else home


def default_save_dir(settings: dict | None = None) -> str:
    chosen = (settings or {}).get("save_dir")
    if isinstance(chosen, str) and chosen and os.path.isdir(chosen):
        return chosen
    return downloads_dir()


def remember_recent(settings: dict, path: str) -> None:
    path = os.path.abspath(path)
    recent = settings.get("recent")     # (the file may have been edited by hand)
    recent = [p for p in recent if isinstance(p, str) and p != path] if isinstance(recent, list) else []
    settings["recent"] = [path] + recent[:7]
    settings["last_db"] = path
    save_settings(settings)


SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud",
                "mobile documents", "sharepoint", "box sync", "creative cloud")


def looks_synced(path: str) -> bool:
    """True when the path sits inside a cloud-sync folder, where a live database
    file gets corrupted or forked into conflict copies."""
    low = os.path.abspath(path).lower()
    return any(m in low for m in SYNC_MARKERS)
