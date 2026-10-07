"""Shared building blocks: scrolling frames, dialogs, field editors and the
form that is generated from a list's design."""
from __future__ import annotations

import calendar
import datetime as _dt
import re
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from .. import blueprint as bpm
from .. import validate
from . import styles
from .styles import fs

IS_MAC = sys.platform == "darwin"
MOD = "Command" if IS_MAC else "Control"
MOD_LABEL = "Cmd" if IS_MAC else "Ctrl"


# ---------------------------------------------------------------- basics
def grab_holder(root: tk.Misc) -> str:
    """Path of the window that holds the grab (an open dialog, the drop-down
    of a combobox), or '' when nothing does. tkinter's own grab_current()
    raises KeyError for windows it did not create, such as that drop-down."""
    try:
        return str(root.tk.call("grab", "current", root._w))
    except tk.TclError:
        return ""


def focus_holder(root: tk.Misc) -> str:
    """Path of the widget with the keyboard focus, or ''. (focus_get() raises
    KeyError when it is in the drop-down of a combobox.)"""
    try:
        return str(root.tk.call("focus"))
    except tk.TclError:
        return ""


def trace_var(var: tk.Variable, widget: tk.Misc, callback) -> None:
    """Call callback whenever var is written - until widget is destroyed.
    (Tk keeps a traced variable for ever, and with it everything its callback
    can reach, which is usually the whole page. So the trace has to go when
    the widget does.)"""
    name = var.trace_add("write", callback)

    def gone(event):
        if event.widget is widget:
            try:
                var.trace_remove("write", name)
            except tk.TclError:
                pass
    widget.bind("<Destroy>", gone, add="+")


class ScrollFrame(ttk.Frame):
    """A frame whose .body scrolls vertically. Put content in .body."""

    def __init__(self, parent, app, panel: bool = False, **kw):
        super().__init__(parent, style="Panel.TFrame" if panel else "TFrame", **kw)
        bg = app.c["panel"] if panel else app.c["bg"]
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.body = ttk.Frame(self.canvas, style="Panel.TFrame" if panel else "TFrame")
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", self._body_changed)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self._bar_shown = False
        self._flips: list[float] = []
        self._pinned = False

    def _on_scroll(self, lo, hi):
        self.bar.set(lo, hi)
        need = float(lo) > 0.0 or float(hi) < 1.0
        if need and not self._bar_shown:
            self.bar.pack(side="right", fill="y")
            self._bar_shown = True
            self._flipped()
        elif not need and self._bar_shown and not self._pinned:
            # Showing the bar narrows the content, which can re-wrap text and
            # change its height. Only hide again when it fits with room to
            # spare, or the bar can flicker on and off for ever.
            if self.body.winfo_reqheight() < self.canvas.winfo_height() - 48:
                self.bar.pack_forget()
                self._bar_shown = False
                self._flipped()

    def _flipped(self):
        import time
        now = time.time()
        self._flips = [t for t in self._flips if now - t < 2.0] + [now]
        if len(self._flips) > 6:        # still flickering: keep the bar for good
            self._pinned = True
            if not self._bar_shown:
                self.bar.pack(side="right", fill="y")
                self._bar_shown = True

    def _body_changed(self, _e=None):
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(),
                                            self.body.winfo_reqheight()))

    def scroll(self, units: int):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)

    def reveal(self, widget):
        """Scroll just far enough for widget to be in view (with its label)."""
        try:
            view = self.canvas.winfo_height()
            total = max(1, self.body.winfo_height())
            if total <= view or view < 40:
                return
            y = widget.winfo_rooty() - self.body.winfo_rooty()
            top = self.canvas.canvasy(0)
            if y - 30 < top:
                self.canvas.yview_moveto(max(0, y - 30) / total)
            elif y + widget.winfo_height() + 14 > top + view:
                self.canvas.yview_moveto((y + widget.winfo_height() + 14 - view) / total)
        except tk.TclError:
            pass

    def show(self, widget):
        """Scroll so that widget is visible."""
        try:
            self.update_idletasks()
            y = widget.winfo_rooty() - self.body.winfo_rooty()
            h = max(1, self.body.winfo_reqheight())
            self.canvas.yview_moveto(max(0, (y - 40) / h))
        except tk.TclError:
            pass


