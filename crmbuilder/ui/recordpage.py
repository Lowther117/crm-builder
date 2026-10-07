"""One record: its form on the left; notes, tasks, files, related records and
history on the right."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import validate
from . import styles, widgets
from .base import Page
from .picker import save_new_record
from .styles import fs

ACTION_WORDS = {
    "created": "Added", "changed": "Changed", "deleted": "Deleted", "restored": "Restored",
    "note-added": "Note added", "note-changed": "Note changed", "note-deleted": "Note deleted",
    "task-added": "Task added", "task-removed": "Task removed", "task-done": "Task done", "task-reopened": "Task reopened",
    "file-added": "File added", "file-removed": "File removed",
}


def human_size(n: int) -> str:
    n = n or 0
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def open_path(path: str):
    """Open a file with whatever the computer normally uses for it."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], stdin=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", path], stdin=subprocess.DEVNULL)
    except Exception:
        pass


class RecordPage(Page):
    auto_refresh = True

    def __init__(self, parent, app, record_id: int | None = None, type_key: str | None = None,
                 preset: dict | None = None, restore: dict | None = None, tab: int = 0, **_kw):
        super().__init__(parent, app)
        db = self.db
        self.record = None
        self.preset = dict(preset or {})
        if record_id is not None:
            self.record = db.get_record(record_id)
            if self.record is None:
                raise dbm.DBError("That record no longer exists.")
            type_key = self.record["type"]
        self.t = bpm.get_type(db.blueprint, type_key or "")
        if self.t is None:
            raise dbm.DBError("That list no longer exists.")
        self.type_key = self.t["key"]
        self.nav_key = "list:" + self.type_key
        self.is_new = self.record is None
        self.deleted = bool(self.record and self.record["deleted_at"])
        self.readonly = not db.can_edit or self.deleted or bool(self.t.get("archived"))
        if self.is_new:
            values = {}
            for f in bpm.active_fields(self.t):
                v = bpm.default_value(f, db.user["id"])
                if v is not None:
                    values[f["key"]] = v
            values.update(self.preset)
            self.base = {}
        else:
            values = dict(self.record["data"])
            self.base = dict(self.record["data"])
        self._tab = tab
        self._build(values)
        if restore and not self.readonly:
            for k, v in restore.items():
                ed = self.form.editors.get(k)
                try:
                    if isinstance(ed, widgets.EntryEditor):
                        ed.var.set(v)      # exactly as typed ("12-03-2027" is not a stored date yet)
                    elif ed is not None:
                        ed.set(v)
                except Exception:
                    pass
        if self.is_new and not self.readonly:
            self.after(60, self.form.focus_first)

    # ------------------------------------------------------------- layout
    def _build(self, values: dict):
        t, app, c, db = self.t, self.app, self.c, self.db
        head = ttk.Frame(self)
        head.pack(fill="x", padx=24, pady=(14, 8))
        # (the buttons are packed first: a long title is cut short, never the buttons)
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s", padx=(12, 0))
        left = ttk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        widgets.link(left, f"‹ {t['plural']}", self.to_list).pack(anchor="w", pady=(0, 2))
        self.title_label = ttk.Label(left, text="", style="H1.TLabel")
        self.title_label.pack(anchor="w")
        self.sub_label = ttk.Label(left, text="", style="Sub.TLabel")
        self.sub_label.pack(anchor="w", pady=(2, 0))
        if not self.readonly:
            self.save_btn = ttk.Button(right, text="Save" if not self.is_new
                                       else f"Add {t['name'].lower()}",
                                       style="Accent.TButton", command=self.save)
            self.save_btn.pack(side="right")
            widgets.Tooltip(self.save_btn, f"{widgets.MOD_LABEL}+S", app)
        if not self.is_new:
            more = ttk.Menubutton(right, text="More", direction="below")
            m = styles.menu(more, c)
            m.add_command(label="Printable record sheet…",
                          command=lambda: app.tool("record_sheet", self.record["id"]))
            if db.can_edit and not self.deleted:
                m.add_command(label="Make a copy", command=self.duplicate)
                m.add_separator()
                m.add_command(label="Delete", command=self.delete)
            more.configure(menu=m)
            more.pack(side="right", padx=(0, 8))
        if self.deleted:
            banner = ttk.Frame(self, style="Hint.TFrame")
            banner.pack(fill="x", padx=24, pady=(0, 8))
            ttk.Label(banner, text="This record is in the recycle bin.", style="Hint.TLabel").pack(side="left")
            if db.can_edit:
                ttk.Button(banner, text="Restore it", style="Hint.TButton",
                           command=self.restore).pack(side="right", padx=8, pady=5)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        # the form
        left_card = tk.Frame(panes, bg=c["panel"], highlightthickness=1,
                             highlightbackground=c["border"], highlightcolor=c["border"])
        self.scroll = widgets.ScrollFrame(left_card, app, panel=True)
        self.scroll.pack(fill="both", expand=True, padx=2, pady=2)
        inner = ttk.Frame(self.scroll.body, style="Panel.TFrame", padding=(18, 6, 18, 18))
        inner.pack(fill="both", expand=True)
        self.form = widgets.Form(inner, app, t, values, columns=2, panel=True,
                                 readonly=self.readonly, on_change=self._form_changed)
        self.form.pack(fill="both", expand=True)
        if self.is_new:
            # defaults and presets count as "nothing typed yet"
            self.form.mark_clean()
        panes.add(left_card, weight=3)
        # the side panel
        self.side = ttk.Frame(panes)
        panes.add(self.side, weight=2)
        self._build_side()
        self._update_heading()
        self._sash_set = False

        def place_sash(event):
            # give the form the larger share the first time the page is laid out
            if not self._sash_set and event.width > 200:
                self._sash_set = True
                # ...but in a small window keep the side wide enough for its five tabs
                side = max(int(event.width * 0.42), min(fs(360), event.width // 2))
                panes.sashpos(0, event.width - side)
        panes.bind("<Configure>", place_sash)

    def _update_heading(self):
        t = self.t
        if self.is_new:
            self.title_label.configure(text=f"New {t['name'].lower()}")
            self.sub_label.configure(text="Fields marked * are needed. Nothing is saved until "
                                          f"you press Add {t['name'].lower()}.")
            return
        r = self.record
        self.title_label.configure(text=r["title"] or r["ref"])
        bits = [r["ref"], f"added {validate.format_stamp(r['created_at'])}"]
        who = self.db.user_name(r["created_by"])
        if who and self.db.mode == "team":
            bits[-1] += f" by {who}"
        if r["updated_at"] and r["updated_at"] != r["created_at"]:
            upd = f"last changed {validate.format_stamp(r['updated_at'])}"
            who = self.db.user_name(r["updated_by"])
            if who and self.db.mode == "team":
                upd += f" by {who}"
            bits.append(upd)
        self.sub_label.configure(text="  ·  ".join(bits))

    def _form_changed(self, _key):
        pass

    def _drafting(self) -> bool:
        """True while the person has typed something that is not saved yet."""
        if self.readonly:
            return False
        try:
            if self.form.is_dirty():
                return True
            if getattr(self, "note_text", None) is not None and self.note_text.winfo_exists() \
                    and self.note_text.get("1.0", "end-1c").strip():
                return True
            if getattr(self, "task_entry", None) is not None and self.task_entry.winfo_exists() \
                    and (self.task_title.get().strip() or self.task_due.get().strip()):
                return True
        except tk.TclError:
            pass
        return False

    def refresh(self):
        """Someone else saved something. If it was this record, show it as it
        is now - unless this person is in the middle of typing, in which case
        nothing moves under them (saving sorts out any clash)."""
        if self.is_new or not self.winfo_exists() or self._drafting():
            return
        rec = self.db.get_record(self.record["id"])
        if rec is None:
            return
        if bool(rec["deleted_at"]) != self.deleted:
            self.app.reload()             # it went to, or came back from, the recycle bin
            return
        if rec["updated_at"] != self.record["updated_at"]:
            self.record = rec
            self.base = dict(rec["data"])
            self.form.set_values(rec["data"])
            self._update_heading()
        sig = self._side_signature()
        if sig != self._side_sig:          # (most changes by other people are to other records)
            self._side_sig = sig
            self._fill_notes()
            self._fill_tasks()
            self._fill_files()
            self._fill_related()
            if self._tab == 4:
                self._fill_history()

    def _side_signature(self):
        """Something that changes when this record's notes, tasks, files or
        related records do."""
        db, rid = self.db, self.record["id"]
        return (tuple((n["id"], n["edited_at"]) for n in db.notes(rid)),
                tuple((k["id"], k["title"], k["due"], k["done_at"])
                      for k in db.tasks(record_id=rid, include_done=True)),
                tuple(f["id"] for f in db.files(rid)),
                tuple((t["key"], f["key"], tuple((r["id"], r["title"]) for r in rows))
                      for t, f, rows in db.related(rid)))

    # --------------------------------------------------------- side panel
    def _build_side(self):
        for w in self.side.winfo_children():
            w.destroy()
        if self.is_new:
            box = widgets.card(self.side, self.app, padding=20)
            box.pack(fill="x", padx=(12, 0))
            ttk.Label(box, text="Notes, tasks and files", style="PanelH3.TLabel").pack(anchor="w")
            ttk.Label(box, text=f"Once this {self.t['name'].lower()} is added you can keep notes "
                                "of calls and meetings here, set follow-up tasks, attach files "
                                "and see everything linked to it.",
                      style="PanelDim.TLabel", wraplength=fs(300), justify="left").pack(anchor="w", pady=(6, 0))
            return
        nb = ttk.Notebook(self.side)
        nb.pack(fill="both", expand=True, padx=(12, 0))
        self.nb = nb
        self.tabs = {}
        self._counts: dict[str, int] = {}
        self._tab_font = styles.pinned_font(self.app.root, self.c["ui"], 10)
        for name in ("Notes", "Tasks", "Files", "Related", "History"):
            frame = ttk.Frame(nb, style="Panel.TFrame", padding=12)
            nb.add(frame, text=name)
            self.tabs[name] = frame
        self._fill_notes()
        self._fill_tasks()
        self._fill_files()
        self._fill_related()
        self._fill_history()
        try:
            nb.select(self._tab)
        except tk.TclError:
            pass
        self._side_sig = self._side_signature()
        nb.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())
        nb.bind("<Configure>", lambda e: self._fit_tabs(e.width) if e.widget is nb else None)

    def _tab_changed(self):
        try:
            self._tab = self.nb.index("current")
        except tk.TclError:
            return
        if self._tab == 4:
            self._fill_history()

    def _tab_title(self, name: str, count: int):
        self._counts[name] = count
        self._fit_tabs()

    def _fit_tabs(self, width: int | None = None):
        """Show the counts on the tabs when there is room for them and bare
        names when there is not (a tab that does not fit has its name cut short)."""
        nb = self.nb
        if width is None:
            width = nb.winfo_width()
        full = {n: (f"{n} ({self._counts[n]})" if self._counts.get(n) else n) for n in self.tabs}
        need = sum(self._tab_font.measure(text) + 30 for text in full.values())
        for name, frame in self.tabs.items():
            text = full[name] if (width <= 1 or need <= width) else name
            if nb.tab(frame, "text") != text:
                nb.tab(frame, text=text)

    def _clear(self, name: str) -> ttk.Frame:
        frame = self.tabs[name]
        for w in frame.winfo_children():
            w.destroy()
        return frame

    # notes
    def _fill_notes(self):
        frame = self._clear("Notes")
        app, db, rid = self.app, self.db, self.record["id"]
        can = db.can_edit and not self.deleted
        if can:
            top = ttk.Frame(frame, style="Panel.TFrame")
            top.pack(fill="x")
            self.note_kind = tk.StringVar(self, value="Note")
            for k in dbm.NOTE_KINDS:
                ttk.Radiobutton(top, text=k, value=k, variable=self.note_kind,
                                style="Panel.TRadiobutton").pack(side="left", padx=(0, 10))
            self.note_text = widgets.make_text(frame, app, height=3)
            self.note_text.pack(fill="x", pady=(6, 0))
            row = ttk.Frame(frame, style="Panel.TFrame")
            row.pack(fill="x", pady=(6, 8))
            ttk.Button(row, text="Add note", command=self.add_note).pack(side="left")
        notes = db.notes(rid)
        self._tab_title("Notes", len(notes))
        sf = widgets.ScrollFrame(frame, app, panel=True)
        sf.pack(fill="both", expand=True)
        if not notes:
            ttk.Label(sf.body, text="No notes yet.", style="PanelHelp.TLabel").pack(anchor="w", pady=8)
        for n in notes[:300]:
            item = ttk.Frame(sf.body, style="Panel.TFrame")
            item.pack(fill="x", pady=(0, 2))
            ttk.Frame(item, style="Border.TFrame", height=1).pack(fill="x", pady=(4, 6))
            head = ttk.Frame(item, style="Panel.TFrame")
            head.pack(fill="x")
            who = db.user_name(n["created_by"])
            meta = f"{n['kind']}  ·  {validate.format_stamp(n['at'])}"
            if who and db.mode == "team":
                meta += f"  ·  {who}"
            if n["edited_at"]:
                meta += "  ·  edited"
            ttk.Label(head, text=meta, style="PanelHelp.TLabel").pack(side="left")
            if can and (n["created_by"] == db.user["id"] or db.is_admin):
                widgets.link(head, "Delete", lambda n=n: self.delete_note(n),
                             style="PanelLink.TLabel").pack(side="right")
                widgets.link(head, "Edit", lambda n=n: self.edit_note(n),
                             style="PanelLink.TLabel").pack(side="right", padx=(0, 10))
            body = ttk.Label(item, text=n["body"], style="Panel.TLabel", justify="left",
                             wraplength=fs(330))
            body.pack(anchor="w", fill="x", pady=(3, 0))
            item.bind("<Configure>", lambda e, b=body: b.configure(wraplength=max(120, e.width - 8)))

    def add_note(self):
        body = self.note_text.get("1.0", "end-1c").strip()
        if not body:
            self.note_text.focus_set()
            return
        self.db.add_note(self.record["id"], body, self.note_kind.get())
        self._fill_notes()
        self.note_text.focus_set()
        self.app.toast("Note added", "good")

    def edit_note(self, note):
        app = self.app
        d = widgets.Dialog(app, "Edit note", width=480)
        text = widgets.make_text(d.body, app, height=8, width=56)
        text.pack(fill="both", expand=True)
        text.insert("1.0", note["body"])

        def save():
            try:
                self.db.update_note(note["id"], text.get("1.0", "end-1c"))
            except dbm.DBError as exc:
                widgets.error(app, "Could not save", str(exc))
                return
            d.ok(True)

        d.add_button("Save", save, accent=True)
        d.add_button("Cancel", d.cancel)
        if d.show(focus=text):
            self._fill_notes()

    def delete_note(self, note):
        if widgets.confirm(self.app, "Delete note", "Delete this note? This cannot be undone "
                           "(the history keeps a record that it was deleted).",
                           yes="Delete", danger=True):
            self.db.delete_note(note["id"])
            self._fill_notes()

    # tasks
    def _fill_tasks(self):
        frame = self._clear("Tasks")
        app, db, rid = self.app, self.db, self.record["id"]
        can = db.can_edit and not self.deleted
        if can:
            row = ttk.Frame(frame, style="Panel.TFrame")
            row.pack(fill="x")
            # what needs doing gets the full width; the date and the button share a line
            ttk.Label(row, text="To do", style="PanelField.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w")
            self.task_title = tk.StringVar(self)
            e = ttk.Entry(row, textvariable=self.task_title)
            e.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))
            ttk.Label(row, text="Due (if it has a date)", style="PanelField.TLabel").grid(
                row=2, column=0, sticky="w", pady=(8, 0))
            self.task_due = widgets.EntryEditor(row, app, {"key": "due", "name": "Due", "kind": "date"})
            self.task_due.widget.grid(row=3, column=0, sticky="w", pady=(3, 0))
            self.task_due.entry.configure(width=12)
            ttk.Button(row, text="Add task", command=self.add_task).grid(
                row=3, column=1, sticky="e", padx=(6, 0), pady=(3, 0))
            row.columnconfigure(1, weight=1)
            e.bind("<Return>", lambda _e: self.add_task())
            self.task_due.entry.bind("<Return>", lambda _e: self.add_task())
            self.task_entry = e
            self.task_error = ttk.Label(frame, text="", style="PanelError.TLabel")
            self.task_error.pack(anchor="w")
        tasks = db.tasks(record_id=rid, include_done=True)
        open_count = sum(1 for t in tasks if not t["done_at"])
        self._tab_title("Tasks", open_count)
        sf = widgets.ScrollFrame(frame, app, panel=True)
        sf.pack(fill="both", expand=True, pady=(6, 0))
        if not tasks:
            ttk.Label(sf.body, text="No tasks. Add one above to remind yourself to follow up.",
                      style="PanelHelp.TLabel", wraplength=fs(320), justify="left").pack(anchor="w", pady=8)
        today = dbm.today_iso()
        for t in tasks[:300]:
            item = ttk.Frame(sf.body, style="Panel.TFrame")
            item.pack(fill="x", pady=2)
            var = tk.BooleanVar(self, value=bool(t["done_at"]))
            cb = ttk.Checkbutton(item, variable=var, style="Panel.TCheckbutton",
                                 command=lambda t=t, v=var: self.toggle_task(t, v.get()))
            cb.pack(side="left", anchor="n")
            if not can:
                cb.configure(state="disabled")
            col = ttk.Frame(item, style="Panel.TFrame")
            col.pack(side="left", fill="x", expand=True)
            ttk.Label(col, text=t["title"], justify="left", wraplength=fs(280),
                      style="PanelDim.TLabel" if t["done_at"] else "Panel.TLabel").pack(anchor="w")
            bits = []
            style = "PanelHelp.TLabel"
            if t["done_at"]:
                bits.append(f"done {validate.format_stamp(t['done_at'])[:10]}")
            elif t["due"]:
                bits.append("due " + validate.friendly_date(t["due"]).lower())
                if t["due"] < today:
                    style = "PanelError.TLabel"
                    bits[-1] = "overdue – was " + bits[-1]
            who = db.user_name(t["assigned_to"])
            if who and db.mode == "team":
                bits.append(who)
            if bits:
                ttk.Label(col, text="  ·  ".join(bits), style=style).pack(anchor="w")
            if can:
                widgets.link(item, "×", lambda t=t: self.remove_task(t),
                             style="PanelLink.TLabel").pack(side="right", anchor="n")

    def add_task(self):
        title = self.task_title.get().strip()
        if not title:
            self.task_entry.focus_set()
            return
        due, err = validate.normalise({"kind": "date", "name": "Due"}, self.task_due.get())
        if err:
            self.task_error.configure(text=err)
            self.task_due.focus()
            return
        self.db.add_task(title, due=due, record_id=self.record["id"],
                         assigned_to=self.db.user["id"])
        self._fill_tasks()
        self.task_entry.focus_set()
        self.app.refresh_sidebar()

    def toggle_task(self, task, done):
        self.db.set_task_done(task["id"], done)
        self._fill_tasks()
        self.app.refresh_sidebar()

    def remove_task(self, task):
        if widgets.confirm(self.app, "Remove task", f"Remove the task “{task['title']}”?",
                           yes="Remove", danger=True):
            self.db.delete_task(task["id"])
            self._fill_tasks()
            self.app.refresh_sidebar()

    # files
    def _fill_files(self):
        frame = self._clear("Files")
        app, db, rid = self.app, self.db, self.record["id"]
        can = db.can_edit and not self.deleted
        files = db.files(rid)
        self._tab_title("Files", len(files))
        if can:
            row = ttk.Frame(frame, style="Panel.TFrame")
            row.pack(fill="x")
            ttk.Button(row, text="Add files…", command=self.add_files).pack(side="left")
            ttk.Label(frame, text="Files are kept inside the CRM file, so they travel with it.",
                      style="PanelHelp.TLabel").pack(anchor="w", pady=(6, 0))
        sf = widgets.ScrollFrame(frame, app, panel=True)
        sf.pack(fill="both", expand=True, pady=(8, 0))
        if not files:
            ttk.Label(sf.body, text="No files attached.", style="PanelHelp.TLabel").pack(anchor="w", pady=8)
        for f in files:
            item = ttk.Frame(sf.body, style="Panel.TFrame")
            item.pack(fill="x", pady=3)
            widgets.link(item, f["name"], lambda f=f: self.open_file(f),
                         style="PanelLink.TLabel").pack(anchor="w")
            line = ttk.Frame(item, style="Panel.TFrame")
            line.pack(fill="x")
            ttk.Label(line, text=f"{human_size(f['size'])}  ·  added "
                                 f"{validate.format_stamp(f['added_at'])[:10]}",
                      style="PanelHelp.TLabel").pack(side="left")
            if can:
                widgets.link(line, "Remove", lambda f=f: self.remove_file(f),
                             style="PanelLink.TLabel").pack(side="right")
            widgets.link(line, "Save a copy…", lambda f=f: self.save_file(f),
                         style="PanelLink.TLabel").pack(side="right", padx=(0, 10))

    def add_files(self):
        if self.app.testing:
            picked = [p for p in [widgets.ask_open_file(self.app, "Add files", [])] if p]
        else:
            from tkinter import filedialog
            from .. import paths
            picked = filedialog.askopenfilenames(
                parent=self.app.root, title="Add files",
                initialdir=paths.default_save_dir(self.app.settings))
        added = 0
        for p in picked or []:
            try:
                self.db.add_file(self.record["id"], p)
                added += 1
            except (dbm.DBError, OSError) as exc:
                widgets.error(self.app, "Could not add that file", f"{os.path.basename(p)}\n\n{exc}")
        if added:
            self._fill_files()
            self.app.toast(f"Added {added} file{'s' if added != 1 else ''}", "good")

    def open_file(self, f):
        got = self.db.file_data(f["id"])
        if not got:
            return
        name, blob = got
        try:
            folder = tempfile.mkdtemp(prefix="crm-builder-")
            path = os.path.join(folder, os.path.basename(name) or "file")
            with open(path, "wb") as fh:
                fh.write(blob)
        except OSError as exc:
            widgets.error(self.app, "The file could not be opened",
                          "A copy could not be made to open. Use “Save a copy…” to put it "
                          f"somewhere yourself.\n\n({exc})")
            return
        open_path(path)

    def save_file(self, f):
        got = self.db.file_data(f["id"])
        if not got:
            return
        name, blob = got
        ext = os.path.splitext(name)[1]
        path = widgets.ask_save_file(self.app, "Save a copy", name,
                                     [("All files", "*.*")], defaultextension=ext)
        if path:
            try:
                with open(path, "wb") as fh:
                    fh.write(blob)
            except OSError as exc:
                widgets.error(self.app, "The file could not be saved",
                              "If a file with that name is open in another program, close it "
                              f"there and try again, or save under a different name.\n\n({exc})")
                return
            self.app.toast(f"Saved to {path}", "good")

    def remove_file(self, f):
        if widgets.confirm(self.app, "Remove file", f"Remove “{f['name']}” from this record?\n\n"
                           "This cannot be undone.", yes="Remove", danger=True):
            self.db.delete_file(f["id"])
            self._fill_files()

    # related
    def _fill_related(self):
        frame = self._clear("Related")
        app, db = self.app, self.db
        groups = db.related(self.record["id"])
        total = sum(len(rows) for _t, _f, rows in groups)
        self._tab_title("Related", total)
        sf = widgets.ScrollFrame(frame, app, panel=True)
        sf.pack(fill="both", expand=True)
        if not groups:
            ttk.Label(sf.body, text=f"Nothing links to {self.t['plural'].lower()} yet. If another "
                                    "list had a 'Link to another record' field pointing here, "
                                    "its records would show up in this tab.",
                      style="PanelHelp.TLabel", wraplength=fs(320), justify="left").pack(anchor="w", pady=8)
        for t, f, rows in groups:
            head = ttk.Frame(sf.body, style="Panel.TFrame")
            head.pack(fill="x", pady=(8, 2))
            label = t["plural"]
            same = [1 for t2, _f2, _r in groups if t2["key"] == t["key"]]
            if len(same) > 1:
                label += f" – as {f['name'].lower()}"
            ttk.Label(head, text=f"{label} ({len(rows)})", style="PanelH3.TLabel").pack(side="left")
            if db.can_edit and not self.deleted:
                widgets.link(head, f"+ New {t['name'].lower()}",
                             lambda t=t, f=f: app.new_record(t["key"], {f["key"]: self.record["id"]}),
                             style="PanelLink.TLabel").pack(side="right")
            bf = bpm.board_field(t)
            for r in rows[:100]:
                line = ttk.Frame(sf.body, style="Panel.TFrame")
                line.pack(fill="x", pady=1)
                widgets.link(line, r["title"], lambda r=r: app.open_record(r["id"]),
                             style="PanelLink.TLabel").pack(side="left")
                if bf and r["data"].get(bf["key"]):
                    ttk.Label(line, text=r["data"][bf["key"]], style="PanelHelp.TLabel").pack(side="right")
            if len(rows) > 100:
                ttk.Label(sf.body, text=f"…and {len(rows) - 100} more", style="PanelHelp.TLabel").pack(anchor="w")

    # history
    def _fill_history(self):
        frame = self._clear("History")
        db = self.db
        rows = db.history(self.record["id"], limit=400)
        sf = widgets.ScrollFrame(frame, self.app, panel=True)
        sf.pack(fill="both", expand=True)
        ttk.Label(sf.body, text="Every change to this record, newest first.",
                  style="PanelHelp.TLabel").pack(anchor="w", pady=(0, 6))
        for h in rows:
            what = ACTION_WORDS.get(h["action"], h["action"])
            if h["action"] == "changed":
                old = h["old"] or "(empty)"
                new = h["new"] or "(empty)"
                what = f"{h['field']}: {old} → {new}"
            elif h["action"] in ("task-added", "task-done", "task-reopened", "file-added"):
                what += f": {h['new']}"
            elif h["action"] == "file-removed":
                what += f": {h['old']}"
            elif h["action"] in ("note-added", "note-changed", "note-deleted") and (h["new"] or h["old"]):
                what += f": {h['new'] or h['old']}"
            line = ttk.Frame(sf.body, style="Panel.TFrame")
            line.pack(fill="x", pady=2)
            meta = validate.format_stamp(h["at"])
            if h["user_name"] and db.mode == "team":
                meta += "  ·  " + h["user_name"]
            ttk.Label(line, text=meta, style="PanelHelp.TLabel").pack(anchor="w")
            lab = ttk.Label(line, text=what, style="Panel.TLabel", justify="left", wraplength=fs(320))
            lab.pack(anchor="w")
            line.bind("<Configure>", lambda e, b=lab: b.configure(wraplength=max(120, e.width - 8)))

    # ------------------------------------------------------------ actions
    def to_list(self):
        self.app.go("list", type_key=self.type_key,
                    select=self.record["id"] if self.record else None)

    def escape(self):
        self.app.back()

    def save(self) -> bool:
        """Check and save. Returns True when saved (or nothing needed saving)."""
        if self.readonly:
            return True
        db, t, form = self.db, self.t, self.form
        # tidy any box the cursor is still in
        self.focus_set()
        if bpm.get_type(db.blueprint, self.type_key) is not t:
            # The design of this list was changed (by someone else) while the
            # form was open. Checking what was typed against a form that no
            # longer matches could fail on a field that is not even shown.
            self.app.toast("The design of this list was changed while you had this page open. "
                           "The form is now up to date: check it, then save again.")
            self.app.replace("record", **self.rebuild_state())
            return False
        if self.is_new:
            rid = save_new_record(self.app, t, form)
            if rid is None:
                if any(lab.winfo_manager() for lab in form.errors.values()):
                    self._errors_toast()
                return False
            form.mark_clean()
            self.app.toast(f"{t['name']} added", "good")
            self.app.refresh_sidebar()
            self.app.replace("record", record_id=rid)
            return True
        data, errors = db.validate_record(self.type_key, form.raw(), base=self.base)
        if errors:
            form.show_errors(errors)
            self._errors_toast(errors)
            return False
        changes = {k: data.get(k) for k in form.editors if data.get(k) != self.base.get(k)}
        if not changes:
            form.set_values(data)
            self.app.toast("Nothing to save – no changes were made.")
            return True
        clash = [(f, r) for f, r in db.unique_clashes(self.type_key, data, self.record["id"])
                 if f["key"] in changes]
        if clash:
            f, r = clash[0]
            form.show_errors({f["key"]: f"{r['title']} ({r['ref']}) already has this. "
                                        f"{f['name']} must be different for every {t['name'].lower()}."})
            self._errors_toast()
            return False
        try:
            try:
                rec = db.update_record(self.record["id"], changes, base=self.base)
            except dbm.Conflict as c:
                names = ", ".join(bpm.get_field(t, k)["name"] for k in c.theirs if bpm.get_field(t, k))
                pick = widgets.choose(
                    self.app, "Someone else changed this record",
                    f"{c.who} changed {names} while you had this record open.\n\n"
                    "Whose version of those fields should be kept?",
                    [("mine", "Keep mine"), ("theirs", "Keep theirs")], cancel="Go back")
                if pick == "mine":
                    rec = db.update_record(self.record["id"], changes, force=True)
                elif pick == "theirs":
                    keep = {k: v for k, v in changes.items() if k not in c.theirs}
                    rec = db.update_record(self.record["id"], keep, force=True) if keep \
                        else db.get_record(self.record["id"])
                else:
                    return False
        except dbm.DBError as exc:
            widgets.error(self.app, "Could not save", str(exc))
            return False
        self.record = rec
        self.base = dict(rec["data"])
        form.set_values(rec["data"])
        self._update_heading()
        self._fill_related()
        if self._tab == 4:
            self._fill_history()
        self.app.refresh_sidebar()
        self.app.toast("Saved", "good")
        return True

    def _errors_toast(self, errors=None):
        n = len(errors) if errors else sum(1 for lab in self.form.errors.values() if lab.winfo_manager())
        self.app.toast(f"Not saved: {n} thing{'s' if n != 1 else ''} to put right, marked in red.",
                       "bad")
        for k, lab in self.form.errors.items():
            if lab.winfo_manager():
                self.scroll.show(self.form.editors[k].widget)
                break

    def can_leave(self) -> bool:
        if self.readonly or not self.winfo_exists() or not self.form.is_dirty():
            return True
        what = f"this new {self.t['name'].lower()}" if self.is_new else "your changes"
        pick = widgets.choose(self.app, "Unsaved changes", f"You have not saved {what}.",
                              [("save", "Save"), ("discard", "Discard")], cancel="Keep editing")
        if pick == "save":
            if self.is_new:
                rid = save_new_record(self.app, self.t, self.form)
                if rid is None:
                    if any(lab.winfo_manager() for lab in self.form.errors.values()):
                        self._errors_toast()
                    return False
                self.form.mark_clean()
                self.record = self.db.get_record(rid)
                self.is_new = False
                self.app.refresh_sidebar()
                return True
            return self.save()
        if pick == "discard":
            self.form.mark_clean()
            return True
        return False

    def delete(self):
        r = self.record
        if not widgets.confirm(self.app, "Delete", f"Move “{r['title']}” to the recycle bin?\n\n"
                               "You can get it back from Tools > Recycle bin.",
                               yes="Delete", danger=True):
            return
        self.db.delete_records([r["id"]])
        self.form.mark_clean()
        self.app.toast("Moved to the recycle bin", "good")
        self.app.refresh_sidebar()
        self.app.replace("list", type_key=self.type_key)

    def restore(self):
        self.db.restore_records([self.record["id"]])
        self.app.toast("Restored", "good")
        self.app.refresh_sidebar()
        self.app.replace("record", record_id=self.record["id"])

    def duplicate(self):
        if not self.can_leave():
            return
        preset = {k: v for k, v in self.record["data"].items()
                  if not (bpm.get_field(self.t, k) or {}).get("unique")}
        self.form.mark_clean()
        self.app.new_record(self.type_key, preset)

    def state(self) -> dict:
        if self.is_new:
            return {"type_key": self.type_key, "preset": self.preset}
        return {"record_id": self.record["id"], "tab": self._tab}

    def rebuild_state(self) -> dict:
        """Like state(), but carries anything typed and not yet saved, so a
        theme switch does not lose it."""
        kw = self.state()
        if not self.readonly and self.form.is_dirty():
            kw["restore"] = self.form.raw()
        return kw
