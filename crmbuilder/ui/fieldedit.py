"""Pieces the setup wizard and the Designer share: the window that edits one
field, the one-click field suggestions, and small helpers for working on a
design before it is saved."""
from __future__ import annotations

import copy
import re
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import validate
from . import widgets
from .styles import fs

# Kinds that make a poor record name.
NOT_FOR_TITLE = ("longtext", "tags", "yesno")
# Which extras apply to which kinds.
UNIQUE_KINDS = ("text", "email", "phone", "postcode", "url", "ni", "number")
TYPED_DEFAULT_KINDS = ("text", "number", "money", "percent", "email", "phone", "postcode", "url")
FLAG_DEFAULTS = {
    "date": ("today", "Start with today's date"),
    "user": ("me", "Start with the person adding the record"),
    "yesno": ("yes", "Start ticked"),
}
DATE_RULES = [("", "Any date"), ("past", "Not in the future"), ("future", "Not in the past")]

SUGGESTIONS = [
    {"name": "Email", "kind": "email"},
    {"name": "Phone", "kind": "phone"},
    {"name": "Mobile", "kind": "phone"},
    {"name": "Address", "kind": "longtext"},
    {"name": "Postcode", "kind": "postcode"},
    {"name": "Notes", "kind": "longtext"},
    {"name": "Status", "kind": "choice", "options": ["New", "Active", "On hold", "Closed"]},
    {"name": "Website", "kind": "url"},
    {"name": "Owner", "kind": "user", "default": "me"},
    {"name": "Date of birth", "kind": "date", "date_rule": "past"},
    {"name": "Job title", "kind": "text"},
    {"name": "Next review", "kind": "date", "remind": True},
    {"name": "Value", "kind": "money"},
    {"name": "Tags", "kind": "tags", "options": ["Important", "Follow up"]},
]

_NAME_HINTS = [
    (r"\b(e-?mail)\b", "email"),
    (r"\b(phone|mobile|telephone|tel|fax)\b", "phone"),
    (r"\b(post ?code)\b", "postcode"),
    (r"\b(ni number|national insurance)\b", "ni"),
    (r"\b(date|dob|birthday|deadline|due|expires|expiry)\b", "date"),
    (r"\b(website|url|web address)\b", "url"),
    (r"\b(notes?|comments?|description|background|address)\b", "longtext"),
    (r"\b(price|cost|amount|value|fee|salary|budget|total|rent)\b", "money"),
    (r"\b(owner|assigned to)\b", "user"),
]


def guess_kind(name: str) -> str:
    """A sensible kind for a field from its name alone ("Date of birth" -> date)."""
    low = (name or "").strip().lower()
    if not low:
        return "text"
    if low.endswith("?") or re.match(r"^(is|has|can|wants) ", low):
        return "yesno"
    for pattern, kind in _NAME_HINTS:
        if re.search(pattern, low):
            return kind
    return "text"


