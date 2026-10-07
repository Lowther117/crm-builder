"""Tasks: everything that needs doing, in one place.

Also holds the pieces Home and Search share with this page: the task row,
the "change task" window, grouping by due date and a few small styles.
"""
from __future__ import annotations

import datetime as _dt
import tkinter as tk
from tkinter import ttk

from .. import db as dbm
from .. import validate
from . import widgets
from .base import Page
from .styles import fs

GROUPS = [("overdue", "Overdue"), ("today", "Today"), ("week", "This week"),
          ("later", "Later"), ("none", "No date")]
PAGE_SIZE = 30          # task rows drawn per group before "Show more"
DUE_FIELD = {"key": "due", "name": "Due", "kind": "date"}
ANYONE = "Anyone"
TICK_MS = 60 * 1000     # how often an open page checks whether the day has moved on


# ------------------------------------------------------------ shared bits
def ensure_styles(app) -> None:
    """The few extra label styles these pages use, in the current palette."""
    c = app.c
    s = ttk.Style(app.root)
    ui = c["ui"]
    for prefix, bg in (("Hm", c["bg"]), ("HmPanel", c["panel"]), ("HmHint", c["hint"])):
        s.configure(f"{prefix}Group.TLabel", background=bg, foreground=c["dim"],
                    font=(ui, fs(8), "bold"))
        s.configure(f"{prefix}GroupBad.TLabel", background=bg, foreground=c["bad"],
                    font=(ui, fs(8), "bold"))
        s.configure(f"{prefix}SmallLink.TLabel", background=bg, foreground=c["accent"],
                    font=(ui, fs(9)))
        s.configure(f"{prefix}Done.TLabel", background=bg, foreground=c["dim"],
                    font=(ui, fs(10), "overstrike"))
        s.configure(f"{prefix}Count.TLabel", background=bg, foreground=c["text"],
                    font=(ui, fs(14), "bold"))
        s.configure(f"{prefix}Text.TLabel", background=bg, foreground=c["text"],
                    font=(ui, fs(10)))
        s.configure(f"{prefix}Dim.TLabel", background=bg, foreground=c["dim"],
                    font=(ui, fs(10)))
        s.configure(f"{prefix}BadSmall.TLabel", background=bg, foreground=c["bad"],
                    font=(ui, fs(9)))
        s.configure(f"{prefix}DimSmall.TLabel", background=bg, foreground=c["dim"],
                    font=(ui, fs(9)))


def group_key(due, today: str | None = None) -> str:
    """Which heading a task with this due date belongs under."""
    if not due:
        return "none"
    today = today or dbm.today_iso()
    due = str(due)[:10]
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    try:
        week = (_dt.date.fromisoformat(today) + _dt.timedelta(days=7)).isoformat()
    except ValueError:
        return "later"
    return "week" if due <= week else "later"


