"""The administration pages: people and passwords, settings, the recycle bin
and the history of changes."""
from __future__ import annotations

import copy
import csv
import datetime as _dt
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import paths, security, validate
from . import widgets
from .base import Page
from .recordpage import ACTION_WORDS, human_size
from .styles import fs

ROLE_ORDER = ["editor", "viewer", "admin"]
ROLE_HELP = {
    "admin": "Everything, including the design and people.",
    "editor": "Can add and change records.",
    "viewer": "Can look at everything but not change it.",
}
IDLE_CHOICES = [(0, "Never"), (5, "5 minutes"), (15, "15 minutes"), (30, "30 minutes"),
                (60, "1 hour")]
HISTORY_PAGE = 300
SYNC_NAMES = "OneDrive, Dropbox, Google Drive or iCloud"

# The form dialog that is open (or was open last). Tests drive it through this.
LAST_DIALOG = None


# ----------------------------------------------------------------- helpers
def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def open_folder(app, folder: str) -> None:
    """Show a folder in Explorer / Finder."""
    if app.testing:
        app.test_log.append(("open-folder", folder, ""))
        return
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder], stdin=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", folder], stdin=subprocess.DEVNULL)
    except Exception:
        widgets.error(app, "Could not open the folder", f"The folder is:\n\n{folder}")


def reveal_file(app, path: str) -> None:
    """Open the folder a file is in, with the file selected where that is possible."""
    if app.testing:
        app.test_log.append(("reveal", path, ""))
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path], stdin=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)], stdin=subprocess.DEVNULL)
    except Exception:
        widgets.error(app, "Could not open the folder", f"The file is:\n\n{path}")


def friendly_day(iso: str | None) -> str:
    """'2026-10-07' -> '07/10/2026 (today)'."""
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso or "")
    delta = (_dt.date.today() - d).days
    text = d.strftime("%d/%m/%Y")
    if delta == 0:
        return text + " (today)"
    if delta == 1:
        return text + " (yesterday)"
    if delta > 1:
        return f"{text} ({delta} days ago)"
    return text


def wrap_to(label: ttk.Label, container: tk.Misc, margin: int = 0, least: int = 160) -> None:
    """Keep a label's text wrapped to the width of its container."""
    def fit(event):
        label.configure(wraplength=max(least, event.width - margin))
    container.bind("<Configure>", fit, add="+")


class FormDialog(widgets.Dialog):
    """A small form in a window: labelled boxes, an inline error line and one
    named action. on_submit(values) does the work; it returns an error
    sentence (or raises DBError) to keep the window open, or None when done."""

    def __init__(self, app, title: str, action: str, on_submit, intro: str | None = None,
                 width: int = 440, danger: bool = False):
        global LAST_DIALOG
        super().__init__(app, title, width=fs(width))
        LAST_DIALOG = self
        self.on_submit = on_submit
        self.vars: dict[str, tk.Variable] = {}
        self.entries: dict[str, tk.Widget] = {}
        self._wrap = fs(width) - 40
        self._first = None
        if intro:
            ttk.Label(self.body, text=intro, wraplength=self._wrap, justify="left").pack(
                anchor="w", pady=(0, 4))
        self.error = ttk.Label(self.body, text="", style="Error.TLabel", wraplength=self._wrap,
                               justify="left")
        self.action = self.add_button(action, self.submit, accent=not danger,
                                      style="Danger.TButton" if danger else None)
        self._default = self.action
        self.add_button("Cancel", self.cancel)

    # building
    def heading(self, text: str):
        ttk.Label(self.body, text=text, style="H3.TLabel").pack(anchor="w", pady=(12, 0))

    def note(self, text: str, style: str = "Help.TLabel"):
        lab = ttk.Label(self.body, text=text, style=style, wraplength=self._wrap, justify="left")
        lab.pack(anchor="w", pady=(6, 0))
        return lab

    def row(self) -> ttk.Frame:
        """A line that holds two boxes side by side."""
        f = ttk.Frame(self.body)
        f.pack(fill="x")
        f.columnconfigure(0, weight=1, uniform="half")
        f.columnconfigure(1, weight=1, uniform="half")
        return f

    def entry(self, key: str, label: str, value: str = "", secret: bool = False,
              parent: ttk.Frame | None = None, column: int = 0) -> ttk.Entry:
        if parent is None:
            cell = ttk.Frame(self.body)
            cell.pack(fill="x")
        else:
            cell = ttk.Frame(parent)
            cell.grid(row=0, column=column, sticky="new", padx=(0, 8) if column == 0 else (8, 0))
        ttk.Label(cell, text=label, style="Field.TLabel").pack(anchor="w", pady=(10, 3))
        var = tk.StringVar(self, value=value)
        e = ttk.Entry(cell, textvariable=var, show="•" if secret else "")
        e.pack(fill="x")
        self.vars[key], self.entries[key] = var, e
        self._first = self._first or e
        return e

    def passwords(self, key: str = "password", label: str = "Password"):
        """Two boxes side by side: the password and the same again."""
        r = self.row()
        self.entry(key, label, secret=True, parent=r, column=0)
        self.entry(key + "2", "The same again", secret=True, parent=r, column=1)

    def roles(self, key: str = "role", value: str = "editor"):
        ttk.Label(self.body, text="What they can do", style="Field.TLabel").pack(
            anchor="w", pady=(12, 2))
        var = tk.StringVar(self, value=value)
        self.vars[key] = var
        for role in ROLE_ORDER:
            line = ttk.Frame(self.body)
            line.pack(fill="x", pady=1)
            rb = ttk.Radiobutton(line, text=dbm.ROLES[role], variable=var, value=role)
            rb.pack(side="left")
            ttk.Label(line, text="– " + ROLE_HELP[role][0].lower() + ROLE_HELP[role][1:].rstrip("."),
                      style="Help.TLabel").pack(side="left", padx=(6, 0))

    # running
    def values(self) -> dict:
        return {k: v.get() for k, v in self.vars.items()}

    def set(self, **values):
        for k, v in values.items():
            self.vars[k].set(v)

    def fail(self, message: str, key: str | None = None):
        self.error.configure(text=message)
        if not self.error.winfo_manager():
            self.error.pack(anchor="w", pady=(10, 0))
        self.update_idletasks()
        need = self.winfo_reqheight()          # make room for the message
        if need > self.winfo_height():
            self.geometry(f"{self.winfo_width()}x{need}")
        if key in self.entries:
            self.entries[key].focus_set()

    def submit(self):
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
            problem = self.on_submit(self.values())
        except dbm.DBError as exc:
            problem = str(exc)
        finally:
            try:
                self.configure(cursor="")
            except tk.TclError:
                pass
        if problem:
            if isinstance(problem, tuple):
                self.fail(problem[0], problem[1])
            else:
                self.fail(str(problem))
            return
        self.ok(True)

    def run(self) -> bool:
        self.default_on_enter()
        return bool(self.show(focus=self._first))


