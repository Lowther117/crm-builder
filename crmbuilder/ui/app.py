"""The application shell: one window, a sidebar of your lists, pages in the middle."""
from __future__ import annotations

import importlib
import os
import re
import sys
import time
import tkinter as tk
import traceback
from tkinter import ttk

from .. import APP_NAME, FILE_EXT, __version__, paths
from .. import blueprint as bpm
from .. import db as dbm
from . import styles, widgets
from .styles import fs
from .widgets import MOD, MOD_LABEL

# page name -> (module in crmbuilder.ui, class)
PAGES = {
    "home": ("home", "HomePage"),
    "tasks": ("tasks", "TasksPage"),
    "search": ("search", "SearchPage"),
    "list": ("listpage", "ListPage"),
    "record": ("recordpage", "RecordPage"),
    "reports": ("reports", "ReportsPage"),
    "design": ("designer", "DesignerPage"),
    "import": ("importer", "ImportPage"),
    "datacheck": ("tools", "DataCheckPage"),
    "people": ("admin", "PeoplePage"),
    "settings": ("admin", "SettingsPage"),
    "bin": ("admin", "BinPage"),
    "history": ("admin", "HistoryPage"),
}
# full-window screens used before a CRM is open
SCREENS = {
    "welcome": ("welcome", "WelcomeScreen"),
    "login": ("welcome", "LoginScreen"),
    "wizard": ("wizard", "WizardScreen"),
}