def install_wheel(root: tk.Misc):
    """One mouse-wheel handler for the whole app: scroll the ScrollFrame under
    the pointer. Lists and text boxes keep their own scrolling."""
    def find(widget):
        w = widget
        while w is not None:
            if isinstance(w, tk.Text):
                # a box with more in it than it shows scrolls itself; one that
                # shows everything lets the page go on scrolling past it
                try:
                    fits = tuple(w.yview()) == (0.0, 1.0)
                except tk.TclError:
                    fits = False
                if not fits:
                    return None
            elif isinstance(w, (ttk.Treeview, tk.Listbox)):
                return None
            if isinstance(w, tk.Canvas) and getattr(w, "wheel_owner", None) is not None:
                return w.wheel_owner
            if isinstance(w, tk.Canvas) and isinstance(w.master, ScrollFrame):
                return w.master
            w = getattr(w, "master", None)
        return None

    def on_wheel(event, units=None):
        try:
            target = root.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return
        frame = find(target)
        if frame is None:
            return
        if units is None:
            units = -1 if event.delta > 0 else 1
        frame.scroll(units * 3)

    def on_tab(event):
        # Tab has just moved the keyboard to this control: if it is in a part
        # of a scrolling page that is out of sight, bring it into view.
        w = event.widget
        if not isinstance(w, tk.Misc):
            return
        m = w
        while m is not None:
            if isinstance(m, tk.Canvas) and isinstance(m.master, ScrollFrame):
                m.master.reveal(w)
                return
            m = getattr(m, "master", None)

    for seq in ("<KeyRelease-Tab>", "<KeyRelease-ISO_Left_Tab>"):
        try:
            root.bind_all(seq, on_tab, add="+")
        except tk.TclError:
            pass                      # no such key on this system

    root.bind_all("<MouseWheel>", on_wheel, add="+")
    # Tk 9 reports two-finger trackpad scrolling as its own event
    state = {"acc": 0.0}

    def on_touchpad(event):
        try:
            dy = (int(event.delta) & 0xFFFF)
            if dy >= 0x8000:
                dy -= 0x10000
        except (TypeError, ValueError):
            return
        state["acc"] += dy
        steps = int(state["acc"] / 30)
        if steps:
            state["acc"] -= steps * 30
            try:
                target = root.winfo_containing(event.x_root, event.y_root)
            except (tk.TclError, KeyError):
                return
            frame = find(target)
            if frame is not None:
                frame.scroll(-steps)
    try:
        root.bind_all("<TouchpadScroll>", on_touchpad, add="+")
    except tk.TclError:
        pass                      # Tk 8.6 has no such event
    if sys.platform.startswith("linux"):
        root.bind_all("<Button-4>", lambda e: on_wheel(e, -1), add="+")
        root.bind_all("<Button-5>", lambda e: on_wheel(e, 1), add="+")


class AutoScrollbar(ttk.Scrollbar):
    """A scrollbar that hides itself when everything fits. Use with grid()."""

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


def make_tree(parent, columns, show="headings", selectmode="browse", height=None, xscroll=False):
    """A Treeview in a frame with scrollbars that appear only when needed.
    Returns (frame, tree): pack/grid the frame, use the tree."""
    frame = ttk.Frame(parent)
    kw = {} if height is None else {"height": height}
    tree = ttk.Treeview(frame, columns=columns, show=show, selectmode=selectmode, **kw)
    ybar = AutoScrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=ybar.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    if xscroll:
        xbar = AutoScrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=xbar.set)
        xbar.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


def card(parent, app, padding=14) -> tk.Frame:
    """A bordered panel. Children should use the Panel.* styles."""
    c = app.c
    f = tk.Frame(parent, bg=c["panel"], highlightthickness=1, highlightbackground=c["border"],
                 highlightcolor=c["border"], padx=padding, pady=padding)
    return f


def link(parent, text, command, style="Link.TLabel") -> ttk.Label:
    """A clickable text link (also reachable with Tab, activated by Enter/Space)."""
    lab = ttk.Label(parent, text=text, style=style, cursor="hand2", takefocus=1)
    lab.bind("<Button-1>", lambda _e: command())
    lab.bind("<Return>", lambda _e: command())
    lab.bind("<space>", lambda _e: command())

    def ring(on: bool):
        # a label has no focus ring of its own: underline the link the keyboard is on
        try:
            font = str(ttk.Style(lab).lookup(style, "font"))
            lab.configure(font=(font + " underline") if (on and font) else "")
        except tk.TclError:
            pass
    lab.bind("<FocusIn>", lambda _e: ring(True), add="+")
    lab.bind("<FocusOut>", lambda _e: ring(False), add="+")
    return lab


def make_text(parent, app, height=4, readonly=False, **kw) -> tk.Text:
    """A themed multi-line box. Tab moves to the next control rather than indenting."""
    t = tk.Text(parent, height=height, **{**styles.text_colors(app.c, readonly), **kw})

    def nxt(_e):
        t.tk_focusNext().focus_set()
        return "break"

    def prev(_e):
        t.tk_focusPrev().focus_set()
        return "break"

    t.bind("<Tab>", nxt)
    t.bind("<Shift-Tab>", prev)
    if sys.platform.startswith("linux"):
        t.bind("<ISO_Left_Tab>", prev)
    if readonly:
        t.configure(state="disabled", takefocus=0)
    return t


def set_text(widget: tk.Text, text: str):
    state = str(widget.cget("state"))
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text or "")
    widget.configure(state=state)