def new_password_problem(v: dict, username: str, key: str = "password"):
    """Check the pair of password boxes. Returns (message, box key) or None."""
    if not v[key]:
        return "Type a password.", key
    problem = security.password_problem(v[key], username)
    if problem:
        return problem, key
    if v[key] != v[key + "2"]:
        return "The two passwords are not the same. Type them again.", key + "2"
    return None


def change_my_password(app) -> bool:
    """Set or change the signed-in person's own password. True when changed."""
    db = app.db
    had = db.has_password
    me = db.user

    def go(v):
        problem = new_password_problem(v, me["username"])
        if problem:
            return problem
        db.set_password(me["id"], v["password"], v.get("old"))
        return None

    if had:
        d = FormDialog(app, "Change my password", "Change password", go)
        d.entry("old", "Your password now", secret=True)
        d.passwords("password", "New password")
    else:
        d = FormDialog(app, "Set a password", "Set password", go,
                       intro="From now on this CRM will ask for this password before it opens.")
        d.passwords()
        d.note("There is no way to get in without it, so choose something you will remember "
               "– or write it down and keep it somewhere safe.")
    if not d.run():
        return False
    app.toast("Password changed" if had else "Password set. This CRM now asks for it when it opens.",
              "good")
    if not had:
        app.rebuild()          # the File menu gains "Lock"
    return True


def remove_my_password(app) -> bool:
    db = app.db

    def go(v):
        db.set_password(db.user["id"], "", v["old"])
        return None

    d = FormDialog(app, "Remove the password", "Remove password", go, danger=True,
                   intro="Without a password this CRM opens straight away for anyone who can "
                         "open the file. Type the password to confirm.")
    d.entry("old", "Your password", secret=True)
    if not d.run():
        return False
    app.toast("Password removed. This CRM now opens without one.", "good")
    app.rebuild()
    return True


class _ScrollPage(Page):
    """A page whose content scrolls: put things in self.body."""

    def _scroll_body(self) -> ttk.Frame:
        self.scroller = widgets.ScrollFrame(self, self.app)
        self.scroller.pack(fill="both", expand=True)
        body = ttk.Frame(self.scroller.body)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 18))
        return body

    def card(self, title: str | None = None) -> tk.Frame:
        card = widgets.card(self.body, self.app, padding=16)
        card.pack(fill="x", pady=(0, 12))
        if title:
            ttk.Label(card, text=title, style="PanelH2.TLabel").pack(anchor="w", pady=(0, 2))
        return card

    def para(self, card, text: str, style: str = "Panel.TLabel", pady=(4, 0)) -> ttk.Label:
        lab = ttk.Label(card, text=text, style=style, justify="left")
        lab.pack(anchor="w", fill="x", pady=pady)
        wrap_to(lab, lab, margin=2)
        return lab

    def line(self, card, text: str, help_text: str | None = None, pady=(10, 0)):
        """A line of a card: words on the left, buttons on the right.
        Returns (frame for buttons, the main label)."""
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=pady)
        right = ttk.Frame(row, style="Panel.TFrame")
        right.pack(side="right", anchor="n", padx=(16, 0))
        left = ttk.Frame(row, style="Panel.TFrame")
        left.pack(side="left", fill="x", expand=True)
        lab = ttk.Label(left, text=text, style="Panel.TLabel", justify="left")
        lab.pack(anchor="w")
        labs = [lab]
        if help_text:
            h = ttk.Label(left, text=help_text, style="PanelHelp.TLabel", justify="left")
            h.pack(anchor="w", pady=(2, 0))
            labs.append(h)

        def fit(event, labs=labs):
            for one in labs:
                one.configure(wraplength=max(160, event.width - 4))
        left.bind("<Configure>", fit)
        return right, lab


