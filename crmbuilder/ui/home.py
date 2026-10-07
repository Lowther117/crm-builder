"""Home: what needs doing today, what is coming up, and a way in to every list."""
from __future__ import annotations

import datetime as _dt
import json
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import validate
from . import widgets
from .base import Page
from .styles import fs
from .tasks import (AutoWrap, DUE_FIELD, TaskRow, clear, count_words, edit_task_dialog,
                    ensure_styles, group_tasks, hold_scroll, lower_first, parse_due, when_words)

# how many tasks Home shows under each heading before pointing at the Tasks page
TODO_CAPS = {"overdue": 5, "today": 5, "week": 4, "later": 2, "none": 2}
UPCOMING_CAP = 6
GAP = 22                # space between the sections
TICK_MS = 60 * 1000     # how often the page checks whether the day has moved on
# board columns with these names hold finished work, so their money is not "open"
CLOSED_WORDS = {"won", "lost", "closed", "complete", "completed", "done", "cancelled",
                "canceled", "withdrawn", "declined", "rejected", "unsuccessful", "finished",
                "ended", "expired", "archived", "paid", "no bid", "dead"}


def is_closed(option: str) -> bool:
    low = str(option).strip().lower()
    return low in CLOSED_WORDS or low.split(" ")[0] in ("won", "lost", "closed")


