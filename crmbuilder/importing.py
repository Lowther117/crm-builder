"""Bringing rows from a spreadsheet into a list. No user interface here.

    mapping  = auto_map(type_def, headers)        # column index -> field key (or None)
    prepared = prepare(db, type_key, rows, mapping, options)
    report   = run(db, type_key, prepared, options)
"""
from __future__ import annotations

import re

from . import blueprint as bpm
from . import db as dbm
from . import validate

SYNONYMS = {
    "first_name": ["forename", "given name", "firstname", "first"],
    "last_name": ["surname", "family name", "lastname", "last"],
    "name": ["full name", "company", "company name", "organisation name", "title"],
    "email": ["e-mail", "email address", "e-mail address", "mail"],
    "mobile": ["mobile phone", "mobile number", "cell", "mob"],
    "phone": ["telephone", "tel", "phone number", "landline", "telephone number"],
    "postcode": ["post code", "zip", "postal code"],
    "organisation": ["company", "organization", "employer", "org", "account", "business"],
    "job_title": ["role", "position", "title"],
    "date_of_birth": ["dob", "birth date", "birthday", "date of birth"],
    "national_insurance_number": ["ni number", "nino", "ni no", "ni"],
    "website": ["url", "web", "web address", "site"],
    "address": ["street", "address line 1", "street address"],
    "value": ["amount", "price", "worth"],
    "stage": ["status", "state"],
    "status": ["stage", "state"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def auto_map(type_def: dict, headers: list[str]) -> dict[int, str | None]:
    """Best guess at which column feeds which field. Each field is used once."""
    fields = bpm.active_fields(type_def)
    used: set[str] = set()
    out: dict[int, str | None] = {i: None for i in range(len(headers))}
    passes = [
        lambda h, f: h == _norm(f["name"]) or h == f["key"].replace("_", " "),
        lambda h, f: h in [_norm(x) for x in SYNONYMS.get(f["key"], [])] and not (
            # "Company" means the linked organisation, not this record's own name,
            # in a list that has both
            f["key"] == "name" and h in ("company", "company name", "organisation name")
            and any(x["key"] == "organisation" for x in fields)),
        lambda h, f: len(h) >= 4 and (h in _norm(f["name"]) or _norm(f["name"]) in h),
    ]
    for test in passes:
        for i, head in enumerate(headers):
            if out[i] is not None:
                continue
            h = _norm(head)
            if not h:
                continue
            for f in fields:
                if f["key"] not in used and test(h, f):
                    out[i] = f["key"]
                    used.add(f["key"])
                    break
    return out


def default_options() -> dict:
    return {
        "add_options": True,     # a new choice/tag value is added to the field's options
        "create_links": True,    # a linked record that does not exist yet is created
        "match_field": None,     # field key: rows matching an existing record on it update that record
        "on_invalid": "skip",    # 'skip' the whole row, or 'blank' just the bad values
    }


def link_can_create(db, field) -> dict | None:
    """The target list's single text title field, if a bare name is enough to make a record."""
    target = bpm.get_type(db.blueprint, field.get("link_type") or "")
    if target is None or target.get("archived"):
        return None
    title = [bpm.get_field(target, k) for k in target.get("title") or []]
    title = [f for f in title if f is not None and not f.get("archived")]
    if len(title) != 1 or title[0]["kind"] != "text":
        return None
    for f in bpm.active_fields(target):
        if f.get("required") and f["key"] != title[0]["key"] and bpm.default_value(f, None) is None:
            return None
    return title[0]


def prepare(db, type_key: str, rows: list[list[str]], mapping: dict, options: dict | None = None,
            progress=None, row_numbers: list[int] | None = None) -> dict:
    """Check every row without changing anything.

    Returns {"rows": [...], "new_options": {field key: [values]},
             "new_links": {field key: [names]}, "summary": {...},
             "design": which version of the design the rows were checked against}.
    row_numbers (from sheets.read_table(with_numbers=True)) gives each row's
    number in the spreadsheet; without it rows are numbered from 2.
    Each row: {"n": spreadsheet row number, "data": validated values,
               "errors": {field key: sentence}, "pending_links": {field key: name},
               "existing": id of the record it will update or None, "action": str}
    action is 'add', 'update', 'skip' (has errors and on_invalid == 'skip') or 'empty'.
    """
    opts = default_options()
    opts.update(options or {})
    t = bpm.get_type(db.blueprint, type_key)
    fields = {f["key"]: f for f in bpm.active_fields(t)}
    cols = [(i, fields[k]) for i, k in mapping.items() if k in fields]
    seen_options: dict[str, list[str]] = {}    # every new value met, for checking later rows
    creatable = {f["key"]: link_can_create(db, f) for _i, f in cols if f["kind"] == "link"}
    link_index: dict[str, dict[str, int]] = {}

    def resolve(kind, text, field):
        """db.resolve, reading each linked list once instead of once for every row."""
        if kind != "link":
            return db.resolve(kind, text, field)
        index = link_index.get(field.get("link_type"))
        if index is None:
            index = link_index[field.get("link_type")] = {}
            for r in reversed(db.records(field.get("link_type") or "")):    # oldest first
                index.setdefault(r["title"].lower(), r["id"])
                index.setdefault(r["ref"].lower(), r["id"])
        return index.get(" ".join(text.split()).lower())

    match = fields.get(opts.get("match_field") or "")
    seen_match: dict[str, int] = {}         # value in the match field -> the new row that has it
    existing_index: dict[str, int] = {}
    if match is not None:
        for r in db.records(type_key):
            v = r["data"].get(match["key"])
            if v not in (None, "", []):
                existing_index.setdefault(str(v).strip().lower(), r["id"])
    unique = [f for _i, f in cols if f.get("unique")]
    seen_unique: dict[str, dict[str, tuple]] = {f["key"]: {} for f in unique}
    if unique:
        for r in db.records(type_key):
            for f in unique:
                v = r["data"].get(f["key"])
                if v not in (None, "", []):
                    seen_unique[f["key"]].setdefault(str(v).strip().lower(),
                                                     ("record", r["id"], r["title"]))
    out = []
    counts = {"add": 0, "update": 0, "skip": 0, "empty": 0, "with_errors": 0}
    for n, row in enumerate(rows):
        if progress and n % 200 == 0:
            progress(n, len(rows))
        number = row_numbers[n] if row_numbers and n < len(row_numbers) else n + 2
        raw = {f["key"]: (row[i] if i < len(row) else "") for i, f in cols}
        if not any(str(v).strip() for v in raw.values()):
            out.append({"n": number, "data": {}, "errors": {}, "pending_links": {},
                        "new_options": {}, "existing": None, "action": "empty"})
            counts["empty"] += 1
            continue
        data, errors, pending, row_options = {}, {}, {}, {}
        left_empty = []
        for _i, f in cols:
            text = str(raw.get(f["key"], "")).strip()
            if not text and f.get("required"):
                left_empty.append(f)        # decided below, once we know if the row updates
                continue
            field = f
            if f["kind"] in ("choice", "tags") and text and opts["add_options"]:
                known = [o.lower() for o in validate.options_of(f)]
                parts = [text] if f["kind"] == "choice" else \
                    [p.strip() for p in re.split(r"[;,|\n]", text) if p.strip()]
                for p in parts:
                    if p.lower() not in known and len(p) <= 60:
                        row_options.setdefault(f["key"], []).append(p)
                        if p.lower() not in [x.lower() for x in seen_options.get(f["key"], [])]:
                            seen_options.setdefault(f["key"], []).append(p)
                field = dict(f, options=validate.options_of(f) + seen_options.get(f["key"], []))
            value, err = validate.normalise(field, text, resolve)
            if err and f["kind"] == "link" and text and opts["create_links"] and creatable.get(f["key"]):
                # the name goes through the same checks as if it were typed in
                name, bad = validate.normalise(dict(creatable[f["key"]], required=False), text)
                if name and not bad:
                    pending[f["key"]] = name
                    err = None
            if err:
                errors[f["key"]] = err
            elif value is not None:
                data[f["key"]] = value
        existing = None
        if match is not None and data.get(match["key"]) not in (None, "", []):
            existing = existing_index.get(str(data[match["key"]]).strip().lower())
        # required fields that have no column at all (a row that updates a
        # record leaves them as the record has them)
        for f in fields.values():
            if f.get("required") and f["key"] not in data and f["key"] not in errors \
                    and f["key"] not in pending and not existing:
                d = bpm.default_value(f, db.user["id"])
                if d is not None:
                    data[f["key"]] = d
                elif f["key"] not in raw:
                    errors[f["key"]] = f"{f['name']} is needed, and no column supplies it."
        # An empty cell in a needed field: a row that updates a record keeps what
        # the record has; a new record takes the field's starting value, as the
        # form would; otherwise it is a problem.
        for f in left_empty:
            if existing:
                continue
            d = bpm.default_value(f, db.user["id"])
            if d is not None:
                data[f["key"]] = d
            else:
                errors[f["key"]] = f"{f['name']} is needed."
        # 'must be different on every record'
        for f in unique:
            key = f["key"]
            if data.get(key) in (None, "", []):
                continue
            v = str(data[key]).strip().lower()
            hit = seen_unique[key].get(v)
            if hit is None:
                continue
            if hit[0] == "record" and hit[1] == existing:
                continue                    # the row updates the record that holds it
            if hit[0] == "record":
                errors[key] = (f"“{hit[2]}” already has this {f['name']}, and it must be "
                               "different on every record.")
            else:
                errors[key] = (f"Row {hit[1]} has the same {f['name']}, and it must be "
                               "different on every record.")
            data.pop(key, None)
        # Matching is on, and an earlier row is already going in as a new record
        # with this value: adding the same thing twice is never what was meant.
        # (Rows that update a record already in the list are applied in turn.)
        twin = None
        if match is not None and not existing and data.get(match["key"]) not in (None, "", []):
            twin = seen_match.get(str(data[match["key"]]).strip().lower())
            if twin is not None:
                errors.setdefault(match["key"], f"Row {twin} has the same {match['name']}, which "
                                  "is what rows are matched on. Only the first is imported.")
        action = "update" if existing else "add"
        if errors:
            counts["with_errors"] += 1
            if opts["on_invalid"] == "skip" or twin is not None:
                action = "skip"
            else:
                blocked = [k for k in errors if fields[k].get("required")]
                if blocked and not existing:
                    action = "skip"
        counts[action] += 1
        if action == "add" and match is not None and data.get(match["key"]) not in (None, "", []):
            seen_match[str(data[match["key"]]).strip().lower()] = number
        if action in ("add", "update"):
            for f in unique:
                v = data.get(f["key"])
                if v not in (None, "", []):
                    seen_unique[f["key"]].setdefault(str(v).strip().lower(), ("row", number, ""))
        out.append({"n": number, "data": data, "errors": errors, "pending_links": pending,
                    "new_options": row_options, "existing": existing, "action": action})
    if progress:
        progress(len(rows), len(rows))
    # only rows that will actually be imported may add options or create linked records
    new_options: dict[str, list[str]] = {}
    new_links: dict[str, list[str]] = {}
    for r in out:
        if r["action"] not in ("add", "update"):
            continue
        for key, values in r["new_options"].items():
            if key in r["errors"]:
                continue
            have = new_options.setdefault(key, [])
            for v in values:
                if v.lower() not in [x.lower() for x in have]:
                    have.append(v)
        for key, name in r["pending_links"].items():
            names = new_links.setdefault(key, [])
            if name.lower() not in [x.lower() for x in names]:
                names.append(name)
    new_options = {k: v for k, v in new_options.items() if v}
    return {"rows": out, "new_options": new_options, "new_links": new_links, "summary": counts,
            "design": db.get_meta("blueprint_at")}      # what the rows were checked against


_link_can_create = link_can_create     # old name


def run(db, type_key: str, prepared: dict, options: dict | None = None, progress=None) -> dict:
    """Carry out a prepared import in one transaction (all or nothing).
    Returns {"added", "updated", "skipped", "links_created", "options_added"}."""
    opts = default_options()
    opts.update(options or {})
    report = {"added": 0, "updated": 0, "skipped": 0, "links_created": 0, "options_added": 0}
    with db.tx():
        # The rows were checked against the design as it was then. If someone
        # has changed it since, they have to be checked again.
        if db.refresh_design() or prepared.get("design") not in (None, db.get_meta("blueprint_at")):
            raise dbm.DBError("The design was changed while this import was being prepared, "
                              "so nothing was imported. Check the file again, then import.")
        bp = db.blueprint
        t = bpm.get_type(bp, type_key)
        if t is None:
            raise dbm.DBError("That list no longer exists.")
        if prepared.get("new_options"):
            import copy
            bp = copy.deepcopy(bp)
            t2 = bpm.get_type(bp, type_key)
            for key, values in prepared["new_options"].items():
                f = bpm.get_field(t2, key)
                if f is None:
                    continue
                opts_now = validate.options_of(f)
                for v in values:
                    if v.lower() not in [o.lower() for o in opts_now]:
                        opts_now.append(v)
                        report["options_added"] += 1
                f["options"] = opts_now
            db.save_blueprint(bp, note="Options added by an import")
            t = bpm.get_type(db.blueprint, type_key)
        made: dict[tuple[str, str], int] = {}
        for key, names in (prepared.get("new_links") or {}).items():
            f = bpm.get_field(t, key)
            title_field = link_can_create(db, f) if f else None
            if not title_field:
                continue
            # as on the form, a new record starts with the starting values of
            # the fields it must have
            start = {}
            for g in bpm.active_fields(bpm.get_type(db.blueprint, f["link_type"])):
                d = bpm.default_value(g, db.user["id"]) if g.get("required") else None
                if d is not None:
                    start[g["key"]] = d
            for name in names:
                found = db.resolve("link", name, f)
                if found is None:
                    found = db.create_record(f["link_type"], dict(start, **{title_field["key"]: name}))
                    report["links_created"] += 1
                made[(key, name.lower())] = found
        rows = prepared["rows"]
        for i, row in enumerate(rows):
            if progress and i % 100 == 0:
                progress(i, len(rows))
            if row["action"] in ("skip", "empty"):
                if row["action"] == "skip":
                    report["skipped"] += 1
                continue
            data = dict(row["data"])
            for key, name in row["pending_links"].items():
                rid = made.get((key, name.lower()))
                if rid:
                    data[key] = rid
            if row["action"] == "update" and row["existing"]:
                db.update_record(row["existing"], data)
                report["updated"] += 1
            else:
                db.create_record(type_key, data)
                report["added"] += 1
        if progress:
            progress(len(rows), len(rows))
        if report["added"] or report["updated"]:
            db.set_meta("has_imported", "1")
    return report


def type_from_sheet(name: str, headers: list[str], rows: list[list[str]], bp: dict) -> tuple[dict, dict]:
    """Design a new list from a spreadsheet: one field per column, kinds guessed
    from the contents. Returns (type definition, mapping column index -> field key)."""
    t = bpm.new_type(name, bp)
    t["fields"] = []
    keys: list[str] = []
    mapping: dict[int, str] = {}
    for i, head in enumerate(headers):
        column = [r[i] for r in rows[:400] if i < len(r)]
        kind, options = validate.guess_kind(head, column)
        f = bpm.new_field(head.strip()[:60] or f"Column {i + 1}", kind, keys)
        if kind == "choice":
            f["options"] = options
        if i < 6 and kind != "longtext":
            f["in_list"] = True
        keys.append(f["key"])
        t["fields"].append(f)
        mapping[i] = f["key"]
    # what a record is called: a "name"-like column, or first + last name, else the first text column
    low = {f["key"]: f for f in t["fields"]}
    first = next((k for k in low if k in ("first_name", "forename", "firstname", "given_name")), None)
    last = next((k for k in low if k in ("last_name", "surname", "lastname", "family_name")), None)
    named = next((f["key"] for f in t["fields"] if f["kind"] == "text" and
                  re.search(r"\b(name|title|subject|summary|company|organisation)\b",
                            f["name"].lower())), None)
    if first and last:
        t["title"] = [first, last]
    elif named:
        t["title"] = [named]
    else:
        t["title"] = [next((f["key"] for f in t["fields"] if f["kind"] == "text"),
                           t["fields"][0]["key"] if t["fields"] else "name")]
    stage = next((f for f in t["fields"] if f["kind"] == "choice" and
                  re.search(r"\b(stage|status)\b", f["name"].lower())), None)
    if stage:
        t["board"] = stage["key"]
    return t, mapping