class SearchEntry(ttk.Entry):
    """An entry with greyed placeholder text that calls on_change shortly after
    typing stops (and at once on Enter)."""

    def __init__(self, parent, app, placeholder="Search", on_change=None, delay=220, width=28):
        self.var = tk.StringVar(parent)
        super().__init__(parent, textvariable=self.var, width=width)
        self.app, self.placeholder, self.on_change, self.delay = app, placeholder, on_change, delay
        self._showing = False
        self._after = None
        self._last = ""
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<KeyRelease>", self._key)
        for seq in ("<<Paste>>", "<<Cut>>"):          # with the mouse: no key is released
            self.bind(seq, lambda _e: self.after(1, self._key), add="+")
        self.bind("<Return>", lambda _e: self._fire(now=True))
        self.bind("<Escape>", self._escape)
        self._focus_out()

    def _focus_in(self, _e=None):
        if self._showing:
            self.var.set("")
            self.configure(foreground=self.app.c["field_text"])
            self._showing = False

    def _focus_out(self, _e=None):
        if not self.var.get():
            self._showing = True
            self.var.set(self.placeholder)
            self.configure(foreground=self.app.c["dim"])

    def _key(self, _e=None):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(self.delay, self._fire)

    def _fire(self, now=False):
        self._after = None
        value = self.get_text()
        if (value != self._last or now) and self.on_change:
            self._last = value
            self.on_change(value)

    def get_text(self) -> str:
        return "" if self._showing else self.var.get().strip()

    def set_text(self, text: str):
        self._focus_in()
        self.var.set(text)
        self._last = text
        if not text and focus_holder(self) != str(self):
            self._focus_out()

    def _escape(self, _e=None):
        """Esc empties the box. Only when it is already empty does Esc go on
        to mean what it means on the page (close the dialog, go back)."""
        if not self.get_text():
            return None
        self.clear()
        return "break"

    def clear(self):
        self.var.set("")
        self._fire(now=True)


class Tooltip:
    def __init__(self, widget, text, app):
        self.widget, self.text, self.app, self.tip, self._after = widget, text, app, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._after = self.widget.after(600, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        c = self.app.c
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=c["hint"], fg=c["text"], padx=8, pady=4,
                 font=(c["ui"], fs(9)), justify="left", wraplength=320,
                 highlightthickness=1, highlightbackground=c["border"]).pack()

    def _hide(self, _e=None):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


# --------------------------------------------------------------- dialogs
class Dialog(tk.Toplevel):
    """A themed modal window. Build content in .body, add buttons with
    add_button(), then call show() which waits and returns .result."""

    def __init__(self, app, title: str, width: int | None = None, resizable=False):
        parent = app.root
        super().__init__(parent)
        self.app = app
        self.result = None
        self.withdraw()
        self.title(title)
        self.configure(bg=app.c["bg"])
        self.transient(parent)
        self.resizable(resizable, resizable)
        self._width = width
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        self.body = ttk.Frame(outer)
        self.body.pack(fill="both", expand=True)
        self.buttons = ttk.Frame(outer)
        self.buttons.pack(fill="x", pady=(16, 0))
        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Return>", self._return_key)
        self.bind("<KP_Enter>", self._return_key)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._default = None
        self._enter_default = False

    def add_button(self, text, command, accent=False, side="right", style=None):
        b = ttk.Button(self.buttons, text=text, command=command,
                       style=style or ("Accent.TButton" if accent else "TButton"))
        b.pack(side=side, padx=(8, 0) if side == "right" else (0, 8))
        if accent:
            self._default = b
        return b

    def default_on_enter(self):
        """Make Enter press the accent button (not inside multi-line boxes)."""
        self._enter_default = True

    def _return_key(self, e):
        w = e.widget
        try:
            if isinstance(w, tk.Text) or not self.winfo_exists():
                return None
            if isinstance(w, ttk.Button):
                # Enter presses the button the keyboard is on (Tk itself only
                # does that for the space bar)
                if w.winfo_exists() and w.winfo_toplevel() is self \
                        and str(w.cget("state")) != "disabled":
                    w.invoke()
                return "break"
            if not self._enter_default:
                return None
            if self._default is not None and str(self._default.cget("state")) != "disabled":
                self._default.invoke()
        except tk.TclError:
            pass
        return "break"

    def ok(self, result=True):
        self.result = result
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def show(self, focus=None):
        self.update_idletasks()
        parent = self.app.root
        w = max(self.winfo_reqwidth(), self._width or 0)
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(h, sh - 80)
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        except tk.TclError:
            x, y = (sw - w) // 2, (sh - h) // 3
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h - 40))
        self.geometry(f"{w}x{h}+{x}+{y}")
        # a dialog opened from another dialog: when this one closes, the one
        # underneath must be modal again and get the keyboard back
        before_grab, before_focus = grab_holder(parent), focus_holder(parent)
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        (focus or self).focus_set()
        self.wait_window(self)
        try:
            if before_grab and int(parent.tk.call("winfo", "exists", before_grab)) \
                    and not grab_holder(parent):
                parent.tk.call("grab", "set", before_grab)
            if before_focus and int(parent.tk.call("winfo", "exists", before_focus)):
                parent.tk.call("focus", before_focus)
        except tk.TclError:
            pass
        return self.result


def info(app, title, message):
    if app.testing:
        app.test_log.append(("info", title, message))
        return
    messagebox.showinfo(title, message, parent=app.root)


def error(app, title, message):
    if app.testing:
        app.test_log.append(("error", title, message))
        return
    messagebox.showerror(title, message, parent=app.root)


def confirm(app, title, message, yes="Yes", no="Cancel", danger=False) -> bool:
    """A yes/no question with the action named on the button."""
    if app.testing:
        app.test_log.append(("confirm", title, message))
        return app.test_answers.pop(0) if app.test_answers else True
    d = Dialog(app, title, width=420)
    ttk.Label(d.body, text=message, wraplength=400, justify="left").pack(anchor="w")
    b = d.add_button(yes, lambda: d.ok(True), accent=not danger,
                     style="Danger.TButton" if danger else None)
    d.add_button(no, d.cancel)
    d.default_on_enter()
    return bool(d.show(focus=b))