def blend(colour: str, onto: str, amount: float) -> str:
    """Mix two #rrggbb colours: amount 1.0 is all `colour`, 0.0 all `onto`."""
    try:
        a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(onto[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return colour
    amount = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % tuple(int(round(x * amount + y * (1 - amount)))
                                   for x, y in zip(a, b))


def money_words(value: float) -> str:
    value = round(float(value), 2)
    if float(value).is_integer():
        return ("-£" if value < 0 else "£") + f"{abs(int(value)):,}"
    return ("-£" if value < 0 else "£") + f"{abs(value):,.2f}"


def or_list(words: list[str]) -> str:
    """['Won', 'Lost', 'No bid'] -> 'Won, Lost or No bid'."""
    words = [str(w) for w in words]
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " or " + words[-1]


def list_stats(db, t: dict) -> dict:
    """What a list's card says besides the count: records per board column
    and the total of its first money field."""
    board, money = bpm.board_field(t), bpm.money_field(t)
    out = {"stages": [], "money": "", "money_tip": ""}
    if board is None and money is None:
        return out
    rows = db.records(t["key"])
    if board is not None:
        options = validate.options_of(board)
        tally = {o: 0 for o in options}
        for r in rows:
            v = r["data"].get(board["key"])
            if v in tally:
                tally[v] += 1
        out["stages"] = [(o, tally[o]) for o in options if tally[o]]
    if money is not None and rows:
        closed = [o for o in validate.options_of(board) if is_closed(o)] if board else []
        open_only = bool(closed) and len(closed) < len(validate.options_of(board))
        total = 0.0
        for r in rows:
            if open_only and r["data"].get(board["key"]) in closed:
                continue
            try:
                total += float(r["data"].get(money["key"]) or 0)
            except (TypeError, ValueError):
                pass
        if open_only:
            # say exactly what was left out, rather than a word like "open"
            out["money"] = f"{money['name']}: {money_words(total)}, not counting {or_list(closed)}"
            out["money_tip"] = (f"{money['name']} added up for every {t['name'].lower()} that is "
                                f"not {or_list(closed)}.")
        else:
            out["money"] = f"{money['name']}: {money_words(total)} in total"
            out["money_tip"] = f"{money['name']} added up for every {t['name'].lower()}."
    return out


class HomePage(Page):
    nav_key = "home"
    auto_refresh = True

    def __init__(self, parent, app, first_time: bool = False, **_kw):
        super().__init__(parent, app)
        ensure_styles(app)
        self.first_time = bool(first_time)
        self._sig = None
        self._day = dbm.today_iso()
        self._width = 0
        self._two = None
        self._all_upcoming = False
        self._side_kind = ""
        self._list_cards: list = []
        self._step_rows: list = []
        self._step_cols = 0
        self._step_intro = None
        self._recent_cells: list = []
        self._list_cols = 0
        self._recent_cols = 0
        self.rows: list[TaskRow] = []
        self.step_links: dict[str, ttk.Label] = {}
        self.task_entry = None
        self._build()
        self._fill(force=True)
        self._tick_id = self.after(TICK_MS, self._tick)
        self.bind("<Destroy>", self._gone, add="+")

    def state(self) -> dict:
        return {"first_time": True} if self.first_time else {}

    def _gone(self, event):
        if event.widget is self:
            try:
                self.after_cancel(self._tick_id)
            except (tk.TclError, ValueError):
                pass

    def _tick(self):
        """The page may be left open all day (and overnight): keep the greeting
        true, and redraw when the day changes so "today" and "overdue" are right."""
        if not self.winfo_exists():
            return
        try:
            if not widgets.grab_holder(self.app.root) and self.db.conn is not None:
                if dbm.today_iso() != self._day:
                    self._fill(force=True)
                else:
                    self._fill_heading(self.db.tasks(mine=True))
        except Exception:               # a window is closing, or the file is busy: try later
            pass
        self._tick_id = self.after(TICK_MS, self._tick)

    # ------------------------------------------------------------- layout
    def _build(self):
        app = self.app
        self.header("Home", " ")
        self.scroll = widgets.ScrollFrame(self, app)
        self.scroll.pack(fill="both", expand=True)
        wrap = ttk.Frame(self.scroll.body, padding=(24, 2, 24, 22))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        self.wrap = wrap
        self.gs_box = ttk.Frame(wrap)
        self.top = ttk.Frame(wrap)
        self.top.grid(row=1, column=0, sticky="ew")
        self.lists_box = ttk.Frame(wrap)
        self.lists_box.grid(row=2, column=0, sticky="ew", pady=(GAP, 0))
        self.recent_box = ttk.Frame(wrap)
        self.footer = ttk.Label(wrap, text="", style="Help.TLabel", justify="left")
        self.footer.grid(row=4, column=0, sticky="w", pady=(GAP + 4, 0))

        # To do: the heading and the add row stay; only the list is redrawn
        self.todo = ttk.Frame(self.top)
        head = ttk.Frame(self.todo)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="To do", style="H2.TLabel").pack(side="left")
        self.all_tasks_link = widgets.link(head, "All tasks", lambda: app.go("tasks"))
        self.all_tasks_link.pack(side="right")
        card = widgets.card(self.todo, app, padding=14)
        card.pack(fill="both", expand=True)
        self.todo_card = card
        if self.db.can_edit:
            row = ttk.Frame(card, style="Panel.TFrame")
            row.pack(fill="x")
            self.task_entry = widgets.SearchEntry(row, app, "Add a task…", on_change=None,
                                                  width=10)
            self.task_entry.grid(row=0, column=0, sticky="ew")
            ttk.Label(row, text="Due", style="PanelHelp.TLabel").grid(row=0, column=1, padx=(10, 5))
            self.task_due = widgets.EntryEditor(row, app, DUE_FIELD)
            self.task_due.entry.configure(width=11)
            for w in self.task_due.widget.winfo_children():
                if isinstance(w, ttk.Button):
                    w.configure(width=5)
            self.task_due.widget.grid(row=0, column=2)
            self.add_btn = ttk.Button(row, text="Add", width=6, command=self.add_task)
            self.add_btn.grid(row=0, column=3, padx=(8, 0))
            row.columnconfigure(0, weight=1)
            self.task_entry.bind("<Return>", lambda _e: self.add_task(), add="+")
            self.task_due.entry.bind("<Return>", lambda _e: self.add_task())
            self.task_error = ttk.Label(card, text="", style="PanelError.TLabel")
            ttk.Frame(card, style="Panel.TFrame", height=10).pack(fill="x")
        self.todo_list = ttk.Frame(card, style="Panel.TFrame")
        self.todo_list.pack(fill="both", expand=True)
        ready = self._sized
        self.todo_wrap = AutoWrap(self.todo_list, default=fs(300), ready=ready)
        self.side = ttk.Frame(self.top)
        self.side_wrap = AutoWrap(self.side, default=fs(240), ready=ready)
        # the canvas width is the one width that never depends on the content,
        # so laying out from it cannot chase its own tail
        self.scroll.canvas.bind("<Configure>", self._resized, add="+")

    def _sized(self) -> bool:
        return self.scroll.canvas.winfo_width() > 60

    def _resized(self, event):
        width = event.width - 48
        if width != self._width and width > 50:
            self._width = width
            self._layout()

    def _layout(self):
        """Place the sections for the width there is."""
        width = self._width or fs(900)
        has_side = bool(self._side_kind)
        two = has_side and width >= fs(760)
        top = self.top
        if (two, has_side) != self._two:
            self._two = (two, has_side)
            self.todo.grid_forget()
            self.side.grid_forget()
            if two:
                top.columnconfigure(0, weight=3, uniform="top")
                top.columnconfigure(1, weight=2, uniform="top")
                self.todo.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
                self.side.grid(row=0, column=1, sticky="new")
            else:
                top.columnconfigure(0, weight=1, uniform="")
                top.columnconfigure(1, weight=0, uniform="")
                self.todo.grid(row=0, column=0, sticky="nsew")
                if has_side:
                    self.side.grid(row=1, column=0, sticky="nsew", pady=(GAP, 0))
        # getting started: the steps in two columns when there is room
        k = len(self._step_rows)
        if k:
            cols = 2 if (width >= fs(760) and k > 3) else 1
            if cols != self._step_cols:
                self._step_cols = cols
                grid = self._step_rows[0].master
                grid.columnconfigure(0, weight=1, uniform="steps")
                grid.columnconfigure(1, weight=1 if cols == 2 else 0,
                                     uniform="steps" if cols == 2 else "")
                per_col = -(-k // cols)
                for i, row in enumerate(self._step_rows):
                    row.grid(row=i % per_col, column=i // per_col, sticky="w", pady=3,
                             padx=(0, 16))
            self._step_intro.configure(wraplength=max(200, width - 40))
        # list cards: as many across as fit
        n = len(self._list_cards)
        if n:
            cols = max(1, min(n, 5, width // fs(215)))
            if cols != self._list_cols:
                self._list_cols = cols
                grid = self._list_cards[0].master
                for i in range(6):
                    grid.columnconfigure(i, weight=1 if i < cols else 0,
                                         uniform="lists" if i < cols else "")
                for i, card in enumerate(self._list_cards):
                    last = i % cols == cols - 1
                    card.grid(row=i // cols, column=i % cols, sticky="nsew",
                              padx=(0, 0 if last else 12), pady=(0, 12 if i < n - cols else 0))
            per = width / cols
            for card in self._list_cards:
                card.name_label.configure(wraplength=max(90, int(per) - 110))
                for lab in card.wrap_labels:
                    lab.configure(wraplength=max(110, int(per) - 44))
        # recently changed: two or three columns of links when there is room
        m = len(self._recent_cells)
        if m:
            cols = max(1, min(3, width // fs(300)))
            if cols != self._recent_cols:
                self._recent_cols = cols
                grid = self._recent_cells[0].master
                for i in range(4):
                    grid.columnconfigure(i, weight=1 if i < cols else 0,
                                         uniform="recent" if i < cols else "")
                per_col = -(-m // cols)
                for i, cell in enumerate(self._recent_cells):
                    cell.grid(row=i % per_col, column=i // per_col, sticky="nw",
                              padx=(0, 16), pady=(0, 8))
            per = width / cols
            for cell in self._recent_cells:
                cell.link.configure(wraplength=max(110, int(per) - 24))
        self.footer.configure(wraplength=max(200, width))

    # --------------------------------------------------------------- data
    def _steps(self) -> list[dict]:
        """The getting-started checklist for this person: [{key, text, done, go}]."""
        db, app = self.db, self.app
        steps = []
        types = bpm.active_types(db.blueprint)
        if db.can_edit and types:
            first = types[0]
            done = any(not r.get("example") for r in db.records(first["key"]))
            steps.append({"key": "add", "text": f"Add your first {first['name'].lower()}",
                          "done": done, "go": lambda k=first["key"]: app.new_record(k)})
            steps.append({"key": "import", "text": "Bring in a spreadsheet you already have",
                          "done": bool(db.get_meta("has_imported")),
                          "go": lambda: app.go("import")})
        if db.is_admin:
            try:
                changed = bool(db.conn.execute(
                    "SELECT 1 FROM audit WHERE action='design' AND new NOT IN "
                    "('Created', 'Options added by an import') LIMIT 1"
                ).fetchone())
            except Exception:
                changed = False
            steps.append({"key": "design", "text": "Change what a list holds",
                          "done": changed, "go": lambda: app.go("design")})
            steps.append({"key": "people",
                          "text": "Add the people who will use it" if db.mode == "team"
                          else "Share it with colleagues",
                          "done": len(db.users()) > 1, "go": lambda: app.go("people")})
        if db.can_edit and db.has_examples():
            steps.append({"key": "examples",
                          "text": "Remove the example records when you have seen enough",
                          "done": False, "go": app.remove_examples})
        return steps

    def _gather(self) -> dict:
        db = self.db
        types = bpm.active_types(db.blueprint)
        counts = db.counts()
        d: dict = {"types": types, "counts": counts}
        d["tasks"] = db.tasks(mine=True)
        d["reminders"] = any(f["kind"] == "date" and f.get("remind")
                             for t in types for f in bpm.active_fields(t))
        d["upcoming"] = db.upcoming() if d["reminders"] else []
        d["stats"] = {t["key"]: list_stats(db, t) for t in types}
        d["recent"] = db.recent()
        d["backup"] = db.last_backup()
        steps = self._steps()
        show = False
        if steps:
            if self.first_time:
                show = True
            elif db.get_meta("hide_getting_started") != "1":
                nearly_empty = sum(counts.get(t["key"], 0) for t in types) < 3
                show = (nearly_empty or db.has_examples()) and not all(s["done"] for s in steps)
        d["steps"] = steps if show else []
        return d

    @staticmethod
    def _signature(d: dict) -> str:
        return json.dumps([
            dbm.today_iso(),
            [(t["id"], t["title"], t["due"], t["record_title"], t["detail"]) for t in d["tasks"]],
            [(u["date"], u["record"]["id"], u["record"]["title"], u["field"]["name"])
             for u in d["upcoming"]],
            [(t["key"], t["plural"], t["color"], d["counts"].get(t["key"], 0),
              d["stats"][t["key"]]) for t in d["types"]],
            [(r["id"], r["title"], r["updated_at"]) for r in d["recent"]],
            [(s["key"], s["text"], s["done"]) for s in d["steps"]],
            d["backup"], d["reminders"]], default=str)

    def refresh(self) -> None:
        self._fill()

    def _fill(self, force: bool = False):
        d = self._gather()
        sig = self._signature(d)
        if sig == self._sig and not force:
            return
        self._sig = sig
        self._day = dbm.today_iso()
        restore = hold_scroll(self.scroll)
        self._fill_heading(d["tasks"])
        self._fill_steps(d["steps"])
        self._fill_todo(d["tasks"])
        if d["reminders"]:
            self._side_kind = "upcoming"
            self._fill_upcoming(d["upcoming"])
            self._fill_recent(d["recent"], self.recent_box)
        else:
            # nothing to remind about in this design: use the space beside
            # To do for what changed recently
            self._side_kind = "recent" if d["recent"] else ""
            clear(self.side)
            self.side_wrap.forget()
            self._fill_recent([], self.recent_box)
            if d["recent"]:
                self._fill_recent(d["recent"], self.side)
        self._fill_lists(d)
        self._fill_footer(d["backup"])
        self._two = None
        self._list_cols = self._recent_cols = self._step_cols = 0
        self._layout()
        restore()

    # ------------------------------------------------------------ heading
    def _fill_heading(self, tasks):
        db = self.db
        now = _dt.datetime.now()
        part = "morning" if now.hour < 12 else ("afternoon" if now.hour < 18 else "evening")
        hello = f"Good {part}"
        if db.mode == "team" and db.user.get("name"):
            hello += ", " + db.user["name"].split(" ")[0]
        self.title_label.configure(text=hello)
        today = dbm.today_iso()
        overdue = sum(1 for t in tasks if t["due"] and t["due"] < today)
        due = sum(1 for t in tasks if t["due"] == today)
        day = f"{now.strftime('%A')} {now.day} {now.strftime('%B')}"
        if overdue and due:
            say = f"{count_words(overdue, 'task')} overdue and {due:,} due today"
        elif overdue:
            say = f"{count_words(overdue, 'task')} overdue"
        elif due:
            say = f"{count_words(due, 'task')} due today"
        else:
            say = "nothing due today"
        self.subtitle_label.configure(text=f"{day}  ·  {say}")

    # ---------------------------------------------------- getting started
    def _fill_steps(self, steps):
        box = self.gs_box
        clear(box)
        self.step_links = {}
        self._step_rows = []
        self._step_cols = 0
        self._step_intro = None
        if not steps:
            box.grid_remove()
            return
        box.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        c = self.c
        card = tk.Frame(box, bg=c["hint"], highlightthickness=1, highlightbackground=c["border"],
                        highlightcolor=c["border"], padx=16, pady=14)
        card.pack(fill="x")
        head = ttk.Frame(card, style="Hint.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=f"Welcome – {self.db.name} is ready" if self.first_time
                  else "Getting started", style="HintH2.TLabel").pack(side="left")
        self.hide_link = widgets.link(head, "Hide this", self.hide_getting_started,
                                      style="HintLink.TLabel")
        self.hide_link.pack(side="right")
        if self.first_time:
            text = ("Your lists are down the left – open one to see what is in it, and press "
                    "+ New at the top to add to any of them. A few things to try next:")
        else:
            text = "A few things to try next. Each one is ticked off as you do it."
        intro = ttk.Label(card, text=text, style="HmHintText.TLabel", justify="left")
        intro.pack(anchor="w", pady=(6, 8))
        self._step_intro = intro
        grid = ttk.Frame(card, style="Hint.TFrame")
        grid.pack(fill="x")
        for s in steps:
            row = ttk.Frame(grid, style="Hint.TFrame")
            self._step_rows.append(row)
            self._tick_box(row, s["done"]).pack(side="left", padx=(0, 9))
            if s["done"]:
                ttk.Label(row, text=s["text"], style="HmHintDim.TLabel").pack(side="left")
                ttk.Label(row, text="Done", style="HintGood.TLabel").pack(side="left", padx=(10, 0))
            else:
                lab = widgets.link(row, s["text"], s["go"], style="HintLink.TLabel")
                lab.pack(side="left")
                self.step_links[s["key"]] = lab

    def _tick_box(self, parent, done: bool) -> tk.Canvas:
        """A small drawn tick box (drawn, so it does not depend on any font)."""
        c = self.c
        size = fs(15)
        cv = tk.Canvas(parent, width=size, height=size, bg=c["hint"], highlightthickness=0)
        if done:
            cv.create_rectangle(1, 1, size - 1, size - 1, outline=c["good"], fill=c["good"])
            cv.create_line(size * 0.24, size * 0.52, size * 0.43, size * 0.72, size * 0.78,
                           size * 0.28, fill=c["hint"], width=2, capstyle="round",
                           joinstyle="round")
        else:
            cv.create_rectangle(1, 1, size - 1, size - 1, outline=c["field_border"],
                                fill=c["field"])
        return cv

    def hide_getting_started(self):
        self.db.set_meta("hide_getting_started", "1")
        self.first_time = False
        self._fill(force=True)

    # -------------------------------------------------------------- to do
    def _fill_todo(self, tasks):
        frame = self.todo_list
        clear(frame)
        self.todo_wrap.forget()
        self.rows = []
        if not tasks:
            text = ("Nothing to do – add a task here or on any record." if self.db.can_edit
                    else "Nothing to do.")
            self.todo_wrap.add(ttk.Label(frame, text=text, style="PanelHelp.TLabel",
                                         justify="left"), 8).pack(anchor="w", pady=(2, 4))
            return
        first = True
        for key, label, items in group_tasks(tasks):
            head = ttk.Frame(frame, style="Panel.TFrame")
            head.pack(fill="x", pady=(0 if first else 12, 3))
            first = False
            ttk.Label(head, text=label.upper(), style="HmPanelGroupBad.TLabel"
                      if key == "overdue" else "HmPanelGroup.TLabel").pack(side="left")
            if len(items) > 1:
                ttk.Label(head, text=f"{len(items):,}", style="HmPanelGroup.TLabel").pack(
                    side="left", padx=(8, 0))
            cap = TODO_CAPS.get(key, 3)
            for t in items[:cap]:
                row = TaskRow(frame, self, t, wrap=self.todo_wrap)
                row.pack(fill="x", pady=4)
                self.rows.append(row)
            if len(items) > cap:
                widgets.link(frame, f"Show all {len(items):,}", lambda: self.app.go("tasks"),
                             style="HmPanelSmallLink.TLabel").pack(anchor="w", padx=(fs(26), 0),
                                                                  pady=(2, 0))

    def _task_problem(self, text: str):
        self.task_error.configure(text=text)
        if text:
            self.task_error.pack(anchor="w", pady=(0, 6), before=self.todo_list)
        else:
            self.task_error.pack_forget()

    def add_task(self):
        if not self.db.can_edit:
            return
        title = self.task_entry.get_text()
        if not title:
            self._task_problem("Say what needs doing first.")
            self.task_entry.focus_set()
            return
        due, problem = parse_due(self.task_due.get())
        if problem:
            self._task_problem(problem)
            self.task_due.focus()
            return
        try:
            self.db.add_task(title, due=due, assigned_to=self.db.user["id"])
        except dbm.DBError as exc:
            self._task_problem(str(exc))
            return
        self._task_problem("")
        self.task_entry.set_text("")
        self.task_due.set(None)
        self.task_entry.focus_set()
        self._fill(force=True)
        self.app.refresh_sidebar()

    def task_toggled(self, _task):
        self._fill_heading(self.db.tasks(mine=True))
        self._sig = None
        self.app.refresh_sidebar()

    def edit_task(self, task):
        if not self.db.can_edit:
            return
        result = edit_task_dialog(self.app, task)
        if result:
            self._fill(force=True)
            self.app.refresh_sidebar()
            if result == "deleted":
                self.app.toast("Task deleted")

    # ---------------------------------------------------------- coming up
    def _fill_upcoming(self, upcoming):
        side = self.side
        clear(side)
        self.side_wrap.forget()
        ttk.Label(side, text="Coming up", style="H2.TLabel").pack(anchor="w", pady=(0, 8))
        card = widgets.card(side, self.app, padding=14)
        card.pack(fill="both", expand=True)
        if not upcoming:
            self.side_wrap.add(ttk.Label(
                card, text="No dates coming up in the next two weeks.", style="PanelHelp.TLabel",
                justify="left"), 40).pack(anchor="w")
            return
        today = dbm.today_iso()
        shown = upcoming if self._all_upcoming else upcoming[:UPCOMING_CAP]
        for i, u in enumerate(shown):
            row = ttk.Frame(card, style="Panel.TFrame")
            row.pack(fill="x", pady=(0 if i == 0 else 9, 0))
            late = u["date"] < today
            ttk.Label(row, text=validate.friendly_date(u["date"]), width=11, anchor="w",
                      style="PanelError.TLabel" if late else "PanelHelp.TLabel").grid(
                row=0, column=0, sticky="nw", pady=(2, 0))
            lab = widgets.link(row, u["record"]["title"],
                               lambda rid=u["record"]["id"]: self.app.open_record(rid),
                               style="PanelLink.TLabel")
            lab.configure(justify="left")
            lab.grid(row=0, column=1, sticky="w")
            self.side_wrap.add(lab, fs(118))
            what = u["field"]["name"]
            ttk.Label(row, text=what + (" – overdue" if late else ""),
                      style="PanelHelp.TLabel").grid(row=1, column=1, sticky="w")
            row.columnconfigure(1, weight=1)
        left = len(upcoming) - len(shown)
        if left > 0:
            widgets.link(card, f"Show {left:,} more", self._show_all_upcoming,
                         style="HmPanelSmallLink.TLabel").pack(anchor="w", pady=(10, 0))

    def _show_all_upcoming(self):
        self._all_upcoming = True
        self._fill(force=True)

    # --------------------------------------------------------- your lists
    def _fill_lists(self, d):
        box = self.lists_box
        clear(box)
        self._list_cards = []
        ttk.Label(box, text="Your lists", style="H2.TLabel").pack(anchor="w", pady=(0, 8))
        types = d["types"]
        if not types:
            row = ttk.Frame(box)
            row.pack(anchor="w")
            ttk.Label(row, text="There are no lists yet.", style="Help.TLabel").pack(side="left")
            if self.db.is_admin:
                widgets.link(row, "Add one in the design", lambda: self.app.go("design")).pack(
                    side="left", padx=(8, 0))
            return
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for t in types:
            self._list_cards.append(self._list_card(grid, t, d["counts"].get(t["key"], 0),
                                                    d["stats"][t["key"]]))

    def _list_card(self, parent, t, count, stats):
        c, app = self.c, self.app
        card = tk.Frame(parent, bg=c["panel"], highlightthickness=1, takefocus=1,
                        highlightbackground=c["border"], highlightcolor=c["accent"])
        tk.Frame(card, bg=t["color"], width=4).pack(side="left", fill="y")
        inner = ttk.Frame(card, style="Panel.TFrame", padding=(12, 10, 12, 11))
        inner.pack(side="left", fill="both", expand=True)
        card.wrap_labels = []
        head = ttk.Frame(inner, style="Panel.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=f"{count:,}", style="HmPanelCount.TLabel").pack(side="right",
                                                                           anchor="n")
        name = ttk.Label(head, text=t["plural"], style="PanelH3.TLabel", justify="left")
        name.pack(side="left", anchor="n", pady=(2, 0))
        card.name_label = name
        if stats["stages"]:
            self._stage_bar(inner, t, stats["stages"]).pack(fill="x", pady=(7, 0))
            lab = ttk.Label(inner, text="  ·  ".join(f"{o} {n:,}" for o, n in stats["stages"]),
                            style="PanelHelp.TLabel", justify="left")
            lab.pack(anchor="w", pady=(6, 0))
            card.wrap_labels.append(lab)
        if stats["money"]:
            lab = ttk.Label(inner, text=stats["money"], style="PanelHelp.TLabel", justify="left")
            lab.pack(anchor="w", pady=(3, 0))
            card.wrap_labels.append(lab)
            widgets.Tooltip(lab, stats["money_tip"], app)
        if not count:
            ttk.Label(inner, text="Nothing here yet", style="PanelHelp.TLabel").pack(
                anchor="w", pady=(3, 0))

        def go(_e=None, key=t["key"]):
            app.go("list", type_key=key)
        card.bind("<Return>", go)
        card.bind("<space>", go)
        stack = [card]
        while stack:
            w = stack.pop()
            w.bind("<Button-1>", go)
            try:
                w.configure(cursor="hand2")
            except tk.TclError:
                pass
            stack.extend(w.winfo_children())
        card.open = go
        return card

    def _stage_bar(self, parent, t, stages) -> tk.Frame:
        """A thin bar split in proportion to how many records are at each
        stage, first stage on the left, in fading tints of the list's colour."""
        c = self.c
        bar = tk.Frame(parent, bg=c["panel"], height=5)
        total = sum(n for _o, n in stages) or 1
        steps = max(1, len(stages) - 1)
        at = 0
        for i, (_o, n) in enumerate(stages):
            tint = blend(t["color"], c["panel"], 1.0 - 0.7 * i / steps)
            last = i == len(stages) - 1
            tk.Frame(bar, bg=tint).place(relx=at / total, rely=0, relheight=1,
                                         relwidth=n / total, width=0 if last else -2)
            at += n
        return bar

    # ---------------------------------------------------- recently changed
    def _fill_recent(self, recent, box):
        if box is self.recent_box:
            clear(box)
            self._recent_cells = []
            if not recent:
                box.grid_remove()
                return
            box.grid(row=3, column=0, sticky="ew", pady=(GAP, 0))
            ttk.Label(box, text="Recently changed", style="H2.TLabel").pack(anchor="w",
                                                                            pady=(0, 8))
            grid = ttk.Frame(box)
            grid.pack(fill="x")
            for r in recent:
                cell = ttk.Frame(grid)
                cell.link = self._recent_item(cell, r, panel=False)
                self._recent_cells.append(cell)
            return
        # beside To do, on a card
        ttk.Label(box, text="Recently changed", style="H2.TLabel").pack(anchor="w", pady=(0, 8))
        card = widgets.card(box, self.app, padding=14)
        card.pack(fill="both", expand=True)
        for i, r in enumerate(recent):
            cell = ttk.Frame(card, style="Panel.TFrame")
            cell.pack(fill="x", pady=(0 if i == 0 else 8, 0))
            self.side_wrap.add(self._recent_item(cell, r, panel=True), 40)

    def _recent_item(self, cell, r, panel: bool):
        t = bpm.get_type(self.db.blueprint, r["type"]) or {}
        pre = "Panel" if panel else ""
        lab = widgets.link(cell, r["title"], lambda rid=r["id"]: self.app.open_record(rid),
                           style=f"{pre}Link.TLabel")
        lab.configure(justify="left")
        lab.pack(anchor="w")
        bits = [t.get("name", ""), lower_first(when_words(r["updated_at"]))]
        ttk.Label(cell, text="  ·  ".join(b for b in bits if b),
                  style=f"{pre}Help.TLabel").pack(anchor="w")
        return lab

    # -------------------------------------------------------------- footer
    def _fill_footer(self, backup):
        if backup:
            words = validate.friendly_date(backup)
            last = "Last automatic backup: " + lower_first(words)
        else:
            last = "No backup yet"
        self.footer.configure(text=f"Kept in {self.db.path}  ·  {last}")