def group_tasks(tasks: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """[(key, heading, tasks)] for the groups that have something in them."""
    today = dbm.today_iso()
    buckets: dict[str, list[dict]] = {k: [] for k, _ in GROUPS}
    for t in tasks:
        buckets[group_key(t.get("due"), today)].append(t)
    return [(k, label, buckets[k]) for k, label in GROUPS if buckets[k]]


def local_date(stamp) -> str:
    """The local calendar day (yyyy-mm-dd) of a stored UTC time stamp."""
    text = validate.format_stamp(stamp or "")
    try:
        return _dt.datetime.strptime(text[:10], "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def when_words(stamp) -> str:
    """'Today 14:05', 'Yesterday', 'Mon 5 Oct' - for a stored time stamp."""
    day = local_date(stamp)
    if not day:
        return ""
    words = validate.friendly_date(day)
    if words == "Today":
        return "Today " + validate.format_stamp(stamp)[11:16]
    return words


def lower_first(text: str) -> str:
    """'Today' -> 'today', but 'Mon 5 Oct' stays as it is."""
    if text in ("Today", "Tomorrow", "Yesterday"):
        return text.lower()
    return text


def shorten(text, limit: int = 90) -> str:
    """One line of at most about `limit` characters, cut between words."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rindex(" ")]
    else:
        cut = cut[:limit]
    return cut.rstrip(" ,.;:–-") + "…"


def count_words(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def clear(frame) -> None:
    for w in frame.winfo_children():
        w.destroy()


def hold_scroll(scroll: widgets.ScrollFrame):
    """Remember where a ScrollFrame is; the function returned puts it back
    there after the contents were rebuilt."""
    try:
        top = scroll.canvas.canvasy(0)
    except tk.TclError:
        top = 0

    def restore():
        try:
            if top <= 0 or not scroll.canvas.winfo_viewable():
                return          # at the top already, or not on screen yet
            scroll.update_idletasks()
            height = max(1, scroll.body.winfo_reqheight())
            scroll.canvas.configure(scrollregion=(0, 0, scroll.body.winfo_reqwidth(), height))
            scroll.canvas.yview_moveto(max(0.0, min(1.0, top / height)))
        except tk.TclError:
            pass
    return restore


class AutoWrap:
    """Keeps the wrap width of labels in step with the width of a container,
    so long text wraps instead of being cut off."""

    def __init__(self, container, default: int = 320, ready=None):
        self.items: list = []
        self.width = 0
        self.default = default
        self.ready = ready      # () -> bool: False while the page has no size yet
        container.bind("<Configure>", self._resized, add="+")

    def add(self, label, reserve: int = 0, share: float = 1.0):
        """reserve = pixels beside the label that it must leave free;
        share = the part of the container's width it lives in."""
        self.items.append((label, reserve, share))
        label.configure(wraplength=self._length(reserve, share))
        return label

    def forget(self):
        self.items = []

    def _length(self, reserve, share):
        if not self.width:
            return self.default
        return max(110, int(self.width * share) - reserve)

    def _resized(self, event):
        if event.width == self.width or (self.ready is not None and not self.ready()):
            return
        self.width = event.width
        keep = []
        for label, reserve, share in self.items:
            try:
                label.configure(wraplength=self._length(reserve, share))
                keep.append((label, reserve, share))
            except tk.TclError:
                pass
        self.items = keep


def seg_buttons(parent, options, current, command) -> dict:
    """A row of buttons that work as a switch. options = [(key, text)].
    Returns {key: button}; use seg_mark() to show which one is on."""
    buttons = {}
    for i, (key, text) in enumerate(options):
        b = ttk.Button(parent, text=text, command=lambda k=key: command(k),
                       style="SegOn.TButton" if key == current else "Seg.TButton")
        b.pack(side="left", padx=(0 if i == 0 else 4, 0))
        buttons[key] = b
    return buttons


def seg_mark(buttons: dict, current) -> None:
    for key, b in buttons.items():
        b.configure(style="SegOn.TButton" if key == current else "Seg.TButton")


def parse_due(text: str):
    """(iso date or None, error sentence or None) from what was typed."""
    return validate.normalise(dict(DUE_FIELD), text or "")


class TaskRow(ttk.Frame):
    """One task on a card: tick box, what it is, the record it is about, when
    it is due. The owner (a page) provides .app, task_toggled(task) and
    edit_task(task)."""

    def __init__(self, parent, owner, task: dict, wrap: AutoWrap | None = None,
                 show_who: bool = False, reserve: int = 0, share: float = 1.0):
        super().__init__(parent, style="Panel.TFrame")
        self.owner, self.app, self.task = owner, owner.app, task
        db = self.app.db
        can = db.can_edit
        self.var = tk.BooleanVar(self, value=bool(task.get("done_at")))
        self.check = None
        if can:
            self.check = ttk.Checkbutton(self, variable=self.var, style="Panel.TCheckbutton",
                                         command=self.toggle)
            self.check.grid(row=0, column=0, sticky="nw", padx=(0, 2), pady=(1, 0))
        else:
            ttk.Label(self, text="•", style="PanelDim.TLabel").grid(
                row=0, column=0, sticky="nw", padx=(2, 8))
        self.title = ttk.Label(self, text=task["title"], justify="left")
        self.title.grid(row=0, column=1, sticky="w")
        if can:
            self.title.configure(cursor="hand2", takefocus=1)
            for seq in ("<Button-1>", "<Return>", "<space>"):
                self.title.bind(seq, lambda _e: owner.edit_task(self.task))
        self.columnconfigure(1, weight=1)
        used = fs(34) + reserve
        if show_who:
            who = db.user_name(task.get("assigned_to")) or ANYONE
            ttk.Label(self, text=shorten(who, 18), style="PanelHelp.TLabel", width=16,
                      anchor="w").grid(row=0, column=2, sticky="ne", padx=(12, 0), pady=(2, 0))
            used += fs(130)
        self.due = ttk.Label(self, text="", width=13, anchor="e")
        self.due.grid(row=0, column=3, sticky="ne", padx=(12, 0), pady=(2, 0))
        used += fs(112)
        if wrap is not None:
            wrap.add(self.title, used, share)
        else:
            self.title.configure(wraplength=fs(300))

        about = task.get("record_title") if task.get("record_id") else ""
        detail = shorten(task.get("detail") or "", 70)
        if about or detail:
            meta = ttk.Frame(self, style="Panel.TFrame")
            meta.grid(row=1, column=1, columnspan=3, sticky="w", pady=(1, 0))
            if about:
                lab = widgets.link(meta, shorten(about, 60),
                                   lambda: self.app.open_record(task["record_id"]),
                                   style="HmPanelSmallLink.TLabel")
                lab.pack(side="left")
                widgets.Tooltip(lab, "Open this record", self.app)
            if detail:
                ttk.Label(meta, text=("  ·  " if about else "") + detail,
                          style="PanelHelp.TLabel").pack(side="left")
        self.restyle()

    def restyle(self):
        t = self.task
        if t.get("done_at"):
            self.title.configure(style="HmPanelDone.TLabel")
            day = local_date(t["done_at"])
            self.due.configure(text="Done " + lower_first(validate.friendly_date(day)) if day
                               else "Done", style="PanelHelp.TLabel")
            return
        self.title.configure(style="Panel.TLabel")
        due = t.get("due")
        if not due:
            self.due.configure(text="", style="PanelHelp.TLabel")
        else:
            self.due.configure(text=validate.friendly_date(due),
                               style="PanelError.TLabel" if str(due)[:10] < dbm.today_iso()
                               else "PanelHelp.TLabel")

    def toggle(self):
        """The tick box was clicked: mark the task done (or open again). The
        row stays where it is, crossed out, so a slip is easy to undo."""
        done = bool(self.var.get())
        try:
            self.app.db.set_task_done(self.task["id"], done)
        except dbm.DBError as exc:
            self.var.set(not done)
            self.app.toast(str(exc), "bad")
            return
        self.task["done_at"] = validate.now_stamp() if done else None
        self.restyle()
        self.owner.task_toggled(self.task)


def edit_task_dialog(app, task: dict):
    """The small window for changing or deleting a task.
    Returns 'saved', 'deleted' or None (nothing changed)."""
    db = app.db
    team = db.mode == "team"
    d = widgets.Dialog(app, "Change task", width=fs(460))
    body = d.body
    ttk.Label(body, text="What needs doing", style="Field.TLabel").pack(anchor="w")
    d.title_var = tk.StringVar(d, value=task["title"])
    entry = ttk.Entry(body, textvariable=d.title_var)
    entry.pack(fill="x", pady=(3, 10))

    row = ttk.Frame(body)
    row.pack(fill="x")
    ttk.Label(row, text="Due", style="Field.TLabel").grid(row=0, column=0, sticky="w")
    d.due = widgets.EntryEditor(row, app, DUE_FIELD, panel=False)
    d.due.entry.configure(width=12)
    d.due.widget.grid(row=1, column=0, sticky="w", pady=(3, 0))
    d.due.set(task.get("due"))
    d.who = None
    people: list[tuple[object, str]] = []
    if team:
        people = [(None, ANYONE)] + [(u["id"], u["name"]) for u in db.users(active_only=True)]
        current = task.get("assigned_to")
        if current is not None and current not in [p[0] for p in people]:
            people.append((current, db.user_name(current) or "Someone who has left"))
        ttk.Label(row, text="For", style="Field.TLabel").grid(row=0, column=1, sticky="w",
                                                             padx=(16, 0))
        d.who = ttk.Combobox(row, state="readonly", values=[p[1] for p in people], width=24)
        d.who.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(3, 0))
        d.who.current([p[0] for p in people].index(current))
    ttk.Label(body, text="Details", style="Field.TLabel").pack(anchor="w", pady=(10, 0))
    d.detail = widgets.make_text(body, app, height=5, width=48)
    d.detail.pack(fill="both", expand=True, pady=(3, 0))
    widgets.set_text(d.detail, task.get("detail") or "")
    d.detail.edit_reset()
    if task.get("record_id") and task.get("record_title"):
        ttk.Label(body, text="About " + shorten(task["record_title"], 60),
                  style="Help.TLabel").pack(anchor="w", pady=(8, 0))
    err = ttk.Label(body, text="", style="Error.TLabel", wraplength=fs(430), justify="left")
    err.pack(anchor="w", pady=(6, 0))

    def save():
        title = d.title_var.get().strip()
        if not title:
            err.configure(text="Say what needs doing.")
            entry.focus_set()
            return
        due, problem = parse_due(d.due.get())
        if problem:
            err.configure(text=problem)
            d.due.focus()
            return
        changes = {"title": title, "due": due,
                   "detail": d.detail.get("1.0", "end-1c").strip()}
        if d.who is not None:
            changes["assigned_to"] = people[max(0, d.who.current())][0]
        try:
            db.update_task(task["id"], **changes)
        except dbm.DBError as exc:
            err.configure(text=str(exc))
            return
        d.ok("saved")

    def delete():
        yes = widgets.confirm(app, "Delete task",
                              f"Delete the task “{shorten(task['title'], 80)}”? "
                              "This cannot be undone.", yes="Delete task", danger=True)
        if not yes:
            try:
                d.grab_set()
            except tk.TclError:
                pass
            return
        try:
            db.delete_task(task["id"])
        except dbm.DBError as exc:
            err.configure(text=str(exc))
            return
        d.ok("deleted")

    d.save_btn = d.add_button("Save task", save, accent=True)
    d.add_button("Cancel", d.cancel)
    d.delete_btn = d.add_button("Delete", delete, side="left", style="Danger.TButton")
    d.default_on_enter()
    entry.icursor("end")
    return d.show(focus=entry)


# ------------------------------------------------------------------ page
class TasksPage(Page):
    nav_key = "tasks"
    auto_refresh = True

    def __init__(self, parent, app, add: bool = False, who: str = "mine", show: str = "open",
                 **_kw):
        super().__init__(parent, app)
        ensure_styles(app)
        db = self.db
        self.team = db.mode == "team"
        self.who = who if (self.team and who in ("mine", "all")) else "mine"
        self.show = show if show in ("open", "done") else "open"
        self.tasks: list[dict] = []
        self._shown: dict[str, int] = {}
        self._groups: dict[str, dict] = {}
        self._sig = None
        self._day = dbm.today_iso()
        self._people: list[tuple[object, str]] = []
        self.rows: list[TaskRow] = []
        self.entry = None
        self._build()
        self.reload(force=True)
        if add and self.entry is not None:
            self.after(60, self._focus_entry)
        self._tick_id = self.after(TICK_MS, self._tick)
        self.bind("<Destroy>", self._gone, add="+")

    def _gone(self, event):
        if event.widget is self:
            try:
                self.after_cancel(self._tick_id)
            except (tk.TclError, ValueError):
                pass

    def _tick(self):
        """Left open overnight, the page must still know what today is."""
        if not self.winfo_exists():
            return
        try:
            if not widgets.grab_holder(self.app.root) and self.db.conn is not None \
                    and dbm.today_iso() != self._day:
                self.reload(force=True)
        except Exception:               # a window is closing, or the file is busy: try later
            pass
        self._tick_id = self.after(TICK_MS, self._tick)

    # ------------------------------------------------------------- layout
    def _build(self):
        app, db = self.app, self.db
        self.header("Tasks", " ")
        if db.can_edit:
            card = widgets.card(self, app, padding=14)
            card.pack(fill="x", padx=24, pady=(0, 12))
            self.add_card = card
            ttk.Label(card, text="What needs doing", style="PanelField.TLabel").grid(
                row=0, column=0, sticky="w")
            self.title_var = tk.StringVar(self)
            self.entry = ttk.Entry(card, textvariable=self.title_var)
            self.entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
            ttk.Label(card, text="Due", style="PanelField.TLabel").grid(
                row=0, column=1, sticky="w", padx=(10, 0))
            self.due = widgets.EntryEditor(card, app, DUE_FIELD)
            self.due.entry.configure(width=11)
            self.due.widget.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(3, 0))
            col = 2
            self.who_box = None
            if self.team:
                self._people = [(u["id"], u["name"]) for u in db.users(active_only=True)]
                self._people.append((None, ANYONE))
                ttk.Label(card, text="For", style="PanelField.TLabel").grid(
                    row=0, column=col, sticky="w", padx=(10, 0))
                self.who_box = ttk.Combobox(card, state="readonly", width=18,
                                            values=[p[1] for p in self._people])
                self.who_box.grid(row=1, column=col, sticky="ew", padx=(10, 0), pady=(3, 0))
                ids = [p[0] for p in self._people]
                self.who_box.current(ids.index(db.user["id"]) if db.user["id"] in ids else 0)
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    self.who_box.bind(seq, lambda _e: "break")
                col += 1
            self.add_btn = ttk.Button(card, text="Add task", style="Accent.TButton",
                                      command=self.add_task)
            self.add_btn.grid(row=1, column=col, padx=(10, 0), pady=(3, 0))
            self.add_error = ttk.Label(card, text="", style="PanelError.TLabel")
            card.columnconfigure(0, weight=1)
            self.entry.bind("<Return>", lambda _e: self.add_task())
            self.due.entry.bind("<Return>", lambda _e: self.add_task())

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=24, pady=(0, 6))
        self.who_buttons = {}
        if self.team:
            box = ttk.Frame(bar)
            box.pack(side="left", padx=(0, 18))
            self.who_buttons = seg_buttons(box, [("mine", "Mine"), ("all", "Everyone")],
                                           self.who, self.set_who)
        box = ttk.Frame(bar)
        box.pack(side="left")
        self.show_buttons = seg_buttons(box, [("open", "Open"), ("done", "Done")],
                                        self.show, self.set_show)

        self.scroll = widgets.ScrollFrame(self, app)
        self.scroll.pack(fill="both", expand=True)
        self.list = ttk.Frame(self.scroll.body, padding=(24, 0, 24, 20))
        self.list.pack(fill="both", expand=True)
        self.wrap = AutoWrap(self.list, default=fs(420),
                             ready=lambda: self.scroll.canvas.winfo_width() > 60)
        # keep the boxes above the list in line with it when its scrollbar shows
        self._fixed = [w for w in (getattr(self, "add_card", None), bar) if w is not None]
        self._bar_gap = None
        self.scroll.canvas.bind("<Configure>", self._align, add="+")

    def _align(self, _event=None):
        gap = self.scroll.bar.winfo_reqwidth() if self.scroll.bar.winfo_manager() else 0
        if gap != self._bar_gap:
            self._bar_gap = gap
            for w in self._fixed:
                w.pack_configure(padx=(24, 24 + gap))

    def _focus_entry(self):
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def state(self) -> dict:
        return {"who": self.who, "show": self.show}

    # --------------------------------------------------------------- data
    def _mine(self) -> bool:
        return self.team and self.who == "mine"

    def _load(self):
        db = self.db
        open_tasks = db.tasks(mine=self._mine())
        if self.show == "done":
            done = [t for t in db.tasks(mine=self._mine(), include_done=True) if t["done_at"]]
            done.sort(key=lambda t: (t["done_at"] or "", t["id"]), reverse=True)
            self.tasks = done
        else:
            self.tasks = open_tasks
        today = dbm.today_iso()
        overdue = sum(1 for t in open_tasks if t["due"] and t["due"] < today)
        due_today = sum(1 for t in open_tasks if t["due"] == today)
        bits = [count_words(len(open_tasks), "open task")] if open_tasks else ["No open tasks"]
        if overdue:
            bits.append(f"{overdue:,} overdue")
        if due_today:
            bits.append(f"{due_today:,} due today")
        self.subtitle_label.configure(text="  ·  ".join(bits))
        if not self.subtitle_label.winfo_manager():
            self.subtitle_label.pack(anchor="w", pady=(2, 0))

    def refresh(self) -> None:
        self.reload()

    def reload(self, force: bool = False):
        """Read the tasks again and redraw, staying at the same place."""
        self._load()
        sig = (self.who, self.show, dbm.today_iso(),
               tuple((t["id"], t["title"], t["due"], t["done_at"], t["assigned_to"], t["detail"],
                      t["record_title"]) for t in self.tasks))
        if sig == self._sig and not force:
            return
        self._sig = sig
        self._day = dbm.today_iso()
        restore = hold_scroll(self.scroll)
        self._render()
        restore()

    # ------------------------------------------------------------ drawing
    def _render(self):
        clear(self.list)
        self.wrap.forget()
        self.rows = []
        self._groups = {}
        if not self.tasks:
            self._empty()
            return
        if self.show == "done":
            groups = [("done", "Done", self.tasks)]
        else:
            groups = group_tasks(self.tasks)
        for key, label, items in groups:
            head = ttk.Frame(self.list)
            head.pack(fill="x", pady=(12, 5))
            ttk.Label(head, text=label.upper(), style="HmGroupBad.TLabel" if key == "overdue"
                      else "HmGroup.TLabel").pack(side="left")
            ttk.Label(head, text=f"{len(items):,}", style="HmGroup.TLabel").pack(side="left",
                                                                                 padx=(8, 0))
            card = widgets.card(self.list, self.app, padding=12)
            card.pack(fill="x")
            g = {"key": key, "items": items, "card": card, "shown": 0, "more": None}
            self._groups[key] = g
            self._extend(g, max(PAGE_SIZE, self._shown.get(key, 0)))

    def _extend(self, g: dict, upto: int | None = None):
        """Draw the next batch of a group's tasks."""
        if g["more"] is not None:
            g["more"].destroy()
            g["more"] = None
        items = g["items"]
        target = min(len(items), upto if upto is not None else g["shown"] + PAGE_SIZE * 2)
        show_who = self.team
        for t in items[g["shown"]:target]:
            row = TaskRow(g["card"], self, t, wrap=self.wrap, show_who=show_who, reserve=fs(76))
            row.pack(fill="x", pady=4)
            self.rows.append(row)
        g["shown"] = target
        self._shown[g["key"]] = target
        left = len(items) - target
        if left > 0:
            g["more"] = widgets.link(
                g["card"], f"Show more  ({left:,} not shown)", lambda: self._more(g),
                style="PanelLink.TLabel")
            g["more"].pack(anchor="w", pady=(8, 2))

    def _more(self, g):
        self._extend(g)

    def _empty(self):
        box = ttk.Frame(self.list)
        box.pack(fill="x", pady=(28, 0))
        if self.show == "done":
            title, text = "Nothing ticked off yet", "Tasks you tick as done are kept here."
        elif self.team and self.who == "all":
            title, text = "Nothing to do", "Nobody has an open task."
        else:
            title = "Nothing to do"
            text = ("Add a task above, or on any record – tasks added on a record stay "
                    "linked to it." if self.db.can_edit else "You have no open tasks.")
        ttk.Label(box, text=title, style="H2.TLabel").pack(anchor="w")
        ttk.Label(box, text=text, style="Help.TLabel", justify="left",
                  wraplength=fs(520)).pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------ actions
    def set_who(self, who: str):
        if who != self.who:
            self.who = who
            seg_mark(self.who_buttons, who)
            self._shown = {}
            self.scroll.to_top()
            self.reload(force=True)

    def set_show(self, show: str):
        if show != self.show:
            self.show = show
            seg_mark(self.show_buttons, show)
            self._shown = {}
            self.scroll.to_top()
            self.reload(force=True)

    def _add_problem(self, text: str):
        self.add_error.configure(text=text)
        if text:
            self.add_error.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        else:
            self.add_error.grid_remove()

    def add_task(self):
        title = self.title_var.get().strip()
        if not title:
            self._add_problem("Say what needs doing first.")
            self.entry.focus_set()
            return
        due, problem = parse_due(self.due.get())
        if problem:
            self._add_problem(problem)
            self.due.focus()
            return
        who = self.db.user["id"]
        if self.who_box is not None:
            who = self._people[max(0, self.who_box.current())][0]
        try:
            self.db.add_task(title, due=due, assigned_to=who)
        except dbm.DBError as exc:
            self._add_problem(str(exc))
            return
        self._add_problem("")
        self.title_var.set("")
        self.due.set(None)
        if self.show != "open":
            self.set_show("open")
        elif self._mine() and who not in (None, self.db.user["id"]):
            self.reload(force=True)
            self.app.toast(f"Added for {self.db.user_name(who)}. Choose Everyone to see it.")
        else:
            self.reload(force=True)
        self.entry.focus_set()
        self.app.refresh_sidebar()

    def task_toggled(self, _task):
        self._load()
        self._sig = None        # the next refresh redraws with the ticked ones gone
        self.app.refresh_sidebar()

    def edit_task(self, task):
        if not self.db.can_edit:
            return
        result = edit_task_dialog(self.app, task)
        if result:
            self.reload(force=True)
            self.app.refresh_sidebar()
            if result == "deleted":
                self.app.toast("Task deleted")