def choose(app, title, message, options: list[tuple[str, str]], cancel="Cancel"):
    """Ask a question with several named answers. options = [(key, label)];
    the first is the suggested one. Returns the key, or None."""
    if app.testing:
        app.test_log.append(("choose", title, message))
        return app.test_answers.pop(0) if app.test_answers else options[0][0]
    d = Dialog(app, title, width=460)
    ttk.Label(d.body, text=message, wraplength=440, justify="left").pack(anchor="w")
    first = None
    for i, (key, label) in enumerate(options):
        b = d.add_button(label, lambda k=key: d.ok(k), accent=(i == 0))
        first = first or b
    d.add_button(cancel, d.cancel, side="left")
    return d.show(focus=first)


def ask_string(app, title, prompt, initial="", ok="OK", secret=False, check=None):
    """Ask for one line of text. check(text) may return an error sentence."""
    if app.testing:
        app.test_log.append(("ask", title, prompt))
        return app.test_answers.pop(0) if app.test_answers else initial
    d = Dialog(app, title, width=420)
    ttk.Label(d.body, text=prompt, wraplength=400, justify="left").pack(anchor="w", pady=(0, 6))
    var = tk.StringVar(d, value=initial)
    e = ttk.Entry(d.body, textvariable=var, show="•" if secret else "")
    e.pack(fill="x")
    err = ttk.Label(d.body, text="", style="Error.TLabel", wraplength=400)
    err.pack(anchor="w", pady=(4, 0))

    def done():
        text = var.get().strip() if not secret else var.get()
        problem = check(text) if check else None
        if problem:
            err.configure(text=problem)
            return
        d.ok(text)

    d.add_button(ok, done, accent=True)
    d.add_button("Cancel", d.cancel)
    d.default_on_enter()
    e.select_range(0, "end")
    return d.show(focus=e)


def _file_answer(app, what, title):
    app.test_log.append((what, title, ""))
    return app.test_answers.pop(0) if app.test_answers else ""


def ask_open_file(app, title, filetypes, initialdir=None) -> str:
    """Ask for a file to open. Returns '' when cancelled."""
    if app.testing:
        return _file_answer(app, "open", title)
    from tkinter import filedialog
    from .. import paths
    return filedialog.askopenfilename(parent=app.root, title=title, filetypes=filetypes,
                                      initialdir=initialdir or paths.default_save_dir(app.settings)) or ""


def ask_save_file(app, title, initialfile, filetypes, defaultextension="", initialdir=None) -> str:
    """Ask where to save. Starts in the default save folder (Downloads unless changed)."""
    if app.testing:
        return _file_answer(app, "save", title)
    from tkinter import filedialog
    from .. import paths
    return filedialog.asksaveasfilename(
        parent=app.root, title=title, initialfile=initialfile, filetypes=filetypes,
        defaultextension=defaultextension,
        initialdir=initialdir or paths.default_save_dir(app.settings)) or ""


def ask_folder(app, title, initialdir=None) -> str:
    if app.testing:
        return _file_answer(app, "folder", title)
    from tkinter import filedialog
    from .. import paths
    return filedialog.askdirectory(parent=app.root, title=title,
                                   initialdir=initialdir or paths.default_save_dir(app.settings)) or ""


class DatePicker(tk.Toplevel):
    """A small pop-up month calendar. Calls on_pick(date) and closes."""

    def __init__(self, app, anchor: tk.Widget, initial: _dt.date | None, on_pick):
        super().__init__(anchor)
        self.app, self.on_pick = app, on_pick
        c = app.c
        self.wm_overrideredirect(True)
        self.configure(bg=c["border"])
        self.month = (initial or _dt.date.today()).replace(day=1)
        self.selected = initial
        self.inner = tk.Frame(self, bg=c["panel"], padx=8, pady=8)
        self.inner.pack(padx=1, pady=1)
        self._build()
        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        if y + self.winfo_reqheight() > self.winfo_screenheight() - 40:
            y = anchor.winfo_rooty() - self.winfo_reqheight() - 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._anchor = anchor
        self.bind("<Escape>", self._escape)
        self.bind("<FocusOut>", self._focus_out)
        self.focus_set()

    def _escape(self, _e=None):
        """Esc closes the calendar only - not the page or dialog under it."""
        self.destroy()
        try:
            self._anchor.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _focus_out(self, _e=None):
        self.after(120, self._maybe_close)

    def _maybe_close(self):
        try:
            if not focus_holder(self).startswith(str(self)):
                self.destroy()
        except tk.TclError:
            pass

    def _shift(self, months):
        y, m = divmod(self.month.year * 12 + self.month.month - 1 + months, 12)
        self.month = _dt.date(y, m + 1, 1)
        self._build()

    def _build(self):
        c = self.app.c
        for w in self.inner.winfo_children():
            w.destroy()
        top = tk.Frame(self.inner, bg=c["panel"])
        top.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 6))
        ttk.Button(top, text="‹", width=3, style="PanelFlat.TButton",
                   command=lambda: self._shift(-1)).pack(side="left")
        ttk.Button(top, text="›", width=3, style="PanelFlat.TButton",
                   command=lambda: self._shift(1)).pack(side="right")
        ttk.Label(top, text=self.month.strftime("%B %Y"), style="PanelH3.TLabel").pack(expand=True)
        for i, d in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            ttk.Label(self.inner, text=d, style="PanelHelp.TLabel", anchor="center",
                      width=4).grid(row=1, column=i)
        today = _dt.date.today()
        for r, week in enumerate(calendar.Calendar(0).monthdatescalendar(
                self.month.year, self.month.month)):
            for col, day in enumerate(week):
                inside = day.month == self.month.month
                sel = day == self.selected
                b = tk.Label(self.inner, text=str(day.day), width=4, cursor="hand2",
                             font=(c["ui"], fs(9), "bold" if day == today else "normal"),
                             bg=c["accent"] if sel else c["panel"],
                             fg=c["accent_text"] if sel else (c["text"] if inside else c["dim"]),
                             pady=3)
                b.grid(row=r + 2, column=col)
                b.bind("<Button-1>", lambda _e, d=day: self._pick(d))
        foot = tk.Frame(self.inner, bg=c["panel"])
        foot.grid(row=9, column=0, columnspan=7, sticky="ew", pady=(6, 0))
        ttk.Button(foot, text="Today", style="PanelFlat.TButton",
                   command=lambda: self._pick(today)).pack(side="left")

    def _pick(self, day):
        self.on_pick(day)
        self.destroy()


