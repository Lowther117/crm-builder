"""Base class for the pages shown in the main area."""
from __future__ import annotations

from tkinter import ttk


class Page(ttk.Frame):
    """One page in the main area. The shell creates it with Page(parent, app, **kw).

    nav_key     which sidebar entry to highlight ('home', 'tasks', 'reports',
                'list:<type key>', 'design', 'settings', or '')
    auto_refresh  True if refresh() should be called when another person
                changes the file, or the window regains focus
    """
    nav_key = ""
    auto_refresh = False

    def __init__(self, parent, app, **_kw):
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.c = app.c

    def can_leave(self) -> bool:
        """Return False to stop navigation (e.g. the person chose to keep editing)."""
        return True

    def refresh(self) -> None:
        """Reload what is shown, keeping the person's place where possible."""

    def state(self) -> dict:
        """Keyword arguments that recreate this page (Back, theme switch)."""
        return {}

    def header(self, title: str, subtitle: str | None = None, back: bool = False) -> ttk.Frame:
        """Standard page heading. Returns a frame on the right for buttons."""
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=24, pady=(18, 10))
        # (the buttons are packed first: a long title is cut short, never the buttons)
        right = ttk.Frame(bar)
        right.pack(side="right", anchor="s", padx=(12, 0))
        left = ttk.Frame(bar)
        left.pack(side="left", fill="x", expand=True)
        if back and self.app.history:
            from . import widgets
            widgets.link(left, "‹ Back", self.app.back).pack(anchor="w", pady=(0, 2))
        self.title_label = ttk.Label(left, text=title, style="H1.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(left, text=subtitle or "", style="Sub.TLabel")
        if subtitle:
            self.subtitle_label.pack(anchor="w", pady=(2, 0))
        return right
