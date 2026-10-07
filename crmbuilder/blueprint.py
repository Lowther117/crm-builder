"""The design of a CRM: which lists exist, what each record holds, how they link.

The whole design ("blueprint") is one JSON document stored in the database, so
everyone who opens the file gets the same lists and forms.

  blueprint = {"version": 2, "name": "...", "types": [type, ...]}
  type  = {"key", "name", "plural", "prefix", "color", "title": [field keys],
           "board": field key or "", "fields": [field, ...], "archived": bool}
  field = {"key", "name", "kind", plus optional: "section", "required", "unique",
           "help", "default", "options", "link_type", "min", "max", "decimals",
           "maxlen", "date_rule", "in_list", "remind", "archived"}

Keys never change once made, so renaming anything is always safe. Removing a
field or a list only archives it: the stored information is kept.
"""
from __future__ import annotations

import copy
import re

from . import validate

VERSION = 2
COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899",
          "#14b8a6", "#f97316", "#6366f1", "#84cc16"]
BOOL_KEYS = ("required", "unique", "in_list", "remind", "archived")


def slug(name: str, taken=()) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "item"
    if base[0].isdigit():
        base = "f_" + base
    key, n = base, 2
    taken = set(taken)
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    return key


def make_prefix(name: str, taken=()) -> str:
    letters = re.sub(r"[^A-Za-z]", "", name or "").upper() or "REC"
    cands = [letters[:3], letters[:2] + letters[-1:], letters[:4]]
    cands += [letters[:2] + str(i) for i in range(2, 10)]
    for c in cands:
        if c and c not in taken:
            return c
    n = 10
    while f"{letters[:2]}{n}" in taken:
        n += 1
    return f"{letters[:2]}{n}"