# ---------------------------------------------------------- field editors
class Editor:
    """One field's editing control. Subclasses create self.widget."""

    def __init__(self, parent, app, field, panel=True):
        self.parent, self.app, self.field, self.panel = parent, app, field, panel
        self.on_change = None
        self.widget: tk.Widget | None = None
        self.focus_widget: tk.Widget | None = None

    def changed(self, *_a):
        if self.on_change:
            self.on_change(self)

    def get(self):
        raise NotImplementedError

    def set(self, value):
        raise NotImplementedError

    def set_bad(self, bad: bool):
        pass

    def set_enabled(self, enabled: bool):
        try:
            (self.focus_widget or self.widget).configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def focus(self):
        (self.focus_widget or self.widget).focus_set()


class EntryEditor(Editor):
    def __init__(self, parent, app, field, panel=True):
        super().__init__(parent, app, field, panel)
        kind = field["kind"]
        self.var = tk.StringVar(parent)
        frame = ttk.Frame(parent, style="Panel.TFrame" if panel else "TFrame")
        self.widget = frame
        self.entry = ttk.Entry(frame, textvariable=self.var)
        self.focus_widget = self.entry
        self.pick_button = None
        allowed = validate.allowed_chars(kind)
        if allowed:
            vcmd = (frame.register(lambda new: all(ch in allowed for ch in new)), "%P")
            self.entry.configure(validate="key", validatecommand=vcmd)
        # the button is packed first so that in a narrow column the box gives
        # way, not the button's label
        if kind == "date":
            self.entry.bind("<KeyRelease>", self._date_key)
            b = ttk.Button(frame, text="Pick", takefocus=0, style="Beside.Small.TButton",
                           command=self._calendar)
            b.pack(side="right", padx=(4, 0))
            self.pick_button = b
            Tooltip(b, "Choose from a calendar. You can also type today, tomorrow or +7.", app)
        elif kind in ("email", "url", "phone", "postcode"):
            tip = {"email": "Write an email to this address", "url": "Open the website",
                   "phone": "Call this number (if this computer can make calls)",
                   "postcode": "Show this postcode on a map"}[kind]
            word = {"email": "Email", "url": "Open", "phone": "Call", "postcode": "Map"}[kind]
            b = ttk.Button(frame, text=word, takefocus=0, style="Beside.Small.TButton",
                           command=self._open)
            b.pack(side="right", padx=(4, 0))
            Tooltip(b, tip, app)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<FocusOut>", self._tidy, add="+")
        trace_var(self.var, self.entry, self.changed)

    def _date_key(self, event):
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Tab", "Home", "End",
                            "Shift_L", "Shift_R"):
            return
        text = self.var.get()
        if self.entry.index("insert") != len(text):
            return
        # Most people type the separators themselves. After "13" the slash is
        # already there, so their own "/" (or "-", "." or a space) must not
        # make a second one; and "13th" or "13 Nov" needs no slash at all.
        m = re.fullmatch(r"((?:\d{1,2}/){1,2})[/\-. ]", text)
        named = re.fullmatch(r"(\d{2})/([A-Za-z])", text)
        if m:
            new = m.group(1)
        elif named:
            new = named.group(1) + named.group(2)
        else:
            new = validate.live_date(text)
        if new != text:
            self.var.set(new)
            self.entry.icursor("end")

    def _calendar(self):
        if str(self.entry.cget("state")) == "disabled":
            return
        current = validate.parse_date(self.var.get())

        def pick(day):
            self.var.set(day.strftime("%d/%m/%Y"))
            self.entry.focus_set()
        DatePicker(self.app, self.entry, current, pick)

    def _open(self):
        value, err = validate.normalise(dict(self.field, required=False), self.var.get())
        if err or not value:
            return
        kind = self.field["kind"]
        try:
            if kind == "email":
                webbrowser.open("mailto:" + value)
            elif kind == "url":
                webbrowser.open(value)
            elif kind == "phone":
                webbrowser.open("tel:" + value.replace(" ", ""))
            elif kind == "postcode":
                webbrowser.open("https://www.google.com/maps/search/" + value.replace(" ", "+"))
        except Exception:
            pass

    def _tidy(self, _e=None):
        """On leaving the box, rewrite what was typed in its tidy form."""
        text = self.var.get()
        if not text.strip():
            return
        value, err = validate.normalise(dict(self.field, required=False), text)
        if not err and value is not None:
            tidy = validate.to_edit(self.field, value)
            if tidy != text:
                self.var.set(tidy)

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(validate.to_edit(self.field, value))

    def set_bad(self, bad):
        self.entry.configure(style="Bad.TEntry" if bad else "TEntry")

    def set_enabled(self, enabled):
        self.entry.configure(state="normal" if enabled else "disabled")
        if self.pick_button is not None:      # (Email, Call, Map and Open still work on a read-only form)
            self.pick_button.configure(state="normal" if enabled else "disabled")


