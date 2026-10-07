"""Choosing a record to link to, and making a new one on the spot."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import blueprint as bpm
from .. import db as dbm
from . import widgets
from .widgets import Dialog


def save_new_record(app, type_def: dict, form, parent_dialog=None) -> int | None:
    """Validate a Form and create the record. Shows problems on the form.
    Returns the new id, or None when it was not saved."""
    db = app.db
    data, errors = db.validate_record(type_def["key"], form.raw())
    if errors:
        form.show_errors(errors)
        return None
    clashes = db.unique_clashes(type_def["key"], data)
    if clashes:
        f, r = clashes[0]
        form.show_errors({f["key"]: f"{r['title']} ({r['ref']}) already has this. "
                                    f"{f['name']} must be different for every {type_def['name'].lower()}."})
        return None
    dupes = db.find_duplicates(type_def["key"], data)
    if dupes:
        f, r = dupes[0]
        if not widgets.confirm(app, "Possible duplicate",
                               f"{r['title']} ({r['ref']}) already has the same "
                               f"{f['name'].lower()}.\n\nAdd this as a new "
                               f"{type_def['name'].lower()} anyway?", yes="Add anyway"):
            return None
    try:
        return db.create_record(type_def["key"], data)
    except dbm.DBError as exc:
        widgets.error(app, "Could not save", str(exc))
        return None


def quick_create(app, type_key: str, preset: dict | None = None) -> int | None:
    """A pop-up form for a new record of the given list. Returns its id."""
    t = bpm.get_type(app.db.blueprint, type_key)
    if t is None:
        return None
    d = Dialog(app, f"New {t['name'].lower()}", width=640, resizable=True)
    values = {}
    for f in bpm.active_fields(t):
        v = bpm.default_value(f, app.db.user["id"])
        if v is not None:
            values[f["key"]] = v
    values.update(preset or {})
    sf = widgets.ScrollFrame(d.body, app)
    sf.pack(fill="both", expand=True)
    sf.canvas.configure(height=min(520, 90 + 62 * len(bpm.active_fields(t)) // 2), width=600)
    form = widgets.Form(sf.body, app, t, values, columns=2, panel=False)
    form.pack(fill="both", expand=True, padx=(0, 8))

    def save():
        rid = save_new_record(app, t, form)
        if rid is not None:
            d.ok(rid)

    d.add_button(f"Add {t['name'].lower()}", save, accent=True)
    d.add_button("Cancel", d.cancel)
    d.default_on_enter()
    d.bind(f"<{widgets.MOD}-s>", lambda _e: save())
    d.after(50, form.focus_first)
    return d.show()


def pick_record(app, type_key: str, current: int | None = None, title: str | None = None) -> int | None:
    """Search one list and choose a record. Returns its id, or None if cancelled."""
    db = app.db
    t = bpm.get_type(db.blueprint, type_key)
    if t is None:
        widgets.error(app, "Nothing to link to", "The list this field links to no longer exists.")
        return None
    d = Dialog(app, title or f"Choose {t['name'].lower()}", width=520, resizable=True)
    search = widgets.SearchEntry(d.body, app, f"Search {t['plural'].lower()}…",
                                 on_change=lambda q: fill(q), delay=150)
    search.pack(fill="x")
    box = ttk.Frame(d.body)
    box.pack(fill="both", expand=True, pady=(8, 0))
    tree = ttk.Treeview(box, columns=("ref",), show="tree", height=12, selectmode="browse")
    tree.column("#0", width=380, stretch=True)
    tree.column("ref", width=90, stretch=False, anchor="e")
    bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    tree.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    empty = ttk.Label(d.body, text="", style="Help.TLabel")
    empty.pack(anchor="w", pady=(6, 0))

    def fill(query=""):
        tree.delete(*tree.get_children())
        rows = db.records(type_key, query)
        rows.sort(key=lambda r: r["title"].lower())
        for r in rows[:500]:
            tree.insert("", "end", iid=str(r["id"]), text=r["title"], values=(r["ref"],))
        if rows:
            pick = str(current) if current and tree.exists(str(current)) else tree.get_children()[0]
            tree.selection_set(pick)
            tree.see(pick)
            empty.configure(text="" if len(rows) <= 500 else
                            "Showing the first 500. Type to narrow it down.")
        else:
            empty.configure(text=f"No {t['plural'].lower()} match." if query
                            else f"There are no {t['plural'].lower()} yet.")

    def choose(_e=None):
        sel = tree.selection()
        if sel:
            d.ok(int(sel[0]))

    def new():
        q = search.get_text()
        preset = {}
        first = bpm.get_field(t, (t.get("title") or [""])[0])
        if q and first and first["kind"] == "text" and len(t.get("title") or []) == 1:
            preset[first["key"]] = q
        rid = quick_create(app, type_key, preset)
        if rid is not None:
            d.ok(rid)

    tree.bind("<Double-1>", choose)
    tree.bind("<Return>", choose)
    search.bind("<Down>", lambda _e: (tree.focus_set(), tree.focus(tree.selection()[0])
                                      if tree.selection() else None))
    search.bind("<Return>", choose, add="+")
    d.add_button("Choose", choose, accent=True)
    d.add_button("Cancel", d.cancel)
    if db.can_edit:
        d.add_button(f"New {t['name'].lower()}…", new, side="left")
    fill()
    return d.show(focus=search)
