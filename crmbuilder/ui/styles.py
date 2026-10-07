"""App-specific ttk styles layered on top of the shared house theme.

theme.py is the house module, copied verbatim into every app, and is never
edited here. This adds the few extra styles CRM Builder needs (sidebar buttons,
cards, headings, error text) using the same palette.
"""
from __future__ import annotations

import sys
import tkinter as tk
import weakref
from tkinter import ttk

from .. import theme

# Tk on macOS measures points at 72 dpi, so a 10 pt font is a quarter smaller
# there than on Windows. Scale so text is the same physical size on both.
SCALE = 1.3 if sys.platform == "darwin" else 1.0


def fs(size: int) -> int:
    return int(round(size * SCALE))


def named_font(root: tk.Misc, name: str):
    """One of Tk's named fonts ("TkDefaultFont" ...) as a Font object.
    (tkinter.font.nametofont only takes a root from Python 3.10 on.)"""
    from tkinter import font as tkfont
    return tkfont.Font(root=root, name=name, exists=True)


_PINNED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def pinned_font(root: tk.Misc, family: str, size: int, bold: bool = False):
    """A Font object for measuring text, made once per window and kept loaded.

    Tk only keeps a font loaded while a widget is using it, and loading one
    takes ten milliseconds or more - every time it is measured with. So each
    font made here is pinned by a label that is never shown."""
    from tkinter import font as tkfont
    cache = _PINNED.setdefault(root, {})
    key = (family, size, bold)
    got = cache.get(key)
    if got is None:
        font = tkfont.Font(root=root, family=family, size=fs(size),
                           weight="bold" if bold else "normal")
        got = cache[key] = [font, None]
    try:
        alive = got[1] is not None and got[1].winfo_exists()
    except tk.TclError:
        alive = False
    if not alive:
        got[1] = tk.Label(root, font=got[0])        # never packed: it only pins the font
    return got[0]