class LongTextEditor(Editor):
    def __init__(self, parent, app, field, panel=True):
        super().__init__(parent, app, field, panel)
        self.text = make_text(parent, app, height=4)
        self.widget = self.focus_widget = self.text
        self.text.bind("<<Modified>>", self._modified)

    def _modified(self, _e=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.changed()

    def get(self):
        return self.text.get("1.0", "end-1c")

    def set(self, value):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "" if value is None else str(value))
        self.text.edit_modified(False)
        self.text.edit_reset()

    def set_bad(self, bad):
        c = self.app.c
        self.text.configure(highlightbackground=c["bad"] if bad else c["field_border"])


class ChoiceEditor(Editor):
    def __init__(self, parent, app, field, panel=True, values=None):
        super().__init__(parent, app, field, panel)
        self.var = tk.StringVar(parent)
        self.options = values if values is not None else validate.options_of(field)
        self.combo = ttk.Combobox(parent, textvariable=self.var, state="readonly",
                                  values=[""] + self.options)
        self.widget = self.focus_widget = self.combo
        self.combo.bind("<<ComboboxSelected>>", self.changed)
        # the wheel must not change a value while the page scrolls past it
        self.combo.bind("<MouseWheel>", lambda _e: "break")
        self.combo.bind("<Button-4>", lambda _e: "break")
        self.combo.bind("<Button-5>", lambda _e: "break")

    def get(self):
        return self.var.get()

    def set(self, value):
        value = "" if value is None else str(value)
        if value and value not in self.options:
            self.combo.configure(values=[""] + self.options + [value])
        self.var.set(value)

    def set_bad(self, bad):
        self.combo.configure(style="Bad.TCombobox" if bad else "TCombobox")

    def set_enabled(self, enabled):
        self.combo.configure(state="readonly" if enabled else "disabled")


class UserEditor(ChoiceEditor):
    def __init__(self, parent, app, field, panel=True):
        self.users = app.db.users(active_only=True) if app.db else []
        super().__init__(parent, app, field, panel, values=[u["name"] for u in self.users])

    def get(self):
        name = self.var.get()
        for u in self.users:
            if u["name"] == name:
                return u["id"]
        return self._extra.get(name, "") if hasattr(self, "_extra") else ""

    def set(self, value):
        self._extra = {}
        name = self.app.db.user_name(value) if (value and self.app.db) else ""
        if name and name not in self.options:
            self._extra[name] = value
            self.combo.configure(values=[""] + self.options + [name])
        self.var.set(name)


class YesNoEditor(Editor):
    def __init__(self, parent, app, field, panel=True):
        super().__init__(parent, app, field, panel)
        self.var = tk.BooleanVar(parent, value=False)
        self.check = ttk.Checkbutton(parent, text="Yes", variable=self.var, command=self.changed,
                                     style="Panel.TCheckbutton" if panel else "TCheckbutton")
        self.widget = self.focus_widget = self.check

    def get(self):
        return bool(self.var.get())

    def set(self, value):
        self.var.set(bool(value))


class TagsEditor(Editor):
    def __init__(self, parent, app, field, panel=True):
        super().__init__(parent, app, field, panel)
        self.widget = ttk.Frame(parent, style="Panel.TFrame" if panel else "TFrame")
        self.vars: dict[str, tk.BooleanVar] = {}
        self.checks = []
        self._cols = 0
        self._build(validate.options_of(field))
        self.widget.bind("<Configure>", lambda e: self._arrange(e.width)
                         if e.widget is self.widget else None)

    def _build(self, options):
        for w in self.widget.winfo_children():
            w.destroy()
        old = {k: v.get() for k, v in self.vars.items()}
        self.vars, self.checks = {}, []
        for opt in options:
            var = tk.BooleanVar(self.widget, value=old.get(opt, False))
            self.vars[opt] = var
            cb = ttk.Checkbutton(self.widget, text=opt, variable=var, command=self.changed,
                                 style="Panel.TCheckbutton" if self.panel else "TCheckbutton")
            self.checks.append(cb)
        self.focus_widget = self.checks[0] if self.checks else self.widget
        self._cols = 0
        self._arrange(self.widget.winfo_width())

    def _arrange(self, width: int):
        """As many columns of tick boxes as fit the width (never a cut-off label)."""
        if not self.checks:
            return
        widest = max(cb.winfo_reqwidth() for cb in self.checks) + 14
        cols = 2 if max(len(o) for o in self.vars) > 14 else 3       # before the width is known
        if width > 1:
            cols = max(1, min(4, width // widest))
        if cols == self._cols:
            return
        self._cols = cols
        for i, cb in enumerate(self.checks):
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 14), pady=1)

    def get(self):
        return [o for o, v in self.vars.items() if v.get()]

    def set(self, value):
        chosen = list(value) if isinstance(value, (list, tuple)) else []
        extra = [v for v in chosen if v not in self.vars]
        if extra:
            self._build(list(self.vars) + extra)
        for o, v in self.vars.items():
            v.set(o in chosen)

    def set_enabled(self, enabled):
        for cb in self.checks:
            cb.configure(state="normal" if enabled else "disabled")


