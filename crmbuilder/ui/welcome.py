"""Screens shown before a CRM is open: welcome, sign-in, and the lock overlay."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from .. import APP_NAME, FILE_EXT, __version__, security
from .. import db as dbm
from . import widgets
from .styles import fs


def press_on_enter(button) -> None:
    """Make Enter press this button while it has the keyboard (Tk only does
    that for Space)."""
    def go(_e):
        button.invoke()
        return "break"
    button.bind("<Return>", go)
    button.bind("<KP_Enter>", go)


class _Centered(ttk.Frame):
    """A full-window frame with one card in the middle."""
    screen_name = ""

    def __init__(self, parent, app, width=520):
        super().__init__(parent)
        self.app, self.c = app, app.c
        self.card = widgets.card(self, app, padding=30)
        self.card.place(relx=0.5, rely=0.45, anchor="center", width=fs(10) * width // 10)

    def state(self) -> dict:
        return {}


class WelcomeScreen(_Centered):
    def __init__(self, parent, app):
        super().__init__(parent, app, width=560)
        card = self.card
        ttk.Label(card, text=APP_NAME, style="PanelH1.TLabel").pack(anchor="w")
        ttk.Label(card, text="Keep track of the people, organisations and work that matter to "
                             "you, in lists you design yourself.",
                  style="PanelDim.TLabel", wraplength=fs(10) * 48, justify="left").pack(anchor="w", pady=(4, 20))
        b = ttk.Button(card, text="Create a new CRM", style="Big.Accent.TButton",
                       command=lambda: app.show_screen("wizard"))
        b.pack(fill="x")
        ttk.Label(card, text="A short guided setup: about two minutes.",
                  style="PanelHelp.TLabel").pack(anchor="w", pady=(4, 12))
        other = ttk.Button(card, text="Open one I already have…", style="Big.TButton",
                           command=app.menu_open)
        other.pack(fill="x")
        press_on_enter(b)
        press_on_enter(other)
        recent = [p for p in app.settings.get("recent", []) if os.path.isfile(p)]
        if recent:
            ttk.Label(card, text="RECENT", style="PanelHelp.TLabel").pack(anchor="w", pady=(22, 4))
            for p in recent[:5]:
                row = ttk.Frame(card, style="Panel.TFrame")
                row.pack(fill="x", pady=1)
                name = os.path.splitext(os.path.basename(p))[0]
                if len(name) > 36:              # leave room for where it is kept
                    name = name[:35].rstrip() + "…"
                widgets.link(row, name, lambda p=p: app.menu_open(p),
                             style="PanelLink.TLabel").pack(side="left")
                folder = os.path.dirname(p)
                room = 62 - len(name)
                if len(folder) > room:
                    folder = "…" + folder[-(room - 1):]
                ttk.Label(row, text=folder, style="PanelHelp.TLabel").pack(side="left", padx=(10, 0))
        ttk.Label(self, text=f"Version {__version__}", style="Help.TLabel").place(
            relx=1.0, rely=1.0, anchor="se", x=-14, y=-10)
        b.focus_set()


def _password_row(parent, label, show="•"):
    ttk.Label(parent, text=label, style="PanelField.TLabel").pack(anchor="w", pady=(10, 3))
    var = tk.StringVar(parent)
    e = ttk.Entry(parent, textvariable=var, show=show)
    e.pack(fill="x")
    return var, e


class LoginScreen(_Centered):
    """Sign in to a CRM that has a password (one person) or accounts (a team)."""

    def __init__(self, parent, app, db: dbm.Database):
        super().__init__(parent, app, width=440)
        self.db = db
        card = self.card
        self.team = db.login_needed() == "user"
        name = os.path.splitext(os.path.basename(db.path))[0]
        ttk.Label(card, text=name, style="PanelH1.TLabel").pack(anchor="w")
        ttk.Label(card, text="Sign in to open this CRM." if self.team
                  else "This CRM is protected by a password.",
                  style="PanelDim.TLabel").pack(anchor="w", pady=(4, 6))
        self.user_var = tk.StringVar(self, value=app.settings.get("last_user", "") if self.team else "")
        self.user_entry = None
        if self.team:
            ttk.Label(card, text="Username", style="PanelField.TLabel").pack(anchor="w", pady=(10, 3))
            self.user_entry = ttk.Entry(card, textvariable=self.user_var)
            self.user_entry.pack(fill="x")
        self.pw_var, self.pw_entry = _password_row(card, "Password")
        self.error = ttk.Label(card, text="", style="PanelError.TLabel", wraplength=fs(10) * 38,
                               justify="left")
        self.error.pack(anchor="w", pady=(6, 0))
        go = ttk.Button(card, text="Open", style="Big.Accent.TButton", command=self.submit)
        go.pack(fill="x", pady=(10, 0))
        press_on_enter(go)
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(14, 0))
        widgets.link(row, "Open a different CRM", self.other, style="PanelLink.TLabel").pack(side="left")
        if db.encrypted:
            widgets.link(row, "Forgotten password?", self.recover,
                         style="PanelLink.TLabel").pack(side="right")
        for e in (self.user_entry, self.pw_entry):
            if e is not None:
                e.bind("<Return>", lambda _e: self.submit())
        (self.user_entry if (self.team and not self.user_var.get()) else self.pw_entry).focus_set()

    def state(self) -> dict:
        return {"db": self.db}

    def other(self):
        self.db.close()
        self.app.settings.pop("last_db", None)
        self.app.save_settings()
        self.app.show_screen("welcome")

    def submit(self):
        username = self.user_var.get().strip() if self.team else None
        if self.team and not username:
            self.error.configure(text="Type your username.")
            return
        self.error.configure(text="Checking…", style="PanelHelp.TLabel")
        self.update_idletasks()
        self.error.configure(style="PanelError.TLabel")
        try:
            self.db.login(username, self.pw_var.get())
        except dbm.DBError as exc:
            self.error.configure(text=str(exc))
            self.pw_var.set("")
            self.pw_entry.focus_set()
            return
        if self.team:
            self.app.settings["last_user"] = username
        self.app.enter(self.db)

    def recover(self):
        app = self.app
        d = widgets.Dialog(app, "Use the recovery code", width=460)
        ttk.Label(d.body, text="Type the recovery code that was shown when encryption was "
                               "switched on, then choose a new password.",
                  wraplength=430, justify="left").pack(anchor="w")
        ttk.Label(d.body, text="Recovery code", style="Field.TLabel").pack(anchor="w", pady=(12, 3))
        code = tk.StringVar(d)
        ce = ttk.Entry(d.body, textvariable=code, font=(app.c["mono"], fs(10)))
        ce.pack(fill="x")
        user = tk.StringVar(d, value=self.user_var.get())
        if self.team:
            ttk.Label(d.body, text="Your username", style="Field.TLabel").pack(anchor="w", pady=(10, 3))
            ttk.Entry(d.body, textvariable=user).pack(fill="x")
        ttk.Label(d.body, text="New password", style="Field.TLabel").pack(anchor="w", pady=(10, 3))
        p1 = tk.StringVar(d)
        ttk.Entry(d.body, textvariable=p1, show="•").pack(fill="x")
        ttk.Label(d.body, text="New password again", style="Field.TLabel").pack(anchor="w", pady=(10, 3))
        p2 = tk.StringVar(d)
        ttk.Entry(d.body, textvariable=p2, show="•").pack(fill="x")
        err = ttk.Label(d.body, text="", style="Error.TLabel", wraplength=430, justify="left")
        err.pack(anchor="w", pady=(6, 0))

        def go():
            problem = security.password_problem(p1.get(), user.get())
            if p1.get() != p2.get():
                problem = "The two passwords are not the same."
            if problem:
                err.configure(text=problem)
                return
            try:
                self.db.recover(code.get(), user.get() if self.team else "", p1.get())
            except dbm.DBError as exc:
                err.configure(text=str(exc))
                return
            d.ok(True)

        d.add_button("Unlock", go, accent=True)
        d.add_button("Cancel", d.cancel)
        d.default_on_enter()
        if d.show(focus=ce):
            app.enter(self.db)


class LockOverlay(tk.Frame):
    """Covers the window until the signed-in person types their password."""

    def __init__(self, app):
        super().__init__(app.root, bg=app.c["bg"])
        self.app = app
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        card = widgets.card(self, app, padding=30)
        card.place(relx=0.5, rely=0.42, anchor="center", width=fs(10) * 42)
        ttk.Label(card, text="Locked", style="PanelH1.TLabel").pack(anchor="w")
        who = app.db.user["name"] if app.db.mode == "team" else app.db.name
        ttk.Label(card, text=f"{who} – type your password to carry on.",
                  style="PanelDim.TLabel", wraplength=fs(10) * 36, justify="left").pack(anchor="w", pady=(4, 0))
        self.var, self.entry = _password_row(card, "Password")
        self.error = ttk.Label(card, text="", style="PanelError.TLabel")
        self.error.pack(anchor="w", pady=(6, 0))
        go = ttk.Button(card, text="Unlock", style="Big.Accent.TButton", command=self.submit)
        go.pack(fill="x", pady=(8, 0))
        press_on_enter(go)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.fails = 0
        self.after(50, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.entry.focus_set()

    def submit(self):
        db = self.app.db
        if not db.refresh_account():
            self.fails = dbm.MAX_FAILED - 1       # switched off meanwhile: back to sign-in
        elif db.check_password(self.var.get()):
            try:
                self.grab_release()
            except tk.TclError:
                pass
            self.destroy()
            self.app.unlocked()
            return
        self.fails += 1
        self.var.set("")
        self.error.configure(text="That password is not right.")
        if self.fails >= dbm.MAX_FAILED:
            # too many guesses: close the CRM and go back to the sign-in screen
            try:
                self.grab_release()
            except tk.TclError:
                pass
            app, path = self.app, self.app.db.path
            self.destroy()
            app.unlocked()
            app.db.close()
            app.db = None
            app.open_path(path)


def show_recovery_code(app, code: str, file_name: str) -> None:
    """Show a new recovery code once, with a way to save it. Blocks until closed."""
    c = app.c
    d = widgets.Dialog(app, "Your recovery code", width=520)
    ttk.Label(d.body, text="Keep this somewhere safe", style="H2.TLabel").pack(anchor="w")
    ttk.Label(d.body, text="This CRM is encrypted. If a password is ever forgotten, this code is "
                           "the only other way in. It is shown once: print it or write it down, "
                           "and keep it away from the computer.",
              wraplength=490, justify="left").pack(anchor="w", pady=(6, 12))
    box = tk.Text(d.body, height=2, width=44, bg=c["field"], fg=c["field_text"], relief="flat",
                  highlightthickness=1, highlightbackground=c["field_border"],
                  font=(c["mono"], fs(12)), padx=10, pady=10, wrap="word")
    box.insert("1.0", code)
    box.configure(state="disabled")
    box.pack(fill="x")
    ttk.Label(d.body, text=f"Also keep “{file_name}.keys” beside the CRM file. The two belong "
                           "together: without the .keys file the CRM cannot be opened at all.",
              style="Help.TLabel", wraplength=490, justify="left").pack(anchor="w", pady=(10, 0))

    def save():
        path = widgets.ask_save_file(app, "Save the recovery code",
                                     os.path.splitext(file_name)[0] + " recovery code.txt",
                                     [("Text file", "*.txt")], defaultextension=".txt")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(f"CRM Builder recovery code for {file_name}\n\n{code}\n\n"
                             "Keep this away from the computer the CRM is on.\n")
            except OSError as exc:
                widgets.error(app, "Could not save the file",
                              f"The recovery code was not saved.\n\n({exc})\n\nChoose another "
                              "place, or write the code down.")
                return
            app.toast(f"Saved to {path}", "good")

    def copy():
        app.root.clipboard_clear()
        app.root.clipboard_append(code)
        app.toast("Copied")

    b = d.add_button("I have kept it safe", lambda: d.ok(True), accent=True)
    for other in (d.add_button("Save to a file…", save, side="left"),
                  d.add_button("Copy", copy, side="left")):
        press_on_enter(other)
    d.protocol("WM_DELETE_WINDOW", lambda: None)
    d.unbind("<Escape>")
    if app.testing:
        app.test_log.append(("recovery", code, file_name))
        d.destroy()
        return
    d.show(focus=b)