def apply(root: tk.Misc, dark: bool) -> dict:
    c = dict(theme.apply(root, dark))
    ui = theme.ui_family(root)
    c["ui"] = ui
    c["mono"] = theme.mono_family(root)
    c["dark"] = dark
    s = ttk.Style(root)
    P, B, SB = c["panel"], c["bg"], c["sidebar"]
    # Entry boxes, menus and classic widgets take Tk's named fonts, not the
    # style font, so set those too or typed text comes out in a different face.
    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10),
                       ("TkHeadingFont", 9), ("TkTooltipFont", 9), ("TkCaptionFont", 10)):
        try:
            named_font(root, name).configure(family=ui, size=fs(size))
        except Exception:
            pass
    try:
        named_font(root, "TkFixedFont").configure(family=c["mono"], size=fs(10))
    except Exception:
        pass

    s.configure(".", font=(ui, fs(10)))
    # (light/dark colour: without them the list gets a near-white frame in dark mode)
    s.configure("Treeview", font=(ui, fs(10)), rowheight=fs(10) * 2 + 8,
                lightcolor=c["border"], darkcolor=c["border"])
    s.configure("Treeview.Heading", font=(ui, fs(9), "bold"), padding=(3, 5),
                bordercolor=c["border"], lightcolor=SB, darkcolor=SB)
    s.configure("Field.TLabel", font=(ui, fs(9), "bold"))
    s.configure("Title.TLabel", font=(ui, fs(15), "bold"))
    s.configure("Sub.TLabel", font=(ui, fs(10)))
    s.configure("Accent.TButton", font=(ui, fs(10), "bold"))
    s.configure("Hint.TButton", font=(ui, fs(9), "bold"))

    # headings and small print, on the page background and on panels
    for prefix, bg in (("", B), ("Panel", P), ("Side", SB), ("Hint", c["hint"])):
        s.configure(f"{prefix}H1.TLabel", background=bg, foreground=c["text"], font=(ui, fs(18), "bold"))
        s.configure(f"{prefix}H2.TLabel", background=bg, foreground=c["text"], font=(ui, fs(12), "bold"))
        s.configure(f"{prefix}H3.TLabel", background=bg, foreground=c["text"], font=(ui, fs(10), "bold"))
        s.configure(f"{prefix}Help.TLabel", background=bg, foreground=c["dim"], font=(ui, fs(9)))
        s.configure(f"{prefix}Error.TLabel", background=bg, foreground=c["bad"], font=(ui, fs(9)))
        s.configure(f"{prefix}Good.TLabel", background=bg, foreground=c["good"])
        s.configure(f"{prefix}Warn.TLabel", background=bg, foreground=c["warn"])
        s.configure(f"{prefix}Bad.TLabel", background=bg, foreground=c["bad"])
        s.configure(f"{prefix}Link.TLabel", background=bg, foreground=readable(c["accent"], bg, c),
                    font=(ui, fs(10)))
        s.configure(f"{prefix}Field.TLabel", background=bg, foreground=c["text"], font=(ui, fs(9), "bold"))
        s.configure(f"{prefix}Big.TLabel", background=bg, foreground=c["text"], font=(ui, fs(22), "bold"))
    s.configure("Side.TLabel", background=SB, foreground=c["text"])
    s.configure("SideDim.TLabel", background=SB, foreground=c["dim"], font=(ui, fs(8), "bold"))
    s.configure("PanelDim.TLabel", background=P, foreground=c["dim"])
    s.configure("Sel.TFrame", background=c["sel"])
    s.configure("Border.TFrame", background=c["border"])
    s.configure("Accent.TFrame", background=c["accent"])

    # sidebar navigation
    s.configure("Nav.TButton", background=SB, foreground=c["text"], anchor="w", relief="flat",
                padding=(12, 7), bordercolor=SB, lightcolor=SB, darkcolor=SB,
                focuscolor=SB, font=(ui, fs(10)))
    s.map("Nav.TButton", background=[("active", c["sel"]), ("pressed", c["sel"])],
          bordercolor=[("active", c["sel"]), ("focus", c["accent"])],
          lightcolor=[("active", c["sel"])], darkcolor=[("active", c["sel"])])
    s.configure("NavSel.TButton", background=c["sel"], foreground=c["text"], anchor="w",
                relief="flat", padding=(12, 7), bordercolor=c["sel"], lightcolor=c["sel"],
                darkcolor=c["sel"], focuscolor=c["sel"], font=(ui, fs(10), "bold"))
    s.map("NavSel.TButton", background=[("active", c["sel"])],
          bordercolor=[("active", c["sel"])], lightcolor=[("active", c["sel"])],
          darkcolor=[("active", c["sel"])])

    # quiet buttons
    for name, bg in (("Flat.TButton", B), ("PanelFlat.TButton", P)):
        s.configure(name, background=bg, foreground=readable(c["accent"], bg, c), relief="flat",
                    padding=(6, 3), bordercolor=bg, lightcolor=bg, darkcolor=bg,
                    focuscolor=c["accent"], font=(ui, fs(10)))
        s.map(name, background=[("active", c["sel"]), ("pressed", c["sel"])],
              bordercolor=[("active", c["sel"]), ("focus", c["accent"])],
              lightcolor=[("active", c["sel"])], darkcolor=[("active", c["sel"])],
              foreground=[("disabled", c["dim"])])
    s.configure("Small.TButton", padding=(8, 3), font=(ui, fs(9)))
    # The button beside a box on a form ("Pick", "Call", "Choose…"). The stock
    # button is never narrower than 11 characters, which is a lot there.
    s.configure("Beside.Small.TButton", width=-5)
    # a pair of buttons used as a switch (List | Board): same size on or off
    s.configure("Seg.TButton", padding=(14, 4), font=(ui, fs(9), "bold"))
    s.configure("SegOn.TButton", padding=(14, 4), font=(ui, fs(9), "bold"),
                background=c["accent"], foreground=c["accent_text"])
    s.map("SegOn.TButton", background=[("active", c["accent"]), ("pressed", c["accent"])])
    s.configure("Danger.TButton", foreground=c["bad"])
    s.map("Danger.TButton", bordercolor=[("active", c["bad"]), ("focus", c["bad"])],
          lightcolor=[("active", c["bad"])], darkcolor=[("active", c["bad"])])
    s.configure("Big.Accent.TButton", padding=(20, 10), font=(ui, fs(11), "bold"))
    s.configure("Big.TButton", padding=(20, 10), font=(ui, fs(11)))

    # a field that failed its check gets a red ring
    for name in ("Bad.TEntry", "Bad.Pick.TEntry"):
        s.configure(name, bordercolor=c["bad"], lightcolor=c["bad"], darkcolor=c["bad"])
        s.map(name, bordercolor=[("focus", c["bad"])], lightcolor=[("focus", c["bad"])],
              darkcolor=[("focus", c["bad"])])
    # The box of a link field is read-only (you choose, you do not type), but
    # it is still something to click, so it keeps the colour of a field.
    for name in ("Pick.TEntry", "Bad.Pick.TEntry"):
        s.map(name, fieldbackground=[("disabled", c["bg"]), ("readonly", c["field"])],
              foreground=[("disabled", c["dim"]), ("readonly", c["field_text"])])
    s.configure("Bad.TCombobox", bordercolor=c["bad"], lightcolor=c["bad"], darkcolor=c["bad"])

    # Tick boxes: the stock theme draws a cross for "on", which reads as "no".
    # Draw our own box with a proper tick instead. Radio buttons keep the
    # stock dot, recoloured.
    _tick_boxes(root, s, c)
    for name in ("TCheckbutton", "Panel.TCheckbutton", "Hint.TCheckbutton"):
        s.configure(name, focuscolor=c["accent"])
        s.map(name, foreground=[("disabled", c["dim"])])
    for name, back in (("TRadiobutton", B), ("Panel.TRadiobutton", P)):
        edge = readable(c["field_border"], back, c, 3.0)     # as for the tick boxes
        s.configure(name, indicatorbackground=c["field"], indicatorforeground=c["accent"],
                    upperbordercolor=edge, lowerbordercolor=edge,
                    focuscolor=c["accent"], indicatormargin=(1, 1, 6, 1),
                    indicatorsize=max(10, fs(10)))
        s.map(name, indicatorbackground=[("disabled", c["bg"]), ("pressed", c["sel"])],
              foreground=[("disabled", c["dim"])])
    # Slim scrollbars without arrow buttons.
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        name = f"{orient}.TScrollbar"
        try:
            s.layout(name, [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
                (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        except tk.TclError:
            pass
        thumb = c["field_border"]
        s.configure(name, background=thumb, troughcolor=B, bordercolor=B, lightcolor=thumb,
                    darkcolor=thumb, gripcount=0, gripsize=0, arrowsize=fs(11), relief="flat")
        s.map(name, background=[("active", c["dim"]), ("pressed", c["dim"])],
              lightcolor=[("active", c["dim"])], darkcolor=[("active", c["dim"])])
    s.configure("TNotebook", lightcolor=c["border"], darkcolor=c["border"], bordercolor=c["border"])
    s.configure("TNotebook.Tab", lightcolor=c["border"], bordercolor=c["border"],
                font=(ui, fs(10)), padding=(10, 6))
    s.map("TNotebook.Tab", lightcolor=[("selected", c["panel"])])
    s.configure("TRadiobutton", background=B, foreground=c["text"])
    s.map("TRadiobutton", background=[("active", B)])
    s.configure("Panel.TRadiobutton", background=P, foreground=c["text"])
    s.map("Panel.TRadiobutton", background=[("active", P)])
    s.map("Panel.TCheckbutton", background=[("active", P)])
    s.configure("Hint.TCheckbutton", background=c["hint"], foreground=c["text"])
    s.map("Hint.TCheckbutton", background=[("active", c["hint"])])
    s.configure("TLabelframe", background=B, bordercolor=c["border"])
    s.configure("TLabelframe.Label", background=B, foreground=c["dim"], font=(ui, fs(9), "bold"))
    s.configure("TProgressbar", background=c["accent"], troughcolor=c["panel"],
                bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])
    s.configure("TSpinbox", fieldbackground=c["field"], foreground=c["field_text"],
                bordercolor=c["field_border"], lightcolor=c["field_border"],
                darkcolor=c["field_border"], arrowcolor=c["accent"], padding=4)
    # "More" and "Filter": a button like the others, with a small arrow
    s.configure("TMenubutton", background=c["panel"], foreground=c["text"],
                bordercolor=c["field_border"], lightcolor=c["field_border"],
                darkcolor=c["field_border"], arrowcolor=c["dim"], padding=(11, 6),
                relief="solid")
    s.map("TMenubutton", background=[("active", c["sel"]), ("pressed", c["sel"])],
          bordercolor=[("active", c["accent"]), ("focus", c["accent"])],
          lightcolor=[("active", c["accent"])], darkcolor=[("active", c["accent"])],
          arrowcolor=[("active", c["text"])], foreground=[("disabled", c["dim"])])

    root.option_add("*Menu.background", c["panel"])
    root.option_add("*Menu.foreground", c["text"])
    root.option_add("*Menu.activeBackground", c["sel"])
    root.option_add("*Menu.activeForeground", c["text"])
    root.option_add("*Menu.borderWidth", 0)
    root.option_add("*Menu.font", (ui, fs(10)))
    root.option_add("*TCombobox*Listbox.font", (ui, fs(10)))
    return c


# window -> {(style prefix, dark): pictures}. Keeps the tick-box pictures alive:
# Tk drops a picture nothing in Python refers to, and what it was drawn on
# goes blank.
_IMAGES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _hex(colour: str) -> tuple:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _mix(a: str, b: str, t: float) -> str:
    x, y = _hex(a), _hex(b)
    return "#%02x%02x%02x" % tuple(int(round(x[i] + (y[i] - x[i]) * t)) for i in range(3))


def contrast(a: str, b: str) -> float:
    """How well two colours stand apart: 1 (not at all) to 21 (black on white)."""
    def lum(colour):
        parts = [(v / 255.0) for v in _hex(colour)]
        parts = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(colour: str, on: str, c: dict, need: float = 4.5) -> str:
    """colour, moved towards the text colour by as little as it takes to stand
    out from the surface it is drawn on (4.5 for words, 3 for the outline of a
    control). The palette's blue is a shade too pale for words on the grey
    page of the light theme, for one."""
    out, t = colour, 0.0
    while contrast(out, on) < need and t < 1.0:
        t += 0.05
        out = _mix(colour, c["text"], t)
    return out


def _box_image(root, size: int, fill: str, border: str, tick: str | None, back: str):
    """A square tick box as a PhotoImage. tick = colour of the tick, or None."""
    gap = max(5, size // 3)         # space between the box and its label
    img = tk.PhotoImage(master=root, width=size + gap, height=size)
    rows = []
    last = size - 1
    for y in range(size):
        row = []
        for x in range(size):
            corner = (x in (0, last)) and (y in (0, last))
            edge = x in (0, last) or y in (0, last)
            row.append(back if corner else (border if edge else fill))
        rows.append(row)
    if tick:
        # a tick from two strokes, about 2px thick, softened at the edges
        s_ = size / 16.0
        pts = [(3.6, 8.3), (6.6, 11.2), (12.4, 4.9)]

        def dist(px, py, ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            cx, cy = ax + t * dx, ay + t * dy
            return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

        for y in range(1, last):
            for x in range(1, last):
                px, py = (x + 0.5) / s_, (y + 0.5) / s_
                d = min(dist(px, py, *pts[0], *pts[1]), dist(px, py, *pts[1], *pts[2]))
                cover = max(0.0, min(1.0, (1.45 - d) / 0.9))
                if cover > 0:
                    rows[y][x] = _mix(fill, tick, cover)
    for r in rows:
        r.extend([back] * gap)
    img.put(" ".join("{" + " ".join(r) + "}" for r in rows))
    return img


def _tick_boxes(root, s, c):
    size = max(14, fs(15))
    made = _IMAGES.setdefault(root, {})
    for prefix, back in (("", c["bg"]), ("Panel.", c["panel"]), ("Hint.", c["hint"])):
        key = (prefix, c["dark"])
        element = f"{prefix.replace('.', '')}Tick{'D' if c['dark'] else 'L'}.indicator"
        # An element can be made only once, and goes on using the pictures it
        # was made with. So when the theme is switched back, the first set must
        # still be there: making (and keeping) a second set would let the first
        # be thrown away, and every tick box in the app would go blank.
        if key not in made:
            imgs = {
                # (the outline is what says "this is a box you can tick", so it
                # is given enough contrast to be seen on its own)
                "off": _box_image(root, size, c["field"],
                                  readable(c["field_border"], back, c, 3.0), None, back),
                "on": _box_image(root, size, c["accent"], c["accent"], c["accent_text"], back),
                "off_dis": _box_image(root, size, c["bg"], c["border"], None, back),
                "on_dis": _box_image(root, size, c["border"], c["border"], c["dim"], back),
                "off_hot": _box_image(root, size, c["field"], c["accent"], None, back),
            }
            made[key] = imgs
            try:
                s.element_create(element, "image", imgs["off"],
                                 ("disabled", "selected", imgs["on_dis"]),
                                 ("disabled", imgs["off_dis"]),
                                 ("selected", imgs["on"]),
                                 ("active", imgs["off_hot"]),
                                 padding=(0, 0, 7, 0), sticky="w")
            except tk.TclError:
                pass
        s.layout(f"{prefix}TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            (element, {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])


def text_colors(c: dict, readonly: bool = False) -> dict:
    """Keyword arguments that make a classic tk.Text match the theme."""
    if readonly:
        return dict(bg=c["panel"], fg=c["text"], insertbackground=c["accent"], relief="flat",
                    highlightthickness=1, highlightbackground=c["border"],
                    highlightcolor=c["border"], selectbackground=c["sel"],
                    selectforeground=c["text"], borderwidth=0, font=(c["ui"], fs(10)),
                    padx=8, pady=6, wrap="word")
    return dict(bg=c["field"], fg=c["field_text"], insertbackground=c["accent"], relief="flat",
                highlightthickness=1, highlightbackground=c["field_border"],
                highlightcolor=c["accent"], selectbackground=c["sel"],
                selectforeground=c["field_text"], borderwidth=0, font=(c["ui"], fs(10)),
                padx=6, pady=5, wrap="word", undo=True)


def menu(parent, c: dict) -> tk.Menu:
    return tk.Menu(parent, tearoff=0, bg=c["panel"], fg=c["text"], activebackground=c["sel"],
                   activeforeground=c["text"], bd=0, font=(c["ui"], fs(10)))
