"""Bring a spreadsheet (CSV or Excel) into one list, in three steps:
choose the file, match its columns to the list's fields, check and import."""
from __future__ import annotations

import csv
import os
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import importing, sheets, validate
from . import styles, widgets
from .base import Page
from .styles import fs

SKIP = "Don't import"
PICK_LIST = "Choose a list…"
ALWAYS_ADD = "Always add a new record"
MAX_PROBLEMS = 300          # rows drawn in the problems table
STEPS = [("choose", "Choose"), ("match", "Match columns"), ("check", "Check and import")]
# kinds a row can sensibly be matched to an existing record on
MATCH_KINDS = ("text", "email", "phone", "postcode", "ni", "url", "number")


def plural_word(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def name_list(names: list[str], limit: int = 6) -> str:
    """'A, B, C and 12 more'."""
    shown = [n if len(n) <= 40 else n[:39].rstrip() + "…" for n in names[:limit]]
    text = ", ".join(shown)
    if len(names) > limit:
        text += f" and {len(names) - limit:,} more"
    return text


def samples(rows: list[list[str]], col: int, count: int = 3) -> str:
    """A few different values from one column, for showing beside its heading."""
    out: list[str] = []
    for r in rows[:300]:
        v = " ".join(str(r[col]).split()) if col < len(r) else ""
        if v and v not in out:
            out.append(v if len(v) <= 30 else v[:29].rstrip() + "…")
            if len(out) >= count:
                break
    return "  ·  ".join(out)


def _settle(row: dict, fields: dict, options: dict) -> None:
    """Decide what happens to a row from the problems it has (the engine's own rule)."""
    if row["action"] == "empty":
        return
    action = "update" if row["existing"] else "add"
    if row["errors"]:
        if options.get("on_invalid") != "blank":
            action = "skip"
        elif not row["existing"] and any(fields.get(k, {}).get("required") for k in row["errors"]):
            action = "skip"
    row["action"] = action


def _recount(prepared: dict) -> None:
    """Work the totals out again after rows changed."""
    counts = {"add": 0, "update": 0, "skip": 0, "empty": 0, "with_errors": 0}
    new_options: dict[str, list[str]] = {}
    new_links: dict[str, list[str]] = {}
    for r in prepared["rows"]:
        counts[r["action"]] += 1
        if r["errors"]:
            counts["with_errors"] += 1
        if r["action"] not in ("add", "update"):
            continue
        for key, values in r.get("new_options", {}).items():
            if key in r["errors"]:
                continue
            have = new_options.setdefault(key, [])
            for v in values:
                if v.lower() not in [x.lower() for x in have]:
                    have.append(v)
        for key, name in r["pending_links"].items():
            names = new_links.setdefault(key, [])
            if name.lower() not in [x.lower() for x in names]:
                names.append(name)
    prepared["summary"] = counts
    prepared["new_options"] = {k: v for k, v in new_options.items() if v}
    prepared["new_links"] = new_links


def refine(db, type_def: dict, prepared: dict, options: dict, rows: list[list[str]],
           mapping: dict) -> int:
    """Two checks on top of the engine's own. Returns how many rows changed.

    1. An empty cell in the column of a needed field is not a mistake when the
       field has a starting value (a new record takes it, as it does on the
       form) or when the row updates a record (which keeps what it has).
    2. A field marked 'must be different on every record' may not repeat a value
       that a record in the list, or an earlier row of the sheet, already has."""
    fields = {f["key"]: f for f in bpm.active_fields(type_def)}
    col_of = {k: i for i, k in mapping.items() if k in fields}
    touched = 0

    for key, col in col_of.items():
        f = fields[key]
        if not f.get("required"):
            continue
        start = bpm.default_value(f, db.user["id"])
        for row, source in zip(prepared["rows"], rows):
            if key not in row["errors"] or row["action"] == "empty":
                continue
            if col < len(source) and str(source[col]).strip():
                continue                      # something was typed, so the problem is real
            if row["existing"]:
                del row["errors"][key]
            elif start is not None:
                del row["errors"][key]
                row["data"][key] = start
            else:
                continue
            _settle(row, fields, options)
            touched += 1

    unique = [f for f in fields.values() if f.get("unique")
              and any(f["key"] in r["data"] for r in prepared["rows"])]
    existing = db.records(type_def["key"]) if unique else []
    for f in unique:
        key = f["key"]
        seen: dict[str, tuple] = {}
        for r in existing:
            v = r["data"].get(key)
            if v not in (None, "", []):
                seen.setdefault(str(v).strip().lower(), ("record", r["id"], r["title"]))
        for row in prepared["rows"]:
            if row["action"] not in ("add", "update") or key not in row["data"]:
                continue
            v = str(row["data"][key]).strip().lower()
            hit = seen.get(v)
            if hit is None:
                seen[v] = ("row", row["n"], "")
                continue
            if hit[0] == "record" and hit[1] == row["existing"]:
                continue        # the row updates the very record that holds this value
            if hit[0] == "record":
                why = (f"“{hit[2]}” already has this {f['name']}, and it must be different "
                       "on every record.")
            else:
                why = (f"Row {hit[1]} has the same {f['name']}, and it must be different "
                       "on every record.")
            row["errors"][key] = why
            row["data"].pop(key, None)
            _settle(row, fields, options)
            touched += 1
    if touched:
        _recount(prepared)
    return touched


class ImportPage(Page):
    def __init__(self, parent, app, type_key: str | None = None, path: str | None = None,
                 sheet: str | None = None, _restore: dict | None = None, **_kw):
        super().__init__(parent, app)
        self.types = bpm.active_types(self.db.blueprint)
        keys = [t["key"] for t in self.types]
        if type_key not in keys:
            type_key = keys[0] if len(keys) == 1 else None
        self.type_key: str | None = type_key
        self.nav_key = "list:" + type_key if type_key else ""
        self.path = ""
        self.sheet: str | None = None
        self.sheet_names: list[str] = []
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.numbers: list[int] = []
        self.mapping: dict[int, str | None] = {}
        self.options = importing.default_options()
        self.prepared: dict | None = None
        self.report: dict | None = None
        self.imported = False
        self.load_error = ""
        self.run_error = ""
        self.problems_saved = ""
        self.stage = "choose"
        self._busy = False
        self._leaving = False
        self.map_vars: dict[int, tk.StringVar] = {}
        self.map_combos: dict[int, ttk.Combobox] = {}

        self.header("Import from a spreadsheet",
                    "Bring a CSV or Excel file you already have into one of your lists.", back=True)
        if not self.db.can_edit:
            self._message("Your account is read only",
                          "Importing adds records, so it needs an account that can make changes. "
                          "Ask an administrator if you need this.")
            return
        if not self.types:
            self._message("There are no lists yet",
                          "A spreadsheet is imported into a list. Add a list to the design first, "
                          "then come back here.",
                          ("Change the design", lambda: app.go("design")) if self.db.is_admin else None)
            return
        self.steps = ttk.Frame(self)
        self.steps.pack(fill="x", padx=24, pady=(0, 12))
        self.foot = ttk.Frame(self)
        self.foot.pack(side="bottom", fill="x", padx=24, pady=(10, 14))
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=24)

        if _restore:
            for k, v in _restore.items():
                setattr(self, k, v)
            if self.type_key not in keys:
                self.type_key, self.stage = None, "choose"
        elif path:
            self._load(path, sheet)
        self._show(self.stage)

    # ------------------------------------------------------------ plumbing
    @property
    def t(self) -> dict | None:
        return bpm.get_type(self.db.blueprint, self.type_key) if self.type_key else None

    def _message(self, title: str, text: str, action=None):
        box = widgets.card(self, self.app, padding=30)
        box.pack(padx=24, pady=30)
        ttk.Label(box, text=title, style="PanelH2.TLabel").pack()
        ttk.Label(box, text=text, style="PanelDim.TLabel", wraplength=fs(440),
                  justify="center").pack(pady=(6, 0))
        if action:
            ttk.Button(box, text=action[0], style="Accent.TButton", command=action[1]).pack(pady=(16, 0))

    def _show(self, stage: str):
        self.stage = stage
        for frame in (self.body, self.foot):
            for w in frame.winfo_children():
                w.destroy()
        self.map_vars, self.map_combos = {}, {}
        self._draw_steps()
        {"choose": self._build_choose, "match": self._build_match,
         "check": self._build_check, "done": self._build_done}[stage]()

    def _draw_steps(self):
        c = self.c
        for w in self.steps.winfo_children():
            w.destroy()
        order = [k for k, _l in STEPS]
        cur = order.index(self.stage) if self.stage in order else len(order)
        for i, (_key, label) in enumerate(STEPS):
            if i:
                ttk.Frame(self.steps, style="Border.TFrame", height=1, width=fs(26)).pack(
                    side="left", padx=10)
            if i == cur:
                bg, fg, style = c["accent"], c["accent_text"], "H3.TLabel"
            elif i < cur:
                bg, fg, style = c["sel"], c["text"], "TLabel"
            else:
                bg, fg, style = c["panel"], c["dim"], "Help.TLabel"
            tk.Label(self.steps, text=str(i + 1), bg=bg, fg=fg, width=2, pady=1,
                     font=(c["ui"], fs(9), "bold"), highlightthickness=1,
                     highlightbackground=bg if i <= cur else c["border"]).pack(side="left")
            ttk.Label(self.steps, text=label, style=style).pack(side="left", padx=(7, 0))

    def _set_list(self, type_key: str | None):
        if type_key == self.type_key:
            return
        self.type_key = type_key
        self.nav_key = "list:" + type_key if type_key else ""
        self.prepared = None
        self.options = importing.default_options()
        if self.headers and self.t is not None:
            self.mapping = importing.auto_map(self.t, self.headers)
        self.app.refresh_sidebar()

    def _clear_file(self):
        self.path, self.sheet, self.sheet_names = "", None, []
        self.headers, self.rows, self.numbers, self.mapping = [], [], [], {}
        self.prepared = self.report = None
        self.imported = False
        self.problems_saved = ""
        self.options = importing.default_options()

    def _load(self, path: str, sheet: str | None = None) -> bool:
        """Read a spreadsheet. On failure nothing stays loaded and the reason is
        left in self.load_error."""
        self._clear_file()
        self.load_error = ""
        try:
            names = sheets.sheet_names(path)
            if sheet not in names:
                sheet = names[0] if names else None
            headers, rows, numbers = sheets.read_table(path, sheet, with_numbers=True)
        except sheets.SheetError as exc:
            self.load_error = str(exc)
            return False
        except OSError as exc:
            self.load_error = f"That file could not be read.\n\n({exc})"
            return False
        if not rows:
            self.load_error = ("That sheet has column headings but no rows under them, so there "
                               "is nothing to import.")
            if len(names) > 1:      # keep the file so another worksheet can be picked
                self.load_error += " Is the information on another worksheet?"
                self.path, self.sheet, self.sheet_names = path, sheet, names
            return False
        self.path, self.sheet, self.sheet_names = path, sheet, names
        self.headers, self.rows, self.numbers = headers, rows, numbers
        self._leaving = False
        self.mapping = importing.auto_map(self.t, headers) if self.t is not None else {}
        return True

    # ---------------------------------------------------------- 1: choose
    def _build_choose(self):
        app = self.app
        card = widgets.card(self.body, app, padding=22)
        card.pack(fill="x", anchor="n")
        ttk.Label(card, text="Which list should the rows go into?", style="PanelH3.TLabel").pack(anchor="w")
        names = [t["plural"] for t in self.types]
        self.list_var = tk.StringVar(self, value=self.t["plural"] if self.t else PICK_LIST)
        self.list_combo = ttk.Combobox(card, textvariable=self.list_var, values=names,
                                       state="readonly", width=34)
        self.list_combo.pack(anchor="w", pady=(6, 0))
        self.list_combo.bind("<<ComboboxSelected>>", self._list_picked)

        ttk.Label(card, text="Which spreadsheet?", style="PanelH3.TLabel").pack(anchor="w", pady=(20, 0))
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.choose_btn = ttk.Button(row, text="Choose a file…", command=self.choose_file)
        self.choose_btn.pack(side="left")
        self.file_label = ttk.Label(row, text="", style="Panel.TLabel")
        self.file_label.pack(side="left", padx=(12, 0))
        self.sheet_row = ttk.Frame(card, style="Panel.TFrame")
        ttk.Label(self.sheet_row, text="Worksheet", style="PanelField.TLabel").pack(side="left")
        self.sheet_var = tk.StringVar(self)
        self.sheet_combo = ttk.Combobox(self.sheet_row, textvariable=self.sheet_var, state="readonly",
                                        width=30)
        self.sheet_combo.pack(side="left", padx=(10, 0))
        self.sheet_combo.bind("<<ComboboxSelected>>", self._sheet_picked)
        self.summary = ttk.Label(card, text="", style="PanelDim.TLabel", justify="left")
        self.error = ttk.Label(card, text="", style="PanelError.TLabel", justify="left")
        if sheets.have_excel():
            tip = ("The first row of the spreadsheet must be the column headings (Name, Email …), "
                   "with one row for each record under it. Excel (.xlsx) and CSV files both work.")
        else:
            tip = ("The first row of the spreadsheet must be the column headings (Name, Email …), "
                   "with one row for each record under it. This copy of the app reads CSV files "
                   "only: in Excel use File > Save As and choose CSV.")
        self.tip = ttk.Label(card, text=tip, style="PanelHelp.TLabel", justify="left")
        self.tip.pack(anchor="w", pady=(20, 0), side="bottom")
        self._wrap(card, [self.summary, self.error, self.tip], margin=48)

        self.next_btn = ttk.Button(self.foot, text="Next: match the columns", command=self.go_match)
        self.next_btn.pack(side="right")
        self.next_hint = ttk.Label(self.foot, text="", style="Help.TLabel")
        self.next_hint.pack(side="right", padx=(0, 12))
        self._refresh_choose()

    def _wrap(self, frame, labels, margin: int):
        """Keep labels wrapped to the width of the frame they sit in."""
        def fit(event):
            if event.widget is frame:
                for lab in labels:
                    if lab.winfo_exists():
                        lab.configure(wraplength=max(200, event.width - margin))
        frame.bind("<Configure>", fit, add="+")

    def _refresh_choose(self):
        """Show what is loaded (or what went wrong) and say what to do next."""
        loaded = bool(self.rows)
        self.file_label.configure(text=os.path.basename(self.path) if self.path else "No file chosen yet")
        self.file_label.configure(style="Panel.TLabel" if self.path else "PanelDim.TLabel")
        if len(self.sheet_names) > 1:
            self.sheet_combo.configure(values=self.sheet_names)
            self.sheet_var.set(self.sheet or self.sheet_names[0])
            self.sheet_row.pack(anchor="w", pady=(10, 0))
        else:
            self.sheet_row.pack_forget()
        self.summary.pack_forget()
        self.error.pack_forget()
        if self.load_error:
            self.error.configure(text=self.load_error)
            self.error.pack(anchor="w", pady=(10, 0))
        elif loaded:
            n, k = len(self.rows), len(self.headers)
            self.summary.configure(
                text=f"{n:,} {plural_word(n, 'row', 'rows')} and {k} "
                     f"{plural_word(k, 'column', 'columns')} found: {name_list(self.headers, 8)}.")
            self.summary.pack(anchor="w", pady=(10, 0))
        ready = loaded and self.t is not None
        self.next_btn.configure(state="normal" if ready else "disabled",
                                style="Accent.TButton" if ready else "TButton")
        self.choose_btn.configure(style="TButton" if loaded else "Accent.TButton")
        hint = ""
        if loaded and self.t is None:
            hint = "Choose which list the rows go into."
        elif not loaded and not self.load_error:
            hint = "Choose a file to carry on."
        self.next_hint.configure(text=hint)

    def _list_picked(self, _e=None):
        name = self.list_var.get()
        t = next((t for t in self.types if t["plural"] == name), None)
        self.list_combo.selection_clear()
        self._set_list(t["key"] if t else None)
        self._refresh_choose()

    def _sheet_picked(self, _e=None):
        self.sheet_combo.selection_clear()
        self._read(self.path, self.sheet_var.get())

    def choose_file(self):
        path = widgets.ask_open_file(self.app, "Choose the spreadsheet to import", sheets.file_types())
        if path:
            self._read(path, None)

    def _read(self, path: str, sheet: str | None):
        self.file_label.configure(text=f"Reading {os.path.basename(path)}…", style="PanelDim.TLabel")
        self.update_idletasks()
        self._load(path, sheet)
        self._refresh_choose()

    def go_match(self):
        if self.rows and self.t is not None and not self._busy:
            self._show("match")

    # ----------------------------------------------------------- 2: match
    def _build_match(self):
        app, t = self.app, self.t
        fields = bpm.active_fields(t)
        self._field_names = {f["name"]: f["key"] for f in fields}
        self._field_by_key = {f["key"]: f for f in fields}
        self.mapping = {i: (k if k in self._field_by_key else None)
                        for i, k in ((i, self.mapping.get(i)) for i in range(len(self.headers)))}

        self.check_btn = ttk.Button(self.foot, text="Check the rows", style="Accent.TButton",
                                    command=self.go_check)
        self.check_btn.pack(side="right")
        ttk.Button(self.foot, text="Back", command=lambda: self._show("choose")).pack(side="left")
        self.match_status = ttk.Label(self.foot, text="", style="Help.TLabel")
        self.match_status.pack(side="right", padx=(0, 12))

        sf = widgets.ScrollFrame(self.body, app)
        sf.pack(fill="both", expand=True)
        self.match_scroll = sf
        intro = ttk.Label(
            sf.body, justify="left",
            text=f"Say where each column of {os.path.basename(self.path)} goes in {t['plural']}. "
                 "The ones we recognised are filled in already – change any that are wrong.")
        intro.pack(anchor="w", pady=(0, 10))
        self.warn_box = ttk.Frame(sf.body)
        self.warn_box.pack(fill="x")

        card = widgets.card(sf.body, app, padding=16)
        card.pack(fill="x", padx=(0, 6))
        card.columnconfigure(1, weight=1)
        for col, text in enumerate(("Column in your spreadsheet", "For example", f"Goes into ({t['name']})")):
            ttk.Label(card, text=text, style="PanelHelp.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 16) if col < 2 else 0)
        ttk.Frame(card, style="Border.TFrame", height=1).grid(row=1, column=0, columnspan=3,
                                                              sticky="ew", pady=(6, 4))
        values = [SKIP] + [f["name"] for f in fields]
        self._sample_labels = []
        for i, head in enumerate(self.headers):
            r = i + 2
            ttk.Label(card, text=head, style="PanelH3.TLabel", wraplength=fs(200),
                      justify="left").grid(row=r, column=0, sticky="w", padx=(0, 16), pady=4)
            lab = ttk.Label(card, text=samples(self.rows, i) or "(empty)", style="PanelHelp.TLabel",
                            justify="left", wraplength=fs(240))
            lab.grid(row=r, column=1, sticky="w", padx=(0, 16), pady=4)
            self._sample_labels.append(lab)
            key = self.mapping.get(i)
            var = tk.StringVar(self, value=self._field_by_key[key]["name"] if key else SKIP)
            combo = ttk.Combobox(card, textvariable=var, values=values, state="readonly", width=26)
            combo.grid(row=r, column=2, sticky="e", pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _e, i=i: self._map_picked(i))
            self._quiet_wheel(combo, sf)
            self.map_vars[i], self.map_combos[i] = var, combo

        def fit(event, card=card):
            if event.widget is not card:
                return
            fixed = fs(200) + 16 + 16 + self.map_combos[0].winfo_reqwidth() + 36
            for lab in self._sample_labels:
                lab.configure(wraplength=max(110, event.width - fixed))
        card.bind("<Configure>", fit, add="+")

        self.options_card = widgets.card(sf.body, app, padding=16)
        self.options_card.pack(fill="x", pady=(12, 4), padx=(0, 6))
        self._option_labels: list = []
        self._warn_labels: list = []

        def fit_page(event):
            if event.widget is sf.body:
                intro.configure(wraplength=max(240, event.width - 12))
                for lab in self._option_labels:
                    if lab.winfo_exists():
                        lab.configure(wraplength=max(200, event.width - 80))
                for lab in self._warn_labels:
                    if lab.winfo_exists():
                        lab.configure(wraplength=max(200, event.width - 40))
        sf.body.bind("<Configure>", fit_page, add="+")
        self.match_var = tk.StringVar(self, value=ALWAYS_ADD)
        self.add_options_var = tk.BooleanVar(self, value=bool(self.options["add_options"]))
        self.create_links_var = tk.BooleanVar(self, value=bool(self.options["create_links"]))
        self.invalid_var = tk.StringVar(self, value=self.options["on_invalid"])
        self._mapping_changed()

    def _quiet_wheel(self, combo, scroll):
        """The wheel scrolls the page, never the value under the pointer."""
        combo.bind("<MouseWheel>", lambda e: scroll.scroll(-3 if e.delta > 0 else 3) or "break")
        combo.bind("<Button-4>", lambda _e: scroll.scroll(-3) or "break")
        combo.bind("<Button-5>", lambda _e: scroll.scroll(3) or "break")

    def _map_picked(self, col: int):
        self.map_combos[col].selection_clear()
        self.set_mapping(col, self._field_names.get(self.map_vars[col].get()))

    def set_mapping(self, col: int, key: str | None):
        """Send column col to the field with this key (None = don't import).
        A field takes one column only: an earlier use of it is cleared."""
        if key is not None and key not in self._field_by_key:
            key = None
        if key is not None:
            for other, k in self.mapping.items():
                if other != col and k == key:
                    self.mapping[other] = None
                    self.map_vars[other].set(SKIP)
        self.mapping[col] = key
        self.map_vars[col].set(self._field_by_key[key]["name"] if key else SKIP)
        self._mapping_changed()

    def _mapped_fields(self) -> list[dict]:
        return [self._field_by_key[k] for k in self.mapping.values() if k in self._field_by_key]

    def _match_choices(self) -> list[tuple[str, str | None]]:
        fields = [f for f in self._mapped_fields() if f["kind"] in MATCH_KINDS]
        fields.sort(key=lambda f: 0 if f.get("unique") else (1 if f["kind"] in ("email", "ni") else 2))
        return [(ALWAYS_ADD, None)] + [(f"Update the record with the same {f['name']}", f["key"])
                                       for f in fields]

    def _mapping_changed(self):
        self.prepared = None
        t = self.t
        mapped = self._mapped_fields()
        keys = {f["key"] for f in mapped}
        n, total = len(mapped), len(self.headers)
        self.match_status.configure(
            text=f"{n} of {total} {plural_word(total, 'column', 'columns')} will be imported"
            if n else "Match at least one column to carry on")
        self.check_btn.configure(state="normal" if n else "disabled")

        # needed fields that no column supplies
        for w in self.warn_box.winfo_children():
            w.destroy()
        self._warn_labels = []
        missing = [f for f in bpm.active_fields(t) if f.get("required") and f["key"] not in keys]
        hard = [f for f in missing if bpm.default_value(f, self.db.user["id"]) is None]
        soft = [f for f in missing if f not in hard]
        self.missing_required = [f["key"] for f in hard]
        lines = []
        if hard:
            names = name_list([f["name"] for f in hard])
            lines.append(("HintWarn.TLabel",
                          f"No column is matched to {names}. Every {t['name'].lower()} needs "
                          f"{'it' if len(hard) == 1 else 'them'}, so rows will be left out unless "
                          "you match a column above. You can still carry on to see the result."))
        for f in soft:
            start = validate.display(f, bpm.default_value(f, self.db.user["id"]), self.db.lookup)
            lines.append(("HintHelp.TLabel",
                          f"{f['name']} has no column, so each new {t['name'].lower()} will start "
                          f"as “{start}”."))
        if lines:
            box = ttk.Frame(self.warn_box, style="Hint.TFrame", padding=(12, 8))
            box.pack(fill="x", pady=(0, 10), padx=(0, 6))
            width = max(200, self.match_scroll.body.winfo_width() - 40)
            for style, text in lines:
                lab = ttk.Label(box, text=text, style=style, justify="left",
                                wraplength=width if width > 220 else fs(600))
                lab.pack(anchor="w", pady=1)
                self._warn_labels.append(lab)
        self._fill_options()

    def _fill_options(self):
        card, app, t = self.options_card, self.app, self.t
        for w in card.winfo_children():
            w.destroy()
        self._option_labels = []
        width = max(200, self.match_scroll.body.winfo_width() - 80)
        if width <= 220:
            width = fs(560)

        def helptext(text, pad=(2, 0), indent=0):
            lab = ttk.Label(card, text=text, style="PanelHelp.TLabel", justify="left", wraplength=width)
            lab.pack(anchor="w", pady=pad, padx=(indent, 0))
            self._option_labels.append(lab)

        ttk.Label(card, text="Options", style="PanelH3.TLabel").pack(anchor="w")
        # matching rows to records that are already there
        choices = self._match_choices()
        self._match_keys = dict(choices)
        current = self.options.get("match_field")
        label = next((lab for lab, key in choices if key == current), ALWAYS_ADD)
        self.options["match_field"] = self._match_keys.get(label)
        self.match_var.set(label)
        ttk.Label(card, text=f"If a row is about a {t['name'].lower()} that is already in the list",
                  style="PanelField.TLabel").pack(anchor="w", pady=(10, 3))
        self.match_combo = ttk.Combobox(card, textvariable=self.match_var, state="readonly",
                                        values=[lab for lab, _k in choices],
                                        width=max(30, max(len(lab) for lab, _k in choices) + 2))
        self.match_combo.pack(anchor="w")
        self.match_combo.bind("<<ComboboxSelected>>", self._option_changed)
        self._quiet_wheel(self.match_combo, self.match_scroll)
        helptext("Updating is useful when you bring in a newer copy of a sheet you imported before. "
                 "Only the columns you matched are changed.")

        ttk.Label(card, text="When a value is not valid (a date that is not a real date, say)",
                  style="PanelField.TLabel").pack(anchor="w", pady=(12, 2))
        ttk.Radiobutton(card, text="Leave that row out – you can save those rows, fix them and "
                                   "import them afterwards",
                        value="skip", variable=self.invalid_var, style="Panel.TRadiobutton",
                        command=self._option_changed).pack(anchor="w")
        ttk.Radiobutton(card, text="Bring the row in with that box left empty", value="blank",
                        variable=self.invalid_var, style="Panel.TRadiobutton",
                        command=self._option_changed).pack(anchor="w")

        mapped = self._mapped_fields()
        self.add_options_check = self.create_links_check = None
        choice = [f for f in mapped if f["kind"] in ("choice", "tags")]
        if choice:
            self.add_options_check = ttk.Checkbutton(
                card, text="Add new choices automatically", variable=self.add_options_var,
                style="Panel.TCheckbutton", command=self._option_changed)
            self.add_options_check.pack(anchor="w", pady=(12, 0))
            helptext(f"For example a {choice[0]['name']} that the list does not offer yet. Without "
                     "this, a value that is not one of the choices counts as not valid.", indent=24)
        links = [f for f in mapped if f["kind"] == "link"]
        can_make = getattr(importing, "_link_can_create", None)
        makeable = [f for f in links if can_make is None or can_make(self.db, f)]
        if makeable:
            target = bpm.get_type(self.db.blueprint, makeable[0].get("link_type") or "")
            what = target["name"].lower() if target else "record"
            self.create_links_check = ttk.Checkbutton(
                card, text="Create linked records that do not exist yet",
                variable=self.create_links_var, style="Panel.TCheckbutton",
                command=self._option_changed)
            self.create_links_check.pack(anchor="w", pady=(12, 0))
            helptext(f"For example {'an' if what[:1] in 'aeiou' else 'a'} {what} named in the sheet "
                     f"that is not in your CRM yet. Without this, a name that matches nothing "
                     "counts as not valid.", indent=24)
        for f in links:
            if f in makeable:
                continue
            target = bpm.get_type(self.db.blueprint, f.get("link_type") or "") or {}
            many = target.get("plural", "records").lower()
            helptext(f"{f['name']} is matched to {many} that are already in your CRM, by name or "
                     f"reference. If the {many} are not in yet, import them first.", pad=(12, 0))

    def _option_changed(self, _e=None):
        self.match_combo.selection_clear()
        self.prepared = None
        self.options = self.read_options()

    def read_options(self) -> dict:
        return {"match_field": self._match_keys.get(self.match_var.get()),
                "add_options": bool(self.add_options_var.get()),
                "create_links": bool(self.create_links_var.get()),
                "on_invalid": "blank" if self.invalid_var.get() == "blank" else "skip"}

    def go_check(self):
        if self._busy or not self._mapped_fields():
            return
        self.options = self.read_options()
        self.prepared = None
        self._show("check")

    # ----------------------------------------------- 3: check and import
    def _working(self, text: str):
        """Replace the body with a progress bar. Returns the progress callback."""
        for frame in (self.body, self.foot):
            for w in frame.winfo_children():
                w.destroy()
        box = ttk.Frame(self.body)
        box.pack(pady=60)
        ttk.Label(box, text=text, style="H2.TLabel").pack()
        bar = ttk.Progressbar(box, length=fs(360), mode="determinate", maximum=100)
        bar.pack(pady=(14, 0))
        self._busy = True
        try:
            self.app.root.update()      # once, so the bar is on screen before the work starts
        except tk.TclError:
            pass

        def progress(done, total):
            try:
                bar.configure(value=100 * done / max(1, total))
                bar.update_idletasks()
            except tk.TclError:
                pass
        return progress

    def _build_check(self):
        if self.prepared is None:
            n = len(self.rows)
            progress = self._working(f"Checking {n:,} {plural_word(n, 'row', 'rows')}…")
            try:
                if not self.winfo_exists():
                    return
                prepared = importing.prepare(self.db, self.type_key, self.rows, dict(self.mapping),
                                             self.options, progress=progress,
                                             row_numbers=self.numbers)
                refine(self.db, self.t, prepared, self.options, self.rows, self.mapping)
                self.prepared = prepared
            finally:
                self._busy = False
            for w in self.body.winfo_children():
                w.destroy()
        self._build_results()

    def problem_list(self) -> list[tuple[int, str, str, str]]:
        """(spreadsheet row, field name, what is wrong, the value) for every problem."""
        fields = {f["key"]: f for f in bpm.active_fields(self.t)}
        col_of = {k: i for i, k in self.mapping.items() if k}
        out = []
        for row, source in zip(self.prepared["rows"], self.rows):
            for key, why in row["errors"].items():
                col = col_of.get(key)
                value = source[col] if col is not None and col < len(source) else ""
                out.append((row["n"], fields[key]["name"] if key in fields else key, why, value))
        return out

    def _build_results(self):
        app, c, t, p = self.app, self.c, self.t, self.prepared
        s = p["summary"]
        blank = self.options.get("on_invalid") == "blank"
        todo = s["add"] + s["update"]
        many = t["plural"].lower()

        self.import_btn = ttk.Button(
            self.foot, text=f"Import {todo:,} {plural_word(todo, 'row', 'rows')}",
            style="Accent.TButton", command=self.run_import, state="normal" if todo else "disabled")
        self.import_btn.pack(side="right")
        ttk.Button(self.foot, text="Back", command=lambda: self._show("match")).pack(side="left")
        self.run_label = ttk.Label(self.foot, text=self.run_error, style="Error.TLabel",
                                   justify="left", wraplength=fs(420))
        self.run_label.pack(side="right", padx=(0, 12))

        top = widgets.card(self.body, app, padding=16)
        top.pack(fill="x")
        stats = ttk.Frame(top, style="Panel.TFrame")
        stats.pack(fill="x")

        def stat(number, caption, colour=None):
            box = ttk.Frame(stats, style="Panel.TFrame")
            box.pack(side="left", padx=(0, 36))
            lab = ttk.Label(box, text=f"{number:,}", style="PanelBig.TLabel")
            if colour:
                lab.configure(foreground=colour)
            lab.pack(anchor="w")
            ttk.Label(box, text=caption, style="PanelDim.TLabel").pack(anchor="w")

        stat(s["add"], "will be added", c["good"] if s["add"] else None)
        if s["update"] or self.options.get("match_field"):
            stat(s["update"], "will be updated")
        if s["skip"] or not blank:
            stat(s["skip"], plural_word(s["skip"], "has a problem and will be left out",
                                        "have problems and will be left out"),
                 c["bad"] if s["skip"] else None)
        kept = s["with_errors"] - s["skip"]
        if blank and kept > 0:
            stat(kept, "will come in with a box left empty", c["warn"])

        notes = []
        fields = {f["key"]: f for f in bpm.active_fields(t)}
        for key, values in p["new_options"].items():
            n = len(values)
            notes.append(f"{n} new {plural_word(n, 'choice', 'choices')} will be added to "
                         f"{fields[key]['name']}: {name_list(values)}.")
        for key, names in p["new_links"].items():
            target = bpm.get_type(self.db.blueprint, fields[key].get("link_type") or "") or {}
            n = len(names)
            word = (target.get("name", "record") if n == 1 else target.get("plural", "records")).lower()
            notes.append(f"{n:,} {word} will be created: {name_list(names)}.")
        if s["empty"]:
            n = s["empty"]
            notes.append(f"{n:,} {plural_word(n, 'row has', 'rows have')} nothing in the matched "
                         "columns and will be ignored.")
        if not todo:
            notes.append("Nothing can be imported as things stand. Go back and check which column "
                         "goes where, or fix the spreadsheet and choose it again.")
        elif not s["with_errors"]:
            notes.append(f"Every row passed the checks. Nothing is added to {many} until you "
                         "press Import.")
        note_labels = []
        for i, text in enumerate(notes):
            lab = ttk.Label(top, text=text, style="Panel.TLabel", justify="left")
            lab.pack(anchor="w", pady=(12 if i == 0 else 3, 0))
            note_labels.append(lab)
        self._wrap(top, note_labels, margin=40)

        problems = self.problem_list()
        self.problem_tree = None
        if not problems:
            return
        head = ttk.Frame(self.body)
        head.pack(fill="x", pady=(14, 6))
        n = len(problems)
        title = f"{n:,} {plural_word(n, 'problem', 'problems')} found"
        ttk.Label(head, text=title, style="H2.TLabel").pack(side="left")
        ttk.Button(head, text="Save the problem rows to a file…", style="Small.TButton",
                   command=self.save_problem_rows).pack(side="right")
        if n > MAX_PROBLEMS:
            ttk.Label(self.body, text=f"Showing the first {MAX_PROBLEMS:,}. Save the problem rows to "
                                      "a file to see them all.",
                      style="Help.TLabel").pack(side="bottom", anchor="w", pady=(4, 0))
        frame, tree = widgets.make_tree(self.body, ("row", "field", "value", "why"), height=4,
                                        xscroll=True)
        frame.pack(fill="both", expand=True)
        shown = problems[:MAX_PROBLEMS]
        font = styles.named_font(self.app.root, "TkDefaultFont")

        def wide(texts, least, most):
            longest = max((font.measure(str(x)) for x in texts), default=0) + 24
            return max(fs(least), min(fs(most), longest))
        widths = {"row": fs(64), "field": wide([p[1] for p in shown] + ["Field"], 90, 260),
                  "why": wide([p[2] for p in shown], 220, 760),
                  "value": wide([" ".join(str(p[3]).split()) for p in shown] + ["In the spreadsheet"],
                                130, 260)}
        for col, text in (("row", "Row"), ("field", "Field"), ("value", "In the spreadsheet"),
                          ("why", "What is wrong")):
            tree.heading(col, text=text, anchor="w")
            tree.column(col, width=widths[col], minwidth=widths[col], anchor="w",
                        stretch=col == "why")
        tree.tag_configure("odd", background=c["bg"])
        for i, (number, field, why, value) in enumerate(shown):
            tree.insert("", "end", values=(number, field, " ".join(str(value).split()), why),
                        tags=("odd",) if i % 2 else ())
        self.problem_tree = tree

    def save_problem_rows(self) -> str:
        """Write the rows that have a problem to a CSV file: the original
        columns plus one saying what is wrong, ready to fix and import again."""
        if not self.prepared:
            return ""
        fields = {f["key"]: f for f in bpm.active_fields(self.t)}
        base = os.path.splitext(os.path.basename(self.path))[0]
        path = widgets.ask_save_file(self.app, "Save the problem rows", f"{base} – rows to fix.csv",
                                     [("CSV file", "*.csv")], defaultextension=".csv")
        if not path:
            return ""
        head = "Problem"
        while head.lower() in [h.lower() for h in self.headers]:
            head += " found"
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                out = csv.writer(fh)
                out.writerow(list(self.headers) + [head])
                count = 0
                for row, source in zip(self.prepared["rows"], self.rows):
                    if not row["errors"]:
                        continue
                    why = "; ".join(f"{fields[k]['name'] if k in fields else k}: {v}"
                                    for k, v in row["errors"].items())
                    out.writerow(list(source) + [why])
                    count += 1
        except OSError as exc:
            widgets.error(self.app, "The file could not be saved",
                          "If a file with that name is open in Excel, close it there and try "
                          f"again, or choose another name.\n\n({exc})")
            return ""
        self.problems_saved = path
        self.app.toast(f"Saved {count:,} {plural_word(count, 'row', 'rows')} to {path}", "good")
        return path

    def run_import(self):
        if self._busy or not self.prepared or self.imported:
            return
        s = self.prepared["summary"]
        todo = s["add"] + s["update"]
        if not todo:
            return
        progress = self._working(f"Importing {todo:,} {plural_word(todo, 'row', 'rows')}…")
        self.run_error = ""
        try:
            if not self.winfo_exists():
                return
            self.report = importing.run(self.db, self.type_key, self.prepared, self.options,
                                        progress=progress)
        except dbm.DBError as exc:
            self._busy = False
            self.run_error = f"Nothing was imported. {exc}"
            # What was checked may no longer hold (the design was changed by
            # someone else, say): check the rows again rather than offer the
            # same Import button, which could only fail the same way.
            self.prepared = None
            t = self.t
            if t is None or t.get("archived"):
                self.load_error, self.type_key = self.run_error, None
                self._show("choose")
            else:
                self._show("check")
            return
        finally:
            self._busy = False
        self.imported = True
        self.app.refresh_sidebar()
        self._show("done")

    def _build_done(self):
        app, t, r = self.app, self.t, self.report or {}
        box = widgets.card(self.body, app, padding=28)
        box.pack(pady=(10, 0), anchor="n")
        ttk.Label(box, text="Import finished", style="PanelH2.TLabel").pack(anchor="w")
        lines = []
        added, updated, skipped = r.get("added", 0), r.get("updated", 0), r.get("skipped", 0)
        if added:
            lines.append((f"{added:,} {plural_word(added, t['name'], t['plural']).lower()} "
                          f"{plural_word(added, 'was', 'were')} added.", "PanelGood.TLabel"))
        if updated:
            lines.append((f"{updated:,} {plural_word(updated, 'was', 'were')} updated.", "Panel.TLabel"))
        if r.get("links_created"):
            n = r["links_created"]
            made = {(bpm.get_field(t, k) or {}).get("link_type")
                    for k in (self.prepared or {}).get("new_links", {})}
            target = bpm.get_type(self.db.blueprint, made.pop() or "") if len(made) == 1 else None
            what = plural_word(n, target["name"], target["plural"]).lower() if target else \
                plural_word(n, "linked record", "linked records")
            lines.append((f"{n:,} {what} {plural_word(n, 'was', 'were')} created.", "Panel.TLabel"))
        if r.get("options_added"):
            n = r["options_added"]
            lines.append((f"{n:,} new {plural_word(n, 'choice was', 'choices were')} added to the design.",
                          "Panel.TLabel"))
        if skipped:
            lines.append((f"{skipped:,} {plural_word(skipped, 'row was', 'rows were')} left out "
                          "because of problems.", "PanelWarn.TLabel"))
        for i, (text, style) in enumerate(lines):
            ttk.Label(box, text=text, style=style, justify="left", wraplength=fs(460)).pack(
                anchor="w", pady=(10 if i == 0 else 3, 0))
        if skipped or (self.prepared and self.prepared["summary"]["with_errors"]):
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack(anchor="w", pady=(10, 0))
            if self.problems_saved:
                ttk.Label(row, text=f"The rows to fix are in {os.path.basename(self.problems_saved)}.",
                          style="PanelHelp.TLabel").pack(side="left")
            else:
                widgets.link(row, "Save the problem rows to a file…", self.save_problem_rows,
                             style="PanelLink.TLabel").pack(side="left")
        ttk.Label(box, text="If this was a mistake, the new records can be selected in the list and "
                            "deleted together.", style="PanelHelp.TLabel", justify="left",
                  wraplength=fs(460)).pack(anchor="w", pady=(12, 0))
        buttons = ttk.Frame(box, style="Panel.TFrame")
        buttons.pack(anchor="w", pady=(18, 0))
        self.see_btn = ttk.Button(buttons, text=f"See the {t['plural'].lower()}", style="Accent.TButton",
                                  command=lambda: app.go("list", type_key=self.type_key))
        self.see_btn.pack(side="left")
        self.another_btn = ttk.Button(buttons, text="Import another file", command=self.another)
        self.another_btn.pack(side="left", padx=(8, 0))

    def another(self):
        self._clear_file()
        self.load_error = self.run_error = ""
        self._show("choose")

    # -------------------------------------------------------------- page
    def can_leave(self) -> bool:
        if self._busy:
            return False
        if not self.rows or self.imported or self._leaving or not self.winfo_exists():
            return True
        if widgets.confirm(self.app, "Leave the import?",
                           f"Nothing from {os.path.basename(self.path)} has been imported yet.",
                           yes="Leave", no="Stay here"):
            self._leaving = True
            return True
        return False

    def state(self) -> dict:
        kw: dict = {"type_key": self.type_key}
        if self.rows and not self.imported:
            kw["path"] = self.path
            kw["sheet"] = self.sheet
        return kw

    def rebuild_state(self) -> dict:
        """Everything, so that switching between dark and light keeps your place."""
        kw = self.state()
        if hasattr(self, "body"):
            if self.stage == "match":
                self.options = self.read_options()
            keep = ("path", "sheet", "sheet_names", "headers", "rows", "numbers", "mapping",
                    "options", "prepared", "report", "imported", "problems_saved", "stage",
                    "load_error", "run_error")
            kw["_restore"] = {k: getattr(self, k) for k in keep}
        return kw