class LinkEditor(Editor):
    """Shows the linked record's title. Click it (or Choose) to pick another;
    Open and Clear appear underneath once something is linked."""

    def __init__(self, parent, app, field, panel=True):
        super().__init__(parent, app, field, panel)
        self.value = None
        self.var = tk.StringVar(parent)
        fstyle = "Panel.TFrame" if panel else "TFrame"
        frame = ttk.Frame(parent, style=fstyle)
        self.widget = frame
        top = ttk.Frame(frame, style=fstyle)
        top.pack(fill="x")
        self.entry = ttk.Entry(top, textvariable=self.var, state="readonly", cursor="hand2",
                               style="Pick.TEntry")
        self.entry.bind("<Button-1>", lambda _e: self._choose())
        self.entry.bind("<Return>", lambda _e: self._choose())
        self.entry.bind("<space>", lambda _e: self._choose())
        self.entry.bind("<BackSpace>", lambda _e: self._clear())
        self.entry.bind("<Delete>", lambda _e: self._clear())
        self.focus_widget = self.entry
        self.choose_btn = ttk.Button(top, text="Choose…", style="Beside.Small.TButton",
                                     takefocus=0, command=self._choose)
        self.choose_btn.pack(side="right", padx=(4, 0))     # first, so its label is never cut
        self.entry.pack(side="left", fill="x", expand=True)
        self.links = ttk.Frame(frame, style=fstyle)
        lstyle = "PanelLink.TLabel" if panel else "Link.TLabel"
        self.open_link = link(self.links, "Open", self._open, style=lstyle)
        self.open_link.configure(takefocus=0)
        self.open_link.pack(side="left")
        self.clear_link = link(self.links, "Clear", self._clear, style=lstyle)
        self.clear_link.configure(takefocus=0)
        self.clear_link.pack(side="left", padx=(12, 0))
        self.enabled = True

    def _open(self):
        # not from inside a pop-up window: the record would open behind it
        if self.value is not None and not grab_holder(self.app.root):
            self.app.open_record(self.value)

    def _choose(self):
        if not self.enabled or not self.app.db:
            return "break"
        from .picker import pick_record
        rid = pick_record(self.app, self.field.get("link_type"), current=self.value)
        if rid is not None:
            self.set(rid)
            self.changed()
        return "break"

    def _clear(self):
        if self.enabled and self.value is not None:
            self.set(None)
            self.changed()
        return "break"

    def get(self):
        return self.value if self.value is not None else ""

    def set(self, value):
        self.value = value if isinstance(value, int) and not isinstance(value, bool) else None
        title = self.app.db.lookup("link", self.value) if (self.value and self.app.db) else ""
        self.var.set(title)
        self._show_links()

    def _show_links(self):
        if self.value is not None:
            self.links.pack(fill="x", pady=(2, 0))
            if self.enabled:
                self.clear_link.pack(side="left", padx=(12, 0))
            else:
                self.clear_link.pack_forget()
        else:
            self.links.pack_forget()

    def set_bad(self, bad):
        self.entry.configure(style="Bad.Pick.TEntry" if bad else
                             ("Pick.TEntry" if self.enabled else "TEntry"))

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.choose_btn.configure(state="normal" if enabled else "disabled")
        # a box nobody can change looks like the panel, not like something to click
        self.entry.configure(style="Pick.TEntry" if enabled else "TEntry",
                             cursor="hand2" if enabled else "")
        self._show_links()


def make_editor(parent, app, field, panel=True) -> Editor:
    kind = field.get("kind", "text")
    cls = {"longtext": LongTextEditor, "choice": ChoiceEditor, "user": UserEditor,
           "yesno": YesNoEditor, "tags": TagsEditor, "link": LinkEditor}.get(kind, EntryEditor)
    return cls(parent, app, field, panel)