# ------------------------------------------------------ people and passwords
class PeoplePage(_ScrollPage):
    nav_key = "settings"
    auto_refresh = True

    def __init__(self, parent, app, select: int | None = None, **_kw):
        super().__init__(parent, app)
        self._select = select
        self.tree = None
        db = self.db
        db.refresh_account()       # an administrator may have changed my role since I signed in
        if not db.is_admin:
            self.header("People and passwords", back=True)
            self.body = self._scroll_body()
            self._not_admin()
        elif db.mode == "team":
            right = self.header("People and passwords",
                                "Everyone signs in with their own username and password.", back=True)
            ttk.Button(right, text="Add a person…", style="Accent.TButton",
                       command=self.add_person).pack(side="right")
            self._team()
        else:
            self.header("People and passwords", "Who can open this CRM.", back=True)
            self.body = self._scroll_body()
            self._solo()

    def state(self) -> dict:
        sel = self.selected()
        return {"select": sel["id"]} if sel else {}

    # ------------------------------------------------------- not an admin
    def _not_admin(self):
        db = self.db
        card = self.card("Only an administrator can manage people")
        admins = [u["name"] for u in db.users(active_only=True) if u["role"] == "admin"]
        who = (" The administrator of this CRM is " + admins[0] + "." if len(admins) == 1 else
               " The administrators of this CRM are " + ", ".join(admins[:-1]) + " and " + admins[-1] + "."
               if admins else "")
        self.para(card, "Adding people, changing what they can do and resetting passwords is "
                        "for administrators." + who)
        right, _lab = self.line(card, "You can change your own password.", pady=(12, 0))
        ttk.Button(right, text="Change my password…",
                   command=lambda: change_my_password(self.app)).pack(side="right")

    # --------------------------------------------------------- one person
    def _solo(self):
        db, app = self.db, self.app
        card = self.card("Just you")
        if db.has_password:
            right, _lab = self.line(
                card, "This CRM asks for your password before it opens.",
                "It also locks itself when it has not been used for a while (see Settings)."
                + (" The file is encrypted, so the password cannot be removed." if db.encrypted else ""),
                pady=(4, 0))
            if not db.encrypted:
                self.remove_btn = ttk.Button(right, text="Remove password…", style="Danger.TButton",
                                             command=lambda: remove_my_password(app))
                self.remove_btn.pack(side="right", padx=(8, 0))
            self.password_btn = ttk.Button(right, text="Change password…",
                                           command=lambda: change_my_password(app))
            self.password_btn.pack(side="right")
        else:
            right, _lab = self.line(
                card, "Right now this CRM opens without signing in.",
                "Anyone who can open the file on this computer can see what is in it. "
                "A password makes it ask first.", pady=(4, 0))
            self.password_btn = ttk.Button(right, text="Set a password…",
                                           command=lambda: change_my_password(app))
            self.password_btn.pack(side="right")

        card = self.card("Share this CRM with other people")
        self.para(card, "Several people can use the same CRM at once. This is what changes:")
        for text in (
                "Everyone signs in with their own username and password, and the history shows "
                "who changed what.",
                "You become the administrator: you add people and decide who can change records "
                "and who can only look.",
                "Put the CRM file on a shared drive that everyone can reach, and have everyone "
                f"open that same file. Do not use a synced folder ({SYNC_NAMES}): two people "
                "saving at once would damage the file."):
            row = ttk.Frame(card, style="Panel.TFrame")
            row.pack(fill="x", pady=(6, 0))
            ttk.Label(row, text="•", style="PanelDim.TLabel").pack(side="left", anchor="n", padx=(2, 8))
            lab = ttk.Label(row, text=text, style="Panel.TLabel", justify="left")
            lab.pack(side="left", fill="x", expand=True)
            wrap_to(lab, row, margin=30)
        if paths.looks_synced(db.path):
            self.para(card, "This CRM is in a synced folder at the moment. Move the file to a "
                            "shared drive before other people start using it.",
                      style="PanelWarn.TLabel", pady=(10, 0))
        self.share_btn = ttk.Button(card, text="Share this CRM with other people…",
                                    style="Accent.TButton", command=self.share)
        self.share_btn.pack(anchor="w", pady=(14, 0))

    def share(self):
        """Turn a one-person CRM into a shared one by adding the first other person."""
        db, app = self.db, self.app
        me = db.user
        need_pw = not db.has_password

        def go(v):
            name, username = v["name"].strip(), v["username"].strip()
            my_name = v["my_name"].strip()
            if not my_name:
                return "Type your own name, so people can see who changed what.", "my_name"
            if need_pw:
                problem = new_password_problem(v, me["username"], "my_password")
                if problem:
                    return problem
            if not name:
                return "Type the other person's name.", "name"
            if not username:
                return "Give them a username to sign in with.", "username"
            if username.lower() == me["username"].lower():
                return "That username is yours. Give them a different one.", "username"
            problem = new_password_problem(v, username)
            if problem:
                return problem
            if not db.has_password:
                db.set_password(me["id"], v["my_password"])
            if my_name != db.user["name"]:
                db.update_user(me["id"], name=my_name)
            db.add_user(username, name, v["role"], v["password"])
            return None

        d = FormDialog(app, "Share this CRM with other people", "Share and add this person", go,
                       width=520)
        d.heading("You")
        r = d.row()
        d.entry("my_name", "Your name", "" if me["name"] == "Me" else me["name"], parent=r, column=0)
        cell = ttk.Frame(r)
        cell.grid(row=0, column=1, sticky="new", padx=(8, 0))
        ttk.Label(cell, text="Your username", style="Field.TLabel").pack(anchor="w", pady=(10, 3))
        ttk.Label(cell, text=me["username"]).pack(anchor="w", pady=(5, 0))
        if need_pw:
            d.passwords("my_password", "Your password")
        else:
            d.note("You keep the password you have now.")
        d.heading("The first other person")
        r = d.row()
        d.entry("name", "Their name", parent=r, column=0)
        d.entry("username", "Their username", parent=r, column=1)
        d.passwords("password", "Their password")
        d.roles()
        d.note("Tell them their username and password yourself. They can change the password "
               "in Settings once they have signed in.")
        if not d.run():
            return
        app.toast(f"{d.vars['name'].get().strip()} can now sign in. From now on this CRM asks "
                  "everyone to sign in.", "good")
        app.rebuild()

    # --------------------------------------------------------------- team
    def _team(self):
        app, c = self.app, self.c
        cols = ("name", "username", "role", "last", "status")
        frame, tree = widgets.make_tree(self, cols, selectmode="browse", height=4)
        frame.pack(fill="both", expand=True, padx=24)
        self.tree = tree
        for key, text, width, stretch in (("name", "Name", 200, True), ("username", "Username", 130, False),
                                          ("role", "What they can do", 150, False),
                                          ("last", "Last signed in", 140, False),
                                          ("status", "Status", 110, False)):
            tree.heading(key, text=text, anchor="w")
            tree.column(key, width=fs(width), minwidth=fs(80), stretch=stretch, anchor="w")
        tree.tag_configure("off", foreground=c["dim"])
        tree.tag_configure("locked", foreground=c["warn"])
        tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        tree.bind("<Double-1>", lambda _e: self.edit_person())
        tree.bind("<Return>", lambda _e: self.edit_person())

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=24, pady=(8, 0))
        self.edit_btn = ttk.Button(bar, text="Change name or role…", command=self.edit_person)
        self.edit_btn.pack(side="left")
        self.pw_btn = ttk.Button(bar, text="Set a new password…", command=self.set_password)
        self.pw_btn.pack(side="left", padx=(8, 0))
        self.onoff_btn = ttk.Button(bar, text="Switch off…", command=self.switch)
        self.onoff_btn.pack(side="left", padx=(8, 0))
        self.unlock_btn = ttk.Button(bar, text="Unlock", command=self.unlock)
        self.hint = ttk.Label(bar, text="", style="Help.TLabel")
        self.hint.pack(side="left", padx=(12, 0))

        legend = widgets.card(self, app, padding=12)
        legend.pack(fill="x", padx=24, pady=(12, 16))
        for i, role in enumerate(("admin", "editor", "viewer")):
            ttk.Label(legend, text=dbm.ROLES[role], style="PanelH3.TLabel").grid(
                row=i, column=0, sticky="nw", padx=(0, 18), pady=1)
            ttk.Label(legend, text=ROLE_HELP[role], style="PanelDim.TLabel").grid(
                row=i, column=1, sticky="w", pady=1)
        if self.db.encrypted:
            ttk.Label(legend, text="This CRM is encrypted: each person's password also unlocks the file.",
                      style="PanelHelp.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.refresh()

    def _status(self, u: dict, keyfile, now: str) -> tuple[str, str]:
        if not u["active"]:
            return "Switched off", "off"
        locked = bool(u["locked_until"] and u["locked_until"] > now)
        if keyfile is not None and keyfile.locked(u["username"], now):
            locked = True
        if locked:
            return "Locked", "locked"
        return "Active", ""

    def refresh(self) -> None:
        tree = self.tree
        if tree is None or not tree.winfo_exists():
            return
        db = self.db
        keep = self._select
        self._select = None
        if keep is None and tree.selection():
            keep = int(tree.selection()[0])
        tree.delete(*tree.get_children())
        self.people = {u["id"]: u for u in db.users()}
        keyfile = security.KeyFile.load(db.path) if db.encrypted else None
        now = validate.now_stamp()
        for u in self.people.values():
            status, tag = self._status(u, keyfile, now)
            u["status"], u["locked"] = status, tag == "locked"
            name = u["name"] + ("  (you)" if u["id"] == db.user["id"] else "")
            last = validate.format_stamp(u["last_login"]) if u["last_login"] else "Never"
            tree.insert("", "end", iid=str(u["id"]), tags=(tag,) if tag else (),
                        values=(name, u["username"], dbm.ROLES.get(u["role"], u["role"]), last, status))
        if keep is not None and tree.exists(str(keep)):
            tree.selection_set(str(keep))
            tree.see(str(keep))
        self._selection_changed()

    def selected(self) -> dict | None:
        if self.tree is None or not self.tree.winfo_exists():
            return None
        sel = self.tree.selection()
        return self.people.get(int(sel[0])) if sel else None

    def _selection_changed(self):
        u = self.selected()
        on = "normal" if u else "disabled"
        self.edit_btn.configure(state=on)
        mine = bool(u) and u["id"] == self.db.user["id"]
        active = bool(u) and bool(u["active"])
        self.pw_btn.configure(state=on if (not u or active) else "disabled",
                              text="Change my password…" if mine else "Set a new password…")
        self.onoff_btn.configure(text="Switch off…" if (not u or active) else "Switch back on…",
                                 state="disabled" if (not u or mine) else "normal")
        if u and u.get("locked"):
            self.unlock_btn.pack(side="left", padx=(8, 0), before=self.hint)
        else:
            self.unlock_btn.pack_forget()
        if not u:
            self.hint.configure(text="Choose a person to change them.")
        elif u.get("locked"):
            self.hint.configure(text=f"Locked after {dbm.MAX_FAILED} wrong passwords. It unlocks by itself "
                                     f"after {dbm.LOCK_MINUTES} minutes.")
        elif mine:
            self.hint.configure(text="This is you.")
        else:
            self.hint.configure(text="")

    def add_person(self):
        db, app = self.db, self.app

        def go(v):
            name, username = v["name"].strip(), v["username"].strip()
            if not name:
                return "Type their name.", "name"
            if not username:
                return "Give them a username to sign in with.", "username"
            problem = new_password_problem(v, username)
            if problem:
                return problem
            d.made = db.add_user(username, name, v["role"], v["password"])
            return None

        d = FormDialog(app, "Add a person", "Add this person", go, width=480)
        d.made = None
        r = d.row()
        d.entry("name", "Name", parent=r, column=0)
        d.entry("username", "Username", parent=r, column=1)
        d.passwords()
        d.roles()
        d.note("Tell them their username and password yourself. They can change the password "
               "in Settings once they have signed in.")
        if not d.run():
            return
        self._select = d.made
        self.refresh()
        app.toast(f"{d.vars['name'].get().strip()} can now sign in as "
                  f"“{d.vars['username'].get().strip()}”", "good")

    def edit_person(self):
        u = self.selected()
        if not u:
            return
        db, app = self.db, self.app

        def go(v):
            name = v["name"].strip()
            if not name:
                return "Type their name.", "name"
            db.update_user(u["id"], name=name, role=v["role"])
            return None

        d = FormDialog(app, f"Change {u['name']}", "Save changes", go, width=480)
        d.entry("name", "Name", u["name"])
        d.note(f"Username: {u['username']} (a username cannot be changed)")
        d.roles(value=u["role"])
        if not d.run():
            return
        mine = u["id"] == db.user["id"]
        if mine:
            # my own name or role changed: the top bar, menus and sidebar follow
            app.rebuild()
            if not db.is_admin:
                app.toast("You are no longer an administrator of this CRM.")
        else:
            self.refresh()
            app.toast("Saved", "good")

    def set_password(self):
        u = self.selected()
        if not u or not u["active"]:
            return
        db, app = self.db, self.app
        if u["id"] == db.user["id"]:
            change_my_password(app)
            return

        def go(v):
            problem = new_password_problem(v, u["username"])
            if problem:
                return problem
            db.set_password(u["id"], v["password"])
            return None

        d = FormDialog(app, f"New password for {u['name']}", "Set password", go,
                       intro=f"{u['name']}'s old password stops working straight away.")
        d.passwords("password", "New password")
        d.note("Tell them the new password yourself. They can change it in Settings once "
               "they have signed in.")
        if d.run():
            self.refresh()
            app.toast(f"New password set for {u['name']}", "good")

    def switch(self):
        u = self.selected()
        if not u or u["id"] == self.db.user["id"]:
            return
        db, app = self.db, self.app
        if u["active"]:
            if not widgets.confirm(
                    app, f"Switch off {u['name']}",
                    f"{u['name']} will no longer be able to sign in. Everything they added stays, "
                    "and the history keeps their name.\n\nYou can switch them back on later; they "
                    "will need a new password then.", yes=f"Switch off {u['name']}", danger=True):
                return
            try:
                db.update_user(u["id"], active=False)
            except dbm.DBError as exc:
                widgets.error(app, "That did not work", str(exc))
                return
            self.refresh()
            app.toast(f"{u['name']} can no longer sign in", "good")
            return

        def go(v):
            problem = new_password_problem(v, u["username"])
            if problem:
                return problem
            db.enable_user(u["id"], v["password"])
            return None

        d = FormDialog(app, f"Switch {u['name']} back on", "Switch back on", go,
                       intro=f"Choose a new password for {u['name']}. Their old one no longer works.")
        d.passwords("password", "New password")
        d.note("Tell them the new password yourself.")
        if d.run():
            self.refresh()
            app.toast(f"{u['name']} can sign in again", "good")

    def unlock(self):
        u = self.selected()
        if not u:
            return
        try:
            self.db.update_user(u["id"], unlock=True)
        except dbm.DBError as exc:
            widgets.error(self.app, "That did not work", str(exc))
            return
        self.refresh()
        self.app.toast(f"{u['name']} can try signing in again", "good")


# ----------------------------------------------------------------- settings
class SettingsPage(_ScrollPage):
    nav_key = "settings"

    def __init__(self, parent, app, **_kw):
        super().__init__(parent, app)
        self.db.refresh_account()
        self.header("Settings")
        self.body = self._scroll_body()
        self._this_crm()
        self._appearance()
        self._backups()
        self._saving()
        self._security()
        if self.db.has_examples() and self.db.can_edit:
            self._examples()

    # ------------------------------------------------------------ this CRM
    def _this_crm(self):
        db, app = self.db, self.app
        card = self.card("This CRM")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Name", style="PanelField.TLabel").pack(side="left", padx=(0, 10))
        if db.is_admin:
            self.name_var = tk.StringVar(self, value=db.name)
            self.name_entry = ttk.Entry(row, textvariable=self.name_var, width=34)
            self.name_entry.pack(side="left")
            self.name_btn = ttk.Button(row, text="Rename", command=self.rename, state="disabled")
            self.name_btn.pack(side="left", padx=(8, 0))
            widgets.trace_var(self.name_var, self.name_entry, lambda *_a: self.name_btn.configure(
                state="normal" if self.name_var.get().strip() not in ("", db.name) else "disabled"))
            self.name_entry.bind("<Return>", lambda _e: self.rename())
        else:
            ttk.Label(row, text=db.name, style="Panel.TLabel").pack(side="left")

        right, _lab = self.line(card, "Everything is kept in one file:", db.path)
        ttk.Button(right, text="Show in folder", command=lambda: reveal_file(app, db.path)).pack(side="right")
        if paths.looks_synced(db.path):
            self.para(card, f"This file is in a synced folder ({SYNC_NAMES}). That is fine for one "
                            "person on one computer, but do not open it on two computers at once.",
                      style="PanelWarn.TLabel", pady=(6, 0))

        s = db.stats()
        bits = [plural(s["records"], "record"), plural(s["notes"], "note"), plural(s["tasks"], "task"),
                plural(s["files"], "file"), human_size(s["size"])]
        self.stats_label = self.para(card, "  ·  ".join(bits), style="PanelDim.TLabel", pady=(10, 0))
        links = ttk.Frame(card, style="Panel.TFrame")
        links.pack(fill="x", pady=(8, 0))
        binned = s["deleted"]
        widgets.link(links, f"Recycle bin ({binned:,})" if binned else "Recycle bin",
                     lambda: app.go("bin"), style="PanelLink.TLabel").pack(side="left")
        widgets.link(links, "History of changes", lambda: app.go("history"),
                     style="PanelLink.TLabel").pack(side="left", padx=(18, 0))

    def rename(self):
        db, app = self.db, self.app
        name = self.name_var.get().strip()
        if not name or name == db.name:
            return
        bp = copy.deepcopy(db.blueprint)
        bp["name"] = name
        try:
            db.save_blueprint(bp, note=f"Renamed to {name[:80]}")
        except dbm.DBError as exc:
            widgets.error(app, "The name was not changed", str(exc))
            return
        app.rebuild()
        app.toast(f"This CRM is now called {db.name}", "good")

    # ------------------------------------------------------------ security
    def _security(self):
        db, app = self.db, self.app
        card = self.card("Security")
        team = db.mode == "team"
        if team:
            me = db.user
            right, _lab = self.line(
                card, f"You are signed in as {me['name']} ({me['username']}).",
                f"{dbm.ROLES.get(me['role'], me['role'])}: {ROLE_HELP.get(me['role'], '')}",
                pady=(4, 0))
            self.password_btn = ttk.Button(right, text="Change my password…",
                                           command=lambda: change_my_password(app))
            self.password_btn.pack(side="right")
        elif db.has_password:
            right, _lab = self.line(card, "This CRM asks for your password before it opens.",
                                    pady=(4, 0))
            self.password_btn = ttk.Button(right, text="Change password…",
                                           command=lambda: change_my_password(app))
            self.password_btn.pack(side="right")
        else:
            right, _lab = self.line(card, "This CRM opens without a password.",
                                    "Fine if you are the only person who uses this computer.",
                                    pady=(4, 0))
            self.password_btn = ttk.Button(right, text="Set a password…",
                                           command=lambda: change_my_password(app))
            self.password_btn.pack(side="right")

        if db.has_password:
            try:
                minutes = int(db.get_meta("idle_minutes", "15") or 0)
            except ValueError:
                minutes = 15
            label = dict(IDLE_CHOICES).get(minutes, f"{minutes} minutes")
            if db.is_admin:
                right, _lab = self.line(
                    card, "Lock when it has not been used for",
                    f"Locking hides everything until the password is typed again. "
                    f"{widgets.MOD_LABEL}+L locks straight away.")
                self.idle_var = tk.StringVar(self, value=label)
                values = [text for _m, text in IDLE_CHOICES]
                if label not in values:
                    values.append(label)
                self.idle_box = ttk.Combobox(right, textvariable=self.idle_var, state="readonly",
                                             values=values, width=12)
                self.idle_box.pack(side="right")
                self.idle_box.bind("<<ComboboxSelected>>", lambda _e: self.set_idle())
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    self.idle_box.bind(seq, lambda _e: "break")
            else:
                self.line(card, "It never locks by itself." if not minutes else
                          f"It locks when it has not been used for {label}.",
                          f"{widgets.MOD_LABEL}+L locks straight away.")

        if db.encrypted:
            keys = os.path.basename(db.path) + ".keys"
            right, _lab = self.line(
                card, "This file is encrypted.",
                "Nobody can read it without a password or the recovery code, even with other "
                f"software. Keep “{keys}” beside the CRM file: it cannot be opened without it.")
            if db.is_admin:
                self.recovery_btn = ttk.Button(right, text="Make a new recovery code…",
                                               command=self.new_recovery_code)
                self.recovery_btn.pack(side="right")
        else:
            self.line(card, "This file is not encrypted.",
                      "A password stops people opening it in CRM Builder, but someone who got hold "
                      "of the file could still read it with other software. Encryption is chosen "
                      "when a CRM is created; it cannot be switched on afterwards.")

        if db.is_admin:
            right, _lab = self.line(
                card, "People who use this CRM" if team else "Other people",
                plural(db.stats()["users"], "person", "people") + " can sign in." if team else
                "Only you use this CRM. It can be shared, with a sign-in for each person.")
            self.people_btn = ttk.Button(right, text="People and passwords…",
                                         command=lambda: app.go("people"))
            self.people_btn.pack(side="right")

    def set_idle(self):
        db, app = self.db, self.app
        chosen = self.idle_var.get()
        minutes = next((m for m, text in IDLE_CHOICES if text == chosen), None)
        if minutes is None:
            return
        try:
            db.set_idle_minutes(minutes)
        except dbm.DBError as exc:
            widgets.error(app, "That did not work", str(exc))
            return
        self.idle_box.selection_clear()
        app.toast("It will not lock by itself" if not minutes else
                  f"It will lock after {chosen} without use", "good")

    def new_recovery_code(self):
        db, app = self.db, self.app
        made = []

        def go(v):
            if not db.check_password(v["old"]):
                return "That password is not right.", "old"
            made.append(db.new_recovery_code())
            return None

        d = FormDialog(app, "Make a new recovery code", "Make a new code", go,
                       intro="The recovery code is the way back in if a password is forgotten. "
                             "Making a new one stops the old code working straight away.\n\n"
                             "Type your password to confirm.")
        d.entry("old", "Your password", secret=True)
        if not d.run() or not made:
            return
        from .welcome import show_recovery_code
        show_recovery_code(app, made[0], os.path.basename(db.path))
        app.toast("The old recovery code no longer works", "good")

    # ------------------------------------------------------------- backups
    def _backups(self):
        db, app = self.db, self.app
        card = self.card("Backups")
        folder = db.backup_dir()
        last = db.last_backup()
        right, self.backup_label = self.line(
            card, f"Last automatic backup: {friendly_day(last)}" if last else "No automatic backup yet.",
            f"A copy is saved by itself the first time this CRM is opened each day, in the folder "
            f"“{os.path.basename(folder)}” beside the file. The newest {dbm.BACKUPS_KEPT} are kept.",
            pady=(4, 0))
        ttk.Button(right, text="Back up now…", command=self.backup_now).pack(side="right", padx=(8, 0))
        ttk.Button(right, text="Open the backups folder", command=self.open_backups).pack(side="right")
        self.para(card, "To go back to a backup, open the backup file with File > Open."
                  + (" Keep each backup's .keys file beside it." if db.encrypted else ""),
                  style="PanelHelp.TLabel", pady=(8, 0))

    def backup_now(self):
        self.app.tool("backup_now")

    def open_backups(self):
        folder = self.db.backup_dir()
        if not os.path.isdir(folder):
            self.app.toast("There is no backups folder yet. It is made with the first automatic backup.")
            return
        open_folder(self.app, folder)

    # ---------------------------------------------------------- appearance
    def _appearance(self):
        app = self.app
        card = self.card("Appearance")
        right, _lab = self.line(card, "Dark mode is on." if app.dark else "Light mode is on.",
                                f"{widgets.MOD_LABEL}+D switches at any time.", pady=(4, 0))
        self.light_btn = ttk.Button(right, text="Light", style="Seg.TButton" if app.dark else "SegOn.TButton",
                                    command=lambda: self.set_dark(False))
        self.dark_btn = ttk.Button(right, text="Dark", style="SegOn.TButton" if app.dark else "Seg.TButton",
                                   command=lambda: self.set_dark(True))
        self.dark_btn.pack(side="right")
        self.light_btn.pack(side="right", padx=(0, 4))

    def set_dark(self, dark: bool):
        if self.app.dark != dark:
            self.app.toggle_dark()

    # -------------------------------------------------------------- saving
    def _saving(self):
        app = self.app
        card = self.card("Saving")
        folder = paths.default_save_dir(app.settings)
        custom = bool(app.settings.get("save_dir")) and os.path.isdir(app.settings.get("save_dir") or "")
        right, self.save_label = self.line(
            card, "Exports, print-outs and backups you save yourself start in:", folder, pady=(4, 0))
        if custom:
            ttk.Button(right, text="Reset to Downloads", command=self.reset_save_dir).pack(
                side="right", padx=(8, 0))
        ttk.Button(right, text="Change…", command=self.choose_save_dir).pack(side="right")

    def choose_save_dir(self):
        before = self.app.settings.get("save_dir")
        self.app.choose_save_dir()
        if self.app.settings.get("save_dir") != before:
            self.app.reload()

    def reset_save_dir(self):
        self.app.reset_save_dir()
        self.app.reload()

    # ------------------------------------------------------------ examples
    def _examples(self):
        card = self.card("Example records")
        right, _lab = self.line(card, "This CRM still has the example records from when it was set up.",
                                "Removing them leaves the records you added yourself alone.",
                                pady=(4, 0))
        self.examples_btn = ttk.Button(right, text="Remove the example records",
                                       command=self.app.remove_examples)
        self.examples_btn.pack(side="right")


# -------------------------------------------------------------- recycle bin
class BinPage(Page):
    nav_key = ""
    auto_refresh = True

    def __init__(self, parent, app, **_kw):
        super().__init__(parent, app)
        db = self.db
        right = self.header("Recycle bin", " ", back=True)
        self.restore_btn = self.purge_btn = None
        if db.can_edit:
            self.restore_btn = ttk.Button(right, text="Restore", style="Accent.TButton",
                                          command=self.restore, state="disabled")
        if db.is_admin:
            self.purge_btn = ttk.Button(right, text="Delete for good…", style="Danger.TButton",
                                        command=self.purge, state="disabled")

        self.holder = ttk.Frame(self)
        self.holder.pack(fill="both", expand=True, padx=24)
        cols = ("list", "title", "ref", "when", "who")
        self.tree_frame, self.tree = widgets.make_tree(self.holder, cols, selectmode="extended",
                                                       height=4, xscroll=True)
        # (widths that fit the smallest window; the Record column takes any room to spare)
        for key, text, width, stretch in (("list", "List", 110, False), ("title", "Record", 220, True),
                                          ("ref", "Reference", 100, False),
                                          ("when", "Deleted", 130, False), ("who", "By", 130, False)):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=fs(width), minwidth=fs(70), stretch=stretch, anchor="w")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        self.tree.bind("<Double-1>", self._open)
        self.tree.bind("<Return>", self._open)
        for mod in {widgets.MOD, "Control"}:
            self.tree.bind(f"<{mod}-a>", lambda _e: self.select_all() or "break")

        self.empty = ttk.Frame(self.holder)
        ttk.Label(self.empty, text="The recycle bin is empty", style="H2.TLabel").pack(pady=(70, 6))
        lab = ttk.Label(self.empty, text="When a record is deleted it comes here first, so a mistake "
                                         "is easy to put right. Nothing is lost until an administrator "
                                         "deletes it for good.",
                        style="Help.TLabel", justify="center", wraplength=fs(420))
        lab.pack()

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=(6, 12))
        self.foot = ttk.Label(foot, text="", style="Help.TLabel")
        self.foot.pack(side="left")
        self.all_link = widgets.link(foot, "Select all", self.select_all)
        self.refresh()

    def refresh(self) -> None:
        db = self.db
        keep = set(self.tree.selection())
        self.rows = {str(r["id"]): r for r in db.deleted_records()}
        self.tree.delete(*self.tree.get_children())
        for iid, r in self.rows.items():
            t = bpm.get_type(db.blueprint, r["type"])
            self.tree.insert("", "end", iid=iid, values=(
                t["name"] if t else r["type"], r["title"], r["ref"],
                validate.format_stamp(r["deleted_at"]), db.user_name(r["deleted_by"])))
        n = len(self.rows)
        if n:
            self.empty.pack_forget()
            self.tree_frame.pack(fill="both", expand=True)
            self.tree.selection_set([i for i in keep if i in self.rows])
            self.all_link.pack(side="right")
            if self.restore_btn is not None:
                self.restore_btn.pack(side="right")
            if self.purge_btn is not None:
                self.purge_btn.pack(side="right", padx=(0, 8))
        else:
            self.tree_frame.pack_forget()
            self.empty.pack(fill="both", expand=True)
            self.all_link.pack_forget()
            for b in (self.restore_btn, self.purge_btn):   # nothing to act on: keep it calm
                if b is not None:
                    b.pack_forget()
        who = ("until you delete them for good" if db.is_admin else
               "until an administrator deletes them for good")
        self.subtitle_label.configure(
            text=f"{plural(n, 'deleted record')}. They stay here {who}." if n else " ")
        self._selection_changed()

    def _selection_changed(self):
        n = len(self.tree.selection()) if self.rows else 0
        state = "normal" if n else "disabled"
        if self.restore_btn is not None:
            self.restore_btn.configure(state=state, text=f"Restore {n}" if n > 1 else "Restore")
        if self.purge_btn is not None:
            self.purge_btn.configure(state=state)
        if not self.rows:
            self.foot.configure(text="")
        elif n:
            self.foot.configure(text=f"{n:,} chosen")
        elif self.db.can_edit:
            self.foot.configure(text="Choose records to restore them. Hold Ctrl or Shift to choose "
                                     "several; double-click to look at one."
                                .replace("Ctrl", widgets.MOD_LABEL))
        else:
            self.foot.configure(text="Double-click a record to look at it.")

    def ids(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def _open(self, _e=None):
        ids = self.ids()
        focus = self.tree.focus()
        if focus:
            self.app.open_record(int(focus))
        elif ids:
            self.app.open_record(ids[0])
        return "break"

    def restore(self):
        ids = self.ids()
        if not ids or not self.db.can_edit:
            return
        try:
            n = self.db.restore_records(ids)
        except dbm.DBError as exc:
            widgets.error(self.app, "Nothing was restored", str(exc))
            return
        self.tree.selection_set(())
        self.refresh()
        self.app.refresh_sidebar()
        self.app.toast(f"Restored {plural(n, 'record')}", "good")

    def purge(self):
        ids = self.ids()
        if not ids or not self.db.is_admin:
            return
        n = len(ids)
        what = (f"“{self.rows[str(ids[0])]['title']}”" if n == 1 else f"these {n:,} records")
        if not widgets.confirm(
                self.app, "Delete for good",
                f"Delete {what} for good?\n\nTheir notes, tasks and files go too, and other "
                "records that pointed to them lose that link. This cannot be undone.",
                yes=f"Delete {plural(n, 'record')} for good", danger=True):
            return
        try:
            done = self.db.purge_records(ids)
        except dbm.DBError as exc:
            widgets.error(self.app, "Nothing was deleted", str(exc))
            return
        self.tree.selection_set(())
        self.refresh()
        self.app.refresh_sidebar()
        self.app.toast(f"Deleted {plural(done, 'record')} for good", "good")


# ------------------------------------------------------------------ history
def describe(db, h: dict) -> str:
    """One history entry as a readable sentence."""
    action, old, new, field = h["action"], h["old"] or "", h["new"] or "", h["field"] or ""
    t = bpm.get_type(db.blueprint, h["type"]) if h["type"] else None
    kind = t["name"].lower() if t else "record"
    if action == "created":
        return f"Added {kind} “{new}”" if new else f"Added a {kind}"
    if action == "changed":
        return f"{field}: {old or '(empty)'} → {new or '(empty)'}"
    if action == "deleted":
        return f"Deleted {kind} “{old}” (moved to the recycle bin)"
    if action == "restored":
        return f"Restored {kind} “{new}” from the recycle bin"
    if action == "erased":
        return f"Deleted {kind} “{old}” for good"
    if action in ("note-added", "note-changed", "note-deleted"):
        what = field if field and field != "Note" else "Note"
        verb = {"note-added": "added", "note-changed": "changed", "note-deleted": "deleted"}[action]
        text = new or old
        return f"{what} {verb}: {text}" if text else f"{what} {verb}"
    if action in ("task-added", "task-done", "task-reopened", "file-added"):
        return f"{ACTION_WORDS.get(action, action)}: {new}"
    if action == "file-removed":
        return f"File removed: {old}"
    if action == "design":
        if new == "Created":
            return "This CRM was created"
        return new if new and new != "Design changed" else "The design was changed"
    if action == "user-added":
        return f"Person added: {new}"
    if action == "user-changed":
        return f"Person changed: {new}"
    if action == "password-changed":
        return f"Password changed for {new}" if new else "Password changed"
    if action == "password-recovered":
        return "Password reset with the recovery code"
    if action == "signin-failed":
        return f"Wrong password when signing in ({new})" if new else "Wrong password when signing in"
    if action == "recovery-code":
        return "New recovery code made (the old one stopped working)"
    if action == "setting":
        return f"Setting changed – {field}: {old} → {new}"
    return ACTION_WORDS.get(action, action.replace("-", " ").capitalize())


def csv_safe(text) -> str:
    """Stop a spreadsheet treating text from a record as a formula."""
    text = "" if text is None else str(text)
    if text[:1] in ("=", "@", "\t", "\r") or (text[:1] in ("+", "-") and not text[1:2].isdigit()
                                               and len(text) > 1):
        return "'" + text
    return text


class HistoryPage(Page):
    nav_key = ""

    def __init__(self, parent, app, search: str = "", shown: int = HISTORY_PAGE, **_kw):
        super().__init__(parent, app)
        self.search = search
        self.limit = max(HISTORY_PAGE, int(shown or HISTORY_PAGE))
        self.rows: list[dict] = []
        self.more = False
        right = self.header("History of changes",
                            "Everything that has been added, changed or deleted, newest first. "
                            "It is a record of what happened, so nothing here can be edited.",
                            back=True)
        wrap_to(self.subtitle_label, self.subtitle_label.master, margin=8)
        self.subtitle_label.configure(justify="left")
        ttk.Button(right, text="Export to a file…", command=self.export).pack(side="right")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=24, pady=(0, 8))
        self.search_box = widgets.SearchEntry(bar, app, "Search the history…",
                                              on_change=self._search_changed, width=34)
        self.search_box.pack(side="left")
        if search:
            self.search_box.set_text(search)
        self.count_label = ttk.Label(bar, text="", style="Help.TLabel")
        self.count_label.pack(side="left", padx=(12, 0))

        foot = ttk.Frame(self)
        foot.pack(side="bottom", fill="x", padx=24, pady=(6, 12))
        self.more_btn = ttk.Button(foot, text="Show more", command=self.show_more)
        self.detail = ttk.Label(foot, text="", style="TLabel", justify="left")
        self.detail.pack(side="left", fill="x", expand=True, anchor="w")
        self.open_link = widgets.link(foot, "Open this record", self.open_selected)
        wrap_to(self.detail, foot, margin=fs(250), least=200)

        cols = ("when", "who", "what", "record")
        frame, self.tree = widgets.make_tree(self, cols, selectmode="browse", height=4, xscroll=True)
        frame.pack(fill="both", expand=True, padx=24)
        # (widths that fit the smallest window; the two wordy columns share any room to spare)
        for key, text, width, stretch in (("when", "When", 130, False), ("who", "Who", 120, False),
                                          ("what", "What happened", 280, True),
                                          ("record", "Record", 180, True)):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=fs(width), minwidth=fs(80), stretch=stretch, anchor="w")
        self.tree.tag_configure("gone", foreground=self.c["dim"])
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.bind("<Return>", lambda _e: self.open_selected())
        self.load()

    def state(self) -> dict:
        return {"search": self.search, "shown": self.limit}

    def _search_changed(self, text: str):
        self.search = text
        self.limit = HISTORY_PAGE
        self.load()

    def show_more(self):
        self.limit += HISTORY_PAGE
        at = len(self.rows)
        self.load()
        kids = self.tree.get_children()
        if at < len(kids):
            self.tree.see(kids[min(len(kids) - 1, at + 8)])

    def refresh(self) -> None:
        self.load()

    def _record_text(self, h: dict) -> tuple[str, bool]:
        """(what to show in the Record column, whether it can be opened)."""
        if not h["record_id"]:
            return "", False
        title = self.db.lookup("link", h["record_id"])
        if not title:
            return f"{h['ref']}  (deleted for good)", False
        return f"{h['ref']}  {title}", True

    def load(self):
        db = self.db
        rows = db.history(limit=self.limit + 1, search=self.search)
        self.more = len(rows) > self.limit
        self.rows = rows[:self.limit]
        tree = self.tree
        tree.delete(*tree.get_children())
        self.by_iid = {}
        for h in self.rows:
            h["what"] = describe(db, h)
            h["record_text"], h["can_open"] = self._record_text(h)
            iid = str(h["id"])
            self.by_iid[iid] = h
            tree.insert("", "end", iid=iid, tags=() if (h["can_open"] or not h["record_id"]) else ("gone",),
                        values=(validate.format_stamp(h["at"]), h["user_name"] or "",
                                h["what"].replace("\n", " "), h["record_text"]))
        n = len(self.rows)
        if not n:
            text = "Nothing in the history matches that." if self.search else "Nothing has happened yet."
        elif self.more:
            text = f"Showing the newest {n:,}"
        else:
            text = plural(n, "entry", "entries") + (" found" if self.search else "")
        self.count_label.configure(text=text)
        if self.more:
            self.more_btn.pack(side="right", padx=(12, 0))
        else:
            self.more_btn.pack_forget()
        self._selection_changed()

    def selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.by_iid.get(sel[0]) if sel else None

    def _selection_changed(self):
        h = self.selected()
        if h is None:
            self.detail.configure(text="Choose a line to read it in full." if self.rows else "",
                                  style="Help.TLabel")
            self.open_link.pack_forget()
            return
        text = h["what"]
        if len(text) > 600:
            text = text[:600] + "…"
        if h["record_text"]:
            text += "\n" + h["record_text"]
        self.detail.configure(text=text, style="TLabel")
        if h["can_open"]:
            self.open_link.pack(side="right", padx=(12, 0))
        else:
            self.open_link.pack_forget()

    def open_selected(self):
        h = self.selected()
        if h is not None and h["can_open"] and self.db.get_record(h["record_id"]) is not None:
            self.app.open_record(h["record_id"])

    def export(self):
        db, app = self.db, self.app
        name = "History of changes" + (f" ({self.search})" if self.search else "")
        name = "".join(ch for ch in name if ch not in '\\/:*?"<>|') + ".csv"
        path = widgets.ask_save_file(app, "Export the history", name,
                                     [("Spreadsheet (CSV)", "*.csv")], defaultextension=".csv")
        if not path:
            return
        rows = db.history(limit=10_000_000, search=self.search)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["When", "Who", "What happened", "List", "Reference", "Field",
                            "Before", "After"])
                for h in rows:
                    t = bpm.get_type(db.blueprint, h["type"]) if h["type"] else None
                    w.writerow([csv_safe(x) for x in (
                        validate.format_stamp(h["at"]), h["user_name"], describe(db, h),
                        t["name"] if t else (h["type"] or ""), h["ref"], h["field"], h["old"], h["new"])])
        except OSError as exc:
            widgets.error(app, "The history was not exported",
                          "The file could not be written there. Check that it is not open in "
                          f"another program, or choose another place.\n\n({exc})")
            return
        app.toast(f"Exported {plural(len(rows), 'entry', 'entries')} to {path}", "good")