def plural_of(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return n
    low = n.lower()
    if low.endswith(("person",)):
        return n[:-6] + ("People" if n[-6].isupper() else "people")
    if low.endswith("y") and len(n) > 1 and low[-2] not in "aeiou":
        return n[:-1] + "ies"
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return n + "es"
    return n + "s"


def new_field(name: str, kind: str = "text", taken=(), **extra) -> dict:
    f = {"key": slug(name, taken), "name": name.strip(), "kind": kind}
    f.update({k: v for k, v in extra.items() if v not in (None, "", [], False)})
    return f


def new_type(name: str, bp: dict | None = None, plural: str | None = None) -> dict:
    types = (bp or {}).get("types", [])
    keys = [t["key"] for t in types]
    prefixes = [t.get("prefix") for t in types]
    t = {
        "key": slug(name, keys), "name": name.strip(),
        "plural": (plural or plural_of(name)).strip(),
        "prefix": make_prefix(name, prefixes),
        "color": COLORS[len(types) % len(COLORS)],
        "title": ["name"], "board": "",
        "fields": [new_field("Name", "text", required=True, in_list=True)],
    }
    return t


def empty(name: str = "My CRM") -> dict:
    return {"version": VERSION, "name": name, "types": []}


# ------------------------------------------------------------------ queries
def active_types(bp: dict) -> list[dict]:
    return [t for t in bp.get("types", []) if not t.get("archived")]


def get_type(bp: dict, key: str) -> dict | None:
    for t in bp.get("types", []):
        if t["key"] == key:
            return t
    return None


def active_fields(t: dict) -> list[dict]:
    return [f for f in t.get("fields", []) if not f.get("archived")]


def get_field(t: dict, key: str) -> dict | None:
    for f in t.get("fields", []):
        if f["key"] == key:
            return f
    return None


def list_fields(t: dict) -> list[dict]:
    """Fields shown as columns in the list: the ticked ones, else the first few."""
    fields = [f for f in active_fields(t) if f["kind"] != "longtext"]
    chosen = [f for f in fields if f.get("in_list")]
    return chosen or fields[:5]


def sections(t: dict) -> list[tuple[str, list[dict]]]:
    """Active fields grouped by section, in first-appearance order."""
    out: list[tuple[str, list[dict]]] = []
    index = {}
    for f in active_fields(t):
        s = (f.get("section") or "").strip()
        if s not in index:
            index[s] = len(out)
            out.append((s, []))
        out[index[s]][1].append(f)
    return out


def board_field(t: dict) -> dict | None:
    f = get_field(t, t.get("board") or "")
    if f and not f.get("archived") and f["kind"] == "choice" and validate.options_of(f):
        return f
    return None


def money_field(t: dict) -> dict | None:
    for f in active_fields(t):
        if f["kind"] == "money":
            return f
    return None


def links_to(bp: dict, type_key: str) -> list[tuple[dict, dict]]:
    """(type, field) pairs whose link field points at type_key."""
    out = []
    for t in active_types(bp):
        for f in active_fields(t):
            if f["kind"] == "link" and f.get("link_type") == type_key:
                out.append((t, f))
    return out


def title_for(t: dict, data: dict, lookup=None) -> str:
    parts = []
    for key in t.get("title") or []:
        f = get_field(t, key)
        if f is None or f.get("archived"):
            continue
        text = validate.display(f, data.get(key), lookup)
        if text:
            parts.append(text)
    if not parts:
        for f in active_fields(t):
            text = validate.display(f, data.get(f["key"]), lookup)
            if text and f["kind"] not in ("longtext", "yesno"):
                parts.append(text)
                break
    sep = " – " if any(get_field(t, k) and get_field(t, k)["kind"] == "link"
                       for k in t.get("title") or []) else " "
    return sep.join(parts).strip()[:200]


def default_value(f: dict, user_id=None):
    d = f.get("default")
    if d in (None, ""):
        return None
    if f["kind"] == "date":
        got = validate.parse_date(str(d))
        return got.isoformat() if got else None
    if f["kind"] == "user":
        return user_id if str(d).lower() == "me" else None
    if f["kind"] == "yesno":
        return str(d).lower() in validate.TRUE_WORDS
    if f["kind"] == "link":
        return None
    v, err = validate.normalise(dict(f, required=False), d)
    return None if err else v


# --------------------------------------------------------------- checking
def _list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def clean(bp: dict) -> dict:
    """Return a tidy, safe copy of a blueprint (used on import and before save).
    Whatever it is given - a damaged file, something that is not a design at
    all - it returns a design and never raises."""
    bp = copy.deepcopy(bp) if isinstance(bp, dict) else {}
    out = {"version": VERSION, "name": str(bp.get("name") or "My CRM")[:80], "types": []}
    tkeys: list[str] = []
    prefixes: list[str] = []
    for t in _list(bp.get("types")):
        if not isinstance(t, dict) or not str(t.get("name") or "").strip():
            continue
        name = str(t["name"]).strip()[:60]
        key = str(t.get("key") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in tkeys:
            key = slug(name, tkeys)
        tkeys.append(key)
        prefix = re.sub(r"[^A-Z0-9]", "", str(t.get("prefix") or "").upper())[:6]
        if not prefix or prefix in prefixes:
            prefix = make_prefix(name, prefixes)
        prefixes.append(prefix)
        nt = {"key": key, "name": name,
              "plural": str(t.get("plural") or plural_of(name)).strip()[:60],
              "prefix": prefix,
              "color": t.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(t.get("color") or ""))
              else COLORS[len(out["types"]) % len(COLORS)],
              "title": [], "board": "", "fields": []}
        if t.get("archived"):
            nt["archived"] = True
        fkeys: list[str] = []
        for f in _list(t.get("fields")):
            if not isinstance(f, dict) or not str(f.get("name") or "").strip():
                continue
            fname = str(f["name"]).strip()[:60]
            fkey = str(f.get("key") or "")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", fkey) or fkey in fkeys:
                fkey = slug(fname, fkeys)
            fkeys.append(fkey)
            kind = f.get("kind")
            kind = kind if isinstance(kind, str) and kind in validate.KINDS else "text"
            nf = {"key": fkey, "name": fname, "kind": kind}
            for k in ("section", "help", "default", "link_type", "date_rule"):
                if str(f.get(k) or "").strip():
                    nf[k] = str(f[k]).strip()[:200]
            for k in ("min", "max", "decimals", "maxlen"):
                if f.get(k) not in (None, ""):
                    try:
                        n = float(f[k])
                        if n - n != 0:          # infinity, not-a-number
                            continue
                        if k in ("min", "max"):
                            nf[k] = n
                        elif k == "decimals" and 0 <= n <= 10:
                            nf[k] = int(n)
                        elif k == "maxlen" and 1 <= n <= 1000000:
                            nf[k] = int(n)
                    except (TypeError, ValueError, OverflowError):
                        pass
            for k in BOOL_KEYS:
                if f.get(k):
                    nf[k] = True
            if kind in ("choice", "tags"):
                opts = []
                for o in _list(f.get("options")):
                    o = str(o).strip()[:60]
                    if o and o.lower() not in [x.lower() for x in opts]:
                        opts.append(o)
                nf["options"] = opts
            if nf.get("date_rule") not in (None, "past", "future"):
                nf.pop("date_rule", None)
            nt["fields"].append(nf)
        nt["title"] = [k for k in _list(t.get("title")) if isinstance(k, str) and k in fkeys][:4]
        if not nt["title"] and nt["fields"]:
            nt["title"] = [nt["fields"][0]["key"]]
        b = get_field(nt, str(t.get("board") or ""))
        if b and b["kind"] == "choice":
            nt["board"] = b["key"]
        out["types"].append(nt)
    for t in out["types"]:  # links must point at a list that exists
        for f in t["fields"]:
            if f["kind"] == "link" and f.get("link_type") not in tkeys:
                f["kind"] = "text"
                f.pop("link_type", None)
    return out


def problems(bp: dict) -> list[str]:
    """Things that stop a design being saved, in plain English."""
    out = []
    names = set()
    for t in active_types(bp):
        if t["name"].lower() in names:
            out.append(f"Two lists are called “{t['name']}”. Give each its own name.")
        names.add(t["name"].lower())
        fields = active_fields(t)
        if not fields:
            out.append(f"“{t['name']}” has no fields. Add at least one.")
        seen = set()
        for f in fields:
            if f["name"].lower() in seen:
                out.append(f"“{t['name']}” has two fields called “{f['name']}”.")
            seen.add(f["name"].lower())
            if f["kind"] in ("choice", "tags") and not validate.options_of(f):
                out.append(f"“{f['name']}” in {t['name']} needs at least one option to pick from.")
            if f["kind"] == "link":
                target = get_type(bp, f.get("link_type") or "")
                if target is None or target.get("archived"):
                    out.append(f"“{f['name']}” in {t['name']} needs to say which list it links to.")
    return out


def describe(bp: dict) -> str:
    bits = []
    for t in active_types(bp):
        bits.append(f"{t['plural']} ({len(active_fields(t))} fields)")
    return ", ".join(bits)
