"""The setup wizard: five plain questions, then a working CRM.

  1  What do you want to keep track of?   (a ready-made start, a spreadsheet, or blank)
  2  Which lists do you want?
  3  What do you record about each one?
  4  Who will use it?
  5  Name it and choose where to keep it

Everything the person has chosen lives in one dictionary (self.d), which is
also what state() hands back, so the screen can be rebuilt (dark/light switch)
without losing anything. The design worked on for each starting point is kept
there too (d["kept"]), so trying a different starting point loses nothing.
"""
from __future__ import annotations

import copy
import os
import re
import sqlite3
import sys
import tkinter as tk
import unicodedata
from tkinter import ttk

from .. import FILE_EXT, importing, paths, security, sheets, templates, validate
from .. import blueprint as bpm
from .. import db as dbm
from . import fieldedit, widgets
from .styles import fs

STEP_NAMES = ["Starting point", "Lists", "Fields", "People", "Name and place"]
MAX_WIDTH = 900
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | \
            {f"LPT{i}" for i in range(1, 10)}
_FILLER = {"export", "exported", "list", "data", "copy", "final", "new", "old", "sheet",
           "spreadsheet", "crm", "db", "database", "backup", "master", "all", "my", "our",
           "the", "csv", "xlsx", "current", "latest"}


def safe_file_name(name: str) -> str:
    """A CRM name made safe to use as a file name on Windows and macOS."""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name or "")
    s = re.sub(r"\s+", " ", s).strip(" .")[:80].strip(" .")
    if not s:
        return "My CRM"
    # Windows refuses these names whatever follows the first dot ("CON.backup" too)
    if s.split(".")[0].strip().upper() in _RESERVED:
        s = re.sub(r"\s+", " ", s.replace(".", " ")).strip()
        if s.upper() in _RESERVED:
            s += " CRM"
    return s