def strip_private(obj):
    """A copy without the working notes (keys starting with an underscore)."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def kind_text(field: dict, bp: dict | None = None) -> str:
    """The kind in plain words; a link says which list it points at."""
    if field.get("kind") == "link" and bp is not None:
        target = bpm.get_type(bp, field.get("link_type") or "")
        if target is not None and (target.get("plural") or target.get("name")):
            return "Link to " + (target.get("plural") or target["name"])
    return validate.kind_label(field.get("kind", "text"))


def _is_live(f: dict) -> bool:
    return not f.get("archived")


def fix_refs(t: dict, live=None) -> None:
    """Make sure the fields a list is named by, and its board field, still exist."""
    live = live or _is_live
    fields = {f["key"]: f for f in t.get("fields", []) if live(f)}
    title = [k for k in (t.get("title") or []) if k in fields]
    if not title and fields:
        pick = (next((f for f in fields.values() if f["kind"] in ("text", "link")), None)
                or next((f for f in fields.values() if f["kind"] not in NOT_FOR_TITLE), None)
                or next(iter(fields.values())))
        title = [pick["key"]]
    t["title"] = title
    board = fields.get(t.get("board") or "")
    if not (board and board["kind"] == "choice"):
        t["board"] = ""


def suggestions(type_def: dict, limit: int = 6) -> list[dict]:
    """Ready-made fields this list does not have yet."""
    have = {f["name"].strip().lower() for f in type_def.get("fields", [])}
    have |= {f["key"] for f in type_def.get("fields", [])}
    out = []
    for s in SUGGESTIONS:
        if s["name"].lower() in have or bpm.slug(s["name"]) in have:
            continue
        out.append(copy.deepcopy(s))
        if len(out) >= limit:
            break
    return out


def unique_name(name: str, taken) -> str:
    low = {str(t).strip().lower() for t in taken}
    if name.strip().lower() not in low:
        return name
    n = 2
    while f"{name} ({n})".lower() in low:
        n += 1
    return f"{name} ({n})"


def flow(parent: tk.Widget, items: list, gap: int = 6) -> None:
    """Lay widgets out left to right, wrapping onto new lines to fit the width
    of parent (a ttk.Frame holding nothing else; the items are its children)."""
    lines: list[tk.Widget] = []
    state = {"shape": None}

    def place(_e=None):
        try:
            if not parent.winfo_exists():
                return
            width = parent.winfo_width()
        except tk.TclError:
            return
        if width <= 1:
            width = 10 ** 6            # not laid out yet: one line for now
        plan: list[list] = [[]]
        used = 0
        for w in items:
            need = w.winfo_reqwidth()
            if plan[-1] and used + need > width:
                plan.append([])
                used = 0
            plan[-1].append(w)
            used += need + gap
        shape = [len(line) for line in plan]
        if shape == state["shape"]:
            return
        state["shape"] = shape
        for w in items:
            w.pack_forget()
        for line in lines:
            line.destroy()
        del lines[:]
        for n, group in enumerate(plan):
            line = ttk.Frame(parent, style=str(parent.cget("style")) or "TFrame")
            line.pack(fill="x", pady=(0 if n == 0 else gap, 0))
            lines.append(line)
            for w in group:
                w.pack(in_=line, side="left", padx=(0, gap))
                w.lift()

    parent.bind("<Configure>", place)
    place()


def _tick(parent, text, variable, help_text, wrap: int = 0):
    """A tick box with a line of plain explanation under it."""
    box = ttk.Frame(parent)
    ttk.Checkbutton(box, text=text, variable=variable).pack(anchor="w")
    lab = ttk.Label(box, text=help_text, style="Help.TLabel", justify="left",
                    wraplength=max(120, wrap - fs(10) * 2 - 8) if wrap else 0)
    lab.pack(anchor="w", padx=(fs(10) * 2 + 3, 0))
    box.bind("<Configure>", lambda e: lab.configure(wraplength=max(120, e.width - fs(10) * 2 - 8)))
    return box


class FieldDialog(widgets.Dialog):
    """Edit one field. show() returns the changed field (a new dict with the
    same key), the string "remove", or None when cancelled.

    simple=True is the short version used in the setup wizard.
    saved = the field as it is in the saved design (None for one that has not
    been saved yet); has_data() says whether any record holds a value for it.
    """

    def __init__(self, app, field: dict, new: bool = False, simple: bool = False, taken=(),
                 targets=(), sections=(), saved: dict | None = None, has_data=None,
                 can_remove: bool = False, focus_options: bool = False):
        title = "Add a field" if new else f"Change “{field.get('name') or 'field'}”"
        width = fs(10) * (50 if simple else 60)
        super().__init__(app, title, width=width)
        # wrap widths are fixed from the start, so the window is the right height first time
        self.full_wrap = width - 36 - 4
        self.half_wrap = (width - 36 - 20) // 2 - 4
        self.field = copy.deepcopy(field)
        self.new, self.simple = new, simple
        self.taken = {str(n).strip().lower() for n in taken}
        self.targets = list(targets)            # [(type key, plural name)]
        self.sections = [s for s in sections if s]
        self.saved, self.has_data = saved, has_data
        self._data_known: bool | None = None
        self._shown = False
        self._focus_options = focus_options
        f = self.field
        kind = f.get("kind", "text")
        if kind not in validate.KINDS:
            kind = "text"

        self.name_var = tk.StringVar(self, value=f.get("name", ""))
        self.kind_var = tk.StringVar(self, value=validate.kind_label(kind))
        self.link_var = tk.StringVar(self, value=dict(self.targets).get(f.get("link_type") or "", ""))
        self.required_var = tk.BooleanVar(self, value=bool(f.get("required")))
        self.in_list_var = tk.BooleanVar(self, value=bool(f.get("in_list")))
        self.help_var = tk.StringVar(self, value=f.get("help", ""))
        self.section_var = tk.StringVar(self, value=f.get("section", ""))
        self.unique_var = tk.BooleanVar(self, value=bool(f.get("unique")))
        self.remind_var = tk.BooleanVar(self, value=bool(f.get("remind")))
        self.rule_var = tk.StringVar(self, value=dict(DATE_RULES).get(f.get("date_rule") or "", "Any date"))
        default = f.get("default")
        default = "" if default is None else str(default)
        self.default_var = tk.StringVar(self, value="" if kind in FLAG_DEFAULTS else default)
        self.flag_var = tk.BooleanVar(self, value=bool(default) and kind in FLAG_DEFAULTS)
        self.num_vars = {k: tk.StringVar(self, value=self._num_text(f.get(k)))
                         for k in ("min", "max", "decimals", "maxlen")}
        self.options_text = "\n".join(validate.options_of(f))
        self.options_box: tk.Text | None = None
        self.more_open = False

        b = self.body
        b.columnconfigure(0, weight=1, uniform="fe")
        b.columnconfigure(1, weight=1, uniform="fe")
        left = ttk.Frame(b)
        left.grid(row=0, column=0, sticky="new", padx=(0, 10))
        ttk.Label(left, text="Name", style="Field.TLabel").pack(anchor="w")
        self.name_entry = ttk.Entry(left, textvariable=self.name_var)
        self.name_entry.pack(fill="x", pady=(3, 0))
        ttk.Label(left, text="What the box is called on the form.",
                  style="Help.TLabel").pack(anchor="w", pady=(3, 0))
        right = ttk.Frame(b)
        right.grid(row=0, column=1, sticky="new", padx=(10, 0))
        ttk.Label(right, text="Kind", style="Field.TLabel").pack(anchor="w")
        kinds = [k for k in validate.KIND_ORDER if k != "link" or self.targets or kind == "link"]
        self.kind_box = ttk.Combobox(right, textvariable=self.kind_var, state="readonly",
                                     values=[validate.kind_label(k) for k in kinds],
                                     height=len(kinds))
        self.kind_box.pack(fill="x", pady=(3, 0))
        self.kind_box.bind("<<ComboboxSelected>>", lambda _e: self._kind_changed())
        self.kind_help = ttk.Label(right, text="", style="Help.TLabel", justify="left",
                                   wraplength=self.half_wrap)
        self.kind_help.pack(anchor="w", pady=(3, 0))

        self.warn = ttk.Label(b, text="", style="Warn.TLabel", justify="left",
                              wraplength=self.full_wrap)
        self.extra = ttk.Frame(b)
        self.extra.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.basics = ttk.Frame(b)
        self.basics.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.basics.columnconfigure(0, weight=1, uniform="fb")
        self.basics.columnconfigure(1, weight=1, uniform="fb")
        self.required_box = _tick(self.basics, "Must be filled in", self.required_var,
                                  "A record cannot be saved while this is empty.",
                                  self.full_wrap if simple else self.half_wrap)
        self.required_box.grid(row=0, column=0, columnspan=2 if simple else 1, sticky="new",
                               padx=(0, 0 if simple else 10))
        if not simple:
            _tick(self.basics, "Show as a column in the list", self.in_list_var,
                  "Appears in the table of records, not only on the form.", self.half_wrap
                  ).grid(row=0, column=1, sticky="new", padx=(10, 0))
            hl = ttk.Frame(self.basics)
            hl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
            ttk.Label(hl, text="Help text shown under the box", style="Field.TLabel").pack(anchor="w")
            ttk.Entry(hl, textvariable=self.help_var).pack(fill="x", pady=(3, 0))
            ttk.Label(hl, text="Optional. A hint for whoever fills it in, e.g. “Only tick when "
                               "they have told you so.”", style="Help.TLabel", justify="left",
                      wraplength=self.full_wrap).pack(anchor="w", pady=(3, 0))
            self.more_link = widgets.link(b, "", self._toggle_more)
            self.more_link.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 0))
            self.more = ttk.Frame(b)
            self.more.columnconfigure(0, weight=1, uniform="fm")
            self.more.columnconfigure(1, weight=1, uniform="fm")
        self.error = ttk.Label(b, text="", style="Error.TLabel", justify="left",
                               wraplength=self.full_wrap)

        self.add_button("Add field" if new else "Update field", self._done, accent=True)
        self.add_button("Cancel", self.cancel)
        if can_remove and not new:
            self.add_button("Remove this field", lambda: self.ok("remove"), side="left",
                            style="Danger.TButton")
        self.default_on_enter()
        self._kind_changed(first=True)

    # ------------------------------------------------------------ plumbing
    @staticmethod
    def _num_text(v) -> str:
        if v in (None, ""):
            return ""
        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""
        return str(int(v)) if v.is_integer() else str(v)

    def kind(self) -> str:
        return validate.kind_from_label(self.kind_var.get())

    def _option_list(self) -> list[str]:
        if self.options_box is not None and self.options_box.winfo_exists():
            self.options_text = self.options_box.get("1.0", "end-1c")
        out: list[str] = []
        for line in self.options_text.replace(";", "\n").split("\n"):
            o = line.strip()[:60]
            if o and o.lower() not in [x.lower() for x in out]:
                out.append(o)
        return out

    def show(self, focus=None):
        self._shown = True
        if focus is None:
            focus = self.name_entry
            if self._focus_options and self.options_box is not None:
                focus = self.options_box
            elif not self.new:
                self.name_entry.select_range(0, "end")
        self.after(60, self._refit)       # in case anything wrapped differently once on screen
        return super().show(focus=focus)

    def _refit(self):
        if not self._shown:
            return
        try:
            self.update_idletasks()
            h = min(self.winfo_reqheight(), self.winfo_screenheight() - 80)
            if h != self.winfo_height():
                self.geometry(f"{self.winfo_width()}x{h}")
        except tk.TclError:
            pass

    def _labelled(self, parent, text, row, column, help_text=""):
        box = ttk.Frame(parent)
        box.grid(row=row, column=column, sticky="new", pady=(12, 0),
                 padx=(0, 10) if column == 0 else (10, 0))
        ttk.Label(box, text=text, style="Field.TLabel").pack(anchor="w")
        if help_text:
            lab = ttk.Label(box, text=help_text, style="Help.TLabel", justify="left",
                            wraplength=self.half_wrap)
            lab.pack(side="bottom", anchor="w", pady=(3, 0))
            box.bind("<Configure>", lambda e: lab.configure(wraplength=max(120, e.width - 4)))
        return box

    # ------------------------------------------------------- kind-specific
    def _kind_changed(self, first: bool = False):
        kind = self.kind()
        self.kind_help.configure(text=validate.KINDS[kind][1])
        self._option_list()                     # keep what was typed if the box is about to go
        for w in self.extra.winfo_children():
            w.destroy()
        self.options_box = None
        if kind in ("choice", "tags"):
            ttk.Label(self.extra, text="The options to pick from", style="Field.TLabel").pack(
                anchor="w", pady=(14, 3))
            self.options_box = widgets.make_text(self.extra, self.app, height=6, width=20)
            self.options_box.pack(fill="x")
            self.options_box.insert("1.0", self.options_text)
            self.options_box.bind("<KeyRelease>", lambda _e: self._option_note())
            tail = " The order here is the order of the columns on a board." if kind == "choice" else ""
            ttk.Label(self.extra, text="One on each line." + tail, style="Help.TLabel",
                      justify="left", wraplength=self.full_wrap).pack(anchor="w", pady=(3, 0))
            self.option_note = ttk.Label(self.extra, text="", style="Help.TLabel", justify="left",
                                         wraplength=self.full_wrap)
        elif kind == "link":
            ttk.Label(self.extra, text="Which list does it link to?", style="Field.TLabel").pack(
                anchor="w", pady=(14, 3))
            names = [name for _k, name in self.targets]
            if self.link_var.get() not in names:
                self.link_var.set(names[0] if len(names) == 1 else "")
            ttk.Combobox(self.extra, textvariable=self.link_var, state="readonly",
                         values=names, width=30).pack(anchor="w")
            ttk.Label(self.extra, text="On the form you pick one record from that list, and the "
                                       "two records show each other.",
                      style="Help.TLabel", justify="left",
                      wraplength=self.full_wrap).pack(anchor="w", pady=(3, 0))
        # "must be filled in" means nothing for a tick box
        for child in self.required_box.winfo_children():
            if isinstance(child, ttk.Checkbutton):
                child.configure(state="disabled" if kind == "yesno" else "normal")
        self._kind_warning(kind)
        if not self.simple:
            self._build_more(kind)
        if not first:
            self._refit()

    def _kind_warning(self, kind: str):
        text = ""
        old = self.saved["kind"] if self.saved else None
        if old and kind != old:
            # what is stored for these reads properly as plain text; a link, a team
            # member, a date, a tick or tags are stored in a form that does not
            plain = validate.TEXTY | {"number", "money", "percent"}
            free = (old in plain and kind in ("text", "longtext")) \
                or (old in validate.TEXTY and kind in validate.TEXTY)
            if not free:
                if self._data_known is None:
                    self._data_known = bool(self.has_data()) if self.has_data else True
                if self._data_known and old in ("link", "user"):
                    text = ("This field already holds information, kept as links. As "
                            f"“{validate.kind_label(kind)}” it would show a number instead of a "
                            "name on each record until that record is edited.")
                elif self._data_known:
                    text = ("This field already holds information. Anything that does not fit "
                            f"“{validate.kind_label(kind)}” will show as it is until that record "
                            "is edited. You can carry on.")
        self.warn.configure(text=text)
        if text:
            self.warn.grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 0))
        else:
            self.warn.grid_forget()

    def _option_note(self):
        if self.saved is None or self.saved.get("kind") not in ("choice", "tags"):
            return
        now = [o.lower() for o in self._option_list()]
        gone = [o for o in validate.options_of(self.saved) if o.lower() not in now]
        if gone:
            self.option_note.configure(
                text="Removed: " + ", ".join(gone) + ". Records that already use "
                     + ("it keep it" if len(gone) == 1 else "them keep them")
                     + " until they are edited – nothing is lost.")
            self.option_note.pack(anchor="w", pady=(3, 0))
        else:
            self.option_note.pack_forget()
        self._refit()

    def _more_summary(self) -> str:
        kind = self.kind()
        used = 0
        used += bool(self.section_var.get().strip())
        used += bool(self.flag_var.get() if kind in FLAG_DEFAULTS else self.default_var.get().strip())
        used += bool(self.unique_var.get() and kind in UNIQUE_KINDS)
        if kind == "date":
            used += bool(self.remind_var.get()) + (self.rule_var.get() != "Any date")
        if kind in ("number", "money", "percent"):
            used += bool(self.num_vars["min"].get().strip()) + bool(self.num_vars["max"].get().strip())
        if kind == "number":
            used += bool(self.num_vars["decimals"].get().strip())
        if kind in ("text", "longtext"):
            used += bool(self.num_vars["maxlen"].get().strip())
        return f"  ({used} in use)" if used else ""

    def _toggle_more(self):
        self.more_open = not self.more_open
        self._show_more()
        self._refit()

    def _show_more(self):
        if self.more_open:
            self.more_link.configure(text="Hide the extra options")
            self.more.grid(row=5, column=0, columnspan=2, sticky="ew")
        else:
            self.more_link.configure(text="More options ›" + self._more_summary())
            self.more.grid_forget()

    def _build_more(self, kind: str):
        m = self.more
        for w in m.winfo_children():
            w.destroy()
        box = self._labelled(m, "Section heading", 0, 0,
                             "Groups fields under a heading on the form, e.g. “Contact details”.")
        ttk.Combobox(box, textvariable=self.section_var, values=self.sections).pack(fill="x", pady=(3, 0))
        if kind in FLAG_DEFAULTS:
            _tick(m, FLAG_DEFAULTS[kind][1], self.flag_var,
                  "Filled in for you on every new record; it can still be changed.",
                  self.half_wrap).grid(row=0, column=1, sticky="new", pady=(12, 0), padx=(10, 0))
        elif kind == "choice":
            box = self._labelled(m, "Starting value", 0, 1, "Already chosen on every new record.")
            combo = ttk.Combobox(box, textvariable=self.default_var, state="readonly")
            combo.configure(postcommand=lambda: combo.configure(values=[""] + self._option_list()))
            combo.pack(fill="x", pady=(3, 0))
        elif kind in TYPED_DEFAULT_KINDS:
            box = self._labelled(m, "Starting value", 0, 1, "Already filled in on every new record.")
            ttk.Entry(box, textvariable=self.default_var).pack(fill="x", pady=(3, 0))
        row = 1
        if kind in UNIQUE_KINDS:
            _tick(m, "No two records may have the same value", self.unique_var,
                  "Stops the same thing being entered twice, e.g. a membership number.",
                  self.full_wrap).grid(row=row, column=0, columnspan=2, sticky="new", pady=(12, 0))
            row += 1
        if kind == "date":
            box = self._labelled(m, "Which dates are allowed?", row, 0)
            ttk.Combobox(box, textvariable=self.rule_var, state="readonly",
                         values=[label for _k, label in DATE_RULES]).pack(fill="x", pady=(3, 0))
            row += 1
            _tick(m, "Remind me on Home when this date is coming up", self.remind_var,
                  "Good for renewals, reviews and deadlines.", self.full_wrap
                  ).grid(row=row, column=0, columnspan=2, sticky="new", pady=(12, 0))
            row += 1
        if kind in ("number", "money", "percent"):
            nums = ttk.Frame(m)
            nums.grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 0))
            cols = [("min", "Smallest allowed"), ("max", "Largest allowed")]
            if kind == "number":
                cols.append(("decimals", "Decimal places"))
            for i, (key, label) in enumerate(cols):
                cell = ttk.Frame(nums)
                cell.grid(row=0, column=i, sticky="w", padx=(0, 16))
                ttk.Label(cell, text=label, style="Field.TLabel").pack(anchor="w")
                ttk.Entry(cell, textvariable=self.num_vars[key], width=12).pack(anchor="w", pady=(3, 0))
            ttk.Label(nums, text="Leave empty for no limit." + (
                " 0 decimal places means whole numbers only." if kind == "number" else ""),
                      style="Help.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
            row += 1
        if kind in ("text", "longtext"):
            box = self._labelled(m, "Longest allowed (characters)", row, 0, "Leave empty for no limit.")
            ttk.Entry(box, textvariable=self.num_vars["maxlen"], width=12).pack(anchor="w", pady=(3, 0))
        self._show_more()

    # --------------------------------------------------------------- done
    def _fail(self, message: str, widget=None):
        self.error.configure(text=message)
        self.error.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        if widget is not None:
            try:
                widget.focus_set()
            except tk.TclError:
                pass
        self._refit()

    def _number(self, key: str, label: str, whole: bool = False):
        """(value or None, error or None) for one of the number boxes."""
        text = self.num_vars[key].get().strip()
        if not text:
            return None, None
        v = validate.parse_number(text)
        if v is None or (whole and (not float(v).is_integer() or v < 0)):
            return None, f"{label} must be a {'whole ' if whole else ''}number."
        return (int(v) if whole else v), None

    def _done(self):
        f = self.field
        kind = self.kind()
        name = re.sub(r"\s+", " ", self.name_var.get()).strip()
        if not name:
            return self._fail("Give the field a name.", self.name_entry)
        if len(name) > 60:
            return self._fail("That name is too long – keep it under 60 characters.", self.name_entry)
        if name.lower() in self.taken:
            return self._fail(f"This list already has a field called “{name}”. Choose another name.",
                              self.name_entry)
        out = {k: v for k, v in f.items()
               if k not in ("options", "link_type", "min", "max", "decimals", "maxlen", "date_rule",
                            "remind", "default", "unique", "required", "in_list", "help", "section")}
        out["name"], out["kind"] = name, kind
        if kind in ("choice", "tags"):
            options = self._option_list()
            if not options:
                return self._fail("Type at least one option to pick from, one on each line.",
                                  self.options_box)
            out["options"] = options
        if kind == "link":
            key = next((k for k, n in self.targets if n == self.link_var.get()), None)
            if key is None:
                return self._fail("Choose which list this field links to.")
            out["link_type"] = key
        if self.required_var.get() and kind != "yesno":
            out["required"] = True
        if self.simple:
            # the short version leaves the other settings as they were, where they still apply
            keep = {"in_list": True, "help": True, "section": True,
                    "unique": kind in UNIQUE_KINDS, "remind": kind == "date",
                    "date_rule": kind == "date", "min": kind in ("number", "money", "percent"),
                    "max": kind in ("number", "money", "percent"), "decimals": kind == "number",
                    "maxlen": kind in ("text", "longtext"),
                    "default": kind == f.get("kind")}
            for k, ok in keep.items():
                if ok and f.get(k) not in (None, "", False):
                    out[k] = f[k]
            if kind == "choice" and out.get("default") and \
                    str(out["default"]).lower() not in [o.lower() for o in out["options"]]:
                out.pop("default")
            return self.ok(out)
        if self.in_list_var.get():
            out["in_list"] = True
        if self.help_var.get().strip():
            out["help"] = self.help_var.get().strip()[:200]
        if self.section_var.get().strip():
            out["section"] = re.sub(r"\s+", " ", self.section_var.get()).strip()[:60]
        if kind in UNIQUE_KINDS and self.unique_var.get():
            out["unique"] = True
        if kind == "date":
            rule = next((k for k, label in DATE_RULES if label == self.rule_var.get()), "")
            if rule:
                out["date_rule"] = rule
            if self.remind_var.get():
                out["remind"] = True
        if kind in ("number", "money", "percent"):
            for key, label in (("min", "Smallest allowed"), ("max", "Largest allowed")):
                v, err = self._number(key, label)
                if err:
                    self.more_open = True
                    self._show_more()
                    return self._fail(err)
                if v is not None:
                    out[key] = v
            if "min" in out and "max" in out and out["min"] > out["max"]:
                self.more_open = True
                self._show_more()
                return self._fail("The smallest allowed is bigger than the largest allowed.")
        if kind == "number":
            v, err = self._number("decimals", "Decimal places", whole=True)
            if err or (v is not None and v > 6):
                self.more_open = True
                self._show_more()
                return self._fail(err or "Use 6 decimal places or fewer.")
            if v is not None:
                out["decimals"] = v
        if kind in ("text", "longtext"):
            v, err = self._number("maxlen", "Longest allowed", whole=True)
            if err or v == 0:
                self.more_open = True
                self._show_more()
                return self._fail(err or "Longest allowed must be at least 1.")
            if v is not None:
                out["maxlen"] = v
        if kind in FLAG_DEFAULTS:
            if self.flag_var.get():
                # keep a starting value this window cannot show (e.g. "+7" for a date)
                same = kind == f.get("kind") and str(f.get("default") or "").strip()
                out["default"] = same or FLAG_DEFAULTS[kind][0]
        elif kind == "choice" or kind in TYPED_DEFAULT_KINDS:
            text = self.default_var.get().strip()
            if text:
                value, err = validate.normalise(dict(out, required=False, unique=False), text)
                if err:
                    self.more_open = True
                    self._show_more()
                    return self._fail("The starting value does not fit this kind of field. " + err)
                out["default"] = validate.to_edit(out, value) if kind != "choice" else value
        self.ok(out)
