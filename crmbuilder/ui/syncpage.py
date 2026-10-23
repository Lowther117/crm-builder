"""The Sync page: how a CRM shared through a synced folder is getting on, and
the clashes a merge has flagged for someone to look at."""
from __future__ import annotations

import os
from tkinter import ttk

from .. import db as dbm
from .. import validate
from . import widgets
from .admin import SYNC_NAMES, _ScrollPage, plural, reveal_file, wrap_to
from .styles import fs


def _when(stamp: str) -> str:
    return validate.format_stamp(stamp) if stamp else "never"


class SyncPage(_ScrollPage):
    nav_key = "sync"
    auto_refresh = True

    def __init__(self, parent, app, **_kw):
        super().__init__(parent, app)
        self.synced = bool(getattr(self.db, "synced", False))
        right = self.header("Sync",
                            "This CRM is shared through a synced folder. Everyone works on their "
                            "own copy; changes are merged into the shared file every couple of "
                            "minutes, and whenever you press Sync now." if self.synced else
                            "Sharing a CRM through a synced folder.", back=True)
        wrap_to(self.subtitle_label, self.subtitle_label.master, margin=8)
        self.subtitle_label.configure(justify="left")
        if self.synced:
            self.sync_btn = ttk.Button(right, text="Sync now", style="Accent.TButton",
                                       command=self.sync_now)
            self.sync_btn.pack(side="right")
        self.body = self._scroll_body()
        self.build()

    def build(self):
        for w in self.body.winfo_children():
            w.destroy()
        if not self.synced:
            self._explain()
            return
        self._status()
        self._copies()
        self._conflicts()

    def refresh(self) -> None:
        self.build()

    # ------------------------------------------------------------- cards
    def _explain(self):
        card = self.card("Not shared this way")
        self.para(card, "This CRM is not in a synced folder, so there is nothing to sync: it is "
                        "saved straight to its file. To share it with people on other computers "
                        f"without a server, move the file into a folder that is synced for all of "
                        f"you ({SYNC_NAMES}) and have everyone open it from there. The app then "
                        "keeps everyone's changes in step the way this page describes.")
        self.para(card, "A shared network drive works too, and needs none of this: everyone can "
                        "open that same file directly.", style="PanelHelp.TLabel", pady=(8, 0))

    def _status(self):
        db, app = self.db, self.app
        info = db.store.info()
        card = self.card("Where things stand")
        right, _lab = self.line(card, "The shared file:", info["shared"], pady=(4, 0))
        ttk.Button(right, text="Show in folder",
                   command=lambda: reveal_file(app, info["shared"])).pack(side="right")
        if not info["online"]:
            self.para(card, "The shared file cannot be reached at the moment. You can carry on "
                            "working: your changes are kept here and go in once it is back.",
                      style="PanelWarn.TLabel", pady=(8, 0))
        n = info["pending"]
        if n:
            self.para(card, f"{plural(n, 'change')} of yours waiting to go into the shared file.",
                      pady=(8, 0))
        else:
            self.para(card, "All your changes are in the shared file.", pady=(8, 0))
        last = info["last_at"]
        if last:
            self.para(card, f"Last merged {_when(last)}"
                            + (f" by {info['last_by']}" if info["last_by"] else "") + ".",
                      style="PanelDim.TLabel")
        if info["merging"]:
            self.para(card, f"{info['merging']} is merging at the moment.",
                      style="PanelDim.TLabel")
        if info["error"]:
            self.para(card, info["error"], style="PanelWarn.TLabel", pady=(8, 0))
        self.para(card, "How it works: each person works on their own copy, kept outside the "
                        "synced folder, and the app writes their changes to a queue of their own "
                        "in the shared folder. Every couple of minutes one copy of the app takes "
                        "the shared file, puts in every queue - everyone's, oldest change first - "
                        "and writes it back whole. Nothing is ever dropped: when two people have "
                        "changed the same thing while apart, the later change wins and it is "
                        "listed below for someone to look at.",
                  style="PanelHelp.TLabel", pady=(12, 0))

    def _copies(self):
        copies = self.db.store.conflict_copies()
        if not copies:
            return
        card = self.card("Extra copies made by your sync service")
        self.para(card, "Your sync service made these when two computers wrote the shared file "
                        "at the same moment. Everything in them is also in the queues, so they "
                        "can be deleted; they are not read by the app.")
        for p in copies:
            right, _lab = self.line(card, os.path.basename(p), pady=(6, 0))
            ttk.Button(right, text="Show in folder",
                       command=lambda p=p: reveal_file(self.app, p)).pack(side="right")

    def _conflicts(self):
        db = self.db
        rows = db.conflicts()
        card = self.card("Changes that clashed" + (f" ({len(rows)})" if rows else ""))
        if not rows:
            self.para(card, "Nothing to look at. When two people change the same thing while "
                            "apart, or someone writes on a record another person deleted, it is "
                            "listed here with both values.", style="PanelDim.TLabel")
            return
        self.para(card, "The change that won is what the record holds now. Open the record to "
                        "put it right if need be, then mark the clash as sorted.",
                  style="PanelDim.TLabel")
        cols = ("when", "record", "field", "kept", "other", "note")
        frame, tree = widgets.make_tree(card, cols, selectmode="browse",
                                        height=min(12, max(4, len(rows))), xscroll=True)
        frame.pack(fill="x", pady=(10, 0))
        for key, text, width, stretch in (("when", "When", 130, False),
                                          ("record", "Record", 160, False),
                                          ("field", "Field", 110, False),
                                          ("kept", "Kept", 150, True),
                                          ("other", "Other value", 150, True),
                                          ("note", "What happened", 260, True)):
            tree.heading(key, text=text, anchor="w")
            tree.column(key, width=fs(width), minwidth=fs(60), stretch=stretch, anchor="w")
        self.by_item: dict[str, dict] = {}
        for r in rows:
            name = f"{r['ref']}" if r["ref"] else "(no record)"
            item = tree.insert("", "end", values=(
                _when(r["at"]), name, r["field"] or "", r["mine"] or "", r["theirs"] or "",
                r["note"] or ""))
            self.by_item[item] = r
        self.tree = tree
        foot = ttk.Frame(card, style="Panel.TFrame")
        foot.pack(fill="x", pady=(8, 0))
        self.detail = ttk.Label(foot, text="", style="Panel.TLabel", justify="left")
        self.detail.pack(side="left", fill="x", expand=True, anchor="w")
        wrap_to(self.detail, foot, margin=fs(260), least=200)
        self.open_btn = ttk.Button(foot, text="Open record", command=self.open_selected,
                                   state="disabled")
        self.open_btn.pack(side="right")
        if db.can_edit:
            self.sorted_btn = ttk.Button(foot, text="Mark as sorted", command=self.mark_sorted,
                                         state="disabled")
            self.sorted_btn.pack(side="right", padx=(0, 8))
        tree.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        tree.bind("<Double-1>", lambda _e: self.open_selected())

    # ----------------------------------------------------------- actions
    def _current(self) -> dict | None:
        sel = self.tree.selection()
        return self.by_item.get(sel[0]) if sel else None

    def _selected(self):
        r = self._current()
        if r is None:
            return
        self.detail.configure(text=r["note"] or "")
        self.open_btn.configure(state="normal" if r["record_id"] else "disabled")
        if hasattr(self, "sorted_btn"):
            self.sorted_btn.configure(state="normal")

    def open_selected(self):
        r = self._current()
        if r and r["record_id"] and self.db.get_record(r["record_id"]):
            self.app.open_record(r["record_id"])
        elif r and r["record_id"]:
            self.app.toast("That record no longer exists.", "bad")

    def mark_sorted(self):
        r = self._current()
        if r is None:
            return
        try:
            self.db.resolve_sync_conflict(r["id"])
        except dbm.DBError as exc:
            widgets.error(self.app, "That did not work", str(exc))
            return
        self.build()
        self.app.refresh_sidebar()

    def sync_now(self):
        app, db = self.app, self.db
        self.sync_btn.configure(state="disabled", text="Syncing…")
        self.update_idletasks()
        try:
            out = db.sync_now()
        except dbm.DBError as exc:
            widgets.error(app, "Could not sync", str(exc))
            out = None
        finally:
            if self.sync_btn.winfo_exists():
                self.sync_btn.configure(state="normal", text="Sync now")
        if out is not None:
            if out.get("busy"):
                app.toast(f"{out['busy']} is syncing right now - try again in a moment.")
            else:
                bits = []
                if out["sent"]:
                    bits.append(f"{plural(out['sent'], 'change')} of yours sent")
                if out["received"]:
                    bits.append(f"{plural(out['received'], 'change')} from other people")
                if out["conflicts"]:
                    bits.append(f"{plural(out['conflicts'], 'clash', 'clashes')} to look at")
                app.toast("Synced: " + ", ".join(bits) + "." if bits else "Synced: nothing new.",
                          "good")
        app.synced_now()
        self.build()
