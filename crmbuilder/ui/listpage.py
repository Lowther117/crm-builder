"""One list (e.g. Contacts): search, filter, sort, open, and act on several at once."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import validate
from . import styles, widgets
from .base import Page
from .styles import fs

MAX_ROWS = 3000
BLANK = "(blank)"
FILTER_KINDS = ("choice", "tags", "user", "yesno")


def filter_values(app, field: dict) -> list[tuple[object, str]]:
    """[(stored value, label)] a field can be filtered by."""
    kind = field["kind"]
    if kind in ("choice", "tags"):
        out = [(o, o) for o in validate.options_of(field)]
    elif kind == "user":
        out = [(u["id"], u["name"]) for u in app.db.users()]
    elif kind == "yesno":
        return [(True, "Yes"), (False, "No")]
    else:
        out = []
    return out + [(None, BLANK)]


def matches(record: dict, filters: dict, type_def: dict) -> bool:
    for key, wanted in filters.items():
        if not wanted:
            continue
        f = bpm.get_field(type_def, key)
        if f is None:
            continue
        value = record["data"].get(key)
        if f["kind"] == "yesno":
            if bool(value) not in wanted:
                return False
        elif f["kind"] == "tags":
            have = value or []
            if not ((None in wanted and not have) or any(v in have for v in wanted if v is not None)):
                return False
        else:
            if value in (None, ""):
                if None not in wanted:
                    return False
            elif value not in wanted:
                return False
    return True


class ListPage(Page):
    auto_refresh = True

    def __init__(self, parent, app, type_key: str, mode: str = "list", search: str = "",
                 filters: dict | None = None, sort=None, select=None, **_kw):
        super().__init__(parent, app)
        self.t = bpm.get_type(self.db.blueprint, type_key)
        if self.t is None or self.t.get("archived"):
            raise dbm.DBError("That list no longer exists.")
        self.type_key = type_key
        self.nav_key = "list:" + type_key
        self.search = search
        self.board_field = bpm.board_field(self.t)
        self.mode = mode if (mode == "board" and self.board_field) else "list"
        self.columns = bpm.list_fields(self.t)
        self.filters = self._usable_filters(filters)
        self.sort = self._usable_sort(sort)
        self.rows: list[dict] = []
        self.shown: list[dict] = []
        self.total = 0
        self._select = select
        self._build()
        self.load()

    def _usable_filters(self, filters) -> dict:
        """Filters as given (by Back, a report, a saved view), without any on
        a field that has since been removed from the design: nothing on the
        page would show such a filter was there, or let it be taken off."""
        ok = {f["key"] for f in bpm.active_fields(self.t) if f["kind"] in FILTER_KINDS}
        return {k: list(v) for k, v in (filters or {}).items() if v and k in ok}

    def _usable_sort(self, sort) -> tuple:
        shown = {"__ref", "__updated"} | {f["key"] for f in self.columns}
        if sort and len(sort) == 2 and sort[0] in shown:
            return (sort[0], bool(sort[1]))
        return ("__updated", True)

    # ------------------------------------------------------------- layout
    def _build(self):
        t, app, c = self.t, self.app, self.c
        right = self.header(t["plural"], " ")
        if self.db.can_edit:
            ttk.Button(right, text=f"+ New {t['name'].lower()}", style="Accent.TButton",
                       command=lambda: app.new_record(self.type_key)).pack(side="right")
        more = ttk.Menubutton(right, text="More", direction="below")
        m = styles.menu(more, c)
        if self.db.can_edit:
            m.add_command(label="Import from a spreadsheet…",
                          command=lambda: app.go("import", type_key=self.type_key))
        m.add_command(label="Export to Excel…", command=lambda: self.export("xlsx"))
        m.add_command(label="Export to CSV…", command=lambda: self.export("csv"))
        m.add_separator()
        m.add_command(label="Save this view…", command=self.save_view)
        self.views_menu = styles.menu(m, c)
        m.add_cascade(label="Saved views", menu=self.views_menu)
        if self.db.is_admin:
            m.add_separator()
            m.add_command(label="Change what this list holds…",
                          command=lambda: app.go("design", type_key=self.type_key))
        more.configure(menu=m)
        more.pack(side="right", padx=(0, 8))
        self._fill_views_menu()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=24, pady=(0, 8))
        self.search_box = widgets.SearchEntry(bar, app, f"Search {t['plural'].lower()}…",
                                              on_change=self._search_changed, width=30)
        self.search_box.pack(side="left")
        if self.search:
            self.search_box.set_text(self.search)
        self.filter_fields = [f for f in bpm.active_fields(t) if f["kind"] in FILTER_KINDS]
        if self.filter_fields:
            fb = ttk.Menubutton(bar, text="Filter", direction="below")
            self.filter_menu = styles.menu(fb, c)
            fb.configure(menu=self.filter_menu)
            fb.pack(side="left", padx=(8, 0))
            self._filter_vars: dict = {}
            self._fill_filter_menu()
        if self.board_field:
            sw = ttk.Frame(bar)
            sw.pack(side="right")
            self.list_btn = ttk.Button(sw, text="List", style="Seg.TButton",
                                       command=lambda: self.set_mode("list"))
            self.list_btn.pack(side="left")
            self.board_btn = ttk.Button(sw, text="Board", style="Seg.TButton",
                                        command=lambda: self.set_mode("board"))
            self.board_btn.pack(side="left", padx=(4, 0))
            widgets.Tooltip(self.board_btn, f"Cards in columns by {self.board_field['name'].lower()}. "
                                            "Drag a card to move it on.", app)
        self.chips = ttk.Frame(self)
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=24)
        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=(6, 12))
        self.foot_left = ttk.Label(foot, text="", style="Help.TLabel")
        self.foot_left.pack(side="left")
        self.foot_right = ttk.Label(foot, text="", style="Help.TLabel")
        self.foot_right.pack(side="right")
        self.tree = None
        self.board = None

    def _fill_filter_menu(self):
        m, c = self.filter_menu, self.c
        m.delete(0, "end")
        self._filter_vars = {}
        for f in self.filter_fields:
            sub = styles.menu(m, c)
            for value, label in filter_values(self.app, f):
                var = tk.BooleanVar(self, value=value in self.filters.get(f["key"], []))
                self._filter_vars[(f["key"], value)] = var
                sub.add_checkbutton(label=label, variable=var, selectcolor=c["accent"],
                                    command=lambda k=f["key"], v=value, var=var:
                                    self._toggle_filter(k, v, var.get()))
            m.add_cascade(label=f["name"], menu=sub)
        m.add_separator()
        m.add_command(label="Clear all filters", command=self.clear_filters)

    def _fill_views_menu(self):
        m = self.views_menu
        m.delete(0, "end")
        m.add_command(label="Everything (no filters)", command=self.clear_view)
        views = self.db.views(self.type_key)
        if views:
            m.add_separator()
        for v in views:
            m.add_command(label=v["name"], command=lambda v=v: self.apply_view(v["spec"]))
        if views:
            m.add_separator()
            sub = styles.menu(m, self.c)
            for v in views:
                sub.add_command(label=v["name"], command=lambda v=v: self.delete_view(v))
            m.add_cascade(label="Delete a saved view", menu=sub)

    def _draw_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        any_chip = False
        for key, wanted in self.filters.items():
            f = bpm.get_field(self.t, key)
            if not f or not wanted:
                continue
            labels = dict(filter_values(self.app, f))
            text = f"{f['name']}: " + ", ".join(str(labels.get(v, v)) for v in wanted)
            chip = tk.Frame(self.chips, bg=self.c["sel"])
            chip.pack(side="left", padx=(0, 6))
            tk.Label(chip, text=text, bg=self.c["sel"], fg=self.c["text"], padx=8, pady=3,
                     font=(self.c["ui"], fs(9))).pack(side="left")
            x = tk.Label(chip, text="×", bg=self.c["sel"], fg=self.c["dim"], padx=6, cursor="hand2",
                         font=(self.c["ui"], fs(9)))
            x.pack(side="left")
            x.bind("<Button-1>", lambda _e, k=key: self._remove_filter(k))
            any_chip = True
        if any_chip:
            widgets.link(self.chips, "Clear filters", self.clear_filters).pack(side="left", padx=(6, 0))
            if not self.chips.winfo_manager():
                self.chips.pack(fill="x", padx=24, pady=(0, 8), before=self.body)
        elif self.chips.winfo_manager():
            self.chips.pack_forget()

    # --------------------------------------------------------------- data
    def load(self):
        """Read the records and redraw."""
        self.total = self.db.count(self.type_key)
        rows = self.db.records(self.type_key, self.search)
        if self.filters:
            rows = [r for r in rows if matches(r, self.filters, self.t)]
        self.rows = self._sorted(rows)
        self._draw_chips()
        self._draw()

    def refresh(self):
        """Reload (someone else changed something, or several records were
        changed at once) without losing the person's place in the list."""
        top = self.tree.yview()[0] if self.tree is not None else 0.0
        self.load()
        if top and self.tree is not None:
            self.tree.update_idletasks()
            self.tree.yview_moveto(top)

    def _sorted(self, rows):
        key, desc = self.sort
        if key == "__ref":
            return sorted(rows, key=lambda r: (r["type"], r["seq"]), reverse=desc)
        if key == "__updated":
            return sorted(rows, key=lambda r: (r["updated_at"] or "", r["id"]), reverse=desc)
        f = bpm.get_field(self.t, key)
        if f is None:
            return rows
        if f["kind"] in ("link", "user"):
            def k(r):
                text = validate.display(f, r["data"].get(key), self.db.lookup)
                return (0 if text else 1, text.lower())
        else:
            def k(r):
                return validate.sort_key(f, r["data"].get(key))
        blanks = [r for r in rows if k(r)[0] == 1]
        filled = [r for r in rows if k(r)[0] == 0]
        filled.sort(key=k, reverse=desc)
        return filled + blanks

    def _draw(self):
        # the list is rebuilt from scratch: remember what was selected and
        # whether the keyboard was in it, and put both back
        keep = None
        if self.tree is not None:
            try:
                keep = (self.tree.selection(), self.tree.focus(),
                        widgets.focus_holder(self) == str(self.tree))
            except tk.TclError:
                keep = None
        for w in self.body.winfo_children():
            w.destroy()
        self.tree = self.board = None
        t = self.t
        if self.board_field:
            self.list_btn.configure(style="Seg.TButton" if self.mode == "board" else "SegOn.TButton")
            self.board_btn.configure(style="SegOn.TButton" if self.mode == "board" else "Seg.TButton")
        n = len(self.rows)
        word = t["plural"].lower() if n != 1 else t["name"].lower()
        filtered = bool(self.search or self.filters)
        self.subtitle_label.configure(
            text=(f"{n:,} of {self.total:,} {t['plural'].lower()}" if filtered else f"{n:,} {word}"))
        if not self.subtitle_label.winfo_manager():
            self.subtitle_label.pack(anchor="w", pady=(2, 0))
        if self.total == 0 or not self.rows:
            if self.total == 0:
                self._draw_empty()
            else:
                self._draw_no_match()
            self.foot_left.configure(text="")
            self.foot_right.configure(text="")
            return
        if self.mode == "board":
            self._draw_board()
        else:
            self._draw_tree(keep)
        money = [f for f in bpm.active_fields(t) if f["kind"] == "money"][:2]
        bits = []
        for f in money:
            total = sum(float(r["data"].get(f["key"]) or 0) for r in self.rows)
            bits.append(f"{f['name']} total {validate.display(f, total)}")
        self.foot_right.configure(text="     ".join(bits))

    def _draw_empty(self):
        t = self.t
        box = widgets.card(self.body, self.app, padding=34)
        box.pack(pady=40)
        ttk.Label(box, text=f"No {t['plural'].lower()} yet", style="PanelH2.TLabel").pack()
        ttk.Label(box, text="Add them one at a time, or bring in a spreadsheet you already have.",
                  style="PanelDim.TLabel").pack(pady=(6, 16))
        if self.db.can_edit:
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack()
            ttk.Button(row, text=f"Add your first {t['name'].lower()}", style="Accent.TButton",
                       command=lambda: self.app.new_record(self.type_key)).pack(side="left")
            ttk.Button(row, text="Import from a spreadsheet…",
                       command=lambda: self.app.go("import", type_key=self.type_key)).pack(
                side="left", padx=(8, 0))

    def _draw_no_match(self):
        """There are records, but the search and filters leave none of them."""
        t = self.t
        box = widgets.card(self.body, self.app, padding=34)
        box.pack(pady=40)
        ttk.Label(box, text=f"No {t['plural'].lower()} match", style="PanelH2.TLabel").pack()
        what = " and ".join(x for x in ("the search" if self.search else "",
                                        "the filters" if self.filters else "") if x)
        ttk.Label(box, text=f"Nothing fits {what} you have set.",
                  style="PanelDim.TLabel").pack(pady=(6, 16))
        ttk.Button(box, text=f"Show all {self.total:,} {t['plural'].lower()}",
                   command=self.show_all).pack()

    def show_all(self):
        """Drop the search and the filters (the sort order stays)."""
        self.search = ""
        self.search_box.set_text("")
        self.clear_filters()

    def _draw_board(self):
        try:
            from .board import BoardView
        except Exception as exc:
            ttk.Label(self.body, text=f"The board could not be loaded ({exc}).",
                      style="Bad.TLabel").pack(pady=30)
            return
        self.board = BoardView(self.body, self.app, self.t, self.rows,
                               on_open=self.app.open_record, on_changed=self.load)
        self.board.pack(fill="both", expand=True)
        self.foot_left.configure(text="Drag a card to another column to move it on. "
                                      "Double-click to open." if self.db.can_edit
                                 else "Double-click a card to open it.")

    def _draw_tree(self, keep=None):
        cols = ["__ref"] + [f["key"] for f in self.columns] + ["__updated"]
        frame, tree = widgets.make_tree(self.body, cols, selectmode="extended", xscroll=True)
        frame.pack(fill="both", expand=True)
        widths = {"date": 96, "money": 100, "number": 80, "percent": 70, "yesno": 60, "phone": 120,
                  "postcode": 90, "email": 200, "choice": 120, "user": 120, "url": 180, "ni": 130}
        self._headings = {"__ref": "Ref", "__updated": "Updated"}
        tree.column("__ref", width=fs(86), stretch=False)
        tree.column("__updated", width=fs(96), stretch=False)
        on_right = set()
        try:
            measure = styles.pinned_font(self.app.root, self.c["ui"], 9, bold=True).measure
        except tk.TclError:
            measure = None
        for f in self.columns:
            self._headings[f["key"]] = f["name"]
            right = f["kind"] in ("money", "number", "percent")
            if right:
                on_right.add(f["key"])
            width = fs(widths.get(f["kind"], 160))
            if measure is not None:       # never narrower than its own heading (and sort arrow)
                width = max(width, min(fs(240), measure(f["name"] + "  ↓") + 18))
            tree.column(f["key"], width=width, minwidth=50,
                        stretch=f["kind"] in ("text", "link", "email", "url", "tags"),
                        anchor="e" if right else "w")
        for col in cols:
            arrow = ""
            if self.sort[0] == col:
                arrow = "  ↓" if self.sort[1] else "  ↑"
            # a heading sits over its figures: numbers and their heading both on the right
            tree.heading(col, text=self._headings[col] + arrow,
                         anchor="e" if col in on_right else "w",
                         command=lambda c=col: self.sort_by(c))
        tree.tag_configure("odd", background=self.c["bg"])
        lookup = self.db.lookup
        self.shown = self.rows[:MAX_ROWS]
        for i, r in enumerate(self.shown):
            values = [r["ref"]]
            for f in self.columns:
                values.append(validate.display(f, r["data"].get(f["key"]), lookup).replace("\n", " "))
            values.append(validate.iso_to_uk(r["updated_at"]))
            tree.insert("", "end", iid=str(r["id"]), values=values, tags=("odd",) if i % 2 else ())
        tree.bind("<Double-1>", self._open_click)
        tree.bind("<Return>", lambda _e: self.open_selected())
        tree.bind("<Delete>", lambda _e: self.delete_selected())
        tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        tree.bind("<Button-3>", self._context)
        if widgets.IS_MAC:
            tree.bind("<Button-2>", self._context)
            tree.bind("<Control-Button-1>", self._context)
            tree.bind("<BackSpace>", lambda _e: self.delete_selected())   # the Mac's "delete" key
        tree.bind(f"<{widgets.MOD}-a>", lambda _e: tree.selection_set(tree.get_children()) or "break")
        self.tree = tree
        if self._select is not None and tree.exists(str(self._select)):
            tree.selection_set(str(self._select))
            tree.see(str(self._select))
            tree.focus(str(self._select))
        elif keep is not None:
            selected = [i for i in keep[0] if tree.exists(i)]
            if selected:
                tree.selection_set(selected)
            if keep[1] and tree.exists(keep[1]):
                tree.focus(keep[1])
            if keep[2]:
                tree.focus_set()
        self._select = None
        self._selection_changed()

    def _selection_changed(self):
        n = len(self.rows)
        text = ""
        if n > MAX_ROWS:
            text = f"Showing the first {MAX_ROWS:,}. Search or filter to narrow it down.   "
        sel = len(self.tree.selection()) if self.tree is not None else 0
        if sel > 1:
            text += f"{sel} selected – right-click for things you can do to all of them."
        elif not text and self.tree is not None:
            text = "Double-click to open. Click a heading to sort. Right-click for more."
        self.foot_left.configure(text=text)

    # ------------------------------------------------------------ actions
    def selected_ids(self) -> list[int]:
        if self.tree is None:
            return []
        return [int(i) for i in self.tree.selection()]

    def _open_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self.app.open_record(int(row))

    def open_selected(self):
        ids = self.selected_ids()
        if ids:
            self.app.open_record(ids[0])

    def _context(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        ids = self.selected_ids()
        m = styles.menu(self.tree, self.c)
        m.add_command(label="Open", command=self.open_selected)
        if self.db.can_edit:
            many = f" for these {len(ids)}" if len(ids) > 1 else ""
            m.add_command(label=f"Change a field{many}…", command=self.bulk_edit)
            m.add_command(label="Add a task…", command=self.add_task)
        m.add_separator()
        m.add_command(label="Export selected to Excel…", command=lambda: self.export("xlsx", True))
        m.add_command(label="Export selected to CSV…", command=lambda: self.export("csv", True))
        if self.db.can_edit:
            m.add_separator()
            m.add_command(label="Delete" + (f" these {len(ids)}" if len(ids) > 1 else ""),
                          command=self.delete_selected)
        m.tk_popup(event.x_root, event.y_root)

    def add_task(self):
        ids = self.selected_ids()
        if not ids:
            return
        title = widgets.ask_string(self.app, "Add a task", "What needs doing?", ok="Add task")
        if not title:
            return
        for rid in ids:
            self.db.add_task(title, record_id=rid, assigned_to=self.db.user["id"])
        self.app.toast(f"Task added to {len(ids)} record{'s' if len(ids) != 1 else ''}", "good")
        self.app.refresh_sidebar()

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids or not self.db.can_edit:
            return
        n = len(ids)
        what = (f"these {n} {self.t['plural'].lower()}" if n > 1
                else f"“{self.db.lookup('link', ids[0])}”")
        if not widgets.confirm(self.app, "Delete", f"Move {what} to the recycle bin?\n\n"
                               "You can get them back from Tools > Recycle bin.",
                               yes="Delete", danger=True):
            return
        self.db.delete_records(ids)
        self.app.toast(f"Moved {n} to the recycle bin", "good")
        self.app.refresh_sidebar()
        self.load()

    def bulk_edit(self):
        ids = self.selected_ids()
        if not ids or not self.db.can_edit:
            return
        fields = [f for f in bpm.active_fields(self.t) if not f.get("unique")]
        if not fields:
            return
        app = self.app
        d = widgets.Dialog(app, "Change a field", width=460)
        ttk.Label(d.body, text=f"Set the same value on {len(ids)} "
                               f"{self.t['plural'].lower() if len(ids) != 1 else self.t['name'].lower()}.",
                  wraplength=430).pack(anchor="w")
        ttk.Label(d.body, text="Field", style="Field.TLabel").pack(anchor="w", pady=(12, 3))
        names = [f["name"] for f in fields]
        which = tk.StringVar(d, value=names[0])
        combo = ttk.Combobox(d.body, textvariable=which, values=names, state="readonly")
        combo.pack(fill="x")
        ttk.Label(d.body, text="New value (leave empty to clear it)", style="Field.TLabel").pack(
            anchor="w", pady=(12, 3))
        holder = ttk.Frame(d.body)
        holder.pack(fill="x")
        err = ttk.Label(d.body, text="", style="Error.TLabel", wraplength=430, justify="left")
        err.pack(anchor="w", pady=(6, 0))
        state = {"editor": None, "field": None}

        def build(_e=None):
            for w in holder.winfo_children():
                w.destroy()
            f = fields[names.index(which.get())]
            ed = widgets.make_editor(holder, app, f, panel=False)
            ed.widget.pack(fill="x")
            state.update(editor=ed, field=f)
            err.configure(text="")

        def apply():
            f, ed = state["field"], state["editor"]
            value, problem = validate.normalise(dict(f, required=False), ed.get(), self.db.resolve)
            if problem:
                err.configure(text=problem)
                return
            if value is None and f.get("required"):
                err.configure(text=f"{f['name']} is a required field, so it cannot be cleared.")
                return
            done = 0
            try:
                with self.db.tx():
                    for rid in ids:
                        self.db.update_record(rid, {f["key"]: value})
                        done += 1
            except dbm.DBError as exc:
                err.configure(text=str(exc))
                return
            d.ok(done)

        combo.bind("<<ComboboxSelected>>", build)
        build()
        d.add_button("Change them", apply, accent=True)
        d.add_button("Cancel", d.cancel)
        d.default_on_enter()
        n = d.show(focus=combo)
        if n:
            self.app.toast(f"Changed {n} record{'s' if n != 1 else ''}", "good")
            self.refresh()

    def export(self, fmt: str, selected_only: bool = False):
        rows = self.rows
        if selected_only:
            ids = set(self.selected_ids())
            rows = [r for r in rows if r["id"] in ids]
        if not rows:
            self.app.toast("There is nothing to export.")
            return
        self.app.tool("export_records", self.t, rows, fmt)

    # -------------------------------------------------- search/filter/sort
    def _search_changed(self, text: str):
        self.search = text
        self.load()

    def _toggle_filter(self, key, value, on):
        cur = self.filters.setdefault(key, [])
        if on and value not in cur:
            cur.append(value)
        elif not on and value in cur:
            cur.remove(value)
        if not cur:
            self.filters.pop(key, None)
        self.load()

    def _remove_filter(self, key):
        self.filters.pop(key, None)
        self._fill_filter_menu()
        self.load()

    def clear_filters(self):
        self.filters = {}
        if self.filter_fields:
            self._fill_filter_menu()
        self.load()

    def sort_by(self, col: str):
        if self.sort[0] == col:
            self.sort = (col, not self.sort[1])
        else:
            self.sort = (col, col == "__updated")
        self.rows = self._sorted(self.rows)
        self._draw()

    def set_mode(self, mode: str):
        if mode != self.mode:
            self.mode = mode
            self._draw()

    # -------------------------------------------------------------- views
    def spec(self) -> dict:
        return {"search": self.search, "filters": self.filters, "sort": list(self.sort),
                "mode": self.mode}

    def save_view(self):
        name = widgets.ask_string(
            self.app, "Save this view",
            "Give this combination of search, filters and sorting a name, so you can come "
            "back to it from More > Saved views.", ok="Save view")
        if not name:
            return
        self.db.save_view(self.type_key, name, self.spec())
        self._fill_views_menu()
        self.app.toast(f"Saved the view “{name}”", "good")

    def apply_view(self, spec: dict):
        self.search = str(spec.get("search") or "")
        self.search_box.set_text(self.search)
        raw = spec.get("filters")
        self.filters = self._usable_filters(raw if isinstance(raw, dict) else {})
        self.sort = self._usable_sort(spec.get("sort"))
        mode = spec.get("mode")
        self.mode = "board" if (mode == "board" and self.board_field) else "list"
        if self.filter_fields:
            self._fill_filter_menu()
        self.load()

    def clear_view(self):
        self.apply_view({"sort": ["__updated", True], "mode": self.mode})

    def delete_view(self, view: dict):
        self.db.delete_view(view["id"])
        self._fill_views_menu()
        self.app.toast(f"Deleted the view “{view['name']}”")

    def state(self) -> dict:
        ids = self.selected_ids()
        return {"type_key": self.type_key, "mode": self.mode, "search": self.search,
                "filters": {k: list(v) for k, v in self.filters.items()},
                "sort": list(self.sort), "select": ids[0] if ids else None}