def suggest_singular(path: str, sheet: str | None = None, several: bool = False) -> str:
    """A guess at what one row is, from the file (or worksheet) name:
    'customers_2024.csv' -> 'Customer'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    base = sheet if (several and sheet and not re.fullmatch(r"(?i)sheet ?\d*", sheet)) else stem
    words = [w for w in re.split(r"[^A-Za-z]+", base) if w and w.lower() not in _FILLER]
    if not words:
        return "Record"
    words = words[:3]
    last = words[-1]
    low = last.lower()
    if low == "people":
        last = "person"
    elif low.endswith("ies") and len(low) > 4:
        last = last[:-3] + "y"
    elif low.endswith(("sses", "ches", "shes", "xes")):
        last = last[:-2]
    elif low.endswith("s") and not low.endswith(("ss", "us", "is")) and len(low) > 3:
        last = last[:-1]
    words[-1] = last
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:]


def plain_letters(text: str) -> str:
    """Lower-case letters and digits only, accents removed: 'Siân' -> 'sian'."""
    flat = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", flat.lower())


def _can_write(folder: str) -> bool:
    probe = os.path.join(folder, ".crm-builder-write-test")
    try:
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def count_text(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


class SelectCard(tk.Frame):
    """A big clickable choice. The chosen one gets an accent border."""

    def __init__(self, parent, app, title: str, blurb: str = "", foot: str = "",
                 command=None, on_double=None, compact: bool = False, aside: str = ""):
        c = app.c
        super().__init__(parent, bg=c["bg"], takefocus=1, cursor="hand2", highlightthickness=0)
        self.c, self.command, self.on_double = c, command, on_double
        self.selected = self.hover = self.focused = False
        self.ring = tk.Frame(self, bg=c["border"])
        self.ring.pack(fill="both", expand=True, padx=1, pady=1)
        self.inner = tk.Frame(self.ring, bg=c["panel"], padx=14, pady=8 if compact else 12)
        self.inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.labels: list[tk.Widget] = []
        self._wrap: list[tk.Label] = []
        if aside:
            # the title, with a quiet note on the same line at the right
            line = tk.Frame(self.inner, bg=c["panel"])
            line.pack(fill="x")
            self.labels.append(line)
            for text, fg, font, side in ((title, c["text"], (c["ui"], fs(11), "bold"), "left"),
                                         (aside, c["dim"], (c["ui"], fs(9)), "right")):
                lab = tk.Label(line, text=text, bg=c["panel"], fg=fg, font=font, anchor="w")
                lab.pack(side=side, anchor="s")
                self.labels.append(lab)
        else:
            self._label(title, c["text"], (c["ui"], fs(11), "bold"))
        if blurb:
            self._label(blurb, c["text"], (c["ui"], fs(10)), pady=(2, 0), wrap=True)
        if foot:
            self._label(foot, c["dim"], (c["ui"], fs(9)), pady=(3, 0), wrap=True)
        for w in [self, self.ring, self.inner] + self.labels:
            w.bind("<Button-1>", self._click)
            w.bind("<Double-Button-1>", self._double)
            w.bind("<Enter>", lambda _e: self._hover(True))
            w.bind("<Leave>", lambda _e: self._hover(False))
        # Tab only moves the ring; Space chooses, Enter chooses and carries on
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        self.bind("<space>", lambda _e: self._choose())
        self.bind("<Return>", lambda _e: self._double() or "break")
        self.bind("<KP_Enter>", lambda _e: self._double() or "break")
        self.inner.bind("<Configure>", self._resized)

    def _label(self, text, fg, font, pady=(0, 0), wrap=False):
        lab = tk.Label(self.inner, text=text, bg=self.c["panel"], fg=fg, font=font,
                       justify="left", anchor="w")
        lab.pack(fill="x", anchor="w", pady=pady)
        self.labels.append(lab)
        if wrap:
            self._wrap.append(lab)

    def _resized(self, event):
        for lab in self._wrap:
            lab.configure(wraplength=max(120, event.width - 32))

    def _choose(self):
        if self.command:
            self.command()

    def _click(self, _e=None):
        self.focus_set()
        self._choose()

    def _double(self, _e=None):
        self._choose()
        if self.on_double:
            self.on_double()

    def _hover(self, on: bool):
        self.hover = on
        self.paint()

    def _focus(self, on: bool):
        self.focused = on
        self.paint()

    def set_selected(self, selected: bool):
        self.selected = selected
        self.paint()

    def paint(self):
        c = self.c
        try:
            self.configure(bg=c["accent"] if self.selected else c["bg"])
            lit = self.selected or self.hover or self.focused
            self.ring.configure(bg=c["accent"] if lit else c["border"])
            fill = c["hint"] if self.selected else c["panel"]
            self.inner.configure(bg=fill)
            for lab in self.labels:
                lab.configure(bg=fill)
        except tk.TclError:
            pass


class WizardScreen(ttk.Frame):
    screen_name = "wizard"

    def __init__(self, parent, app, restore: dict | None = None):
        super().__init__(parent)
        self.app, self.c = app, app.c
        self.d = restore if restore else self._fresh()
        self.d.setdefault("kept", {})
        self._finished = False
        self._busy = False
        self._guard = False            # True while the code (not the person) sets a box

        bar = ttk.Frame(self, style="Panel.TFrame")
        bar.pack(side="bottom", fill="x")
        ttk.Frame(self, style="Border.TFrame", height=1).pack(side="bottom", fill="x")
        self.bar_in = ttk.Frame(bar, style="Panel.TFrame")
        self.bar_in.pack(fill="x", padx=24, pady=12)
        self.back_btn = ttk.Button(self.bar_in, text="‹ Back", command=self.back)
        self.cancel_btn = ttk.Button(self.bar_in, text="Cancel", style="PanelFlat.TButton",
                                     command=self.cancel)
        self.next_btn = ttk.Button(self.bar_in, text="Next", style="Big.Accent.TButton",
                                   command=self.next)
        self.next_btn.pack(side="right")
        self.status = ttk.Label(self.bar_in, text="", style="PanelDim.TLabel")
        self.status.pack(side="right", padx=(0, 16))

        self.top = ttk.Frame(self)
        self.top.pack(fill="both", expand=True, padx=24, pady=(16, 0))
        self.steps_row = ttk.Frame(self.top)
        self.steps_row.pack(fill="x")
        self.kicker = ttk.Label(self.top, text="", style="Help.TLabel")
        self.kicker.pack(anchor="w", pady=(16, 0))
        self.heading = ttk.Label(self.top, text="", style="H1.TLabel")
        self.heading.pack(anchor="w")
        self.sub = ttk.Label(self.top, text="", style="Sub.TLabel", justify="left",
                             wraplength=MAX_WIDTH)
        self.sub.pack(anchor="w", pady=(3, 0))
        self.message = ttk.Label(self.top, text="", style="Error.TLabel", justify="left",
                                 wraplength=MAX_WIDTH, font=(self.c["ui"], fs(10)))
        self.body = ttk.Frame(self.top)
        self.body.pack(fill="both", expand=True, pady=(12, 10))

        bar.lift()                     # Tab goes through the page first, then Back / Cancel / Next
        self.bind("<Configure>", self._resized)
        top = self.winfo_toplevel()
        self._return_id = top.bind("<Return>", self._on_return, add="+")
        self._kp_id = top.bind("<KP_Enter>", self._on_return, add="+")
        self.bind("<Destroy>", self._gone)
        self._show()

    # ------------------------------------------------------------ plumbing
    def _fresh(self) -> dict:
        return {"step": 0, "view": "cards", "start": None, "bp": None, "bp_from": None,
                "sheet": None, "cur": None, "who": "solo", "person": "", "username": "",
                "user_auto": True, "pw": "", "pw2": "", "encrypt": False, "name": "",
                "name_auto": True, "folder": paths.default_save_dir(self.app.settings),
                "examples": True, "kept": {}}

    def state(self) -> dict:
        return {"restore": self.d}

    def _has_progress(self) -> bool:
        return self.d["step"] > 0 or self.d["sheet"] is not None

    def can_leave(self) -> bool:
        if self._finished or not self._has_progress():
            return True
        return widgets.confirm(
            self.app, "Leave the setup?",
            "Your new CRM has not been created yet. If you leave now, the choices you have "
            "made so far are lost.", yes="Leave the setup", no="Carry on with the setup")

    def cancel(self):
        if self._busy or not self.can_leave():
            return
        self._finished = True
        if self.app.db is not None:
            self.app.enter(self.app.db)
        else:
            self.app.show_screen("welcome")

    def escape(self):
        """Esc backs out of the setup (asking first if anything would be lost)."""
        self.cancel()

    def _resized(self, event):
        if event.widget is not self:
            return
        pad = max(24, (event.width - fs(10) * MAX_WIDTH // 10) // 2)
        self.top.pack_configure(padx=pad)
        self.bar_in.pack_configure(padx=pad)
        wrap = max(300, event.width - 2 * pad - 6)
        self.sub.configure(wraplength=wrap)
        self.message.configure(wraplength=wrap)

    def _gone(self, event):
        """Take the Enter-key binding off the window when this screen goes."""
        if event.widget is not self:
            return
        try:
            top = self.winfo_toplevel()
            top.unbind("<Return>", self._return_id)
            top.unbind("<KP_Enter>", self._kp_id)
        except (tk.TclError, AttributeError):
            pass

    def _on_return(self, event):
        if not self.winfo_exists() or self._busy:
            return None
        w = event.widget
        if isinstance(w, tk.Text):
            return None
        if isinstance(w, ttk.Button) and w is not self.next_btn:
            w.invoke()
            return "break"
        if isinstance(w, ttk.Combobox):
            return None
        self.next()
        return "break"

    def _say(self, text: str = ""):
        """Show a problem just above the buttons (or clear it)."""
        self.message.configure(text=text)
        if text and not self.message.winfo_manager():
            self.message.pack(side="bottom", anchor="w", pady=(0, 8), before=self.body)
        elif not text and self.message.winfo_manager():
            self.message.pack_forget()

    def _head(self, question: str, sub: str):
        self.heading.configure(text=question)
        self.sub.configure(text=sub)

    def _draw_steps(self):
        c, step = self.c, self.d["step"]
        for w in self.steps_row.winfo_children():
            w.destroy()
        for i, name in enumerate(STEP_NAMES):
            self.steps_row.columnconfigure(i, weight=1, uniform="steps")
            cell = ttk.Frame(self.steps_row)
            cell.grid(row=0, column=i, sticky="ew", padx=(0, 0 if i == len(STEP_NAMES) - 1 else 6))
            tk.Frame(cell, bg=c["accent"] if i <= step else c["border"], height=3).pack(fill="x")
            lab = tk.Label(cell, text=f"{i + 1}   {name}", bg=c["bg"], anchor="w",
                           fg=c["text"] if i <= step else c["dim"],
                           font=(c["ui"], fs(9), "bold" if i == step else "normal"))
            lab.pack(fill="x", pady=(5, 0))
            if i < step:
                lab.configure(cursor="hand2")
                lab.bind("<Button-1>", lambda _e, i=i: self.goto(i))
        self.kicker.configure(text=f"Step {step + 1} of {len(STEP_NAMES)}")

    def _show(self):
        d = self.d
        for w in self.body.winfo_children():
            w.destroy()
        self._say("")
        self.status.configure(text="")
        self._draw_steps()
        [self._step_start, self._step_lists, self._step_fields, self._step_people,
         self._step_name][d["step"]]()
        self.cancel_btn.pack_forget()
        self.back_btn.pack_forget()
        if d["step"] > 0 or d["view"] == "sheet":
            self.back_btn.pack(side="left")
        self.cancel_btn.pack(side="left", padx=(8 if self.back_btn.winfo_manager() else 0, 0))
        self.next_btn.configure(text="Create my CRM" if d["step"] == 4 else "Next")

    def goto(self, step: int):
        """Jump back to an earlier step (the step names at the top are clickable)."""
        if self._busy or step >= self.d["step"]:
            return
        self.d["step"] = step
        if step == 0:
            self.d["view"] = "sheet" if (self.d["start"] == "sheet" and self.d["sheet"]) else "cards"
        self._show()

    def back(self):
        d = self.d
        if self._busy:
            return
        if d["step"] == 0:
            if d["view"] == "sheet":
                d["view"] = "cards"
                self._show()
            return
        self.goto(d["step"] - 1)

    def next(self):
        if self._busy:
            return
        self._say("")
        step = self.d["step"]
        leave = [self._leave_start, self._leave_lists, self._leave_fields, self._leave_people,
                 self._create][step]
        if not leave() or step == 4:
            return
        self.d["step"] = step + 1
        self._show()

    # ------------------------------------------------- the working design
    def _types(self) -> list[dict]:
        return self.d["bp"]["types"] if self.d["bp"] else []

    def _kept_types(self) -> list[dict]:
        return [t for t in self._types() if not t.get("_off")]

    def _live_fields(self, t: dict) -> list[dict]:
        """The fields of a list that will really be created."""
        kept = {x["key"] for x in self._kept_types()}
        return [f for f in t["fields"] if not f.get("_off")
                and not (f["kind"] == "link" and f.get("link_type") not in kept)]

    def _effective(self, t: dict) -> dict:
        out = {k: v for k, v in t.items() if k != "fields"}
        out["fields"] = [fieldedit.strip_private(f) for f in self._live_fields(t)]
        return out

    def _stash_design(self):
        """Put the working design aside under its starting point, so that trying
        another starting point never throws away what was set up."""
        d = self.d
        if d["bp"] is None or d["bp_from"] is None:
            return
        s = d["sheet"] or {}
        d["kept"][d["bp_from"]] = {"bp": d["bp"], "cur": d["cur"], "examples": d["examples"],
                                   "type_key": s.get("type_key"), "mapping": s.get("mapping")}
        d["bp"] = d["bp_from"] = d["cur"] = None

    def _unstash_design(self, key) -> bool:
        """Bring back the design set aside for this starting point, if there is one."""
        d = self.d
        kept = d["kept"].pop(key, None)
        if kept is None:
            return False
        d["bp"], d["bp_from"] = kept["bp"], key
        d["cur"], d["examples"] = kept["cur"], kept["examples"]
        if isinstance(key, tuple) and d["sheet"]:
            d["sheet"]["type_key"], d["sheet"]["mapping"] = kept["type_key"], kept["mapping"]
        return True

    def _ensure_design(self):
        """Build the working design for the chosen starting point (once)."""
        d = self.d
        start = d["start"]
        if d["bp_from"] == start:
            return
        self._stash_design()
        if self._unstash_design(start):
            return
        if start == "blank":
            bp = bpm.empty()
            t = bpm.new_type("Contact", bp)
            t["_new"] = t["_auto"] = True
            bp["types"].append(t)
        else:
            bp = templates.build(start)
            for t in bp["types"]:
                t["_orig"] = t["name"]
                t["_auto"] = t["plural"] == bpm.plural_of(t["name"])
        d["bp"], d["bp_from"], d["cur"] = bp, start, None
        d["examples"] = True

    def _build_sheet_design(self, fresh: bool = False):
        """The design for the chosen spreadsheet. fresh=True after the file was read again."""
        d, s = self.d, self.d["sheet"]
        key = ("sheet", s["path"], s["sheet"])
        self._stash_design()
        if fresh:
            d["kept"].pop(key, None)
        elif self._unstash_design(key):
            return
        bp = bpm.empty()
        t, mapping = importing.type_from_sheet(s["singular"] or "Record", s["headers"], s["rows"], bp)
        t["_new"] = t["_auto"] = True
        bp["types"].append(t)
        s["type_key"], s["mapping"] = t["key"], mapping
        d["bp"], d["bp_from"], d["cur"] = bp, key, None

    def _default_name(self) -> str:
        d = self.d
        if d["start"] == "sheet":
            kept = self._kept_types()
            return kept[0]["plural"] if kept else "My CRM"
        tpl = templates.template(d["start"] or "")
        return tpl["name"] if tpl else "My CRM"

    def final_design(self) -> tuple[dict, dict]:
        """The design that will be created, and {working list key: final key}."""
        d = self.d
        kept = [t for t in self._kept_types() if t["name"].strip()]
        keymap: dict[str, str] = {}
        taken = [t["key"] for t in kept if not t.get("_new")]
        for t in kept:
            if t.get("_new"):
                keymap[t["key"]] = bpm.slug(t["name"], taken)
                taken.append(keymap[t["key"]])
            else:
                keymap[t["key"]] = t["key"]
        renamed = [t for t in kept if t.get("_new") or t["name"].strip() != t.get("_orig", t["name"])]
        prefixes = [t["prefix"] for t in kept if t not in renamed]
        out = bpm.empty(d["name"].strip() or self._default_name())
        for t in kept:
            nt = {k: copy.deepcopy(v) for k, v in t.items()
                  if not k.startswith("_") and k != "fields"}
            nt["key"] = keymap[t["key"]]
            nt["name"] = t["name"].strip()
            nt["plural"] = t["plural"].strip() or bpm.plural_of(nt["name"])
            if t in renamed:
                nt["prefix"] = bpm.make_prefix(nt["name"], prefixes)
                prefixes.append(nt["prefix"])
            nt["fields"] = []
            for f in self._live_fields(t):
                nf = fieldedit.strip_private(f)
                if nf["kind"] == "link":
                    nf["link_type"] = keymap[nf["link_type"]]
                nt["fields"].append(nf)
            fieldedit.fix_refs(nt)
            out["types"].append(nt)
        return bpm.clean(out), keymap

    # =============================================================== step 1
    def _step_start(self):
        if self.d["view"] == "sheet" and self.d["sheet"]:
            return self._view_sheet()
        self.d["view"] = "cards"
        self._head("What do you want to keep track of?",
                   "Pick the closest match. You can rename, remove or add anything on the next "
                   "steps, and at any time afterwards.")
        # The ready-made starts scroll if the window is short; the two other
        # ways in stay in view under them whatever the size of the window.
        sf = widgets.ScrollFrame(self.body, self.app)
        other = ttk.Frame(self.body)
        other.pack(side="bottom", fill="x")
        sf.pack(fill="both", expand=True)
        self.cards: dict[str, SelectCard] = {}

        def add(grid, key, row, col, title, blurb, aside=""):
            card = SelectCard(grid, self.app, title, blurb, aside=aside,
                              command=lambda: self._pick_start(key),
                              on_double=self.next, compact=True)
            card.grid(row=row, column=col, sticky="nsew", padx=(0, 5) if col == 0 else (5, 0),
                      pady=(0, 8))
            self.cards[key] = card

        for grid in (sf.body, other):
            grid.columnconfigure(0, weight=1, uniform="cards")
            grid.columnconfigure(1, weight=1, uniform="cards")
        for n, tpl in enumerate(templates.TEMPLATES):
            add(sf.body, tpl["key"], n // 2, n % 2, tpl["name"], tpl["blurb"],
                "Includes: " + ", ".join(t["plural"] for t in tpl["types"]))
        ttk.Label(other, text="OR START ANOTHER WAY", style="Help.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(6, 6))
        add(other, "sheet", 1, 0, "Start from a spreadsheet I already have",
            "Its columns become the fields and its rows are brought in.",
            "Excel or CSV file" if sheets.have_excel() else "CSV file")
        add(other, "blank", 1, 1, "Start with a blank CRM",
            "One empty list that you name and shape yourself.")

        def align(_e=None):
            """Keep the two rows of boxes the same width when the scrollbar shows."""
            try:
                shown = sf.bar.winfo_ismapped()
                other.pack_configure(padx=(0, sf.bar.winfo_reqwidth() if shown else 0))
            except tk.TclError:
                pass                       # the step is being taken down
        sf.bar.bind("<Map>", align)
        sf.bar.bind("<Unmap>", align)
        self._paint_cards()
        chosen = self.cards.get(self.d["start"] or "")
        if chosen is not None and chosen.master is sf.body:
            self.after_idle(lambda: chosen.winfo_exists() and sf.show(chosen))

    def _paint_cards(self):
        for key, card in self.cards.items():
            card.set_selected(key == self.d["start"])

    def _pick_start(self, key: str):
        if self.d["start"] != key:
            self.d["start"] = key
            self._say("")
        self._paint_cards()

    def _leave_start(self) -> bool:
        d = self.d
        if d["view"] == "sheet":
            return self._leave_sheet()
        if not d["start"]:
            self._say("Click one of the boxes above to choose where to start.")
            return False
        if d["start"] != "sheet":
            self._ensure_design()
            return True
        if d["sheet"] is None and not self.choose_sheet():
            return False
        if d["bp_from"] != ("sheet", d["sheet"]["path"], d["sheet"]["sheet"]):
            self._build_sheet_design()
        d["view"] = "sheet"
        self._show()
        return False

    # ---- the spreadsheet route
    def choose_sheet(self) -> bool:
        """Ask for a spreadsheet and read it. False if nothing usable was chosen."""
        path = widgets.ask_open_file(self.app, "Choose your spreadsheet", sheets.file_types())
        if not path:
            return False
        return self._load_sheet(path, None)

    def _load_sheet(self, path: str, sheet: str | None) -> bool:
        self._say("")
        self.status.configure(text="Reading the spreadsheet…")
        self.update_idletasks()
        try:
            names = sheets.sheet_names(path)
            if sheet is None and names:
                sheet = names[0]
            headers, rows, numbers = sheets.read_table(path, sheet, with_numbers=True)
        except (sheets.SheetError, OSError) as exc:
            self.status.configure(text="")
            self._say(str(exc).replace("\n\n", " "))
            return False
        self.status.configure(text="")
        self._stash_design()               # while the sheet it belongs to is still known
        old = self.d["sheet"]
        singular = suggest_singular(path, sheet, len(names) > 1)
        if old and old["path"] == path and old.get("singular_typed"):
            singular = old["singular"]
        self.d["sheet"] = {"path": path, "names": names, "sheet": sheet, "headers": headers,
                           "rows": rows, "numbers": numbers, "singular": singular,
                           "singular_typed": bool(old and old["path"] == path
                                                  and old.get("singular_typed")),
                           "type_key": None, "mapping": {}}
        self._build_sheet_design(fresh=True)
        return True

    def _view_sheet(self):
        d, s = self.d, self.d["sheet"]
        app = self.app
        self._head("Is this your spreadsheet?",
                   "Each column becomes a field. Check it has been understood – you can change "
                   "any of it on the next steps. The rows are brought in when your CRM is created.")
        t = bpm.get_type(d["bp"], s["type_key"])
        top = widgets.card(self.body, app)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=os.path.basename(s["path"]), style="PanelH2.TLabel").grid(
            row=0, column=0, sticky="w")
        found = count_text(len(s["rows"]), "row") + ", " + count_text(len(s["headers"]), "column")
        if not s["rows"]:
            found = "No rows under the headings – the list will start empty"
        ttk.Label(top, text=found, style="PanelDim.TLabel").grid(row=0, column=1, sticky="w", padx=14)
        ttk.Button(top, text="Choose a different file…", style="Small.TButton",
                   command=self._other_sheet).grid(row=0, column=2, sticky="e")
        if len(s["names"]) > 1:
            ttk.Label(top, text="Worksheet", style="PanelField.TLabel").grid(
                row=1, column=0, sticky="w", pady=(10, 0))
            self.sheet_var = tk.StringVar(self, value=s["sheet"])
            box = ttk.Combobox(top, textvariable=self.sheet_var, state="readonly",
                               values=s["names"], width=30)
            box.grid(row=1, column=1, sticky="w", padx=14, pady=(10, 0))
            box.bind("<<ComboboxSelected>>", lambda _e: self._other_worksheet())

        ask = ttk.Frame(self.body)
        ask.pack(fill="x", pady=(14, 0))
        ttk.Label(ask, text="What is each row of this sheet?", style="Field.TLabel").grid(
            row=0, column=0, sticky="w")
        self.singular_var = tk.StringVar(self, value=s["singular"])
        self.singular_entry = ttk.Entry(ask, textvariable=self.singular_var, width=26)
        self.singular_entry.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.singular_help = ttk.Label(ask, text="", style="Help.TLabel")
        self.singular_help.grid(row=1, column=1, sticky="w", padx=(12, 0))

        def typed(*_a):
            s["singular"] = self.singular_var.get().strip()
            if not self._guard:
                s["singular_typed"] = True
            word = s["singular"]
            self.singular_help.configure(
                text=f"One word is best. The list will be called “{bpm.plural_of(word)}”."
                if word else "One word is best, for example Customer, Member or Order.")
        self.singular_var.trace_add("write", typed)
        self._guard = True
        typed()
        self._guard = False

        ttk.Label(self.body, text="What was understood", style="H3.TLabel").pack(
            anchor="w", pady=(16, 6))
        frame, tree = widgets.make_tree(self.body, ("col", "kind", "eg"), height=5)
        frame.pack(fill="both", expand=True)
        tree.heading("col", text="Column in your sheet", anchor="w")
        tree.heading("kind", text="Will be kept as", anchor="w")
        tree.heading("eg", text="From the first rows", anchor="w")
        tree.column("col", width=fs(200), stretch=False)
        tree.column("kind", width=fs(230), stretch=False)
        tree.column("eg", width=fs(300), stretch=True)
        tree.configure(selectmode="none")
        self.sheet_tree = tree
        for i, head in enumerate(s["headers"]):
            f = bpm.get_field(t, s["mapping"].get(i, "")) if t else None
            if f is None:
                continue
            kind = validate.kind_label(f["kind"])
            if f["kind"] == "choice":
                kind += f" – {len(validate.options_of(f))} options"
            if f["key"] in (t.get("title") or []):
                kind += "  ·  names the record"
            egs = []
            for r in s["rows"][:40]:
                v = " ".join(r[i].split()) if i < len(r) else ""
                if v and v not in egs:
                    v = v if len(v) <= 28 else v[:27] + "…"
                    if egs and sum(len(e) + 7 for e in egs) + len(v) > 58:
                        break               # no room for another without it being cut off
                    egs.append(v)
                if len(egs) == 3:
                    break
            tree.insert("", "end", values=(head, kind, "   ·   ".join(egs)))
        self.singular_entry.focus_set()
        self.singular_entry.select_range(0, "end")

    def _other_sheet(self):
        if self.choose_sheet():
            self._show()

    def _other_worksheet(self):
        s = self.d["sheet"]
        if self.sheet_var.get() != s["sheet"]:
            if self._load_sheet(s["path"], self.sheet_var.get()):
                self._show()
            else:
                self.sheet_var.set(s["sheet"])

    def _leave_sheet(self) -> bool:
        d, s = self.d, self.d["sheet"]
        word = re.sub(r"\s+", " ", s["singular"]).strip()
        if not word:
            self._say("Type what each row is – for example Customer.")
            self.singular_entry.focus_set()
            return False
        if d["bp_from"] != ("sheet", s["path"], s["sheet"]):
            self._build_sheet_design()
        t = bpm.get_type(d["bp"], s["type_key"])
        if t is not None and t["name"] != word:
            was_auto = t.get("_auto") or t["plural"] == bpm.plural_of(t["name"])
            t["name"] = word
            if was_auto:
                t["plural"] = bpm.plural_of(word)
        return True

    # =============================================================== step 2
    def _step_lists(self, focus_last: bool = False):
        app, d = self.app, self.d
        self._head("Which lists do you want?",
                   "Each of these gets its own list in the sidebar. Untick any you do not need, "
                   "rename them to suit you, or add your own.")
        sf = widgets.ScrollFrame(self.body, app)
        sf.pack(fill="both", expand=True)
        card = widgets.card(sf.body, app, padding=18)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1, uniform="names")
        card.columnconfigure(2, weight=1, uniform="names")
        ttk.Label(card, text="One of them is called", style="PanelHelp.TLabel").grid(
            row=0, column=1, sticky="w", padx=(6, 8))
        ttk.Label(card, text="The list is called", style="PanelHelp.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8))
        self.list_rows = []
        for i, t in enumerate(self._types()):
            self._list_row(card, i + 1, t)
        widgets.link(card, "+ Add another list", self.add_list, style="PanelLink.TLabel").grid(
            row=len(self._types()) + 1, column=1, sticky="w", padx=(6, 0), pady=(12, 0))
        self.lists_note = ttk.Label(sf.body, text="", style="Help.TLabel", justify="left",
                                    wraplength=fs(10) * 84)
        self.lists_note.pack(anchor="w", pady=(10, 0))
        self._lists_note()
        if focus_last and self.list_rows:
            self.list_rows[-1]["singular"].focus_set()
            self.after_idle(lambda: sf.show(self.list_rows[-1]["singular"]))

    def _list_row(self, card, row: int, t: dict):
        keep = tk.BooleanVar(self, value=not t.get("_off"))
        s_var = tk.StringVar(self, value=t["name"])
        p_var = tk.StringVar(self, value=t["plural"])
        tick = ttk.Checkbutton(card, variable=keep, style="Panel.TCheckbutton")
        tick.grid(row=row, column=0, sticky="w", pady=(8, 0))
        s_entry = ttk.Entry(card, textvariable=s_var)
        s_entry.grid(row=row, column=1, sticky="ew", padx=(6, 8), pady=(8, 0))
        p_entry = ttk.Entry(card, textvariable=p_var)
        p_entry.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=(8, 0))
        info = ttk.Label(card, text="", style="PanelHelp.TLabel", width=12)
        info.grid(row=row, column=3, sticky="w", pady=(8, 0))
        item = {"type": t, "keep": keep, "s": s_var, "p": p_var, "singular": s_entry,
                "plural": p_entry, "tick": tick, "info": info}
        self.list_rows.append(item)

        def paint():
            on = keep.get()
            for e in (s_entry, p_entry):
                e.configure(state="normal" if on else "disabled")
            info.configure(text=count_text(len([f for f in t["fields"] if not f.get("_off")]),
                                           "field") if on else "left out")

        def toggled():
            if keep.get():
                t.pop("_off", None)
            else:
                t["_off"] = True
            paint()
            self._say("")
            self._lists_note()

        def singular_typed(*_a):
            t["name"] = s_var.get()
            if t.get("_auto"):
                self._guard = True
                p_var.set(bpm.plural_of(s_var.get().strip()))
                self._guard = False

        def plural_typed(*_a):
            t["plural"] = p_var.get()
            if not self._guard:
                text = p_var.get().strip()
                t["_auto"] = text == "" or text == bpm.plural_of(t["name"].strip())

        tick.configure(command=toggled)
        s_var.trace_add("write", singular_typed)
        p_var.trace_add("write", plural_typed)
        paint()

    def _lists_note(self):
        """Say which linking fields go when a list they point at is dropped."""
        kept = self._kept_types()
        lines = []
        for t in self._types():
            if not t.get("_off"):
                continue
            holders = [k["plural"] or k["name"] for k in kept
                       if any(f["kind"] == "link" and f.get("link_type") == t["key"]
                              and not f.get("_off") for f in k["fields"])]
            if holders:
                name = t["plural"] or t["name"]
                lines.append(f"Without {name}, the field that links to it is left out of "
                             + " and ".join(holders) + " too. Tick it again to bring that back.")
        self.lists_note.configure(text="\n".join(lines))

    def add_list(self):
        bp = self.d["bp"]
        t = bpm.new_type("New list", bp)
        t["name"] = t["plural"] = ""
        t["_new"] = t["_auto"] = True
        bp["types"].append(t)
        for w in self.body.winfo_children():
            w.destroy()
        self._step_lists(focus_last=True)

    def _leave_lists(self) -> bool:
        types = self._types()
        blank = [t for t in types if t.get("_new") and not t["name"].strip()
                 and not t["plural"].strip()]
        if blank and len(blank) < len(types):
            for t in blank:               # an added row that was never filled in
                types.remove(t)
        kept = self._kept_types()
        if not kept:
            self._say("Keep at least one list – tick one of them.")
            return False
        seen: dict[str, str] = {}
        shown: dict[str, str] = {}
        for t in kept:
            name = re.sub(r"\s+", " ", t["name"]).strip()
            if not name:
                self._say("Give every list a name, or untick the ones you do not want.")
                self._focus_list(t)
                return False
            t["name"] = name
            t["plural"] = re.sub(r"\s+", " ", t["plural"]).strip() or bpm.plural_of(name)
            if len(name) > 60 or len(t["plural"]) > 60:
                self._say("That name is too long – keep it under 60 characters.")
                self._focus_list(t)
                return False
            if name.lower() in seen:
                self._say(f"Two lists are called “{name}”. Give each its own name.")
                self._focus_list(t)
                return False
            if t["plural"].lower() in shown:
                self._say(f"Two lists would both show as “{t['plural']}” in the sidebar. "
                          "Change one of them.")
                self._focus_list(t)
                return False
            seen[name.lower()] = shown[t["plural"].lower()] = t["key"]
        return True

    def _focus_list(self, t: dict):
        for item in getattr(self, "list_rows", []):
            if item["type"] is t and item["singular"].winfo_exists():
                item["singular"].focus_set()

    # =============================================================== step 3
    def _step_fields(self, show_key: str | None = None):
        app, d = self.app, self.d
        kept = self._kept_types()
        if d["cur"] not in [t["key"] for t in kept]:
            d["cur"] = kept[0]["key"]
        t = bpm.get_type(d["bp"], d["cur"])
        self._head("What do you record about each one?",
                   "These are good to start with – you can change anything later. Untick what you "
                   "do not need, click a field to change it, or add your own.")
        body = self.body
        body.columnconfigure(0, weight=11, uniform="fields")
        body.columnconfigure(1, weight=9, uniform="fields")
        body.rowconfigure(1, weight=1)
        if len(kept) > 1:
            seg = ttk.Frame(body)
            seg.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            buttons = [ttk.Label(seg, text="Fields for", style="Help.TLabel")]
            for k in kept:
                b = ttk.Button(seg, text=k["plural"],
                               style="SegOn.TButton" if k["key"] == d["cur"] else "Seg.TButton",
                               command=lambda key=k["key"]: self.show_list(key))
                buttons.append(b)
            fieldedit.flow(seg, buttons, gap=4)

        left = widgets.card(body, app, padding=0)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        head = ttk.Frame(left, style="Panel.TFrame", padding=(14, 12, 14, 6))
        head.pack(fill="x")
        self.fields_count = ttk.Label(head, text="", style="PanelHelp.TLabel")
        self.fields_count.pack(side="right", anchor="n", pady=(2, 0))
        title = ttk.Label(head, text=f"Each {t['name'].lower()} has", style="PanelH3.TLabel",
                          justify="left")
        title.pack(side="left")
        head.bind("<Configure>", lambda e: title.configure(
            wraplength=max(120, e.width - self.fields_count.winfo_reqwidth() - 44)))
        add = ttk.Frame(left, style="Panel.TFrame", padding=(14, 8, 14, 8))
        add.pack(side="bottom", fill="x")
        ttk.Frame(left, style="Border.TFrame", height=1).pack(side="bottom", fill="x")
        ttk.Label(add, text="Add a field", style="PanelField.TLabel").pack(anchor="w", pady=(0, 5))
        chips = ttk.Frame(add, style="Panel.TFrame")
        chips.pack(fill="x")
        items = []
        self.suggest_buttons: dict[str, ttk.Button] = {}
        for s in fieldedit.suggestions(t, limit=7):
            b = ttk.Button(chips, text="+ " + s["name"], style="Small.TButton", takefocus=0,
                           command=lambda s=s: self.add_suggested(s))
            self.suggest_buttons[s["name"]] = b
            items.append(b)
        self.other_button = ttk.Button(chips, text="Something else…", style="Small.TButton",
                                       command=self.add_other)
        items.append(self.other_button)
        fieldedit.flow(chips, items)

        sf = widgets.ScrollFrame(left, app, panel=True)
        sf.pack(fill="both", expand=True, padx=(14, 4), pady=(0, 6))
        add.lift()                         # Tab reaches the fields before "Add a field"
        rows = sf.body
        rows.columnconfigure(1, weight=1)
        self.field_rows = []
        kept_keys = {x["key"] for x in kept}
        target = None
        n = 0
        for f in t["fields"]:
            if f["kind"] == "link" and f.get("link_type") not in kept_keys:
                continue
            var = tk.BooleanVar(self, value=not f.get("_off"))
            tick = ttk.Checkbutton(rows, variable=var, style="Panel.TCheckbutton",
                                   command=lambda f=f, var=var: self._toggle_field(f, var.get()))
            tick.grid(row=n, column=0, sticky="w", pady=3)
            name = ttk.Label(rows, text=f["name"] + ("  *" if f.get("required") else ""),
                             style="Panel.TLabel" if not f.get("_off") else "PanelDim.TLabel",
                             cursor="hand2")
            name.grid(row=n, column=1, sticky="w", padx=(2, 8))
            # the kind doubles as the keyboard way in: Tab to it, Enter to change the field
            kind = widgets.link(rows, fieldedit.kind_text(f, d["bp"]),
                                lambda f=f: self.edit_field(f), style="PanelHelp.TLabel")
            kind.grid(row=n, column=2, sticky="e", padx=(0, 10))
            name.bind("<Button-1>", lambda _e, f=f: self.edit_field(f))
            self.field_rows.append({"field": f, "var": var, "tick": tick, "name": name})
            if f["key"] == show_key:
                target = name
            n += 1
        self._count_fields(t)

        right = widgets.card(body, app, padding=0)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(right, text="This is how the form will look", style="PanelHelp.TLabel").pack(
            anchor="w", padx=14, pady=(12, 2))
        self.preview_frame = widgets.ScrollFrame(right, app, panel=True)
        self.preview_frame.pack(fill="both", expand=True, padx=(14, 4), pady=(0, 10))
        self._draw_preview()
        if target is not None:
            self.after_idle(lambda: target.winfo_exists() and sf.show(target))

    def _count_fields(self, t: dict):
        n = len(self._live_fields(t))
        self.fields_count.configure(text=count_text(n, "field") + " kept" + ("   * must be filled in"
                                    if any(f.get("required") for f in self._live_fields(t)) else ""))

    def _draw_preview(self):
        t = bpm.get_type(self.d["bp"], self.d["cur"])
        holder = self.preview_frame.body
        for w in holder.winfo_children():
            w.destroy()
        form = widgets.Form(holder, self.app, self._effective(t), columns=1, preview=True)
        form.pack(fill="x", padx=(0, 10), pady=(0, 8))
        self.preview_form = form

    def _redraw_fields(self, show_key: str | None = None):
        for w in self.body.winfo_children():
            w.destroy()
        self._step_fields(show_key)

    def show_list(self, key: str):
        self.d["cur"] = key
        self._say("")
        self._redraw_fields()

    def _toggle_field(self, f: dict, on: bool):
        if on:
            f.pop("_off", None)
        else:
            f["_off"] = True
        self._say("")
        for item in self.field_rows:
            if item["field"] is f:
                item["name"].configure(style="Panel.TLabel" if on else "PanelDim.TLabel")
        t = bpm.get_type(self.d["bp"], self.d["cur"])
        self._count_fields(t)
        self._draw_preview()

    def _dialog_args(self, t: dict, f: dict | None) -> dict:
        kept = self._kept_types()
        kept_keys = {x["key"] for x in kept}
        return {
            "simple": True,
            "taken": [x["name"] for x in t["fields"] if x is not f
                      and not (x["kind"] == "link" and x.get("link_type") not in kept_keys)],
            "targets": [(x["key"], x["plural"] or x["name"]) for x in kept],
        }

    def edit_field(self, f: dict):
        t = bpm.get_type(self.d["bp"], self.d["cur"])
        out = fieldedit.FieldDialog(self.app, f, **self._dialog_args(t, f)).show()
        if not isinstance(out, dict):
            return
        out.pop("_off", None)             # changing a field means you want it
        t["fields"][t["fields"].index(f)] = out
        self._redraw_fields(out["key"])

    def _append_field(self, t: dict, f: dict):
        keys = [x["key"] for x in t["fields"]]
        f["key"] = bpm.slug(f["name"], keys)
        f["_new"] = True
        t["fields"].append(f)
        self._say("")
        self._redraw_fields(f["key"])

    def add_suggested(self, s: dict):
        t = bpm.get_type(self.d["bp"], self.d["cur"])
        self._append_field(t, copy.deepcopy(s))

    def add_other(self):
        t = bpm.get_type(self.d["bp"], self.d["cur"])
        out = fieldedit.FieldDialog(self.app, {"name": "", "kind": "text"}, new=True,
                                    **self._dialog_args(t, None)).show()
        if isinstance(out, dict):
            self._append_field(t, out)

    def _leave_fields(self) -> bool:
        for t in self._kept_types():
            if not self._live_fields(t):
                self.d["cur"] = t["key"]
                self._redraw_fields()
                self._say(f"{t['plural']} has nothing ticked. Keep at least one field, or go "
                          "back and untick the whole list.")
                return False
        return self._design_ok()

    def _design_ok(self) -> bool:
        """Check the design as a whole; on a problem go to the step that fixes it."""
        bp, _map = self.final_design()
        found = bpm.problems(bp)
        if not found:
            return True
        about_lists = found[0].startswith("Two lists")
        step = 1 if about_lists else 2
        if not about_lists:
            for t in self._kept_types():
                if f"in {t['name']} " in found[0] or f"“{t['name']}” has" in found[0]:
                    self.d["cur"] = t["key"]
        self.d["step"] = step
        self._show()
        self._say(found[0])
        return False

    # =============================================================== step 4
    def _step_people(self):
        app, d = self.app, self.d
        self._head("Who will use it?", "You can change this later, from the Setup menu.")
        sf = widgets.ScrollFrame(self.body, app)
        sf.pack(fill="both", expand=True)
        cards = ttk.Frame(sf.body)
        cards.pack(fill="x")
        cards.columnconfigure(0, weight=1, uniform="who")
        cards.columnconfigure(1, weight=1, uniform="who")
        self.who_cards = {
            "solo": SelectCard(cards, app, "Just me",
                               "Opens straight away. You can add a password if you want one.",
                               command=lambda: self.set_who("solo"), on_double=self.next),
            "team": SelectCard(cards, app, "Me and other people",
                               "Everyone signs in with their own username and password.",
                               command=lambda: self.set_who("team"), on_double=self.next),
        }
        self.who_cards["solo"].grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.who_cards["team"].grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.people_box = ttk.Frame(sf.body)
        self.people_box.pack(fill="x", pady=(10, 0))
        self._people_details()

    def set_who(self, who: str):
        if self.d["who"] != who:
            self.d["who"] = who
            self._say("")
            self._people_details()
        else:
            self._paint_who()

    def _paint_who(self):
        for key, card in self.who_cards.items():
            card.set_selected(key == self.d["who"])

    def _bound_entry(self, parent, key: str, label: str, row: int, column: int, secret=False,
                     on_type=None):
        d = self.d
        cell = ttk.Frame(parent, style="Panel.TFrame")
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0),
                  pady=(0, 10))
        ttk.Label(cell, text=label, style="PanelField.TLabel").pack(anchor="w")
        var = tk.StringVar(self, value=d[key])
        entry = ttk.Entry(cell, textvariable=var, show="•" if secret else "")
        entry.pack(fill="x", pady=(3, 0))

        def typed(*_a):
            d[key] = var.get()
            if not self._guard:
                self._say("")
            if on_type:
                on_type()
        var.trace_add("write", typed)
        self.people_vars[key] = var
        self.people_entries[key] = entry
        return entry

    def _people_details(self):
        app, d = self.app, self.d
        self._paint_who()
        for w in self.people_box.winfo_children():
            w.destroy()
        self.people_vars: dict[str, tk.StringVar] = {}
        self.people_entries: dict[str, ttk.Entry] = {}
        card = widgets.card(self.people_box, app, padding=16)
        card.pack(fill="x")
        card.columnconfigure(0, weight=1, uniform="pp")
        card.columnconfigure(1, weight=1, uniform="pp")
        if d["who"] == "team":
            def name_typed():
                if d["user_auto"]:
                    first = plain_letters((d["person"].strip().split() or [""])[0])
                    self._guard = True
                    self.people_vars["username"].set(first)
                    self._guard = False

            def user_typed():
                if not self._guard:
                    d["user_auto"] = d["username"].strip() == ""

            self._bound_entry(card, "person", "Your name", 0, 0, on_type=name_typed)
            self._bound_entry(card, "username", "Username (what you type to sign in)", 0, 1,
                              on_type=user_typed)
            self._bound_entry(card, "pw", "Password", 1, 0, secret=True, on_type=self._paint_encrypt)
            self._bound_entry(card, "pw2", "Password again", 1, 1, secret=True)
            note = "You can add the others afterwards in Setup > People and passwords."
        else:
            self._bound_entry(card, "pw", "Password (optional)", 0, 0, secret=True,
                              on_type=self._paint_encrypt)
            self._bound_entry(card, "pw2", "Password again", 0, 1, secret=True)
            note = "Leave both empty to open without a password."
        ttk.Label(card, text=note, style="PanelHelp.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Frame(card, style="Border.TFrame", height=1).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        self.encrypt_var = tk.BooleanVar(self, value=bool(d["encrypt"]))
        self.encrypt_tick = ttk.Checkbutton(
            card, text="Encrypt the file", variable=self.encrypt_var, style="Panel.TCheckbutton",
            command=lambda: d.__setitem__("encrypt", bool(self.encrypt_var.get())))
        self.encrypt_tick.grid(row=4, column=0, sticky="w")
        self.encrypt_why = ttk.Label(card, text="", style="PanelHelp.TLabel")
        self.encrypt_why.grid(row=4, column=1, sticky="w")
        guide = ttk.Label(
            card, style="PanelHelp.TLabel", justify="left", wraplength=fs(10) * 84,
            text="Without encryption, a password stops someone casually opening the CRM in this "
                 "app, but the file itself can still be read by other software. With encryption "
                 "it cannot – and a forgotten password then needs the recovery code you will "
                 "be shown.")
        guide.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        card.bind("<Configure>", lambda e: guide.configure(wraplength=max(200, e.width - 40)))
        self._paint_encrypt()

    def _encrypt_possible(self) -> tuple[bool, str]:
        if dbm.sqlcipher_module() is None:
            return False, "Not available in this copy of the app."
        if not self.d["pw"]:
            return False, "Set a password first – encryption needs one."
        return True, ""

    def _paint_encrypt(self):
        ok, why = self._encrypt_possible()
        self.encrypt_tick.configure(state="normal" if ok else "disabled")
        self.encrypt_why.configure(text=why)
        if not ok and self.encrypt_var.get():
            self.encrypt_var.set(False)
            self.d["encrypt"] = False

    def _leave_people(self) -> bool:
        d = self.d

        def stop(text, key):
            self._say(text)
            entry = getattr(self, "people_entries", {}).get(key)
            if entry is not None and entry.winfo_exists():
                entry.focus_set()
            return False

        username = "me"
        if d["who"] == "team":
            d["person"] = re.sub(r"\s+", " ", d["person"]).strip()
            username = d["username"].strip()
            if not d["person"]:
                return stop("Type your name, so the others can see who changed what.", "person")
            if not username:
                return stop("Choose a username – a short name you will sign in with.", "username")
            if re.search(r"\s", username):
                return stop("A username cannot contain spaces.", "username")
            if not d["pw"]:
                return stop("Choose a password. When several people share a CRM, everyone "
                            "signs in with one.", "pw")
        if d["pw"] or d["pw2"]:
            if d["pw"] != d["pw2"]:
                return stop("The two passwords are not the same. Type them again.", "pw2")
            problem = security.password_problem(d["pw"], username)
            if problem:
                lead = "" if "password" in problem.lower() else "Choose a different password. "
                return stop(lead + problem, "pw")
        if d["encrypt"] and not self._encrypt_possible()[0]:
            d["encrypt"] = False
        return True

    # =============================================================== step 5
    def _step_name(self):
        app, d = self.app, self.d
        if d["name_auto"] or not d["name"].strip():
            d["name"] = self._default_name()
        self._head("Name it and choose where to keep it",
                   "Everything is kept in one file on this computer or a shared drive. Nothing "
                   "is sent anywhere.")
        body = self.body
        body.columnconfigure(0, weight=11, uniform="name")
        body.columnconfigure(1, weight=9, uniform="name")
        body.rowconfigure(0, weight=1)
        sf = widgets.ScrollFrame(body, app)
        sf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left = widgets.card(sf.body, app, padding=18)
        left.pack(fill="x")
        ttk.Label(left, text="What should it be called?", style="PanelField.TLabel").pack(anchor="w")
        self.name_var = tk.StringVar(self, value=d["name"])
        self.name_entry = ttk.Entry(left, textvariable=self.name_var)
        self.name_entry.pack(fill="x", pady=(3, 0))
        ttk.Label(left, text="Where should it be kept?", style="PanelField.TLabel").pack(
            anchor="w", pady=(14, 0))
        row = ttk.Frame(left, style="Panel.TFrame")
        row.pack(fill="x", pady=(3, 0))
        self.change_btn = ttk.Button(row, text="Change…", command=self.change_folder)
        self.change_btn.pack(side="right", padx=(8, 0))
        self.folder_label = ttk.Label(row, text="", style="Panel.TLabel", justify="left")
        self.folder_label.pack(side="left", fill="x", expand=True)
        self.file_label = ttk.Label(left, text="", style="PanelHelp.TLabel", justify="left")
        self.file_label.pack(anchor="w", pady=(6, 0))
        self.sync_label = ttk.Label(left, text="", style="PanelWarn.TLabel", justify="left")
        self.examples_var = tk.BooleanVar(self, value=bool(d["examples"]))
        self.examples_tick = None
        if self._has_examples():
            ttk.Frame(left, style="Border.TFrame", height=1).pack(fill="x", pady=(16, 12), side="top")
            self.examples_tick = ttk.Checkbutton(
                left, variable=self.examples_var, style="Panel.TCheckbutton",
                text="Add a few example records so I can see how it works",
                command=self._examples_toggled)
            self.examples_tick.pack(anchor="w")
            ttk.Label(left, text="They are marked as examples and can be removed in one click.",
                      style="PanelHelp.TLabel").pack(anchor="w", padx=(fs(10) * 2 + 3, 0))
        self._wrap_left = [self.folder_label, self.file_label, self.sync_label]
        left.bind("<Configure>", self._wrap_name_step)

        right = widgets.card(body, app, padding=18)
        right.grid(row=0, column=1, sticky="new", padx=(8, 0))
        ttk.Label(right, text="What you will get", style="PanelH3.TLabel").pack(anchor="w")
        self.summary = ttk.Frame(right, style="Panel.TFrame")
        self.summary.pack(fill="both", expand=True, pady=(8, 0))
        right.bind("<Configure>", lambda e: self._wrap_summary(e.width))
        self._summary_width = fs(10) * 32

        def typed(*_a):
            d["name"] = self.name_var.get()
            if not self._guard:
                d["name_auto"] = False
                self._say("")
            self._paint_place()
        self.name_var.trace_add("write", typed)
        self._paint_place()
        self._draw_summary()
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    def _has_examples(self) -> bool:
        return bool(templates.SAMPLES.get(self.d["start"] or ""))

    def _examples_toggled(self):
        self.d["examples"] = bool(self.examples_var.get())
        self._draw_summary()

    def _wrap_name_step(self, event):
        beside = self.change_btn.winfo_reqwidth() + 12
        for lab in self._wrap_left:
            lab.configure(wraplength=max(160, event.width - 44 - (
                beside if lab is self.folder_label else 0)))

    def target_path(self) -> tuple[str, bool]:
        """Where the file will be saved, and whether the first-choice name was taken."""
        d = self.d
        base = safe_file_name(d["name"])
        path = os.path.join(d["folder"], base + FILE_EXT)
        n = 1
        while os.path.exists(path) or os.path.exists(path + ".keys"):
            n += 1
            path = os.path.join(d["folder"], f"{base} {n}{FILE_EXT}")
        return path, n > 1

    def _paint_place(self):
        d = self.d
        self.folder_label.configure(text=d["folder"])
        path, clash = self.target_path()
        name = os.path.basename(path)
        if clash:
            first = safe_file_name(d["name"]) + FILE_EXT
            self.file_label.configure(
                style="PanelWarn.TLabel",
                text=f"There is already a “{first}” in that folder, so this one will be saved "
                     f"as “{name}”. Change the name above if you would rather.")
        else:
            self.file_label.configure(style="PanelHelp.TLabel", text=f"It will be saved as “{name}”.")
        if paths.looks_synced(d["folder"]):
            self.sync_label.configure(
                text="This looks like a folder that is synced to the cloud (OneDrive, Dropbox, "
                     "iCloud and the like). Syncing can damage a CRM file while it is in use. "
                     "A normal folder or a shared network drive is safer – but you can carry on.")
            self.sync_label.pack(anchor="w", pady=(8, 0), after=self.file_label)
        else:
            self.sync_label.pack_forget()

    def change_folder(self):
        folder = widgets.ask_folder(self.app, "Choose where to keep your CRM",
                                    initialdir=self.d["folder"])
        if folder:
            self.d["folder"] = os.path.abspath(folder)
            self._say("")
            self._paint_place()

    def _wrap_summary(self, width: int):
        self._summary_width = max(160, width - 60)
        for lab in getattr(self, "_summary_labels", []):
            if lab.winfo_exists():
                lab.configure(wraplength=self._summary_width)

    def _draw_summary(self):
        d, c = self.d, self.c
        for w in self.summary.winfo_children():
            w.destroy()
        self._summary_labels = []

        def line(text, style="Panel.TLabel", pady=(0, 0), colour=None):
            row = ttk.Frame(self.summary, style="Panel.TFrame")
            row.pack(fill="x", pady=pady)
            if colour:
                tk.Frame(row, bg=colour, width=4, height=fs(10) + 4).pack(side="left", padx=(0, 8))
            lab = ttk.Label(row, text=text, style=style, justify="left",
                            wraplength=self._summary_width)
            lab.pack(side="left", anchor="w")
            self._summary_labels.append(lab)

        kept = self._kept_types()
        line(count_text(len(kept), "list") + " in the sidebar", "PanelHelp.TLabel", pady=(0, 4))
        for t in kept:
            line(f"{t['plural']}  –  " + count_text(len(self._live_fields(t)), "field"),
                 colour=t.get("color"), pady=(0, 3))
        s = d["sheet"] if d["start"] == "sheet" else None
        if s and s["rows"] and bpm.get_type(d["bp"], s["type_key"]) in kept:
            line(count_text(len(s["rows"]), "row") + " brought in from "
                 + os.path.basename(s["path"]), pady=(10, 0))
        if self._has_examples() and d["examples"]:
            line("A few example records to try things on", pady=(10, 0))
        if d["who"] == "team":
            who = f"For {d['person'] or 'you'} and the people you add. Everyone signs in."
        elif d["pw"]:
            who = "Just for you, opened with your password."
        else:
            who = "Just for you. Opens straight away, no password."
        line(who, pady=(10, 0))
        if d["encrypt"] and d["pw"]:
            line("The file is encrypted. You will be shown a recovery code to keep safe.",
                 pady=(10, 0))

    # ------------------------------------------------------------- create
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        try:
            self.status.configure(text=text)
            for b in (self.next_btn, self.back_btn, self.cancel_btn):
                b.configure(state="disabled" if busy else "normal")
            self.update_idletasks()
        except tk.TclError:
            pass

    def _create(self) -> bool:
        app, d = self.app, self.d
        d["name"] = re.sub(r"\s+", " ", d["name"]).strip()
        if not d["name"]:
            self._say("Give your CRM a name.")
            self.name_entry.focus_set()
            return False
        if not os.path.isdir(d["folder"]):
            self._say("That folder cannot be found. Press Change… and choose another.")
            return False
        if not _can_write(d["folder"]):
            self._say("Files cannot be saved in that folder. Press Change… and choose another.")
            return False
        # every earlier step is checked again: the design must be sound before anything is made
        if not self._leave_people():
            self.d["step"] = 3
            message = self.message.cget("text")
            self._show()
            self._say(message)
            return False
        if not self._design_ok():
            return False
        bp, keymap = self.final_design()
        s = d["sheet"] if d["start"] == "sheet" else None
        type_key = keymap.get(s["type_key"]) if s else None
        importing_rows = bool(s and type_key and s["rows"])
        # A field added here and marked "must be filled in" has no column in the sheet, so
        # every row would be turned away. Bring the rows in first, then switch the rule on.
        late_required: list[str] = []
        if importing_rows:
            fed = set(s["mapping"].values())
            for f in bpm.get_type(bp, type_key)["fields"]:
                if f.get("required") and f["key"] not in fed and bpm.default_value(f, 1) is None:
                    late_required.append(f["key"])
                    f.pop("required")
        path, _clash = self.target_path()
        team = d["who"] == "team"
        password = d["pw"] or None
        encrypt = bool(d["encrypt"] and password and self._encrypt_possible()[0])
        self._set_busy(True, "Creating your CRM…")
        try:
            db, code = dbm.Database.create(
                path, bp, mode="team" if team else "solo",
                name=d["person"] if team else "Me",
                username=d["username"].strip() if team else "me",
                password=password, encrypt=encrypt)
        except dbm.DBError as exc:
            self._set_busy(False)
            self._say(str(exc))
            return False
        except (OSError, sqlite3.Error) as exc:
            self._set_busy(False)
            self._say(f"The file could not be created in that folder ({exc}). Press Change… "
                      "and choose another.")
            return False
        except Exception:
            # Something nobody planned for. Nothing was made (create() clears up after
            # itself) and every choice is still here, so the person can try again.
            self._set_busy(False)
            self._say("Your CRM could not be created, and nothing was saved. Your choices are "
                      "still here – try again, or press Change… and choose another folder.")
            raise

        # From here on the file exists. Whatever goes wrong with the extras, the
        # person must still be shown the recovery code and let in to their CRM.
        notes: list[str] = []
        report = None
        try:
            if self._has_examples() and d["examples"]:
                try:
                    templates.load_samples(db, d["start"])
                except dbm.DBError as exc:
                    notes.append(f"The example records could not be added: {exc}")
            if importing_rows:
                report = self._import_rows(db, type_key, s)
                if late_required:
                    try:
                        now = copy.deepcopy(db.blueprint)
                        for f in bpm.get_type(now, type_key)["fields"]:
                            if f["key"] in late_required:
                                f["required"] = True
                        db.save_blueprint(now, note="Created")
                    except dbm.DBError as exc:
                        notes.append(str(exc))
        except Exception:
            self._log_unexpected()
            notes.append("Your CRM was created, but something went wrong while it was being "
                         "filled in, so it may be missing some of its first records.")
        self._finished = True
        self._set_busy(False)
        if code:
            from .welcome import show_recovery_code
            show_recovery_code(app, code, os.path.basename(path))
        app.enter(db, first_time=True)
        # this screen no longer exists from here on
        if report is not None:
            show_import_report(app, report)
        for text in notes:
            app.toast(text, "bad")
        return True

    def _log_unexpected(self):
        """Hand the error being handled to the shell (it logs it and tells the person)."""
        try:
            self.app.root.report_callback_exception(*sys.exc_info())
        except Exception:
            pass

    def _import_rows(self, db, type_key: str, s: dict) -> dict:
        """Bring the spreadsheet's rows into the new CRM. Returns a small report."""
        t = bpm.get_type(db.blueprint, type_key)
        report = {"file": os.path.basename(s["path"]), "list": t["plural"], "one": t["name"],
                  "added": 0, "problems": [], "error": ""}

        def progress(i, n):
            if n > 400:
                self.status.configure(text=f"Bringing in your rows… {i:,} of {n:,}")
                self.update_idletasks()

        try:
            prepared = importing.prepare(db, type_key, s["rows"], s["mapping"],
                                         progress=progress, row_numbers=s["numbers"])
            done = importing.run(db, type_key, prepared, progress=progress)
        except dbm.DBError as exc:
            report["error"] = str(exc)
            return report
        except Exception:
            # the import is all or nothing, so no rows came in
            self._log_unexpected()
            report["error"] = "Something unexpected went wrong while the rows were being read."
            return report
        report["added"] = done["added"]
        fields = {f["key"]: f["name"] for f in t["fields"]}
        for row in prepared["rows"]:
            if row["action"] == "skip":
                why = " ".join(f"{fields.get(k, k)}: {v}" for k, v in row["errors"].items())
                report["problems"].append((row["n"], why))
        return report


