"""Change the design: add, rename, reorder and remove lists and fields.

The page works on a copy of the design. Nothing changes for anyone until
"Save changes" is pressed. Removing a list or a field only hides it (it is
"archived"): what was stored in it is kept and it can be brought back.
"""
from __future__ import annotations

import copy
import json
import re
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import validate
from . import fieldedit, styles, widgets
from .base import Page
from .styles import fs

TITLE = "Change the design"
SUBTITLE = ("Add, rename or remove lists and fields. Nothing you remove is deleted – it is only "
            "hidden, and can be brought back.")
NOTHING = "(nothing)"
NO_BOARD = "No board"
# the short notes at the right of a field's row, and what each means (shown on hover)
MARKS = {"needed": "It must be filled in.",
         "in list": "It is a column in the list.",
         "no repeats": "No two records may have the same value.",
         "reminder": "Home shows this date when it is coming up.",
         "name": "It is part of what each record is called.",
         "board": "Its options are the columns of the board."}


class DesignerPage(Page):
    nav_key = "design"

    def __init__(self, parent, app, type_key: str | None = None, restore: dict | None = None,
                 **_kw):
        super().__init__(parent, app)
        self.ready = False
        if not self.db.is_admin:
            self.header(TITLE)
            box = widgets.card(self, app, padding=30)
            box.pack(anchor="w", padx=24, pady=20)
            ttk.Label(box, text="Only an administrator can change the design",
                      style="PanelH2.TLabel").pack(anchor="w")
            ttk.Label(box, text="Ask the person who looks after this CRM if a list or a field "
                                "needs adding or changing.",
                      style="PanelDim.TLabel").pack(anchor="w", pady=(6, 0))
            return
        self.saved = bpm.clean(self.db.blueprint)
        if restore:
            self.bp = restore["bp"]
            self.base_stamp = restore["stamp"]
        else:
            self.bp = copy.deepcopy(self.saved)
            self.base_stamp = self.db.get_meta("blueprint_at")
        active = [t["key"] for t in bpm.active_types(self.bp)]
        self.sel = type_key if type_key in active else (active[0] if active else None)
        self.sel_field: str | None = (restore or {}).get("field")
        self.tab = (restore or {}).get("tab") or "fields"
        self.problems: list[str] = []
        self._guard = False
        self._drag = None
        self._elements: list[tuple[str, str, tk.Widget]] = []
        self._rows: dict[str, dict] = {}
        self._kind_touched = False
        self._preview_after = None
        self._preview_shown = None
        self._build()
        self.ready = True
        self._draw_lists()
        self._draw_settings()
        self._draw_fields()
        self._draw_preview()
        self._paint_save()
        self.show_tab(self.tab)

    # ------------------------------------------------------------ plumbing
    def state(self) -> dict:
        return {"type_key": getattr(self, "sel", None)}

    def rebuild_state(self) -> dict:
        """Like state(), but unsaved work survives a dark/light switch."""
        kw = self.state()
        if self.ready and self.is_dirty():
            kw["restore"] = {"bp": self.bp, "stamp": self.base_stamp, "field": self.sel_field,
                             "tab": self.tab}
        return kw

    def cur(self) -> dict | None:
        return bpm.get_type(self.bp, self.sel or "")

    def is_dirty(self) -> bool:
        return self.ready and bpm.clean(self.bp) != self.saved

    def saved_field(self, key: str) -> dict | None:
        """The field as it is in the saved design (None if it is new)."""
        t = bpm.get_type(self.saved, self.sel or "")
        return bpm.get_field(t, key) if t else None

    def can_leave(self) -> bool:
        if not self.is_dirty():
            return True
        answer = widgets.choose(
            self.app, "Save your changes to the design?",
            "You have changed the design but not saved it yet.",
            [("save", "Save changes"), ("discard", "Discard them")], cancel="Keep editing")
        if answer == "discard":
            return True
        if answer == "save" and self._save(rebuild=False):
            # the sidebar and menus must show the new design before we move on
            self.app.rebuild()
            self.app.toast("Design saved", "good")
            return True
        return False

    def save(self):
        """Save changes (the button and Ctrl+S)."""
        if self.ready and self.is_dirty():
            self._save()

    def _changed(self, lists=False, settings=False, fields=False, preview=True):
        """Call after any change to the working design."""
        self._show_problems([])
        self._paint_save()
        if lists:
            self._draw_lists()
        if settings:
            self._draw_settings()
        if fields:
            self._draw_fields()
        if preview:
            self._queue_preview()

    def _paint_save(self):
        dirty = self.is_dirty()
        self.save_btn.configure(state="normal" if dirty else "disabled")
        text = "Not saved yet" if dirty else ""
        if self.dirty_label.cget("text") != text:
            self.dirty_label.configure(text=text)
            self._wrap_header()

    # -------------------------------------------------------------- layout
    def _build(self):
        app, c = self.app, self.c
        # the field rows are ttk labels with their own styles: a long list of
        # fields is redrawn after every change, and these are quick to make
        style = ttk.Style(app.root)
        for pre, bg in (("Ds", c["panel"]), ("DsSel", c["sel"])):
            style.configure(pre + "Name.TLabel", background=bg, foreground=c["text"],
                            font=(c["ui"], fs(10)))
            style.configure(pre + "Kind.TLabel", background=bg, foreground=c["dim"],
                            font=(c["ui"], fs(9)))
            style.configure(pre + "Mark.TLabel", background=bg, foreground=c["dim"],
                            font=(c["ui"], fs(8)))
        right = self.header(TITLE, SUBTITLE)
        self.subtitle_label.configure(justify="left")
        # the buttons keep their full width; the sentence beside them wraps instead
        left_side = self.title_label.master
        right.pack_forget()
        right.pack(side="right", anchor="s", before=left_side)
        self.save_btn = ttk.Button(right, text="Save changes", style="Accent.TButton",
                                   command=self.save)
        self.save_btn.pack(side="right")
        more = ttk.Menubutton(right, text="More", direction="below")
        m = styles.menu(more, c)
        m.add_command(label="Export this design to a file…", command=self.export_design)
        m.add_command(label="Import a design from a file…", command=self.import_design)
        m.add_separator()
        m.add_command(label="Run the setup wizard for a new CRM…", command=app.menu_new)
        more.configure(menu=m)
        more.pack(side="right", padx=(0, 8))
        self.more_menu = m
        self.dirty_label = ttk.Label(right, text="", style="Warn.TLabel")
        self.dirty_label.pack(side="right", padx=(0, 12))
        self._header_right = right

        self.problem_box = tk.Frame(self, bg=c["panel"], highlightthickness=1,
                                    highlightbackground=c["bad"], padx=14, pady=10)
        self.cols = ttk.Frame(self)
        self.cols.pack(fill="both", expand=True, padx=24, pady=(4, 16))

        # left: the lists
        left = widgets.card(self.cols, app, padding=0)
        left.configure(width=fs(10) * 20 + 6)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="Lists", style="PanelH2.TLabel").pack(anchor="w", padx=14, pady=(12, 6))
        tools = ttk.Frame(left, style="Panel.TFrame", padding=(10, 10, 10, 12))
        tools.pack(side="bottom", fill="x")
        ttk.Frame(left, style="Border.TFrame", height=1).pack(side="bottom", fill="x")
        tools.columnconfigure(0, weight=1, uniform="lt")
        tools.columnconfigure(1, weight=1, uniform="lt")
        self.list_up = ttk.Button(tools, text="Move up", style="Small.TButton",
                                  command=lambda: self.move_list(-1))
        self.list_up.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.list_down = ttk.Button(tools, text="Move down", style="Small.TButton",
                                    command=lambda: self.move_list(1))
        self.list_down.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.list_rename = ttk.Button(tools, text="Rename", style="Small.TButton",
                                      command=self.rename_list)
        self.list_rename.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(6, 0))
        self.list_remove = ttk.Button(tools, text="Remove", style="Small.TButton",
                                      command=self.remove_list)
        self.list_remove.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=(6, 0))
        widgets.Tooltip(self.list_up, "Move this list up in the sidebar", app)
        widgets.Tooltip(self.list_down, "Move this list down in the sidebar", app)
        widgets.Tooltip(self.list_remove, "Hide this list. Its records are kept and it can be "
                                          "brought back.", app)
        self.lists_scroll = widgets.ScrollFrame(left, app, panel=True)
        self.lists_scroll.pack(fill="both", expand=True)
        self.lists_box = self.lists_scroll.body
        self.lists_box.configure(takefocus=1)
        self.lists_box.bind("<Up>", lambda _e: self._step_list(-1) or "break")
        self.lists_box.bind("<Down>", lambda _e: self._step_list(1) or "break")

        # right: the form preview (hidden when the window is narrow)
        self.preview_col = widgets.card(self.cols, app, padding=0)
        self.preview_col.configure(width=fs(10) * 28)
        self.preview_col.pack_propagate(False)
        ttk.Label(self.preview_col, text="This is how the form will look.",
                  style="PanelHelp.TLabel").pack(anchor="w", padx=14, pady=(12, 4))
        self.preview_scroll = widgets.ScrollFrame(self.preview_col, app, panel=True)
        self.preview_scroll.pack(fill="both", expand=True, padx=(14, 4), pady=(0, 10))

        # middle: the selected list - its fields, or its settings
        self.mid = widgets.ScrollFrame(self.cols, app)
        self.mid.pack(side="left", fill="both", expand=True, padx=(14, 0))
        body = self.mid.body
        head = ttk.Frame(body)
        head.pack(fill="x", padx=(0, 2))
        self.list_title = ttk.Label(head, text="", style="H2.TLabel")
        self.list_title.pack(side="left")
        seg = ttk.Frame(head)
        seg.pack(side="right")
        self.tab_fields = ttk.Button(seg, text="Fields", style="Seg.TButton", takefocus=0,
                                     command=lambda: self.show_tab("fields"))
        self.tab_fields.pack(side="left")
        self.tab_settings = ttk.Button(seg, text="List settings", style="Seg.TButton", takefocus=0,
                                       command=lambda: self.show_tab("settings"))
        self.tab_settings.pack(side="left", padx=(4, 0))
        self.list_title.configure(justify="left")
        head.bind("<Configure>", lambda e: self.list_title.configure(
            wraplength=max(140, e.width - seg.winfo_reqwidth() - 16)))
        self.tab_help = ttk.Label(body, text="", style="Help.TLabel", justify="left")
        self.tab_help.pack(anchor="w", pady=(4, 8))
        body.bind("<Configure>", lambda e: self.tab_help.configure(wraplength=max(200, e.width - 8)),
                  add="+")
        self.settings_card = widgets.card(body, app, padding=16)
        self.fields_card = widgets.card(body, app, padding=16)
        self._build_fields_card()

        self.bind("<Configure>", self._resized)
        self.bind("<Destroy>", self._gone, add="+")

    def _gone(self, event):
        """Do not leave a redraw of the preview waiting for a page that has gone."""
        if event.widget is self and self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except (tk.TclError, ValueError):
                pass
            self._preview_after = None

    def _resized(self, event):
        if event.widget is not self:
            return
        self._wrap_header(event.width)
        wide = event.width - 48 >= fs(10) * 88
        if wide != self._preview_shown:
            self._preview_shown = wide
            if wide:
                self.preview_col.pack(side="right", fill="y", padx=(14, 0), before=self.mid)
                self.see_form.pack_forget()
            else:
                self.preview_col.pack_forget()
                self.see_form.pack(side="left", padx=(4, 0))

    def _wrap_header(self, width: int | None = None):
        """Wrap the sentence under the heading so it never runs under the buttons."""
        width = width or self.winfo_width()
        if width > 1:
            self.update_idletasks()
            room = width - 48 - self._header_right.winfo_reqwidth() - 24
            self.subtitle_label.configure(wraplength=max(260, room))

    def _build_fields_card(self):
        app, card = self.app, self.fields_card
        self.see_form = ttk.Button(self.tab_fields.master, text="See the form…", style="Seg.TButton",
                                   takefocus=0, command=self.show_form)
        ttk.Label(card, text="Add a field", style="PanelField.TLabel").pack(anchor="w", pady=(0, 4))
        add = ttk.Frame(card, style="Panel.TFrame")
        add.pack(fill="x")
        add.columnconfigure(0, weight=1)
        self.add_name = tk.StringVar(self)
        self.add_entry = ttk.Entry(add, textvariable=self.add_name)
        self.add_entry.grid(row=0, column=0, sticky="ew")
        self.add_kind = tk.StringVar(self, value=validate.kind_label("text"))
        self.add_kind_box = ttk.Combobox(add, textvariable=self.add_kind, state="readonly", width=14,
                                         values=[validate.kind_label(k) for k in validate.KIND_ORDER],
                                         height=len(validate.KIND_ORDER))
        self.add_kind_box.grid(row=0, column=1, padx=(6, 6))
        self.add_btn = ttk.Button(add, text="Add field", style="Small.TButton", command=self.quick_add)
        self.add_btn.grid(row=0, column=2, sticky="ns")
        self.add_hint = ttk.Label(card, text="", style="PanelHelp.TLabel", justify="left")
        self.add_hint.pack(anchor="w", pady=(3, 0))
        self._add_hint()
        self.add_entry.bind("<Return>", lambda _e: self.quick_add() or "break")
        self.add_kind_box.bind("<Return>", lambda _e: self.quick_add() or "break")
        self.add_kind_box.bind("<<ComboboxSelected>>", lambda _e: self._kind_picked())
        self.add_name.trace_add("write", self._add_name_typed)

        ttk.Frame(card, style="Border.TFrame", height=1).pack(fill="x", pady=(10, 8))
        bar = ttk.Frame(card, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        self.f_edit = ttk.Button(bar, text="Change…", style="Small.TButton", command=self.edit_selected)
        self.f_edit.pack(side="left")
        self.f_up = ttk.Button(bar, text="Move up", style="Small.TButton",
                               command=lambda: self.move_field(-1))
        self.f_up.pack(side="left", padx=(6, 0))
        self.f_down = ttk.Button(bar, text="Move down", style="Small.TButton",
                                 command=lambda: self.move_field(1))
        self.f_down.pack(side="left", padx=(4, 0))
        self.f_remove = ttk.Button(bar, text="Remove", style="Small.TButton",
                                   command=self.remove_selected)
        self.f_remove.pack(side="left", padx=(6, 0))
        # the rows: click, or Tab here and use the arrow keys, Enter and Delete
        self.fields_box = tk.Frame(card, bg=self.c["panel"], takefocus=1, highlightthickness=1,
                                   highlightbackground=self.c["panel"],
                                   highlightcolor=self.c["accent"])
        self.fields_box.pack(fill="x")
        fb = self.fields_box
        fb.bind("<FocusIn>", lambda _e: self.sel_field or self._step_field(0))
        fb.bind("<Up>", lambda _e: self._step_field(-1) or "break")
        fb.bind("<Down>", lambda _e: self._step_field(1) or "break")
        fb.bind("<Return>", lambda _e: self.edit_selected() or "break")
        fb.bind("<Delete>", lambda _e: self.remove_selected() or "break")
        for mod in ("Alt", "Control"):
            fb.bind(f"<{mod}-Up>", lambda _e: self.move_field(-1) or "break")
            fb.bind(f"<{mod}-Down>", lambda _e: self.move_field(1) or "break")
        self.marker = tk.Frame(self.fields_box, bg=self.c["accent"], height=2)
        self.removed_box = ttk.Frame(card, style="Panel.TFrame")
        self.removed_box.pack(fill="x")

    def _add_hint(self, text: str = "", bad: bool = False):
        self.add_hint.configure(
            style="PanelError.TLabel" if bad else "PanelHelp.TLabel",
            text=text or "Type a name, choose what kind of information it holds, and press Enter.")

    # ------------------------------------------------------------ problems
    def _show_problems(self, problems: list[str]):
        self.problems = list(problems)
        for w in self.problem_box.winfo_children():
            w.destroy()
        if not problems:
            self.problem_box.pack_forget()
            return
        c = self.c
        tk.Label(self.problem_box, text="This needs putting right before the design can be saved",
                 bg=c["panel"], fg=c["bad"], font=(c["ui"], fs(10), "bold"), anchor="w").pack(fill="x")
        for p in problems[:6]:
            tk.Label(self.problem_box, text="•  " + p, bg=c["panel"], fg=c["text"], anchor="w",
                     justify="left", font=(c["ui"], fs(10)), wraplength=fs(10) * 80).pack(
                fill="x", pady=(3, 0))
        self.problem_box.pack(fill="x", padx=24, pady=(0, 10), before=self.cols)

    def _letters_changed(self, t: dict) -> bool:
        was = bpm.get_type(self.saved, t["key"])
        return was is None or was.get("archived") or was.get("prefix") != t["prefix"]

    def _own_problems(self) -> list[str]:
        out = []
        shown: dict[str, str] = {}
        letters: dict[str, dict] = {}
        for t in bpm.active_types(self.bp):
            if not t["name"].strip():
                out.append("One of the lists has no name. Select it and type one.")
                continue
            if len(t["name"]) > 60 or len(t["plural"]) > 60:
                out.append(f"The name of {t['plural'][:40]}… is too long. Select the list, press "
                           "List settings and keep both names under 60 characters.")
            plural = t["plural"].strip().lower()
            if plural in shown:
                out.append(f"Two lists would both show as “{t['plural']}” in the sidebar. "
                           "Change one of them.")
            shown[plural] = t["key"]
            other = letters.get(t["prefix"])
            # only a clash made here is put to the person: two lists that already
            # shared their letters (some CRMs were created that way) still save
            if other is not None and (self._letters_changed(t) or self._letters_changed(other)):
                out.append(f"{other['plural']} and {t['plural']} both use the reference "
                           f"letters “{t['prefix']}”. Select one of those lists, press List "
                           "settings and change its reference letters.")
            letters[t["prefix"]] = t
        return out

    # ---------------------------------------------------------------- save
    def _tidy(self):
        """Small repairs before checking: names trimmed, gaps filled."""
        taken = [t.get("prefix") for t in self.bp["types"] if t.get("prefix")]
        for t in self.bp["types"]:
            t["name"] = re.sub(r"\s+", " ", t["name"]).strip()
            t["plural"] = re.sub(r"\s+", " ", t["plural"]).strip() or bpm.plural_of(t["name"])
            if not t.get("prefix") and t["name"]:
                t["prefix"] = bpm.make_prefix(t["name"], taken)
                taken.append(t["prefix"])
            if not t.get("archived"):
                fieldedit.fix_refs(t)

    def _adopt(self, theirs: dict, base: dict):
        """Bring someone else's saved changes into the working design, so that
        saving does not wipe them out. base = the design both started from.
        Anything changed here as well stays as it is here."""
        for other in theirs["types"]:
            mine = bpm.get_type(self.bp, other["key"])
            if mine is None:                    # a list they added
                self.bp["types"].append(copy.deepcopy(other))
                continue
            was = bpm.get_type(base, other["key"]) or {}
            for k in ("name", "plural", "prefix", "color", "title", "board", "archived"):
                if mine.get(k) == was.get(k) and other.get(k) != was.get(k):
                    if k in other:
                        mine[k] = copy.deepcopy(other[k])
                    else:
                        mine.pop(k, None)
            for i, f in enumerate(mine["fields"]):
                new, old = bpm.get_field(other, f["key"]), bpm.get_field(was, f["key"])
                if new is not None and old is not None and new != old \
                        and fieldedit.strip_private(f) == old:
                    mine["fields"][i] = copy.deepcopy(new)
            for f in other["fields"]:
                if bpm.get_field(mine, f["key"]) is None:       # a field they added
                    f = copy.deepcopy(f)
                    if not f.get("archived"):
                        f["name"] = fieldedit.unique_name(
                            f["name"], [x["name"] for x in bpm.active_fields(mine)])
                    mine["fields"].append(f)

    def _save(self, rebuild: bool = True) -> bool:
        app, db = self.app, self.db
        self._tidy()
        bp = bpm.clean(self.bp)
        problems = self._own_problems() or bpm.problems(bp)
        if problems:
            self._show_problems(problems)
            app.toast("The design was not saved – see the note at the top.", "bad")
            return False
        if db.get_meta("blueprint_at") != self.base_stamp:
            answer = widgets.choose(
                app, "Someone else changed the design",
                "While you were working, someone else saved a change to the design. Saving "
                "yours keeps their changes as well. Where you both changed the same thing, "
                "yours is kept.",
                [("mine", "Save mine"), ("theirs", "Drop mine and use theirs")],
                cancel="Keep editing")
            if answer not in ("mine", "theirs"):
                return False
            db.refresh_design()
            base, self.saved = self.saved, bpm.clean(db.blueprint)
            self.base_stamp = db.get_meta("blueprint_at")
            if answer == "theirs":
                self.bp = copy.deepcopy(self.saved)
                app.rebuild()
                return False
            self._adopt(self.saved, base)
            self._tidy()
            bp = bpm.clean(self.bp)
            problems = self._own_problems() or bpm.problems(bp)
            if problems:
                self._changed(lists=True, settings=True, fields=True)
                self._show_problems(problems)
                app.toast("The design was not saved – see the note at the top.", "bad")
                return False
        try:
            db.save_blueprint(bp)
        except dbm.DBError as exc:
            self._show_problems([str(exc)])
            return False
        self.saved = bpm.clean(db.blueprint)
        self.bp = copy.deepcopy(self.saved)
        self.base_stamp = db.get_meta("blueprint_at")
        if rebuild:
            app.rebuild()
            app.toast("Design saved", "good")
        return True

    # ---------------------------------------------------------- the lists
    def _draw_lists(self):
        c, box = self.c, self.lists_box
        for w in box.winfo_children():
            w.destroy()
        active = bpm.active_types(self.bp)
        for t in active:
            on = t["key"] == self.sel
            bg = c["sel"] if on else c["panel"]
            row = tk.Frame(box, bg=bg, cursor="hand2")
            row.pack(fill="x", padx=8, pady=1)
            tk.Frame(row, bg=t.get("color") or c["accent"], width=4).pack(side="left", fill="y")
            lab = tk.Label(row, text=t["plural"] or t["name"] or "(no name)", bg=bg, fg=c["text"],
                           anchor="w", padx=8, pady=6, justify="left", wraplength=fs(10) * 16,
                           font=(c["ui"], fs(10), "bold" if on else "normal"))
            lab.pack(side="left", fill="x", expand=True)
            for w in (row, lab):
                w.bind("<Button-1>", lambda _e, k=t["key"]: (self.lists_box.focus_set(),
                                                             self.select_list(k)))
        widgets.link(box, "+ Add a list", self.add_list, style="PanelLink.TLabel").pack(
            anchor="w", padx=14, pady=(8, 4))
        removed = [t for t in self.bp["types"] if t.get("archived")]
        if removed:
            ttk.Label(box, text="REMOVED", style="PanelHelp.TLabel").pack(
                anchor="w", padx=14, pady=(14, 2))
            for t in removed:
                row = ttk.Frame(box, style="Panel.TFrame")
                row.pack(fill="x", padx=14, pady=1)
                ttk.Label(row, text=t["plural"] or t["name"], style="PanelDim.TLabel").pack(side="left")
                widgets.link(row, "Bring back", lambda k=t["key"]: self.restore_list(k),
                             style="PanelLink.TLabel").pack(side="right")
        keys = [t["key"] for t in active]
        i = keys.index(self.sel) if self.sel in keys else -1
        self.list_up.configure(state="normal" if i > 0 else "disabled")
        self.list_down.configure(state="normal" if 0 <= i < len(keys) - 1 else "disabled")
        self.list_remove.configure(state="normal" if i >= 0 and len(keys) > 1 else "disabled")
        self.list_rename.configure(state="normal" if i >= 0 else "disabled")

    def _step_list(self, delta: int):
        keys = [t["key"] for t in bpm.active_types(self.bp)]
        if self.sel in keys:
            self.select_list(keys[max(0, min(keys.index(self.sel) + delta, len(keys) - 1))])

    def select_list(self, key: str):
        if key == self.sel or bpm.get_type(self.bp, key) is None:
            return
        self.sel = key
        self.sel_field = None
        self._draw_lists()
        self._draw_settings()
        self._draw_fields()
        self._draw_preview()
        self.mid.to_top()

    def add_list(self):
        def check(text):
            name = text.strip()
            if not name:
                return "Type a name, for example Supplier."
            if len(name) > 60:
                return "That name is too long – keep it under 60 characters."
            if name.lower() in [t["name"].lower() for t in bpm.active_types(self.bp)]:
                return f"There is already a list for “{name}”."
            return None
        name = widgets.ask_string(
            self.app, "Add a list",
            "What is one of the things in this list called? For example Supplier, Project or "
            "Volunteer.", ok="Add list", check=check)
        name = re.sub(r"\s+", " ", name or "").strip()
        if not name or check(name):
            return
        t = bpm.new_type(name, self.bp)
        self.bp["types"].append(t)
        self.sel, self.sel_field = t["key"], None
        self._changed(lists=True, settings=True, fields=True)
        self.app.toast(f"{t['plural']} added. Now add the fields it needs.")
        self.add_entry.focus_set()

    def move_list(self, delta: int):
        types = self.bp["types"]
        active = [t for t in types if not t.get("archived")]
        t = self.cur()
        if t is None or t not in active:
            return
        j = active.index(t) + delta
        if not 0 <= j < len(active):
            return
        a, b = types.index(t), types.index(active[j])
        types[a], types[b] = types[b], types[a]
        self._changed(lists=True, preview=False)

    def _relink(self):
        """Linking fields follow their list: hidden while it is removed, back when it is."""
        live = {t["key"] for t in bpm.active_types(self.bp)}
        for t in self.bp["types"]:
            for f in t["fields"]:
                if f["kind"] != "link":
                    continue
                if not f.get("archived") and f.get("link_type") not in live:
                    f["archived"] = True
                    f["_with_list"] = f.get("link_type")
                elif f.get("archived") and f.get("_with_list") in live:
                    f["name"] = fieldedit.unique_name(
                        f["name"], [x["name"] for x in bpm.active_fields(t)])
                    f.pop("archived", None)
                    f.pop("_with_list", None)
            if not t.get("archived"):
                fieldedit.fix_refs(t)

    def remove_list(self):
        t = self.cur()
        active = bpm.active_types(self.bp)
        if t is None or len(active) < 2:
            self.app.toast("Keep at least one list.", "bad")
            return
        holders = sorted({h["plural"] for h, _f in bpm.links_to(self.bp, t["key"]) if h is not t})
        text = (f"{t['plural']} will disappear from the sidebar. Its records are kept, and you "
                "can bring the list back from “Removed” at any time.")
        if holders:
            text += (f"\n\n{' and '.join(holders)} link to it. Those linking fields will be "
                     "hidden too, and come back with it.")
        if not widgets.confirm(self.app, f"Remove {t['plural']}?", text,
                               yes=f"Remove {t['plural']}", danger=True):
            return
        if bpm.get_type(self.saved, t["key"]) is None:
            self.bp["types"].remove(t)          # never saved, so there is nothing to keep
        else:
            t["archived"] = True
        self._relink()
        self.sel = bpm.active_types(self.bp)[0]["key"]
        self.sel_field = None
        self._changed(lists=True, settings=True, fields=True)

    def restore_list(self, key: str):
        t = bpm.get_type(self.bp, key)
        if t is None:
            return
        t.pop("archived", None)
        others = [x["name"] for x in bpm.active_types(self.bp) if x is not t]
        t["name"] = fieldedit.unique_name(t["name"], others)
        # Fields that link to this list were hidden with it and come back with it.
        # Once the removal has been saved the note saying so is gone, so every
        # hidden link to this list is taken to have gone with it.
        was = bpm.get_type(self.saved, key)
        back = 0
        for holder in self.bp["types"]:
            for f in holder["fields"]:
                if f["kind"] == "link" and f.get("link_type") == key and f.get("archived") \
                        and not holder.get("archived"):
                    if was is not None and was.get("archived"):
                        f["_with_list"] = key
                    back += f.get("_with_list") == key
        self._relink()
        self.sel, self.sel_field = key, None
        self._changed(lists=True, settings=True, fields=True)
        self.app.toast(f"{t['plural']} is back" + (", and so are the fields that link to it."
                                                    if back else "."), "good")

    # ------------------------------------------------- the list's settings
    def _draw_settings(self):
        app, c, card = self.app, self.c, self.settings_card
        for w in card.winfo_children():
            w.destroy()
        t = self.cur()
        if t is None:
            ttk.Label(card, text="Add a list to get started.", style="PanelDim.TLabel").pack(anchor="w")
            return
        card.columnconfigure(0, weight=1, uniform="set")
        card.columnconfigure(1, weight=1, uniform="set")
        self.name_var = tk.StringVar(self, value=t["name"])
        self.plural_var = tk.StringVar(self, value=t["plural"])
        self.prefix_var = tk.StringVar(self, value=t.get("prefix", ""))

        def cell(row, col, label):
            f = ttk.Frame(card, style="Panel.TFrame")
            f.grid(row=row, column=col, sticky="new", padx=(0, 8) if col == 0 else (8, 0),
                   pady=(0, 14))
            ttk.Label(f, text=label, style="PanelField.TLabel").pack(anchor="w")
            return f

        f = cell(0, 0, "One of them is called")
        self.name_entry = ttk.Entry(f, textvariable=self.name_var)
        self.name_entry.pack(fill="x", pady=(3, 0))
        f = cell(0, 1, "The list is called")
        self.plural_entry = ttk.Entry(f, textvariable=self.plural_var)
        self.plural_entry.pack(fill="x", pady=(3, 0))

        f = ttk.Frame(card, style="Panel.TFrame")
        f.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(f, text="What is each record called?", style="PanelField.TLabel").pack(anchor="w")
        line = ttk.Frame(f, style="Panel.TFrame")
        line.pack(fill="x", pady=(3, 0))
        self.title_vars = [tk.StringVar(self) for _ in range(3)]
        self.title_boxes = []
        for i, var in enumerate(self.title_vars):
            line.columnconfigure(i * 2, weight=1, uniform="title")
            box = ttk.Combobox(line, textvariable=var, state="readonly", width=10)
            box.grid(row=0, column=i * 2, sticky="ew")
            box.bind("<<ComboboxSelected>>", lambda _e: self._title_picked())
            self._no_wheel(box)
            self.title_boxes.append(box)
            if i < 2:
                ttk.Label(line, text="+", style="PanelDim.TLabel").grid(row=0, column=i * 2 + 1, padx=6)
        self.title_help = ttk.Label(f, text="", style="PanelHelp.TLabel", justify="left")
        self.title_help.pack(anchor="w", pady=(3, 0))

        f = ttk.Frame(card, style="Panel.TFrame")
        f.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(f, text="Board", style="PanelField.TLabel").pack(anchor="w")
        self.board_var = tk.StringVar(self)
        self.board_box = ttk.Combobox(f, textvariable=self.board_var, state="readonly", width=28)
        self.board_box.pack(anchor="w", pady=(3, 0))
        self.board_box.bind("<<ComboboxSelected>>", lambda _e: self._board_picked())
        self._no_wheel(self.board_box)
        self.board_help = ttk.Label(f, text="", style="PanelHelp.TLabel", justify="left")
        self.board_help.pack(anchor="w", pady=(3, 0))

        f = ttk.Frame(card, style="Panel.TFrame")
        f.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(f, text="Reference letters", style="PanelField.TLabel").pack(anchor="w")
        line = ttk.Frame(f, style="Panel.TFrame")
        line.pack(fill="x", pady=(3, 0))
        self.prefix_entry = ttk.Entry(line, textvariable=self.prefix_var, width=8)
        self.prefix_entry.pack(side="left")
        self.prefix_help = ttk.Label(line, text="", style="PanelHelp.TLabel")
        self.prefix_help.pack(side="left", padx=(10, 0))
        self.prefix_note = ttk.Label(f, text="Every record gets a reference. Changing the letters "
                                             "only affects records added from now on.",
                                     style="PanelHelp.TLabel", justify="left")
        self.prefix_note.pack(anchor="w", pady=(3, 0))

        f = ttk.Frame(card, style="Panel.TFrame")
        f.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Label(f, text="Colour", style="PanelField.TLabel").pack(anchor="w")
        sw = tk.Frame(f, bg=c["panel"])
        sw.pack(anchor="w", pady=(5, 0))
        self.swatches = {}
        for colour in bpm.COLORS:
            ring = tk.Frame(sw, bg=c["panel"], cursor="hand2")
            ring.pack(side="left", padx=(0, 4))
            dot = tk.Frame(ring, bg=colour, width=fs(18), height=fs(18), cursor="hand2")
            dot.pack(padx=2, pady=2)
            for w in (ring, dot):
                w.bind("<Button-1>", lambda _e, col=colour: self.set_colour(col))
            self.swatches[colour] = ring
        self._paint_swatches()
        card.bind("<Configure>", lambda e: [lab.configure(wraplength=max(200, e.width - 40))
                                            for lab in (self.title_help, self.board_help,
                                                        self.prefix_note)])

        self.name_var.trace_add("write", self._name_typed)
        self.plural_var.trace_add("write", self._plural_typed)
        self.prefix_var.trace_add("write", self._prefix_typed)
        self._paint_prefix()
        self._fill_choices()

    @staticmethod
    def _no_wheel(box):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            box.bind(seq, lambda _e: "break")

    def _name_typed(self, *_a):
        t = self.cur()
        if t is None or self._guard:
            return
        follow = t["plural"].strip() in ("", bpm.plural_of(t["name"].strip()))
        t["name"] = self.name_var.get()
        if follow:
            self._guard = True
            self.plural_var.set(bpm.plural_of(t["name"].strip()))
            self._guard = False
            t["plural"] = self.plural_var.get()
        self._changed(lists=True, preview=False)
        self._fields_heading()

    def _plural_typed(self, *_a):
        t = self.cur()
        if t is None or self._guard:
            return
        t["plural"] = self.plural_var.get()
        self._changed(lists=True, preview=False)
        self._fields_heading()

    def _prefix_typed(self, *_a):
        t = self.cur()
        if t is None or self._guard:
            return
        tidy = re.sub(r"[^A-Z0-9]", "", self.prefix_var.get().upper())[:6]
        if tidy != self.prefix_var.get():
            self._guard = True
            self.prefix_var.set(tidy)
            self._guard = False
        t["prefix"] = tidy
        self._paint_prefix()
        self._changed(preview=False)

    def _paint_prefix(self):
        prefix = self.prefix_var.get()
        t = self.cur()
        clash = next((x for x in bpm.active_types(self.bp)
                      if x is not t and prefix and x.get("prefix") == prefix), None)
        if clash is not None:
            # said here, as it is typed: letters another list has are not kept
            self.prefix_help.configure(
                style="PanelError.TLabel",
                text=f"{clash['plural']} already uses {prefix} – choose different letters.")
            return
        self.prefix_help.configure(
            style="PanelHelp.TLabel",
            text=f"References look like {prefix}-0001" if prefix
            else "Two to four letters, e.g. CON")

    def set_colour(self, colour: str):
        t = self.cur()
        if t is None:
            return
        t["color"] = colour
        self._paint_swatches()
        self._changed(lists=True, preview=False)

    def _paint_swatches(self):
        t = self.cur()
        for colour, ring in self.swatches.items():
            ring.configure(bg=self.c["text"] if t and t.get("color") == colour else self.c["panel"])

    def _title_fields(self, t: dict) -> list[dict]:
        return [f for f in bpm.active_fields(t)
                if f["kind"] not in fieldedit.NOT_FOR_TITLE or f["key"] in (t.get("title") or [])]

    def _fill_choices(self):
        """Fill the 'called' and 'board' dropdowns from the fields as they are now."""
        t = self.cur()
        if t is None or not getattr(self, "title_boxes", None) \
                or not self.title_boxes[0].winfo_exists():
            return
        fieldedit.fix_refs(t)
        fields = self._title_fields(t)
        names = [f["name"] for f in fields]
        by_key = {f["key"]: f["name"] for f in fields}
        title = [k for k in t["title"] if k in by_key][:3]
        for i, box in enumerate(self.title_boxes):
            box.configure(values=names if i == 0 else [NOTHING] + names)
            self.title_vars[i].set(by_key[title[i]] if i < len(title) else NOTHING)
        example = self._title_example(t)
        self.title_help.configure(
            text="The fields that make up its name at the top of the record, in lists and in "
                 "search." + (f" For example: {example}" if example else ""))
        choices = [f for f in bpm.active_fields(t) if f["kind"] == "choice"]
        self.board_box.configure(values=[NO_BOARD] + [f["name"] for f in choices])
        board = bpm.get_field(t, t.get("board") or "")
        self.board_var.set(board["name"] if board else NO_BOARD)
        if board:
            text = (f"{t['plural']} can be shown as cards in columns, one column for each option "
                    f"of “{board['name']}”. Drag a card to move it on.")
        elif choices:
            text = ("Pick a choice field (such as a stage or status) to see this list as cards "
                    "in columns as well.")
        else:
            text = ("A board shows the list as cards in columns. It needs a “Choice (pick one)” "
                    "field, such as Status – add one below first.")
        self.board_help.configure(text=text)

    def _title_example(self, t: dict) -> str:
        if bpm.get_type(self.saved, t["key"]) is None:
            return ""
        try:
            for r in self.db.records(t["key"])[:1]:
                return bpm.title_for(t, r["data"], self.db.lookup)
        except dbm.DBError:
            pass
        return ""

    def _title_picked(self):
        t = self.cur()
        by_name = {f["name"]: f["key"] for f in self._title_fields(t)}
        keys = []
        for var in self.title_vars:
            key = by_name.get(var.get())
            if key and key not in keys:
                keys.append(key)
        if keys:
            t["title"] = keys
        self._fill_choices()
        self._changed(preview=False)

    def _board_picked(self):
        t = self.cur()
        f = next((f for f in bpm.active_fields(t)
                  if f["kind"] == "choice" and f["name"] == self.board_var.get()), None)
        t["board"] = f["key"] if f else ""
        self._fill_choices()
        self._changed(preview=False)

    # ----------------------------------------------------------- the fields
    def _ordered(self, t: dict) -> list[dict]:
        """Active fields in the order the form shows them (grouped by section)."""
        return [f for _s, fields in bpm.sections(t) for f in fields]

    def _set_order(self, t: dict, ordered: list[dict]):
        t["fields"] = ordered + [f for f in t["fields"] if f.get("archived")]

    def _fields_heading(self):
        t = self.cur()
        if t is None:
            return
        one = (t["name"].strip() or "record").lower()
        self.list_title.configure(text=t["plural"].strip() or t["name"].strip() or "(no name)")
        if self.tab == "fields":
            text = (f"What you record about each {one}. Double-click a field to change it, or "
                    "drag it to another place.")
        else:
            text = f"What the list is called, how each {one} is named, and how the list looks."
        self.tab_help.configure(text=text)

    def show_tab(self, tab: str):
        """Show the fields of the selected list, or its settings."""
        self.tab = tab if tab in ("fields", "settings") else "fields"
        on_fields = self.tab == "fields"
        self.tab_fields.configure(style="SegOn.TButton" if on_fields else "Seg.TButton")
        self.tab_settings.configure(style="Seg.TButton" if on_fields else "SegOn.TButton")
        (self.settings_card if on_fields else self.fields_card).pack_forget()
        (self.fields_card if on_fields else self.settings_card).pack(fill="x", padx=(0, 2))
        self._fields_heading()
        self.mid.to_top()

    def rename_list(self):
        if self.cur() is None:
            return
        self.show_tab("settings")
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    def _draw_fields(self):
        c, box = self.c, self.fields_box
        for w in box.winfo_children():
            if w is not self.marker:
                w.destroy()
        for w in self.removed_box.winfo_children():
            w.destroy()
        self._elements, self._rows = [], {}
        t = self.cur()
        if t is None:
            return
        self._fields_heading()
        active = bpm.active_fields(t)
        if self.sel_field not in [f["key"] for f in active]:
            self.sel_field = None
        if not active:
            tk.Label(box, text="No fields yet. Add the first one above.", bg=c["panel"],
                     fg=c["dim"], font=(c["ui"], fs(10)), anchor="w", pady=8).pack(fill="x")
        for section, fields in bpm.sections(t):
            if section:
                head = tk.Frame(box, bg=c["panel"])
                head.pack(fill="x", pady=(10, 2))
                tk.Label(head, text=section.upper(), bg=c["panel"], fg=c["dim"],
                         font=(c["ui"], fs(8), "bold")).pack(side="left", padx=(8, 0))
                lk = widgets.link(head, "Rename", lambda s=section: self.rename_section(s),
                                  style="PanelLink.TLabel")
                lk.configure(font=(c["ui"], fs(8)), takefocus=0)
                lk.pack(side="left", padx=(8, 0))
                self._elements.append(("section", section, head))
            for f in fields:
                self._field_row(box, t, f, section)
        removed = [f for f in t["fields"] if f.get("archived")]
        if removed:
            ttk.Frame(self.removed_box, style="Border.TFrame", height=1).pack(fill="x", pady=(12, 8))
            ttk.Label(self.removed_box, text="REMOVED FIELDS", style="PanelHelp.TLabel").pack(anchor="w")
            ttk.Label(self.removed_box, text="Hidden from the form. What was typed in them is "
                                             "kept.", style="PanelHelp.TLabel").pack(anchor="w")
            for f in removed:
                row = ttk.Frame(self.removed_box, style="Panel.TFrame")
                row.pack(fill="x", pady=(4, 0))
                ttk.Label(row, text=f["name"], style="PanelDim.TLabel").pack(side="left")
                ttk.Label(row, text=fieldedit.kind_text(f, self.bp), style="PanelHelp.TLabel").pack(
                    side="left", padx=(10, 0))
                widgets.link(row, "Bring back", lambda k=f["key"]: self.restore_field(k),
                             style="PanelLink.TLabel").pack(side="right")
        self._paint_rows()
        self._fill_choices()

    def _field_row(self, box, t: dict, f: dict, section: str):
        c = self.c
        row = tk.Frame(box, bg=c["panel"], cursor="hand2")
        row.pack(fill="x", pady=1)
        name = ttk.Label(row, text=f["name"], style="DsName.TLabel", anchor="w", padding=(8, 4))
        name.pack(side="left")
        marks = []
        if f.get("required"):
            marks.append("needed")
        if f.get("in_list"):
            marks.append("in list")
        if f.get("unique"):
            marks.append("no repeats")
        if f.get("remind"):
            marks.append("reminder")
        if f["key"] in (t.get("title") or []):
            marks.append("name")
        if f["key"] == t.get("board"):
            marks.append("board")
        mark = ttk.Label(row, text=" · ".join(marks), style="DsMark.TLabel", anchor="e",
                         padding=(8, 0))
        mark.pack(side="right")
        if marks:
            widgets.Tooltip(mark, " ".join(MARKS[m] for m in marks), self.app)
        kind = ttk.Label(row, text=fieldedit.kind_text(f, self.bp), style="DsKind.TLabel",
                         anchor="e")
        kind.pack(side="right")
        parts = [row, name, kind, mark]
        for w in parts:
            w.bind("<ButtonPress-1>", lambda e, k=f["key"]: self._press(e, k))
            w.bind("<B1-Motion>", self._motion)
            w.bind("<ButtonRelease-1>", self._release)
            w.bind("<Double-Button-1>", lambda _e, k=f["key"]: self.edit_field(k))
        self._rows[f["key"]] = {"row": row, "parts": parts, "field": f,
                                "labels": ((name, "Name"), (kind, "Kind"), (mark, "Mark"))}
        self._elements.append(("field", section, row))

    def _paint_rows(self):
        c = self.c
        for key, item in self._rows.items():
            on = key == self.sel_field
            try:
                item["row"].configure(bg=c["sel"] if on else c["panel"])
                for lab, part in item["labels"]:
                    lab.configure(style=("DsSel" if on else "Ds") + part + ".TLabel")
            except tk.TclError:
                pass
        t = self.cur()
        order = [f["key"] for f in self._ordered(t)] if t else []
        i = order.index(self.sel_field) if self.sel_field in order else -1
        self.f_edit.configure(state="normal" if i >= 0 else "disabled")
        self.f_remove.configure(state="normal" if i >= 0 else "disabled")
        self.f_up.configure(state="normal" if i > 0 else "disabled")
        self.f_down.configure(state="normal" if 0 <= i < len(order) - 1 else "disabled")

    def select_field(self, key: str | None):
        self.sel_field = key
        self._paint_rows()

    def _step_field(self, delta: int):
        """Move the selection up or down a row (0 = make sure something is selected)."""
        t = self.cur()
        order = [f["key"] for f in self._ordered(t)] if t else []
        if not order:
            return
        i = order.index(self.sel_field) + delta if self.sel_field in order else 0
        self.select_field(order[max(0, min(i, len(order) - 1))])
        self._reveal(self.sel_field)

    # ---- drag to reorder
    def _press(self, event, key: str):
        self.fields_box.focus_set()
        self.select_field(key)
        self._drag = {"key": key, "y": event.y_root, "on": False}

    def _drop_point(self, y_root: int):
        """(position among the fields, section, y of the marker) for a pointer height."""
        pos, section, mark_y = 0, None, 0
        seen_field = False
        for kind, sec, w in self._elements:
            top = w.winfo_rooty()
            if kind == "section":
                if y_root < top and seen_field:
                    break
                if y_root >= top:
                    section, mark_y = sec, w.winfo_y() + w.winfo_height()
                continue
            if y_root < top + w.winfo_height() / 2:
                if section is None:
                    section, mark_y = sec, w.winfo_y()
                break
            pos += 1
            seen_field = True
            section, mark_y = sec, w.winfo_y() + w.winfo_height()
        return pos, section or "", mark_y

    def _motion(self, event):
        d = self._drag
        if not d:
            return
        if not d["on"] and abs(event.y_root - d["y"]) < 6:
            return
        d["on"] = True
        d["pos"], d["section"], y = self._drop_point(event.y_root)
        self.marker.place(x=0, y=max(0, y - 1), relwidth=1, height=2)
        self.marker.lift()

    def _release(self, _event):
        d, self._drag = self._drag, None
        self.marker.place_forget()
        if d and d["on"] and "pos" in d:
            self.move_field_to(d["key"], d["pos"], d["section"])

    def move_field_to(self, key: str, pos: int, section: str | None = None):
        """Put a field at a position among the list's fields (as shown), in a section."""
        t = self.cur()
        order = self._ordered(t)
        f = bpm.get_field(t, key)
        if f is None or f not in order:
            return
        i = order.index(f)
        order.remove(f)
        if i < pos:
            pos -= 1
        pos = max(0, min(pos, len(order)))
        if section is None:
            near = order[pos - 1] if pos > 0 else (order[0] if order else f)
            section = near.get("section", "")
        if section:
            f["section"] = section
        else:
            f.pop("section", None)
        order.insert(pos, f)
        self._set_order(t, order)
        self.sel_field = key
        self._changed(fields=True)

    def move_field(self, delta: int):
        t = self.cur()
        order = self._ordered(t) if t else []
        f = bpm.get_field(t, self.sel_field or "") if t else None
        if f is None or f not in order:
            return
        i = order.index(f)
        j = i + delta
        if not 0 <= j < len(order):
            return
        other = order[j]
        if other.get("section", "") == f.get("section", ""):
            order[i], order[j] = order[j], order[i]
        elif other.get("section"):          # step over the heading into the next group
            f["section"] = other["section"]
        else:
            f.pop("section", None)
        self._set_order(t, order)
        self._changed(fields=True)
        self._reveal(f["key"])

    def _reveal(self, key: str):
        item = self._rows.get(key)
        if item is None:
            return
        self.update_idletasks()
        try:
            row, canvas = item["row"], self.mid.canvas
            top = row.winfo_rooty() - canvas.winfo_rooty()
            if top < 0 or top + row.winfo_height() > canvas.winfo_height():
                self.mid.show(row)
        except tk.TclError:
            pass

    def rename_section(self, section: str):
        t = self.cur()
        new = widgets.ask_string(self.app, "Rename the heading",
                                 "The heading these fields sit under on the form.",
                                 initial=section, ok="Rename")
        new = re.sub(r"\s+", " ", new or "").strip()[:60]
        if not new or new == section:
            return
        for f in t["fields"]:
            if f.get("section") == section:
                f["section"] = new
        self._changed(fields=True)

    # ---- adding
    def _add_name_typed(self, *_a):
        self._add_hint()
        if not self._kind_touched:
            self.add_kind.set(validate.kind_label(fieldedit.guess_kind(self.add_name.get())))

    def _kind_picked(self):
        self._kind_touched = True
        self.add_entry.focus_set()

    def _targets(self) -> list[tuple[str, str]]:
        return [(t["key"], t["plural"] or t["name"]) for t in bpm.active_types(self.bp)]

    def _sections(self, t: dict) -> list[str]:
        return [s for s, _f in bpm.sections(t) if s]

    def quick_add(self):
        t = self.cur()
        if t is None:
            return
        name = re.sub(r"\s+", " ", self.add_name.get()).strip()
        if not name:
            self._add_hint("Type a name for the new field first.", bad=True)
            self.add_entry.focus_set()
            return
        if len(name) > 60:
            self._add_hint("That name is too long – keep it under 60 characters.", bad=True)
            self.add_entry.focus_set()
            return
        if name.lower() in [f["name"].lower() for f in bpm.active_fields(t)]:
            self._add_hint(f"This list already has a field called “{name}”.", bad=True)
            self.add_entry.focus_set()
            return
        kind = validate.kind_from_label(self.add_kind.get())
        field = {"name": name, "kind": kind}
        if kind in ("choice", "tags", "link"):
            # these need one more answer (the options, or which list) before they can exist
            out = fieldedit.FieldDialog(
                self.app, field, new=True, targets=self._targets(), sections=self._sections(t),
                taken=[f["name"] for f in bpm.active_fields(t)], focus_options=True).show()
            if not isinstance(out, dict):
                return
            field = out
        self.add_field(field)
        self._guard = True
        self.add_name.set("")
        self._guard = False
        self._kind_touched = False
        self.add_kind.set(validate.kind_label("text"))
        self.add_entry.focus_set()

    def add_field(self, field: dict) -> dict:
        """Add a field (a dict without a key) to the end of the selected list."""
        t = self.cur()
        extra = {k: v for k, v in field.items() if k not in ("name", "kind", "key")}
        f = bpm.new_field(field["name"], field["kind"], [x["key"] for x in t["fields"]], **extra)
        order = self._ordered(t)
        order.append(f)
        self._set_order(t, order)
        self.sel_field = f["key"]
        self._changed(fields=True)
        self._reveal(f["key"])
        return f

    # ---- changing and removing
    def edit_selected(self):
        if self.sel_field:
            self.edit_field(self.sel_field)

    def _has_data(self, type_key: str, key: str) -> bool:
        try:
            return any(r["data"].get(key) not in (None, "", [])
                       for r in self.db.records(type_key))
        except dbm.DBError:
            return True

    def edit_field(self, key: str):
        t = self.cur()
        f = bpm.get_field(t, key) if t else None
        if f is None or f.get("archived"):
            return
        self._drag = None
        self.marker.place_forget()
        self.select_field(key)
        saved = self.saved_field(key)
        out = fieldedit.FieldDialog(
            self.app, f, targets=self._targets(), sections=self._sections(t), saved=saved,
            taken=[x["name"] for x in bpm.active_fields(t) if x is not f],
            has_data=lambda: self._has_data(t["key"], key), can_remove=True).show()
        try:
            self.fields_box.focus_set()
        except tk.TclError:
            return
        if out == "remove":
            self.remove_field(key)
        elif isinstance(out, dict):
            i = t["fields"].index(f)
            t["fields"][i] = out
            if out.get("section", "") != f.get("section", ""):
                t["fields"].append(t["fields"].pop(i))      # joins its new group at the end
            self._set_order(t, self._ordered(t))
            fieldedit.fix_refs(t)
            self._changed(fields=True)

    def remove_selected(self):
        if self.sel_field:
            self.remove_field(self.sel_field)

    def remove_field(self, key: str):
        t = self.cur()
        f = bpm.get_field(t, key) if t else None
        if f is None:
            return
        if len(bpm.active_fields(t)) < 2:
            self.app.toast("A list needs at least one field, so this one stays.", "bad")
            return
        named_by = key in (t.get("title") or [])
        was_board = t.get("board") == key
        if self.saved_field(key) is None:
            t["fields"].remove(f)               # never saved: nothing to keep
            said = f"“{f['name']}” is removed."
        else:
            f["archived"] = True
            said = f"“{f['name']}” is hidden."
        fieldedit.fix_refs(t)
        # say what else changed with it; otherwise, where to find it again
        if named_by:
            now = " + ".join(x["name"] for x in (bpm.get_field(t, k) for k in t["title"]) if x)
            said += (f" Each {(t['name'].strip() or 'record').lower()} is now named by “{now}” – "
                     "change that in List settings.")
        if was_board:
            said += " The board used it, so this list has no board now."
        if f.get("archived") and not (named_by or was_board):
            said += " Bring it back from Removed fields, below."
        if f.get("archived") or named_by or was_board:
            self.app.toast(said)
        self.sel_field = None
        self._changed(fields=True)

    def restore_field(self, key: str):
        t = self.cur()
        f = bpm.get_field(t, key) if t else None
        if f is None:
            return
        if f["kind"] == "link":
            target = bpm.get_type(self.bp, f.get("link_type") or "")
            if target is None or target.get("archived"):
                name = target["plural"] if target else "it links to"
                self.app.toast(f"Bring back the list {name} first – this field links to it.", "bad")
                return
        f["name"] = fieldedit.unique_name(f["name"], [x["name"] for x in bpm.active_fields(t)])
        f.pop("archived", None)
        f.pop("_with_list", None)
        # if it was part of the record's name, or the board, before it went: put that back
        was = bpm.get_type(self.saved, t["key"]) or {}
        live = [x["key"] for x in bpm.active_fields(t)]
        if key in (was.get("title") or []) and key not in t["title"]:
            t["title"] = [k for k in was["title"] if k in live]
        if was.get("board") == key and not t.get("board") and f["kind"] == "choice":
            t["board"] = key
        self._set_order(t, self._ordered(t))
        self.sel_field = key
        self._changed(fields=True)
        self._reveal(key)

    # ------------------------------------------------------------- preview
    def _queue_preview(self):
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
        self._preview_after = self.after(120, self._draw_preview)

    def _draw_preview(self):
        self._preview_after = None
        if not self.winfo_exists():
            return
        holder = self.preview_scroll.body
        for w in holder.winfo_children():
            w.destroy()
        t = self.cur()
        if t is None:
            return
        self.preview_form = widgets.Form(holder, self.app, fieldedit.strip_private(t), columns=1,
                                         preview=True)
        self.preview_form.pack(fill="x", padx=(0, 10), pady=(0, 8))

    def show_form(self):
        """The form preview in its own window (used when the page is too narrow)."""
        t = self.cur()
        if t is None:
            return
        d = widgets.Dialog(self.app, f"The {t['name'].lower()} form", width=fs(10) * 62)
        ttk.Label(d.body, text="This is how the form will look.", style="Help.TLabel").pack(
            anchor="w", pady=(0, 8))
        box = widgets.card(d.body, self.app, padding=0)
        box.pack(fill="both", expand=True)
        sf = widgets.ScrollFrame(box, self.app, panel=True)
        sf.canvas.configure(height=fs(10) * 38)
        sf.pack(fill="both", expand=True, padx=(14, 4), pady=10)
        widgets.Form(sf.body, self.app, fieldedit.strip_private(t), columns=2,
                     preview=True).pack(fill="x", padx=(0, 10))
        b = d.add_button("Close", d.cancel, accent=True)
        b.bind("<Return>", lambda _e: d.cancel() or "break")
        d.default_on_enter()
        d.show(focus=b)

    # ------------------------------------------------------ export / import
    def export_design(self):
        name = re.sub(r'[\\/:*?"<>|]', " ", self.db.name).strip() or "CRM"
        path = widgets.ask_save_file(self.app, "Export this design", f"{name} design.json",
                                     [("Design file", "*.json")], defaultextension=".json")
        if not path:
            return
        self._tidy()
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(bpm.clean(self.bp), fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            widgets.error(self.app, "Could not save the file", f"The design was not exported.\n\n({exc})")
            return
        self.app.toast(f"Design saved to {path}", "good")

    def import_design(self):
        app = self.app
        path = widgets.ask_open_file(app, "Import a design",
                                     [("Design file", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None
        new = bpm.clean(raw) if isinstance(raw, dict) and isinstance(raw.get("types"), list) else None
        if new is None or not bpm.active_types(new):
            widgets.error(app, "That is not a design file",
                          "Choose a file that was made with “Export this design to a file”.")
            return
        if not widgets.confirm(
                app, "Use the design from this file?",
                f"It has these lists: {bpm.describe(new)}.\n\nLists and fields you have now that "
                "are not in the file are hidden, not deleted – you can bring them back. Nothing "
                "changes until you press Save changes.", yes="Use this design"):
            return
        new["name"] = self.bp.get("name") or new["name"]
        for t in self.bp["types"]:
            mine = bpm.get_type(new, t["key"])
            if mine is None:
                gone = copy.deepcopy(t)
                gone["archived"] = True
                new["types"].append(gone)
                continue
            for f in t["fields"]:
                if bpm.get_field(mine, f["key"]) is None:
                    gone = copy.deepcopy(f)
                    gone["archived"] = True
                    mine["fields"].append(gone)
        self.bp = new
        self._relink()
        self.sel = bpm.active_types(new)[0]["key"]
        self.sel_field = None
        self._changed(lists=True, settings=True, fields=True)
        app.toast("Design loaded from the file. Press Save changes to keep it.")
