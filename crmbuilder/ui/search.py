"""Search results for the box in the top bar: every list at once, and tasks."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import validate
from . import widgets
from .base import Page
from .styles import fs
from .tasks import (AutoWrap, TaskRow, clear, count_words, edit_task_dialog, ensure_styles,
                    shorten)

LIMIT = 60
TASK_LIMIT = 20
SQUASHED = ("phone", "postcode", "ni")      # kinds also found when typed without spaces


def snippet(text: str, word: str, limit: int = 90) -> str:
    """One line of text around the first place `word` appears."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    at = text.lower().find(word)
    if at < 0:
        at = 0
    start = max(0, at - limit // 3)
    if start:
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    end = min(len(text), start + limit)
    if end < len(text):
        space = text.rfind(" ", start, end)
        if space > at + len(word):
            end = space
    return ("… " if start else "") + text[start:end].strip() + (" …" if end < len(text) else "")


def where_matched(db, t: dict, record: dict, words: list[str]) -> str:
    """'Field: value' for the first field that explains why this record was
    found, or '' when its name or reference already shows why."""
    head = (record["title"] + " " + record["ref"]).lower()
    missing = [w for w in words if w not in head]
    if not missing:
        return ""
    title_keys = set(t.get("title") or [])
    for f in bpm.active_fields(t):
        if f["key"] in title_keys and f["kind"] != "link":
            continue
        text = validate.display(f, record["data"].get(f["key"]), db.lookup)
        if not text:
            continue
        low = text.lower()
        squashed = low.replace(" ", "") if f["kind"] in SQUASHED else ""
        for w in missing:
            if w in low or (squashed and w in squashed):
                return f"{f['name']}: {snippet(text, w)}"
    return ""


def find_tasks(db, words: list[str]) -> list[dict]:
    """Tasks whose wording contains every word: open ones first, soonest first."""
    out = []
    for t in db.tasks(include_done=True):
        text = f"{t.get('title') or ''} {t.get('detail') or ''}".lower()
        if all(w in text for w in words):
            out.append(t)
    return out


class SearchPage(Page):
    nav_key = ""

    def __init__(self, parent, app, query: str = "", **_kw):
        super().__init__(parent, app)
        ensure_styles(app)
        self.query = (query or "").strip()
        self.results: list[dict] = []
        self.tasks: list[dict] = []
        self.rows: list[TaskRow] = []
        self.links: list[ttk.Label] = []
        self.header("Search", " ")
        self.scroll = widgets.ScrollFrame(self, app)
        self.scroll.pack(fill="both", expand=True)
        self.body = ttk.Frame(self.scroll.body, padding=(24, 0, 24, 20))
        self.body.pack(fill="both", expand=True)
        self.wrap = AutoWrap(self.body, default=fs(480),
                             ready=lambda: self.scroll.canvas.winfo_width() > 60)
        self._show()

    def state(self) -> dict:
        return {"query": self.query}

    def set_query(self, text: str) -> None:
        """Called by the shell as the person types in the top search box."""
        text = (text or "").strip()
        if text == self.query:
            return
        self.query = text
        self.scroll.to_top()
        self._show()

    def refresh(self) -> None:
        self._show()

    def open(self, record_id: int):
        self.app.open_record(record_id)

    # ------------------------------------------------------------ drawing
    def _show(self):
        clear(self.body)
        self.wrap.forget()
        self.links = []
        self.results = []
        self.tasks = []
        self.rows = []
        if not self.query:
            self.subtitle_label.configure(text="Find anything, in any list")
            self._hint()
            return
        db = self.db
        words = self.query.lower().split()
        asked = shorten(self.query, 60)         # a very long search must not run off the page
        self.results = db.search_all(self.query, limit=LIMIT)
        self.tasks = find_tasks(db, words)
        n = len(self.results)
        if not n and not self.tasks:
            self.subtitle_label.configure(text=f"Nothing found for “{asked}”")
            self._nothing()
            return
        more = n >= LIMIT
        found = [f"The first {n} records" if more else count_words(n, "record")] if n else []
        if self.tasks:
            found.append(count_words(len(self.tasks), "task"))
        self.subtitle_label.configure(text=f"{' and '.join(found)} found for “{asked}”")
        by_type: dict[str, list[dict]] = {}
        for r in self.results:
            by_type.setdefault(r["type"], []).append(r)
        first = True
        for t in bpm.active_types(db.blueprint):
            rows = by_type.get(t["key"])
            if rows:
                self._group(t, rows, words, first)
                first = False
        if more:
            self.wrap.add(ttk.Label(
                self.body, text=f"Only the first {LIMIT} records are shown. Add another word to "
                "narrow it down.", style="Help.TLabel", justify="left"), 8).pack(
                anchor="w", pady=(14, 0))
        if self.tasks:
            self._task_group(first)

    def _group(self, t, rows, words, first):
        c, app = self.c, self.app
        head = ttk.Frame(self.body)
        head.pack(fill="x", pady=(4 if first else 16, 6))
        tk.Frame(head, bg=t["color"], width=4, height=fs(14)).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=t["plural"], style="H3.TLabel").pack(side="left")
        ttk.Label(head, text=f"{len(rows):,}", style="Help.TLabel").pack(side="left", padx=(8, 0))
        widgets.link(head, f"Show these in {t['plural']}",
                     lambda k=t["key"]: app.go("list", type_key=k, search=self.query),
                     style="HmSmallLink.TLabel").pack(side="right")
        card = widgets.card(self.body, app, padding=0)
        card.pack(fill="x")
        for i, r in enumerate(rows):
            if i:
                tk.Frame(card, bg=c["border"], height=1).pack(fill="x", padx=14)
            row = ttk.Frame(card, style="Panel.TFrame", padding=(14, 8, 14, 8), cursor="hand2")
            row.pack(fill="x")
            lab = widgets.link(row, r["title"], lambda rid=r["id"]: self.open(rid),
                               style="PanelLink.TLabel")
            lab.configure(justify="left")
            lab.grid(row=0, column=0, sticky="w")
            self.wrap.add(lab, fs(150))
            self.links.append(lab)
            ref = ttk.Label(row, text=r["ref"], style="PanelHelp.TLabel")
            ref.grid(row=0, column=1, sticky="ne", padx=(12, 0), pady=(2, 0))
            row.columnconfigure(0, weight=1)
            extras = [ref]
            why = where_matched(self.db, t, r, words)
            if why:
                w = ttk.Label(row, text=why, style="PanelHelp.TLabel", justify="left")
                w.grid(row=1, column=0, columnspan=2, sticky="w", pady=(1, 0))
                self.wrap.add(w, fs(40))
                extras.append(w)
            # anywhere on the row opens it (the title is the keyboard target)
            for w in [row] + extras:
                w.bind("<Button-1>", lambda _e, rid=r["id"]: self.open(rid))
                w.configure(cursor="hand2")

    def _task_group(self, first: bool):
        """The tasks that match, under the records: tick them off or click to change."""
        c, app = self.c, self.app
        head = ttk.Frame(self.body)
        head.pack(fill="x", pady=(4 if first else 16, 6))
        tk.Frame(head, bg=c["dim"], width=4, height=fs(14)).pack(side="left", padx=(0, 8))
        ttk.Label(head, text="Tasks", style="H3.TLabel").pack(side="left")
        ttk.Label(head, text=f"{len(self.tasks):,}", style="Help.TLabel").pack(side="left",
                                                                              padx=(8, 0))
        widgets.link(head, "Show all tasks", lambda: app.go("tasks"),
                     style="HmSmallLink.TLabel").pack(side="right")
        card = widgets.card(self.body, app, padding=12)
        card.pack(fill="x")
        for t in self.tasks[:TASK_LIMIT]:
            row = TaskRow(card, self, t, wrap=self.wrap, reserve=fs(76))
            row.pack(fill="x", pady=4)
            self.rows.append(row)
        if len(self.tasks) > TASK_LIMIT:
            ttk.Label(card, text=f"Only the first {TASK_LIMIT} are shown.",
                      style="PanelHelp.TLabel").pack(anchor="w", pady=(6, 0))

    # the task rows call these (as they do on the Tasks page)
    def task_toggled(self, _task):
        self.app.refresh_sidebar()

    def edit_task(self, task):
        if not self.db.can_edit:
            return
        result = edit_task_dialog(self.app, task)
        if result:
            self._show()
            self.app.refresh_sidebar()
            if result == "deleted":
                self.app.toast("Task deleted")

    def _hint(self):
        types = bpm.active_types(self.db.blueprint)
        ref = f"{types[0]['prefix']}-0004" if types else "CON-0004"
        box = ttk.Frame(self.body)
        box.pack(fill="x", pady=(6, 0))
        self.wrap.add(ttk.Label(
            box, text="Type in the box at the top of the window. Every list is searched at "
            "once, and results appear as you type.", justify="left"), 8).pack(anchor="w")
        ttk.Label(box, text="You can search for", style="H3.TLabel").pack(anchor="w", pady=(14, 4))
        for line in ("names, and anything else written on a record",
                     "phone numbers, with or without the spaces",
                     "postcodes and email addresses",
                     f"references such as {ref}",
                     "tasks, by what needs doing"):
            self.wrap.add(ttk.Label(box, text="•  " + line, style="HmDim.TLabel",
                                    justify="left"), 8).pack(anchor="w", pady=1)
        self.wrap.add(ttk.Label(
            box, text="Type more than one word to narrow it down: only records and tasks "
            "containing all of them are shown.", style="Help.TLabel", justify="left"), 8).pack(
            anchor="w", pady=(14, 0))

    def _nothing(self):
        box = ttk.Frame(self.body)
        box.pack(fill="x", pady=(6, 0))
        ttk.Label(box, text="Nothing matches", style="H2.TLabel").pack(anchor="w")
        self.wrap.add(ttk.Label(
            box, text="Check the spelling, or try fewer words – a record or a task is only "
            "found when it contains every word you type.", style="Help.TLabel",
            justify="left"), 8).pack(
            anchor="w", pady=(4, 0))