def import_report_text(report: dict) -> tuple[str, str]:
    """(headline, detail) describing what came in from the spreadsheet."""
    n, bad = report["added"], report["problems"]
    what = report["one"].lower() if n == 1 else report["list"].lower()
    if report["error"]:
        return ("Your CRM is ready, but the rows were not brought in",
                f"{report['error']}\n\nYou can bring them in later with Tools > Import from a "
                "spreadsheet.")
    head = f"{n:,} {what} brought in from {report['file']}"
    if not bad:
        return head, ""
    rows = count_text(len(bad), "row")
    detail = (f"{rows} had a problem and {'was' if len(bad) == 1 else 'were'} left out, so "
              "nothing wrong got in. Correct the sheet and use Tools > Import from a spreadsheet "
              "to bring " + ("it" if len(bad) == 1 else "them") + " in later.")
    return head, detail


def show_import_report(app, report: dict) -> None:
    """Tell the person how the spreadsheet import went (after the CRM has opened)."""
    head, detail = import_report_text(report)
    bad = report["problems"]
    if not bad and not report["error"]:
        app.toast(head, "good")
        return
    lines = [f"Row {n}: {why}" for n, why in bad[:8]]
    if len(bad) > 8:
        lines.append(f"… and {len(bad) - 8} more.")
    if app.testing:
        app.test_log.append(("info", head, detail + "\n" + "\n".join(lines)))
        return
    d = widgets.Dialog(app, "Your spreadsheet", width=fs(10) * 54)
    ttk.Label(d.body, text=head, style="H2.TLabel", wraplength=fs(10) * 50,
              justify="left").pack(anchor="w")
    ttk.Label(d.body, text=detail, wraplength=fs(10) * 50, justify="left").pack(
        anchor="w", pady=(8, 0))
    if lines:
        # long reasons wrap, so the box is as tall as the wrapped text (within reason)
        # and gets a scrollbar when there is more than fits
        tall = sum(1 + len(line) // 70 for line in lines)
        holder = ttk.Frame(d.body)
        holder.pack(fill="x", pady=(12, 0))
        holder.columnconfigure(0, weight=1)
        box = widgets.make_text(holder, app, height=min(11, tall), width=20, readonly=True)
        bar = widgets.AutoScrollbar(holder, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=bar.set)
        box.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        widgets.set_text(box, "\n".join(lines))
    from .welcome import press_on_enter
    b = d.add_button("Carry on", lambda: d.ok(True), accent=True)
    press_on_enter(b)
    d.default_on_enter()
    d.show(focus=b)