class App:
    def __init__(self, root: tk.Tk, open_path: str | None = None, testing: bool = False):
        self.root = root
        self.testing = testing
        self.test_log: list = []
        self.test_answers: list = []
        self.settings = paths.load_settings() if not testing else {}
        self.dark = bool(self.settings.get("dark", True))
        self.c = styles.apply(root, self.dark)
        self.db: dbm.Database | None = None
        self.page = None
        self.page_name = ""
        self.screen = None
        self.history: list[tuple[str, dict]] = []
        self.shell = None
        self.content = None
        self.nav_buttons: dict[str, ttk.Button] = {}
        self._toast = None
        self._toast_after = None
        self._last_activity = time.time()
        self._data_version = None
        self._locked = False
        self._hidden: list = []          # dialogs taken off the screen while locked
        self._overlay = None             # the lock screen, while it is up
        self._file_lost = False
        self.search_entry = None

        root.title(APP_NAME)
        # The smallest window every page is laid out for is 980 x 620 at the
        # Windows text size. Text is drawn larger on a Mac (see styles.SCALE),
        # so the smallest window is larger there by the same amount.
        self._min_size = (max(640, min(fs(980), root.winfo_screenwidth() - 20)),
                          max(480, min(fs(620), root.winfo_screenheight() - 80)))
        root.geometry(self._start_geometry())
        root.minsize(*self._min_size)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        if widgets.IS_MAC:
            # Cmd+Q and the app menu's Quit would otherwise end the program on
            # the spot: no "you have unsaved changes", nothing closed properly.
            for name, fn in (("::tk::mac::Quit", self.quit),
                             ("::tk::mac::OpenDocument", self._mac_open),
                             ("::tk::mac::ShowPreferences", self._mac_settings)):
                try:
                    root.createcommand(name, fn)
                except tk.TclError:
                    pass
        widgets.install_wheel(root)
        self._bind_keys()
        root.report_callback_exception = self._callback_error

        start = open_path or self.settings.get("last_db")
        if start and os.path.isfile(start):
            self.open_path(start, quiet=not open_path)
        else:
            self.show_screen("welcome")
        root.after(5000, self._tick)

    # ----------------------------------------------------------- plumbing
    def _start_geometry(self) -> str:
        """The size and place the window had last time - unless that place is
        no longer on a screen (a second monitor was unplugged, or the app was
        closed while minimised, which Windows reports as -32000,-32000)."""
        r = self.root
        mw, mh = self._min_size
        default = "%dx%d" % (max(mw, min(fs(1220), r.winfo_screenwidth() - 40)),
                             max(mh, min(fs(780), r.winfo_screenheight() - 100)))
        saved = str(self.settings.get("geometry") or "")
        m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", saved)
        if not m:
            return default
        try:
            left, top = r.winfo_vrootx(), r.winfo_vrooty()
            right = left + max(r.winfo_vrootwidth(), r.winfo_screenwidth())
            bottom = top + max(r.winfo_vrootheight(), r.winfo_screenheight())
        except tk.TclError:
            return default
        w = max(mw, min(int(m.group(1)), right - left))
        h = max(mh, min(int(m.group(2)), bottom - top))
        if m.group(3)[0] == "-" or m.group(4)[0] == "-":
            return f"{w}x{h}"                 # measured from the far edge: let the system place it
        x, y = int(m.group(3)[1:]), int(m.group(4)[1:])
        if x + w < left + 160 or x > right - 160 or y < top or y > bottom - 120:
            return f"{w}x{h}"
        return f"{w}x{h}+{x}+{y}"

    def _callback_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        print(text, file=sys.stderr)
        if self.testing:
            self.test_log.append(("exception", str(val), text))
            return
        try:
            with open(os.path.join(os.path.dirname(paths.settings_path()),
                                   "crm-builder-crash.log"), "a", encoding="utf-8") as fh:
                fh.write(text + "\n" + "-" * 60 + "\n")
        except OSError:
            pass
        if isinstance(val, dbm.DBError):
            widgets.error(self, "That did not work", str(val))
        elif type(val).__name__ in ("OperationalError", "DatabaseError"):
            # SQLite (or SQLCipher) could not get at the file
            widgets.error(self, "The CRM file could not be read or saved to",
                          "If the file is on a shared or removable drive, check that the drive "
                          "is still connected, then try again. If it keeps happening, close "
                          f"{APP_NAME} and open the CRM again.\n\n({val})")
        else:
            widgets.error(self, "Something went wrong",
                          f"{type(val).__name__}: {val}\n\nThe details were written to "
                          "crm-builder-crash.log in:\n"
                          f"{os.path.dirname(paths.settings_path())}")

    def _bind_keys(self):
        r = self.root
        for mod in {MOD, "Control"}:
            r.bind_all(f"<{mod}-k>", lambda _e: self.focus_search())
            r.bind_all(f"<{mod}-n>", lambda _e: self.new_shortcut())
            r.bind_all(f"<{mod}-s>", lambda _e: self._page_call("save"))
            r.bind_all(f"<{mod}-d>", lambda _e: self.toggle_dark())
            r.bind_all(f"<{mod}-l>", lambda _e: self.lock())
        # Ctrl+D is also "delete character" in entry boxes; the theme switch wins.
        for cls in ("TEntry", "Entry", "Text", "TCombobox"):
            r.bind_class(cls, "<Control-d>", lambda _e: self.toggle_dark() or "break")
        r.bind_all("<Alt-Left>", lambda _e: self.back())
        if widgets.IS_MAC:               # (Option+Left moves by a word there; Back is Cmd+[)
            r.bind_all("<Command-bracketleft>", lambda _e: self.back())
        r.bind_all("<Escape>", lambda _e: self._page_call("escape"), add="+")
        r.bind_all("<Key>", self._activity, add="+")
        r.bind_all("<Button>", self._activity, add="+")
        r.bind_all("<MouseWheel>", self._activity, add="+")     # reading counts as using it
        r.bind("<FocusIn>", self._focus_in, add="+")
        r.bind_all("<FocusIn>", self._keep_focus_locked, add="+")

    def _mac_open(self, *files):
        """A CRM file was double-clicked in the Finder, or dropped on the icon."""
        for path in files:
            if str(path).lower().endswith(FILE_EXT) and not self._grabbed():
                self.menu_open(str(path))
                break

    def _mac_settings(self):
        if self.db is not None and self.shell is not None and not self._grabbed():
            self.go("settings")

    def _grabbed(self) -> bool:
        """True while a dialog (or the drop-down of a combobox) is open."""
        return bool(widgets.grab_holder(self.root))

    def _page_call(self, method):
        if self._locked or self._grabbed():
            return None
        fn = getattr(self.page, method, None) if self.page is not None else None
        if fn is None and self.screen is not None:
            fn = getattr(self.screen, method, None)
        if callable(fn):
            fn()
            return "break"
        return None

    def _activity(self, _e=None):
        self._last_activity = time.time()

    def _keep_focus_locked(self, event):
        """While locked, the keyboard stays on the lock screen: Tab must not
        wander off to the boxes and buttons hidden underneath it."""
        ov = self._overlay
        if not self._locked or ov is None:
            return
        try:
            if ov.winfo_exists() and not str(event.widget).startswith(str(ov)):
                (getattr(ov, "entry", None) or ov).focus_set()
        except tk.TclError:
            pass

    def _focus_in(self, event):
        if event.widget is self.root:
            self.root.after(50, self.check_external)

    def _tick(self):
        try:
            self.check_external()
            self._check_idle()
        finally:
            self.root.after(5000, self._tick)

    def check_external(self):
        """Pick up changes other people have saved to the shared file."""
        if self.db is None or self.db.conn is None or self.shell is None or self._locked:
            return
        if self._grabbed():
            return
        try:
            if not os.path.isfile(self.db.path):
                raise OSError("file not found")
            version = self.db.data_version()
        except Exception:
            self._file_trouble()
            return
        self._file_lost = False
        if version == self._data_version:
            return
        self._data_version = version
        try:
            was = self.db.user["role"]
            if not self.db.refresh_account():
                # an administrator switched this account off while it was open
                path = self.db.path
                self.db.close()
                self.db = None
                self.open_path(path)
                widgets.info(self, "Signed out", "Your account has been switched off by an "
                                                  "administrator, so this CRM has been closed.")
                return
            if self.db.user["role"] != was:
                self.rebuild()
                self.toast("An administrator changed what your account can do.")
                return
            if self.db.refresh_design():
                if self.page is not None and self.page_name in ("record", "design"):
                    self.toast("The design was changed by someone else. Reopen this page to see it.")
                    self.refresh_sidebar()
                else:
                    self.rebuild()
                return
            self.refresh_sidebar()
            if self.page is not None and self.page.auto_refresh:
                self.page.refresh()
        except Exception:
            pass

    def _file_trouble(self):
        """The file cannot be reached (a shared drive dropped, the file was
        moved). Say so once, not every few seconds."""
        if self._file_lost:
            return
        self._file_lost = True
        widgets.error(self, "The CRM file cannot be reached",
                      f"{self.db.path}\n\nIf it is on a shared or removable drive, check that "
                      "the drive is still connected. Nothing can be saved until the file is "
                      "back. If it has been moved or renamed, close this CRM (File > Close "
                      "this CRM) and open it from where it is now.")

    def _check_idle(self):
        if self.db is None or self.shell is None or self._locked or self.testing:
            return
        try:
            minutes = int(self.db.get_meta("idle_minutes", "15") or 0)
        except (ValueError, Exception):
            minutes = 15
        if minutes > 0 and time.time() - self._last_activity > minutes * 60:
            self.lock()

    def lock(self):
        """Hide everything behind a password prompt (only if there is a password)."""
        if self.db is None or self.shell is None or self._locked:
            return
        try:
            if not self.db.has_password:
                return
            from .welcome import LockOverlay
        except Exception:
            return
        self._locked = True
        # Dialogs are windows of their own, which the overlay cannot cover:
        # take them off the screen until the password has been typed.
        holder = widgets.grab_holder(self.root)
        try:
            if holder.endswith(".popdown"):            # the open list of a combobox
                self.root.tk.call("ttk::combobox::Unpost", holder[:-len(".popdown")])
            elif holder:
                self.root.tk.call("grab", "release", holder)
        except tk.TclError:
            pass
        self._hidden = []
        for w in self.root.winfo_children():
            try:
                if isinstance(w, tk.Toplevel) and w.winfo_viewable():
                    w.withdraw()
                    self._hidden.append(w)
            except tk.TclError:
                pass
        # The native menu bar is outside the overlay's reach on Windows and
        # macOS, so take the menus away while locked.
        self.root.configure(menu=styles.menu(self.root, self.c))
        self._overlay = LockOverlay(self)

    def unlocked(self):
        self._locked = False
        self._overlay = None
        self._last_activity = time.time()
        if self.db is not None and self.shell is not None:
            self._build_menu(True)
        if self._hidden:
            self.root.after(1, self._show_hidden, self.db)

    def _show_hidden(self, db):
        """Bring back the dialogs that were open when the lock came down - or
        close them, if the CRM they belong to is no longer the one on screen."""
        hidden, self._hidden = self._hidden, []
        same = db is not None and self.db is db and self.shell is not None and not self._locked
        last = None
        for w in hidden:
            try:
                if not w.winfo_exists():
                    continue
                if same:
                    w.deiconify()
                    w.lift()
                    last = w
                else:
                    w.destroy()
            except tk.TclError:
                pass
        if last is not None:
            try:
                last.grab_set()
                last.focus_set()
            except tk.TclError:
                pass

    # ------------------------------------------------------------- theme
    def toggle_dark(self):
        if self._grabbed() or self._locked:
            return
        self.dark = not self.dark
        self.settings["dark"] = self.dark
        self.save_settings()
        self.c = styles.apply(self.root, self.dark)
        self.rebuild()

    def save_settings(self):
        if not self.testing:
            paths.save_settings(self.settings)

    def rebuild(self):
        """Redraw everything (after a theme or design change), keeping the page."""
        if self.db is not None and self.shell is not None:
            kw = {}
            if self.page is not None:
                kw = getattr(self.page, "rebuild_state", self.page.state)()
            name = self.page_name
            self._build_shell()
            if not self._go(name or "home", kw, push=False, check=False, quiet=True):
                self._go("home", {}, push=False, check=False)
        elif self.screen is not None:
            name, kw = self.screen.screen_name, self.screen.state()
            self.show_screen(name, **kw)

    # ----------------------------------------------------------- screens
    def _clear_root(self):
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel):
                continue
            w.destroy()
        self.root.configure(menu="")
        self.shell = self.content = self.page = self.screen = None
        self.page_name = ""
        self.nav_buttons = {}
        self._toast = None
        self.search_entry = None

    def show_screen(self, name: str, **kw):
        """Show a full-window screen: 'welcome', 'login' (db=) or 'wizard'."""
        module, cls = SCREENS[name]
        klass = getattr(importlib.import_module(f"crmbuilder.ui.{module}"), cls)
        self._clear_root()
        self.root.title(APP_NAME)
        self._build_menu(False)
        self.screen = klass(self.root, self, **kw)
        self.screen.screen_name = name
        self.screen.pack(fill="both", expand=True)

    def open_path(self, path: str, quiet: bool = False) -> bool:
        """Open a CRM file, asking for a password if it has one."""
        try:
            db = dbm.Database.open(path)
        except dbm.DBError as exc:
            self.show_screen("welcome")
            if not quiet:
                widgets.error(self, "Could not open that file", str(exc))
            return False
        self._opened(db)
        return True

    def _opened(self, db: dbm.Database):
        if db.login_needed() == "none":
            db.login()
            self.enter(db)
        else:
            self.show_screen("login", db=db)

    def enter(self, db: dbm.Database, first_time: bool = False):
        """A CRM is open and signed in: show the main window."""
        if self.db is not None and self.db is not db:
            self.db.close()
        self.db = db
        self.history = []
        if not self.testing:
            paths.remember_recent(self.settings, db.path)
            db.daily_backup()
        self._data_version = db.data_version()
        self._last_activity = time.time()
        self._build_shell()
        self._go("home", {"first_time": first_time} if first_time else {}, push=False, check=False)

    def close_db(self):
        if self._locked:
            return
        if self.page is not None and not self.page.can_leave():
            return
        if self.db is not None:
            self.db.close()
        self.db = None
        self.settings.pop("last_db", None)
        self.save_settings()
        self.show_screen("welcome")

    def quit(self):
        holder = widgets.grab_holder(self.root)
        if holder and not (self._locked and self._overlay is not None and holder == str(self._overlay)):
            return                  # a dialog is open: that has to be answered first
        if self._locked:
            # Nothing can be saved from behind the lock (that would take the
            # password), but do not throw away what was typed without a word.
            page = self.page
            try:
                unsaved = getattr(page, "_drafting", None) or getattr(page, "is_dirty", None)
                unsaved = bool(unsaved()) if callable(unsaved) else False
            except Exception:
                unsaved = False
            if unsaved and not widgets.confirm(
                    self, "Close without saving?",
                    "This CRM is locked, and something was typed before it locked that has "
                    "not been saved. Unlock it to save that – or close now and lose it.",
                    yes="Close and lose it", no="Go back", danger=True):
                return
            self.page = None
        if self.page is not None and not self.page.can_leave():
            return
        if self.screen is not None and hasattr(self.screen, "can_leave") and not self.screen.can_leave():
            return
        try:
            self.settings["geometry"] = self.root.geometry()
            self.save_settings()
        except tk.TclError:
            pass
        if self.db is not None:
            self.db.close()
        self.root.destroy()

    # -------------------------------------------------------------- shell
    def _build_shell(self):
        self._clear_root()
        c = self.c
        db = self.db
        self.root.title(f"{db.name} – {APP_NAME}")
        self._build_menu(True)
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        self.shell = shell

        top = ttk.Frame(shell, style="Panel.TFrame", padding=(16, 9))
        top.pack(fill="x")
        ttk.Label(top, text=db.name, style="PanelH2.TLabel").pack(side="left")
        right = ttk.Frame(top, style="Panel.TFrame")
        right.pack(side="right")
        ttk.Label(right, text=db.user["name"] if db.mode == "team" else "",
                  style="PanelDim.TLabel").pack(side="right", padx=(12, 0))
        if db.can_edit and bpm.active_types(db.blueprint):
            self.new_button = ttk.Menubutton(right, text="+ New", style="Accent.TButton",
                                             direction="below")
            m = styles.menu(self.new_button, c)
            for t in bpm.active_types(db.blueprint):
                m.add_command(label=t["name"], command=lambda k=t["key"]: self.new_record(k))
            m.add_separator()
            m.add_command(label="Task", command=lambda: self.go("tasks", add=True))
            self.new_button.configure(menu=m)
            self.new_button.pack(side="right")
        mid = ttk.Frame(top, style="Panel.TFrame")
        mid.pack(side="left", fill="x", expand=True, padx=30)
        self.search_entry = widgets.SearchEntry(
            mid, self, f"Search everything   ({MOD_LABEL}+K)", on_change=self._search, delay=350,
            width=46)
        self.search_entry.pack(side="left")
        ttk.Frame(shell, style="Border.TFrame", height=1).pack(fill="x")

        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, style="Side.TFrame", width=fs(10) * 21)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.sidebar = side
        ttk.Frame(body, style="Border.TFrame", width=1).pack(side="left", fill="y")
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        self._fill_sidebar()

    def _nav(self, parent, key, text, command, count=None):
        label = text if count is None else f"{text}   {count}"
        b = ttk.Button(parent, text=label, style="Nav.TButton", command=command, takefocus=0)
        b.pack(fill="x", padx=8, pady=1)
        self.nav_buttons[key] = b
        return b

    def _fill_sidebar(self):
        side, db = self.sidebar, self.db
        for w in side.winfo_children():
            w.destroy()
        self.nav_buttons = {}
        # the bottom entries are placed first so they can never be pushed off
        # the window; the lists in between scroll when there are many
        bottom = ttk.Frame(side, style="Side.TFrame")
        bottom.pack(side="bottom", fill="x", pady=(0, 10))
        if db.is_admin:
            self._nav(bottom, "design", "Change the design", lambda: self.go("design"))
        self._nav(bottom, "settings", "Settings", lambda: self.go("settings"))
        scroller = widgets.ScrollFrame(side, self)
        scroller.configure(style="Side.TFrame")
        # width=1: let the sidebar decide the width, so the scrollbar has room
        scroller.canvas.configure(bg=self.c["sidebar"], width=1)
        scroller.body.configure(style="Side.TFrame")
        scroller.pack(side="top", fill="both", expand=True)
        side = scroller.body
        ttk.Frame(side, style="Side.TFrame", height=10).pack()
        self._nav(side, "home", "Home", lambda: self.go("home"))
        due = self._tasks_due()
        self._nav(side, "tasks", "Tasks", lambda: self.go("tasks"), due if due else None)
        types = bpm.active_types(db.blueprint)
        ttk.Label(side, text="YOUR LISTS", style="SideDim.TLabel").pack(anchor="w", padx=20, pady=(16, 4))
        counts = db.counts()
        for t in types:
            row = tk.Frame(side, bg=self.c["sidebar"])
            row.pack(fill="x")
            dot = tk.Frame(row, bg=t["color"], width=4)
            dot.pack(side="left", fill="y", padx=(8, 0), pady=4)
            b = ttk.Button(row, text=f"{t['plural']}   {counts.get(t['key'], 0)}", style="Nav.TButton",
                           command=lambda k=t["key"]: self.go("list", type_key=k), takefocus=0)
            b.pack(side="left", fill="x", expand=True, padx=(2, 8), pady=1)
            self.nav_buttons["list:" + t["key"]] = b
        if not types:
            ttk.Label(side, text="No lists yet.", style="SideHelp.TLabel").pack(anchor="w", padx=20)
        ttk.Label(side, text="MORE", style="SideDim.TLabel").pack(anchor="w", padx=20, pady=(16, 4))
        self._nav(side, "reports", "Reports", lambda: self.go("reports"))
        self._mark_nav()

    def _tasks_due(self) -> int:
        try:
            today = dbm.today_iso()
            return sum(1 for t in self.db.tasks(mine=True) if t["due"] and t["due"] <= today)
        except Exception:
            return 0

    def refresh_sidebar(self):
        if self.shell is not None and self.db is not None:
            try:
                self._fill_sidebar()
            except Exception:
                pass

    def _mark_nav(self):
        key = getattr(self.page, "nav_key", "") if self.page is not None else ""
        for k, b in self.nav_buttons.items():
            b.configure(style="NavSel.TButton" if k == key else "Nav.TButton")

    # --------------------------------------------------------------- menu
    def _build_menu(self, open_db: bool = False):
        c = self.c
        bar = styles.menu(self.root, c)
        acc = MOD_LABEL + "+"
        open_db = open_db and self.db is not None

        m = styles.menu(bar, c)
        m.add_command(label="New CRM…", command=self.menu_new)
        m.add_command(label="Open…", command=self.menu_open)
        recent = [p for p in self.settings.get("recent", []) if os.path.isfile(p)]
        if recent:
            sub = styles.menu(m, c)
            for p in recent:
                sub.add_command(label=p, command=lambda p=p: self.menu_open(p))
            m.add_cascade(label="Open recent", menu=sub)
        m.add_separator()
        if open_db:
            m.add_command(label="Back up now…", command=lambda: self.tool("backup_now"))
            m.add_command(label="Close this CRM", command=self.close_db)
            if self.db.has_password:
                m.add_command(label="Lock", accelerator=acc + "L", command=self.lock)
            m.add_separator()
        m.add_command(label="Default save folder…", command=self.choose_save_dir)
        m.add_command(label="Reset save folder to Downloads", command=self.reset_save_dir)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        bar.add_cascade(label="File", menu=m)

        m = styles.menu(bar, c)
        m.add_command(label="Light mode" if self.dark else "Dark mode", accelerator=acc + "D",
                      command=self.toggle_dark)
        if open_db:
            m.add_separator()
            m.add_command(label="Home", command=lambda: self.go("home"))
            m.add_command(label="Tasks", command=lambda: self.go("tasks"))
            m.add_command(label="Reports", command=lambda: self.go("reports"))
            m.add_command(label="Search", accelerator=acc + "K", command=self.focus_search)
            m.add_command(label="Back", accelerator="Cmd+[" if widgets.IS_MAC else "Alt+Left",
                          command=self.back)
        bar.add_cascade(label="View", menu=m)

        if open_db:
            m = styles.menu(bar, c)
            if self.db.can_edit:
                m.add_command(label="Import from a spreadsheet…", command=lambda: self.go("import"))
            m.add_command(label="Export everything to Excel…", command=lambda: self.tool("export_all"))
            m.add_separator()
            m.add_command(label="Check the data…", command=lambda: self.go("datacheck"))
            m.add_command(label="Recycle bin", command=lambda: self.go("bin"))
            m.add_command(label="History of changes", command=lambda: self.go("history"))
            bar.add_cascade(label="Tools", menu=m)

            m = styles.menu(bar, c)
            if self.db.is_admin:
                m.add_command(label="Change the design…", command=lambda: self.go("design"))
                m.add_command(label="People and passwords…", command=lambda: self.go("people"))
            m.add_command(label="Settings…", command=lambda: self.go("settings"))
            if self.db.can_edit and self.db.has_examples():
                m.add_separator()
                m.add_command(label="Remove the example records", command=self.remove_examples)
            bar.add_cascade(label="Setup", menu=m)

        m = styles.menu(bar, c)
        m.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        m.add_command(label=f"About {APP_NAME}", command=self.show_about)
        bar.add_cascade(label="Help", menu=m)
        self.root.configure(menu=bar)

    def menu_new(self):
        if self._locked:
            return
        if self._leave_ok():
            self.show_screen("wizard")

    def menu_open(self, path: str | None = None):
        if self._locked:
            return
        if not self._leave_ok():
            return
        if path is None:
            path = widgets.ask_open_file(
                self, "Open a CRM", [("CRM Builder file", "*" + FILE_EXT), ("All files", "*.*")],
                initialdir=os.path.dirname(self.settings.get("last_db") or "") or None)
        if not path:
            return
        if self.db is not None and os.path.abspath(path) == self.db.path:
            return
        try:
            new = dbm.Database.open(path)
        except dbm.DBError as exc:
            # whatever is open now stays open
            widgets.error(self, "Could not open that file", str(exc))
            return
        old, self.db = self.db, None
        self._opened(new)
        if old is not None:
            old.close()

    def _leave_ok(self) -> bool:
        if self.page is not None and not self.page.can_leave():
            return False
        if self.screen is not None and hasattr(self.screen, "can_leave"):
            return bool(self.screen.can_leave())
        return True

    def choose_save_dir(self):
        folder = widgets.ask_folder(self, "Choose where exports and backups are saved by default")
        if folder:
            self.settings["save_dir"] = folder
            self.save_settings()
            self.toast(f"Exports will now start in {folder}")

    def reset_save_dir(self):
        self.settings.pop("save_dir", None)
        self.save_settings()
        self.toast("Exports will start in your Downloads folder")

    def remove_examples(self):
        if not widgets.confirm(self, "Remove the example records",
                               "This erases the example records the setup wizard added, with "
                               "their notes and tasks. Records you added yourself are not touched.",
                               yes="Remove examples", danger=True):
            return
        n = self.db.remove_examples()
        self.toast(f"Removed {n} example record{'s' if n != 1 else ''}")
        self.rebuild()

    def tool(self, name: str, *args):
        """Run a function from ui/tools.py by name (keeps start-up quick and
        lets the menu work even if that module failed to load)."""
        if self._locked:
            return None
        try:
            mod = importlib.import_module("crmbuilder.ui.tools")
            fn = getattr(mod, name)
        except Exception as exc:
            widgets.error(self, "Not available", f"That tool could not be loaded.\n\n{exc}")
            return None
        return fn(self, *args)

    def show_shortcuts(self):
        rows = [(f"{MOD_LABEL}+K", "Search everything"), (f"{MOD_LABEL}+N", "New record in this list"),
                (f"{MOD_LABEL}+S", "Save"), ("Esc", "Close the record / cancel"),
                ("Cmd+[" if widgets.IS_MAC else "Alt+Left", "Back"),
                (f"{MOD_LABEL}+D", "Dark / light mode"),
                (f"{MOD_LABEL}+L", "Lock (when there is a password)"),
                ("Enter", "Open the selected record"), ("Tab", "Next box, including out of notes")]
        d = widgets.Dialog(self, "Keyboard shortcuts")
        for i, (k, what) in enumerate(rows):
            ttk.Label(d.body, text=k, style="H3.TLabel").grid(row=i, column=0, sticky="w", padx=(0, 24), pady=3)
            ttk.Label(d.body, text=what).grid(row=i, column=1, sticky="w", pady=3)
        b = d.add_button("Close", d.cancel, accent=True)
        d.show(focus=b)

    def show_about(self):
        widgets.info(self, f"About {APP_NAME}",
                     f"{APP_NAME} {__version__}\n\nA CRM you design yourself. Everything is kept "
                     "in one file on your own computer or shared drive; nothing is sent anywhere.")

    # --------------------------------------------------------- navigation
    def go(self, name: str, **kw) -> bool:
        """Show a page. Returns False if the person chose to stay where they were."""
        return self._go(name, kw, push=True, check=True)

    def replace(self, name: str, **kw) -> bool:
        """Like go(), but Back will skip the page being replaced."""
        return self._go(name, kw, push=False, check=False)

    def _go(self, name, kw, push, check, quiet=False) -> bool:
        """quiet: say nothing if the page turns out to be about something that
        no longer exists (a record deleted for good, a list removed)."""
        if self.db is None or self.content is None or self._locked:
            return False
        if check and self.page is not None and not self.page.can_leave():
            return False
        old, old_name = self.page, self.page_name
        old_state = None
        if old is not None and push:
            try:
                old_state = old.state()
            except Exception:
                old_state = {}
        try:
            module, cls = PAGES[name]
            klass = getattr(importlib.import_module(f"crmbuilder.ui.{module}"), cls)
            page = klass(self.content, self, **kw)
        except Exception as exc:
            if not isinstance(exc, dbm.DBError):
                self._callback_error(type(exc), exc, exc.__traceback__)
            elif not quiet:
                widgets.error(self, "That did not work", str(exc))
            for w in self.content.winfo_children():
                if w is not old:
                    w.destroy()
            return False
        if old is not None:
            if push and old_name and not (old_name == name and old_state == kw):
                self.history.append((old_name, old_state))
                del self.history[:-40]
            old.destroy()
        page.pack(fill="both", expand=True)
        self.page, self.page_name = page, name
        self._mark_nav()
        return True

    def back(self):
        if self.db is None or self.content is None or self._grabbed() \
                or self._locked:
            return
        if self.page is not None and not self.page.can_leave():
            return
        while self.history:
            name, kw = self.history.pop()
            if self._go(name, kw, push=False, check=False, quiet=True):
                return
        self._go("home", {}, push=False, check=False)

    def reload(self):
        """Rebuild the current page from scratch (after data changed under it)."""
        if self.page is not None:
            self._go(self.page_name, self.page.state(), push=False, check=False)
        self.refresh_sidebar()

    def open_record(self, record_id: int):
        self.go("record", record_id=record_id)

    def new_record(self, type_key: str, preset: dict | None = None):
        if not self.db.can_edit:
            self.toast("Your account is read only.", "bad")
            return
        self.go("record", type_key=type_key, preset=preset or {})

    def new_shortcut(self):
        if self.db is None or self.shell is None or self._grabbed() or self._locked:
            return
        key = getattr(self.page, "nav_key", "")
        if key.startswith("list:"):
            self.new_record(key[5:])
        elif hasattr(self, "new_button") and self.new_button.winfo_exists():
            self.new_button.event_generate("<Button-1>")

    def focus_search(self):
        if self.search_entry is not None and not self._grabbed() and not self._locked:
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")

    def _search(self, query: str):
        if self.page_name == "search" and hasattr(self.page, "set_query"):
            self.page.set_query(query)
        elif query:
            self.go("search", query=query)
            if self.search_entry is not None:
                self.search_entry.focus_set()
                self.search_entry.icursor("end")

    # --------------------------------------------------------------- toast
    def toast(self, text: str, kind: str = "info"):
        """A short message at the bottom of the window that fades by itself.
        kind: 'info', 'good' or 'bad'."""
        self.test_log.append(("toast", text, kind))
        host = self.shell or self.root
        c = self.c
        if self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError:
                pass
        if self._toast_after:
            try:
                self.root.after_cancel(self._toast_after)
            except tk.TclError:
                pass
        colour = {"good": c["good"], "bad": c["bad"]}.get(kind, c["accent"])
        frame = tk.Frame(host, bg=colour)
        label = tk.Label(frame, text=text, bg=c["panel"], fg=c["text"], padx=16, pady=9,
                         font=(c["ui"], fs(10)), wraplength=640, justify="left")
        label.pack(padx=(4, 1), pady=1)
        frame.place(relx=0.5, rely=1.0, anchor="s", y=-18)
        frame.lift()
        self._toast = frame

        def gone(_e=None):
            try:
                frame.destroy()
            except tk.TclError:
                pass
            if self._toast is frame:
                self._toast = None
        label.bind("<Button-1>", gone)      # in the way of something? a click clears it
        self._toast_after = self.root.after(5200 if kind == "bad" else 3600, gone)


def run(argv: list[str] | None = None) -> int:
    argv = [a for a in (argv or []) if not a.startswith("-psn_")]
    path = next((a for a in argv if not a.startswith("-") and os.path.isfile(a)), None)
    root = tk.Tk()
    root.withdraw()
    try:
        App(root, open_path=path)
    except Exception:
        root.destroy()
        raise
    root.deiconify()
    root.mainloop()
    return 0