# ------------------------------------------------------------------ form
class Form(ttk.Frame):
    """The form for one list, generated from its design.

    values: stored record data ({field key: value}). preview=True builds a
    lifeless copy for the Designer and the wizard (nothing can be typed).
    """

    def __init__(self, parent, app, type_def: dict, values: dict | None = None, columns: int = 2,
                 panel: bool = True, preview: bool = False, readonly: bool = False,
                 on_change=None, only: list[str] | None = None):
        super().__init__(parent, style="Panel.TFrame" if panel else "TFrame")
        self.app, self.type_def, self.panel = app, type_def, panel
        self.on_change = on_change
        self.editors: dict[str, Editor] = {}
        self.errors: dict[str, ttk.Label] = {}
        self._loading = True
        self._max_columns = columns
        self._columns = 0
        self._cells: list[tuple[tk.Widget, str]] = []   # (widget, 'section' | 'wide' | 'field')
        pre = "Panel" if panel else ""
        frame_style = "Panel.TFrame" if panel else "TFrame"
        values = values or {}
        for section, fields in bpm.sections(type_def):
            if only is not None:
                fields = [f for f in fields if f["key"] in only]
            if not fields:
                continue
            if section:
                head = ttk.Frame(self, style=frame_style)
                ttk.Label(head, text=section, style=f"{pre}H3.TLabel").pack(side="left")
                ttk.Frame(head, style="Border.TFrame", height=1).pack(
                    side="left", fill="x", expand=True, padx=(10, 0), pady=(3, 0))
                self._cells.append((head, "section"))
            elif self._cells:
                self._cells.append((ttk.Frame(self, style=frame_style), "break"))
            for f in fields:
                wide = f["kind"] in ("longtext", "tags")
                cell = ttk.Frame(self, style=frame_style)
                label = f["name"] + (" *" if f.get("required") else "")
                ttk.Label(cell, text=label, style=f"{pre}Field.TLabel").pack(anchor="w")
                ed = make_editor(cell, app, f, panel)
                ed.widget.pack(fill="x", pady=(3, 0))
                wraps = []
                if f.get("help"):
                    lab = ttk.Label(cell, text=f["help"], style=f"{pre}Help.TLabel", justify="left")
                    lab.pack(anchor="w", pady=(2, 0))
                    wraps.append(lab)
                err = ttk.Label(cell, text="", style=f"{pre}Error.TLabel", justify="left")
                wraps.append(err)
                cell.bind("<Configure>", lambda e, labs=wraps: [
                    lab.configure(wraplength=max(80, e.width - 4)) for lab in labs])
                self.errors[f["key"]] = err
                self.editors[f["key"]] = ed
                if f["key"] in values:
                    ed.set(values[f["key"]])
                ed.on_change = self._changed
                if preview or readonly:
                    ed.set_enabled(False)
                self._cells.append((cell, "wide" if wide else "field"))
        if not self.editors:
            ttk.Label(self, text="No fields yet.", style=f"{pre}Help.TLabel").grid(
                row=0, column=0, sticky="w")
        self._layout(columns)
        if columns > 1:
            self.bind("<Configure>", self._resized)
        self._initial = self.raw()
        self._loading = False

    def _resized(self, event):
        want = self._max_columns if event.width >= fs(430) else 1
        if want != self._columns:
            self._layout(want)

    def _layout(self, columns: int):
        """Place the cells in a grid of the given number of columns."""
        self._columns = columns
        for c in range(self._max_columns):
            self.columnconfigure(c, weight=1 if c < columns else 0,
                                 uniform="formcol" if c < columns else "")
        row = col = 0
        for widget, kind in self._cells:
            if kind in ("section", "break"):
                if col:
                    row, col = row + 1, 0
                if kind == "section":
                    widget.grid(row=row, column=0, columnspan=columns, sticky="ew", pady=(14, 2))
                    row += 1
                else:
                    widget.grid_forget()
                continue
            wide = kind == "wide" or columns == 1
            if wide and col:
                row, col = row + 1, 0
            last = wide or col == columns - 1
            widget.grid(row=row, column=col, columnspan=columns if wide else 1, sticky="new",
                        padx=(0, 0 if last else 16), pady=(8, 0))
            if wide:
                row += 1
            else:
                col += 1
                if col >= columns:
                    row, col = row + 1, 0

    def _changed(self, editor: Editor):
        if self._loading:
            return
        key = editor.field["key"]
        if self.errors[key].winfo_manager():
            self.errors[key].pack_forget()
            editor.set_bad(False)
        if self.on_change:
            self.on_change(key)

    def raw(self) -> dict:
        return {k: ed.get() for k, ed in self.editors.items()}

    def is_dirty(self) -> bool:
        return self.raw() != self._initial

    def mark_clean(self):
        self._initial = self.raw()

    def set_values(self, values: dict):
        self._loading = True
        for k, ed in self.editors.items():
            ed.set(values.get(k))
        self._loading = False
        self.clear_errors()
        self.mark_clean()

    def set_value(self, key: str, value):
        if key in self.editors:
            self.editors[key].set(value)

    def clear_errors(self):
        for k, lab in self.errors.items():
            lab.pack_forget()
            self.editors[k].set_bad(False)

    def show_errors(self, errors: dict) -> None:
        """Mark each failing field and put the cursor in the first one."""
        self.clear_errors()
        first = None
        for f in bpm.active_fields(self.type_def):
            k = f["key"]
            if k in errors and k in self.editors:
                self.errors[k].configure(text=errors[k])
                self.errors[k].pack(anchor="w", pady=(2, 0))
                self.editors[k].set_bad(True)
                first = first or self.editors[k]
        if first is not None:
            first.focus()

    def focus_first(self):
        for ed in self.editors.values():
            ed.focus()
            break


def error_summary(type_def: dict, errors: dict) -> str:
    lines = []
    for f in bpm.active_fields(type_def):
        if f["key"] in errors:
            lines.append(f"• {f['name']}: {errors[f['key']]}")
    return "\n".join(lines)
