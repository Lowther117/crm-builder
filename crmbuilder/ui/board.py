"""The board: one list shown as cards in columns, one column per option of a
choice field (the stages of a deal, the status of a case...). Drag a card to
another column to move it on.

    BoardView(parent, app, type_def, records, on_open, on_changed)

The list page builds a new BoardView every time the records change, so this
module also remembers where you were (scroll positions, the selected card,
how many cards each column was showing) and puts you back there.

Cards are drawn on one Canvas per column rather than built from widgets: a
few thousand records stay quick that way. Each column shows its first PAGE
cards and offers "Show more" for the rest.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import sys
import tkinter as tk
import weakref
from tkinter import font as tkfont
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from .. import validate
from . import styles, widgets
from .styles import fs

PAGE = 150            # cards drawn per column before "Show more"
DRAG_THRESHOLD = 6    # pixels the pointer must travel before a drag starts
GAP = 10              # space between columns
RING = 2              # the ring round a column that lights up as a drop target
BAR_W = 7             # the slim scroll indicator inside a column
CARD_GAP = 8
LABELLED = ("date", "number", "percent", "yesno")   # shown as "Field: value"

# app -> {(file, list key, field key): what the board looked like} so a rebuilt
# board can put you back where you were
_memory: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_refocus = {"on": False}     # True while the list page rebuilds us after a move
_fonts: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def font_for(app, size: int, bold: bool = False) -> tkfont.Font:
    """A font object for measuring and drawing text, made once per app.

    Tk only keeps a font loaded while some widget is using it, and loading one
    can take tens of milliseconds, so each font is pinned by a label that is
    never shown. The board is rebuilt after every move; without the pin its
    fonts would be reloaded each time."""
    cache = _fonts.setdefault(app, {})
    key = (app.c["ui"], size, bold)
    got = cache.get(key)
    if got is None:
        font = tkfont.Font(root=app.root, family=app.c["ui"], size=fs(size),
                           weight="bold" if bold else "normal")
        got = cache[key] = [font, None]
    try:
        alive = got[1] is not None and got[1].winfo_exists()
    except tk.TclError:
        alive = False
    if not alive:
        got[1] = tk.Label(app.root, font=got[0])       # never packed: it only pins the font
    return got[0]


def money_text(amount: float) -> str:
    """£10,900 for whole pounds, £10,900.50 otherwise."""
    sign = "-£" if amount < 0 else "£"
    amount = abs(amount)
    if abs(amount - round(amount)) < 0.005:
        return f"{sign}{int(round(amount)):,}"
    return f"{sign}{amount:,.2f}"


def fit(font: tkfont.Font, text: str, width: int) -> str:
    """text, cut short with … so that it is no wider than width pixels."""
    if width <= 0:
        return ""
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo > 0 else "…"


def wrap(font: tkfont.Font, text: str, width: int, max_lines: int = 2) -> list[str]:
    """Word-wrap text to at most max_lines lines; the last ends in … if cut."""
    text = " ".join(str(text).split())
    if not text:
        return [""]
    if font.measure(text) <= width:
        return [text]
    lines: list[str] = []
    words = text.split(" ")
    i = 0
    while i < len(words) and len(lines) < max_lines:
        if len(lines) == max_lines - 1:
            lines.append(fit(font, " ".join(words[i:]), width))
            i = len(words)
            break
        line = words[i]
        i += 1
        if font.measure(line) > width:       # one very long word: break inside it
            cut = len(line)
            while cut > 1 and font.measure(line[:cut]) > width:
                cut -= 1
            words.insert(i, line[cut:])
            line = line[:cut]
        else:
            while i < len(words) and font.measure(line + " " + words[i]) <= width:
                line += " " + words[i]
                i += 1
        lines.append(line)
    return lines


class MiniBar(tk.Canvas):
    """A slim scroll indicator you can also drag. Quieter than a full
    scrollbar, so a board of several columns does not look like a wall of them."""

    def __init__(self, parent, c: dict, bg: str, vertical: bool, moveto):
        size = {"width": BAR_W} if vertical else {"height": BAR_W}
        super().__init__(parent, bg=bg, highlightthickness=0, borderwidth=0, **size)
        self.vertical = vertical
        self.moveto = moveto
        self.colour = c["field_border"]
        self.view = (0.0, 1.0)
        self.bind("<ButtonPress-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Configure>", lambda _e: self.set(*self.view))

    def needed(self) -> bool:
        return self.view[0] > 0.0 or self.view[1] < 1.0

    def set(self, lo, hi):
        self.view = lo, hi = float(lo), float(hi)
        self.delete("all")
        length = self.winfo_height() if self.vertical else self.winfo_width()
        if not self.needed() or length < 30:
            return
        a, b = lo * length, hi * length
        if b - a < 24:
            a = max(0.0, min(length - 24.0, a - (24 - (b - a)) * lo))
            b = a + 24
        if self.vertical:
            self.create_rectangle(1, a, BAR_W - 2, b, fill=self.colour, outline="")
        else:
            self.create_rectangle(a, 1, b, BAR_W - 2, fill=self.colour, outline="")

    def _drag(self, event):
        length = max(1, self.winfo_height() if self.vertical else self.winfo_width())
        at = event.y if self.vertical else event.x
        lo, hi = self.view
        self.moveto(at / float(length) - (hi - lo) / 2)


class Column:
    """One column of the board: a heading, and a canvas of cards that scrolls."""

    def __init__(self, board: "BoardView", value, title: str, kind: str, records: list[dict]):
        self.board = board
        self.value = value            # stored value of the board field (None = no value)
        self.title = title
        self.kind = kind              # 'blank' | 'option' | 'gone' (no longer an option)
        self.records = records
        self.shown = min(PAGE, len(records))
        self.can_drop = kind == "option" or (kind == "blank" and not board.required)
        self.tops: list[int] = []
        self.bottoms: list[int] = []
        self.ids: list[int] = []
        self.more_box = None
        self.content_h = 1
        self.is_target = False
        self._hover = None
        self.frame = self.head = self.canvas = self.bar = None

    # ------------------------------------------------------------ widgets
    def build(self, parent):
        b, c = self.board, self.board.c
        lane = b.lane
        self.frame = tk.Frame(parent, bg=c["bg"], highlightthickness=RING,
                              highlightbackground=c["bg"], highlightcolor=c["bg"])
        self.inner = tk.Frame(self.frame, bg=lane, highlightthickness=1,
                              highlightbackground=c["border"], highlightcolor=c["border"])
        self.inner.pack(fill="both", expand=True)
        self.head = tk.Canvas(self.inner, bg=lane, highlightthickness=0, borderwidth=0,
                              height=b.head_h, width=b.canvas_w + BAR_W)
        self.head.pack(fill="x")
        self.bar = MiniBar(self.inner, c, lane, True, lambda f: self.canvas.yview_moveto(f))
        self.bar.pack(side="right", fill="y", pady=(0, 4))
        self.canvas = tk.Canvas(self.inner, bg=lane, highlightthickness=0, borderwidth=0,
                                width=b.canvas_w, height=10, takefocus=1,
                                yscrollincrement=fs(10) * 2, yscrollcommand=self._on_yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        for w in (self.head, self.canvas, self.bar):
            w.wheel_owner = self
            b.bind_sideways(w, self)
        cv = self.canvas
        cv.bind("<ButtonPress-1>", lambda e: b.press(self, e))
        cv.bind("<B1-Motion>", b.motion)
        cv.bind("<ButtonRelease-1>", b.release)
        cv.bind("<Double-Button-1>", lambda e: b.double(self, e))
        cv.bind("<Button-3>", lambda e: b.context(self, e))
        if widgets.IS_MAC:
            cv.bind("<Button-2>", lambda e: b.context(self, e))
            cv.bind("<Control-Button-1>", lambda e: b.context(self, e))
        cv.bind("<Motion>", self._motion)
        cv.bind("<Leave>", lambda _e: self._set_hover(None))
        cv.bind("<FocusIn>", lambda _e: b.focus_in(self))
        cv.bind("<FocusOut>", lambda _e: b.focus_out(self))
        cv.bind("<Return>", lambda _e: b.open_selected() or "break")
        cv.bind("<KP_Enter>", lambda _e: b.open_selected() or "break")
        cv.bind("<Left>", lambda _e: b.key_move(-1))
        cv.bind("<Right>", lambda _e: b.key_move(1))
        cv.bind("<Up>", lambda _e: b.key_step(-1))
        cv.bind("<Down>", lambda _e: b.key_step(1))
        cv.bind("<Home>", lambda _e: b.key_step(-10 ** 6))
        cv.bind("<End>", lambda _e: b.key_step(10 ** 6))
        cv.bind("<Escape>", lambda _e: b.cancel_drag())
        cv.bind("<Shift-F10>", lambda _e: b.key_menu())
        for name in ("<Menu>", "<App>"):
            try:
                cv.bind(name, lambda _e: b.key_menu())
            except tk.TclError:
                pass
        self.draw_head()
        self.draw()

    def draw_head(self):
        b, c, cv = self.board, self.board.c, self.head
        cv.delete("all")
        w = b.canvas_w + BAR_W
        mid = b.head_h // 2 + 1
        total = ""
        if b.money is not None and self.records:
            key = b.money["key"]
            total = money_text(sum(_number(r["data"].get(key)) for r in self.records))
        right = w - 12
        if total:
            cv.create_text(right, mid, text=total, anchor="e", font=b.f_small, fill=c["dim"])
            right -= b.f_small.measure(total) + 10
        count = f"{len(self.records):,}"
        count_w = b.f_small.measure(count)
        name = fit(b.f_bold, self.title, right - 12 - count_w - 8)
        cv.create_text(12, mid, text=name, anchor="w", font=b.f_bold,
                       fill=c["text"] if self.kind == "option" else c["dim"])
        cv.create_text(12 + b.f_bold.measure(name) + 8, mid, text=count, anchor="w",
                       font=b.f_small, fill=c["dim"])

    def draw(self):
        """(Re)draw the cards this column is showing."""
        b, c, cv = self.board, self.board.c, self.canvas
        cv.delete("all")
        self.tops, self.bottoms, self.ids = [], [], []
        self.more_box = None
        self._hover = None
        x0, x1 = 8, b.canvas_w - 3
        y = 2
        for r in self.records[:self.shown]:
            info = b.card_info(r)
            h = paint_card(cv, x0, y, x1, info, b, ("card", f"c{r['id']}"))
            self.tops.append(y)
            self.bottoms.append(y + h)
            self.ids.append(r["id"])
            y += h + CARD_GAP
        hidden = len(self.records) - self.shown
        if hidden > 0:
            n = min(PAGE, hidden)
            bh = b.f_body.metrics("linespace") + 14
            cv.create_rectangle(x0, y, x1, y + bh, fill=b.lane, outline=c["border"], tags=("more",))
            cv.create_text((x0 + x1) // 2, y + bh // 2, text=f"Show {n:,} more",
                           font=b.f_body, fill=styles.readable(c["accent"], b.lane, c),
                           tags=("more",))
            self.more_box = (y, y + bh)
            y += bh + 6
            cv.create_text((x0 + x1) // 2, y, anchor="n", font=b.f_small, fill=c["dim"],
                           text=f"Showing {self.shown:,} of {len(self.records):,}")
            y += b.f_small.metrics("linespace") + CARD_GAP
        if not self.records:
            cv.create_text((x0 + x1) // 2, 14, anchor="n", text="Nothing here", font=b.f_small,
                           fill=c["dim"], tags=("empty",))
        self.content_h = max(1, y + 2)
        cv.configure(scrollregion=(0, 0, b.canvas_w, self.content_h))
        if b.selected in self.ids:
            self.mark(b.selected, True)

    # ---------------------------------------------------------- scrolling
    def scroll(self, units: int):
        """Mouse wheel over this column (see widgets.install_wheel)."""
        if self.content_h > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def offset(self) -> int:
        return int(self.canvas.canvasy(0))

    def set_offset(self, pixels: int):
        self.canvas.yview_moveto(max(0, pixels) / float(self.content_h))

    def _on_yview(self, lo, hi):
        self.bar.set(lo, hi)
        self.board.remember()

    def see(self, rid: int):
        """Scroll so that the card is in view (showing more cards if needed)."""
        if rid not in self.ids:
            idx = next((i for i, r in enumerate(self.records) if r["id"] == rid), None)
            if idx is None:
                return
            self.shown = min(len(self.records), (idx // PAGE + 1) * PAGE)
            self.draw()
        i = self.ids.index(rid)
        top, h = self.offset(), self.canvas.winfo_height()
        if h < 20:
            return
        if self.tops[i] - 6 < top:
            self.set_offset(self.tops[i] - 6)
        elif self.bottoms[i] + 6 > top + h:
            self.set_offset(self.bottoms[i] + 6 - h + fs(10) * 2)

    # ------------------------------------------------------------- cards
    def card_at(self, y: int):
        """Record id of the card at canvas-y, or None."""
        i = bisect.bisect_right(self.tops, y) - 1
        if i >= 0 and y <= self.bottoms[i]:
            return self.ids[i]
        return None

    def card_box(self, rid: int):
        """(x0, y0, x1, y1) of a drawn card in canvas coordinates, or None."""
        if rid not in self.ids:
            return None
        i = self.ids.index(rid)
        return (8, self.tops[i], self.board.canvas_w - 3, self.bottoms[i])

    def mark(self, rid: int, selected: bool = False, hover: bool = False):
        c = self.board.c
        tag = f"box{rid}"
        if selected:
            self.canvas.itemconfigure(tag, outline=c["accent"], width=2)
        elif hover:
            self.canvas.itemconfigure(tag, outline=c["dim"], width=1)
        else:
            self.canvas.itemconfigure(tag, outline=c["border"], width=1)

    def _set_hover(self, rid):
        if rid == self._hover:
            return
        b = self.board
        if self._hover is not None and self._hover != b.selected:
            self.mark(self._hover)
        self._hover = rid
        if rid is not None and rid != b.selected:
            self.mark(rid, hover=True)

    def _motion(self, event):
        y = int(self.canvas.canvasy(event.y))
        rid = self.card_at(y)
        over_more = self.more_box is not None and self.more_box[0] <= y <= self.more_box[1]
        self._set_hover(rid)
        cursor = "hand2" if (rid is not None or over_more) else ""
        if str(self.canvas.cget("cursor")) != cursor and self.board._drag is None:
            self.canvas.configure(cursor=cursor)

    def show_more(self):
        self.shown = min(len(self.records), self.shown + PAGE)
        self.draw()
        self.board.remember()

    def set_target(self, on: bool):
        if on == self.is_target:
            return
        self.is_target = on
        c = self.board.c
        ring = c["accent"] if on else c["bg"]
        lane = c["sel"] if on else self.board.lane
        self.frame.configure(highlightbackground=ring, highlightcolor=ring)
        edge = c["accent"] if on else c["border"]
        self.inner.configure(bg=lane, highlightbackground=edge, highlightcolor=edge)
        for w in (self.head, self.canvas, self.bar):
            w.configure(bg=lane)
        self.canvas.itemconfigure("empty", text="Drop here" if on else "Nothing here")


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def paint_card(cv: tk.Canvas, x0: int, y0: int, x1: int, info: dict, board: "BoardView",
               tags: tuple) -> int:
    """Draw one card with its top-left corner at (x0, y0). Returns its height."""
    c = board.c
    rid = info["id"]
    title_h = board.lh_bold * len(info["title"])
    lines = info["lines"]
    h = 9 + title_h + ((4 + board.lh_small * len(lines)) if lines else 0) + 9
    cv.create_rectangle(x0, y0, x1, y0 + h, fill=c["panel"], outline=c["border"], width=1,
                        tags=tags + (f"box{rid}",))
    cv.create_rectangle(x0 + 1, y0 + 1, x0 + 4, y0 + h, fill=board.colour, outline="", tags=tags)
    tx = x0 + 13
    cv.create_text(tx, y0 + 9, anchor="nw", text="\n".join(info["title"]), font=board.f_bold,
                   fill=c["text"], tags=tags)
    y = y0 + 9 + title_h + 4
    for text, tone in lines:
        cv.create_text(tx, y, anchor="nw", text=text, font=board.f_small,
                       fill=c[tone], tags=tags)
        y += board.lh_small
    return h


class BoardView(ttk.Frame):
    """Cards in columns. See the module docstring."""

    def __init__(self, parent, app, type_def: dict, records: list[dict], on_open, on_changed):
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.c = c = app.c
        self.t = type_def
        self.records = records
        self.on_open = on_open
        self.on_changed = on_changed
        self.field = bpm.board_field(type_def)
        self.money = bpm.money_field(type_def)
        self.can_edit = bool(self.db.can_edit)
        self.colour = type_def.get("color") or c["accent"]
        self.lane = c["sidebar"]
        self.selected = None
        self.columns: list[Column] = []
        self._press = None
        self._drag = None
        self._auto = None
        self._relayout = None
        self._has_focus = False
        self._swipe = [0.0, 0.0]
        self._cards: dict[int, dict] = {}
        self._restoring = True
        if self.field is None:
            ttk.Label(self, text="This list has no field to make columns from. Choose one in "
                                 "Change the design.", style="Help.TLabel").pack(pady=30)
            return
        self.key = self.field["key"]
        self.required = bool(self.field.get("required"))
        self.f_bold = font_for(app, 10, True)
        self.f_body = font_for(app, 10)
        self.f_small = font_for(app, 9)
        self.lh_bold = self.f_bold.metrics("linespace")
        self.lh_small = self.f_small.metrics("linespace") + 2
        self.head_h = self.lh_bold + 20
        title_keys = set(type_def.get("title") or [])
        self.line_fields = [f for f in bpm.list_fields(type_def)
                            if f["key"] not in title_keys and f["key"] != self.key]
        self._today = _dt.date.today().isoformat()

        mem_key = (getattr(self.db, "path", ""), type_def["key"], self.key)
        store = _memory.setdefault(app, {})
        old = store.get(mem_key) or {}
        self._mem_store, self._mem_key = store, mem_key

        self.hc = tk.Canvas(self, bg=c["bg"], highlightthickness=0, borderwidth=0,
                            xscrollincrement=fs(10) * 2)
        self.hbar = MiniBar(self, c, c["bg"], False, lambda f: self.hc.xview_moveto(f))
        self.hc.configure(xscrollcommand=self._on_xview)
        self.hc.grid(row=0, column=0, sticky="nsew")
        self.hbar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.hbar.wheel_owner = self
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.holder = tk.Frame(self.hc, bg=c["bg"])
        self._win = self.hc.create_window(0, 0, window=self.holder, anchor="nw")
        self.hc.wheel_owner = self
        self.bind_sideways(self.hc)
        self.hc.bind("<Configure>", self._resized)
        self.bind("<Destroy>", self._destroyed)

        self._group()
        avail = 0
        try:
            avail = parent.winfo_width()
            if avail < 200:
                avail = app.content.winfo_width() - 48
        except (tk.TclError, AttributeError):
            pass
        self.col_w = self._column_width(avail)
        self.canvas_w = self.col_w - 2 * (RING + 1) - BAR_W

        # put back what the last board of this list was showing
        shown = old.get("shown") or {}
        sel = old.get("selected")
        for col in self.columns:
            n = shown.get(col.value)
            if n:
                col.shown = min(len(col.records), max(PAGE, int(n)))
        where = self.where(sel) if sel is not None else None
        if where is not None:
            self.selected = sel
            col, idx = where
            if idx >= col.shown:
                col.shown = min(len(col.records), (idx // PAGE + 1) * PAGE)
        self._build_columns()
        self._restore = {"x": old.get("x", 0.0), "y": dict(old.get("y") or {}),
                         "focus": bool(_refocus["on"])}
        self.after_idle(self._after_layout)

    # ----------------------------------------------------------- building
    def _group(self):
        """Sort the records into columns."""
        f = self.field
        options = validate.options_of(f)
        groups: dict = {o: [] for o in options}
        blank: list[dict] = []
        extra: dict = {}
        key = self.key
        for r in self.records:
            v = r["data"].get(key)
            if v is None or v == "":
                blank.append(r)
            elif v in groups:
                groups[v].append(r)
            else:
                extra.setdefault(str(v), []).append(r)
        cols = []
        if blank:
            cols.append(Column(self, None, f"No {f['name'].lower()}", "blank", blank))
        for o in options:
            cols.append(Column(self, o, o, "option", groups[o]))
        for v, rows in extra.items():
            cols.append(Column(self, v, v, "gone", rows))
        self.columns = cols
        self._where = {}
        for col in cols:
            for i, r in enumerate(col.records):
                self._where[r["id"]] = (col, i)

    def where(self, rid):
        """(column, position in it) of a record, or None."""
        return self._where.get(rid)

    def _column_width(self, avail: int) -> int:
        """Columns are a fixed comfortable width; when they all fit with room
        to spare they widen a little to use it."""
        base, widest = fs(10) * 25, fs(10) * 34
        n = max(1, len(self.columns))
        if avail >= n * base + (n - 1) * GAP:
            return int(min(widest, (avail - (n - 1) * GAP) // n))
        return base

    def _build_columns(self):
        for w in self.holder.winfo_children():
            w.destroy()
        for i, col in enumerate(self.columns):
            col.build(self.holder)
            col.frame.pack(side="left", fill="y", padx=(0, GAP if i < len(self.columns) - 1 else 0))
        total = len(self.columns) * self.col_w + (len(self.columns) - 1) * GAP
        self._total_w = total
        self.hc.itemconfigure(self._win, width=total)
        self.hc.configure(scrollregion=(0, 0, total, max(1, self.hc.winfo_height())))

    def _resized(self, event):
        self.hc.itemconfigure(self._win, height=event.height)
        self.hc.configure(scrollregion=(0, 0, self._total_w, event.height))
        if self._column_width(event.width) != self.col_w and self._relayout is None:
            self._relayout = self.after(60, self._do_relayout)

    def _destroyed(self, event):
        """The list page is replacing this board (perhaps in mid-drag, when
        someone else's change arrives): leave nothing behind."""
        if event.widget is not self:
            return
        for name in ("_auto", "_relayout"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
            setattr(self, name, None)
        d, self._drag = self._drag, None
        if d is not None:
            try:
                d["ghost"].destroy()
            except tk.TclError:
                pass

    def _do_relayout(self):
        """The window changed size enough for the columns to change width."""
        self._relayout = None
        if self._drag is not None:
            return
        width = self._column_width(self.hc.winfo_width())
        if width == self.col_w:
            return
        offsets = {col.value: col.offset() for col in self.columns}
        focused = self._has_focus
        self.col_w = width
        self.canvas_w = width - 2 * (RING + 1) - BAR_W
        self._cards = {}
        self._build_columns()
        self.update_idletasks()
        for col in self.columns:
            col.set_offset(offsets.get(col.value, 0))
        if focused and self.selected is not None:
            self.where(self.selected)[0].canvas.focus_set()

    def _after_layout(self):
        """Once the widgets have their real size: put the scroll positions back."""
        try:
            self.update_idletasks()
            r = self._restore
            for col in self.columns:
                y = r["y"].get(col.value)
                if y:
                    col.set_offset(int(y))
            if r["x"]:
                self.hc.xview_moveto(r["x"])
            if self.selected is not None:
                col = self.where(self.selected)[0]
                if r["focus"]:
                    col.see(self.selected)
                    self.see_column(col)
                    col.canvas.focus_set()
        except tk.TclError:
            return
        self._restoring = False
        self.remember()

    def remember(self):
        """Note the current view so the next board for this list can restore it."""
        if self._restoring or not self.columns:
            return
        try:
            self._mem_store[self._mem_key] = {
                "x": self.hc.xview()[0],
                "y": {col.value: col.offset() for col in self.columns},
                "shown": {col.value: col.shown for col in self.columns},
                "selected": self.selected,
            }
        except tk.TclError:
            pass

    # ------------------------------------------------------ card contents
    def card_info(self, r: dict) -> dict:
        """What a card says: its title (wrapped) and up to three short lines."""
        got = self._cards.get(r["id"])
        if got is not None:
            return got
        width = self.canvas_w - 3 - 8 - 13 - 9
        lines = []
        lookup = self.db.lookup
        for f in self.line_fields:
            value = r["data"].get(f["key"])
            if value is None or value == "" or value == [] or value is False:
                continue
            text = " ".join(validate.display(f, value, lookup).split())
            if not text:
                continue
            tone = "dim"
            kind = f["kind"]
            if kind in LABELLED:
                text = f"{f['name']}: {text}"
                if kind == "date" and f.get("remind") and str(value) < self._today:
                    tone = "bad"
            elif kind == "money":
                tone = "text"
            lines.append((fit(self.f_small, text, width), tone))
            if len(lines) == 3:
                break
        info = {"id": r["id"], "title": wrap(self.f_bold, r.get("title") or r.get("ref") or "", width),
                "lines": lines}
        self._cards[r["id"]] = info
        return info

    # ---------------------------------------------------------- scrolling
    def scroll(self, units: int):
        """Sideways scrolling (wheel over the gaps, Shift+wheel anywhere)."""
        if self._total_w > self.hc.winfo_width():
            self.hc.xview_scroll(units, "units")

    def _on_xview(self, lo, hi):
        self.hbar.set(lo, hi)
        self.remember()

    def bind_sideways(self, widget, column=None):
        """Shift+wheel (and a sideways wheel or two-finger swipe) scrolls the
        board along. The plain wheel is handled app-wide through wheel_owner."""
        def go(event, units=None):
            if units is None:
                units = -1 if event.delta > 0 else 1
            self.scroll(units * 3)
            return "break"
        widget.bind("<Shift-MouseWheel>", go)
        extra = []
        if sys.platform.startswith("linux"):
            extra = [("<Shift-Button-4>", lambda e: go(e, -1)), ("<Shift-Button-5>", lambda e: go(e, 1)),
                     ("<Button-6>", lambda e: go(e, -1)), ("<Button-7>", lambda e: go(e, 1))]
        # Tk 9 reports trackpad scrolling as its own event, in pixels, both ways at once
        extra.append(("<TouchpadScroll>", lambda e: self._touchpad(e, column)))
        for name, fn in extra:
            try:
                widget.bind(name, fn)
            except tk.TclError:          # older Tk: no such button / event
                pass

    def _touchpad(self, event, column):
        try:
            d = int(event.delta)
        except (TypeError, ValueError):
            return "break"
        low = d & 0xFFFF
        dx, dy = d >> 16, (low if low < 0x8000 else low - 0x10000)
        step = float(fs(10) * 2)
        acc = self._swipe
        acc[0] -= dx
        acc[1] -= dy
        nx, ny = int(acc[0] / step), int(acc[1] / step)
        if nx:
            acc[0] -= nx * step
            self.scroll(nx)
        if ny:
            acc[1] -= ny * step
            if column is not None:
                column.scroll(ny)
        return "break"

    def see_column(self, col: Column):
        """Scroll sideways so the whole column is in view."""
        view = self.hc.winfo_width()
        if view < 50 or self._total_w <= view:
            return
        x0 = self.columns.index(col) * (self.col_w + GAP)
        left = self.hc.canvasx(0)
        if x0 < left:
            self.hc.xview_moveto(x0 / float(self._total_w))
        elif x0 + self.col_w > left + view:
            step = fs(10) * 2
            self.hc.xview_moveto((x0 + self.col_w - view + step) / float(self._total_w))

    # ---------------------------------------------------------- selection
    def select(self, rid, see: bool = True):
        if rid is not None and rid not in self._where:
            rid = None
        if rid != self.selected:
            if self.selected is not None:
                self.where(self.selected)[0].mark(self.selected)
            self.selected = rid
        if rid is not None:
            col = self.where(rid)[0]
            if see:
                col.see(rid)
                self.see_column(col)
            col.mark(rid, selected=True)
        self.remember()

    def focus_in(self, col: Column):
        self._has_focus = True
        if self._press is not None or self._drag is not None:
            return
        where = self.where(self.selected) if self.selected is not None else None
        if (where is None or where[0] is not col) and col.records:
            self.select(col.records[0]["id"])    # arrived with Tab: start at the top card

    def focus_out(self, col: Column):
        self._has_focus = False
        if self._drag is not None and self._drag["col"] is col:
            self.cancel_drag()         # the window lost the keyboard in mid-drag

    def open_selected(self):
        if self.selected is not None:
            self.on_open(self.selected)
            return True
        return False

    def key_step(self, step: int):
        """Up / Down: move the selection within its column."""
        if self.selected is None:
            for col in self.columns:
                if col.records:
                    self.select(col.records[0]["id"])
                    break
            return "break"
        col, i = self.where(self.selected)
        j = max(0, min(len(col.records) - 1, i + step))
        if j != i:
            self.select(col.records[j]["id"])
        return "break"

    def key_move(self, direction: int):
        """Left / Right: move the selected card one column along."""
        if self.selected is None or not self.can_edit:
            return "break"
        col = self.where(self.selected)[0]
        i = self.columns.index(col) + direction
        while 0 <= i < len(self.columns) and not self.columns[i].can_drop:
            i += direction
        if 0 <= i < len(self.columns):
            self.move(self.selected, self.columns[i].value)
        return "break"

    def key_menu(self):
        if self.selected is None:
            return "break"
        col = self.where(self.selected)[0]
        box = col.card_box(self.selected)
        if box is not None:
            x = col.canvas.winfo_rootx() + box[0] + 30
            y = col.canvas.winfo_rooty() + box[1] - col.offset() + 20
            self._popup(self.context_menu(self.selected), x, y)
        return "break"

    # -------------------------------------------------------- mouse: menu
    def context_menu(self, rid: int) -> tk.Menu:
        """The right-click menu for a card (also the way to move a card
        without dragging)."""
        m = styles.menu(self, self.c)
        m.add_command(label="Open", command=lambda: self.on_open(rid))
        if self.can_edit:
            here = self.where(rid)[0]
            sub = styles.menu(m, self.c)
            for col in self.columns:
                if not col.can_drop:
                    continue
                sub.add_command(label=col.title, state="disabled" if col is here else "normal",
                                command=lambda v=col.value: self.move(rid, v))
            m.add_cascade(label="Move to", menu=sub)
            m.move_menu = sub
        return m

    def _popup(self, menu, x, y):
        if getattr(self.app, "testing", False):
            self.last_menu = menu
            return
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def context(self, col: Column, event):
        rid = col.card_at(int(col.canvas.canvasy(event.y)))
        if rid is None:
            return
        self.select(rid, see=False)
        col.canvas.focus_set()
        self._popup(self.context_menu(rid), event.x_root, event.y_root)

    # ------------------------------------------------- mouse: click, drag
    def press(self, col: Column, event):
        self.cancel_drag()
        y = int(col.canvas.canvasy(event.y))
        if col.more_box is not None and col.more_box[0] <= y <= col.more_box[1]:
            self._press = None
            col.show_more()
            return
        rid = col.card_at(y)
        self._press = {"col": col, "rid": rid, "x": event.x_root, "y": event.y_root,
                       "dx": event.x - 8, "dy": y - (col.card_box(rid)[1] if rid is not None else 0)}
        self.select(rid, see=False)
        col.canvas.focus_set()
        if rid is None or not self.can_edit:
            self._press = None

    def double(self, col: Column, event):
        self._press = None
        rid = col.card_at(int(col.canvas.canvasy(event.y)))
        if rid is not None:
            self.select(rid, see=False)
            self.on_open(rid)

    def motion(self, event):
        p = self._press
        if p is None:
            return
        if self._drag is None:
            if (abs(event.x_root - p["x"]) < DRAG_THRESHOLD
                    and abs(event.y_root - p["y"]) < DRAG_THRESHOLD):
                return
            self._start_drag()
        self._drag_to(event.x_root, event.y_root)

    def _start_drag(self):
        p, c = self._press, self.c
        col, rid = p["col"], p["rid"]
        info = self.card_info(col.records[self.where(rid)[1]])
        top = self.winfo_toplevel()
        ghost = tk.Frame(top, bg=c["accent"], padx=2, pady=2)
        w = self.canvas_w - 11
        gc = tk.Canvas(ghost, bg=c["panel"], highlightthickness=0, borderwidth=0, width=w, height=10)
        h = paint_card(gc, 0, 0, w, info, self, ("ghost",))
        gc.itemconfigure(f"box{rid}", outline=c["panel"])
        gc.configure(height=h)
        gc.pack()
        # the card being dragged leaves a dashed gap where it was
        box = col.card_box(rid)
        col.canvas.itemconfigure(f"c{rid}", state="hidden")
        col.canvas.create_rectangle(box[0], box[1], box[2], box[3], outline=c["dim"], dash=(4, 3),
                                    fill="", tags=("gap",))
        col.canvas.configure(cursor="fleur")
        self._drag = {"ghost": ghost, "rid": rid, "col": col, "target": None,
                      "x": p["x"], "y": p["y"], "top": top, "dx": p["dx"], "dy": p["dy"]}
        self._auto = self.after(80, self._autoscroll)

    def _drag_to(self, x_root: int, y_root: int):
        d = self._drag
        d["x"], d["y"] = x_root, y_root
        top = d["top"]
        d["ghost"].place(x=x_root - top.winfo_rootx() - d["dx"] - 2,
                         y=y_root - top.winfo_rooty() - d["dy"] - 2)
        tk.Misc.tkraise(d["ghost"])
        self._set_target(self.column_at(x_root, y_root))

    def _set_target(self, col):
        d = self._drag
        if col is not None and (col is d["col"] or not col.can_drop):
            col = None
        if col is d["target"]:
            return
        if d["target"] is not None:
            d["target"].set_target(False)
        d["target"] = col
        if col is not None:
            col.set_target(True)

    def column_at(self, x_root: int, y_root: int):
        """The column under a screen position (only the part of the board in view)."""
        hc = self.hc
        hx, hy = hc.winfo_rootx(), hc.winfo_rooty()
        if not (hx <= x_root < hx + hc.winfo_width() and hy <= y_root < hy + hc.winfo_height()):
            return None
        x = hc.canvasx(x_root - hx)
        i = int((x + GAP / 2.0) // (self.col_w + GAP))
        if 0 <= i < len(self.columns):
            return self.columns[i]
        return None

    def _autoscroll(self):
        """While dragging near the left or right edge, slide the board along."""
        self._auto = None
        d = self._drag
        if d is None:
            return
        hc = self.hc
        hx, w = hc.winfo_rootx(), hc.winfo_width()
        edge = 44
        lo, hi = hc.xview()
        moved = False
        if d["x"] < hx + edge and lo > 0.0:
            hc.xview_scroll(-1, "units")
            moved = True
        elif d["x"] > hx + w - edge and hi < 1.0:
            hc.xview_scroll(1, "units")
            moved = True
        if moved:
            self.update_idletasks()
            self._set_target(self.column_at(d["x"], d["y"]))
        self._auto = self.after(40, self._autoscroll)

    def _end_drag(self):
        """Take the ghost away and put the card back. Returns the drag details."""
        d = self._drag
        self._drag = None
        if self._auto is not None:
            try:
                self.after_cancel(self._auto)
            except tk.TclError:
                pass
            self._auto = None
        if d is None:
            return None
        try:
            d["ghost"].destroy()
            if d["target"] is not None:
                d["target"].set_target(False)
            cv = d["col"].canvas
            cv.delete("gap")
            cv.itemconfigure(f"c{d['rid']}", state="normal")
            cv.configure(cursor="")
        except tk.TclError:
            pass
        return d

    def cancel_drag(self):
        """Esc, or the window lost the keyboard: forget the drag."""
        if self._drag is not None:
            self._end_drag()
            self._press = None
            return "break"
        return None

    def release(self, event):
        p, self._press = self._press, None
        if self._drag is None or p is None:
            return
        self._drag_to(event.x_root, event.y_root)
        over = self.column_at(event.x_root, event.y_root)
        d = self._end_drag()
        if over is None or over is d["col"]:
            return                                  # dropped outside, or where it started
        if not over.can_drop:
            name = self.field["name"]
            if over.kind == "blank":
                self.app.toast(f"{name} must be filled in, so a card cannot go back to "
                               f"“{over.title}”.", "bad")
            else:
                self.app.toast(f"“{over.title}” is no longer one of the options for {name}.", "bad")
            return
        self.move(d["rid"], over.value)

    # ------------------------------------------------------------- moving
    def move(self, rid: int, value) -> bool:
        """Set the board field of a record (None = no value), then ask the
        list page to reload. Every way of moving a card comes through here."""
        app = self.app
        if not self.can_edit:
            app.toast("Your account is read only, so cards cannot be moved.", "bad")
            return False
        where = self.where(rid)
        if where is None:
            return False
        col = where[0]
        target = next((x for x in self.columns if x.value == value), None)
        if value in (None, "") and self.required:
            app.toast(f"{self.field['name']} must be filled in.", "bad")
            return False
        if col.value == value:
            return False
        label = target.title if target is not None else (value or f"No {self.field['name'].lower()}")
        had_focus = self._has_focus or self._drag is not None or self._press is not None
        try:
            self.db.update_record(rid, {self.key: value}, base={self.key: col.value})
        except dbm.Conflict as exc:
            theirs = exc.theirs.get(self.key)
            app.toast(f"{exc.who} has just moved this to “{theirs or 'no value'}”. The board is now "
                      "up to date – move it again if you still want to.", "bad")
            self.on_changed()
            return False
        except dbm.DBError as exc:
            widgets.error(app, "The card could not be moved", str(exc))
            self.on_changed()
            return False
        self.selected = rid
        self._restoring = False
        self.remember()
        self._restoring = True         # the old board is about to go: stop taking notes
        _refocus["on"] = bool(had_focus)
        try:
            self.on_changed()
        finally:
            _refocus["on"] = False
        app.toast(f"Moved to {label}", "good")
        return True
