"""Self-test: proves that this copy of CRM Builder actually works.

    python crm_builder.py selftest          from source
    "CRM Builder.exe" selftest              a built copy (the build scripts do this)

It makes a throw-away CRM from every template in a temporary folder, exercises
records, notes, tasks, files, search, users, encryption and importing, then
opens every page of the window in dark and in light. Nothing outside the
temporary folder is touched except the report, crm-builder-selftest.txt, which
is written beside the app. No network is used.

Options (after the word selftest):
    --no-gui               skip the window checks
    --expect-encryption    a missing encryption module is a failure, not a note
                           (the build scripts pass this when they bundled it)

Every check is one line, "ok", "FAIL" or "note"; the last line is either
SELF-TEST PASSED or PROBLEMS FOUND: n.
"""
from __future__ import annotations

import datetime as _dt
import importlib
import importlib.util
import inspect
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback

from . import APP_NAME, __version__, paths
from . import blueprint as bpm
from . import db as dbm
from . import importing, sheets, templates, validate

REPORT_NAME = "crm-builder-selftest.txt"
TIMEOUT_SECONDS = 240          # a stuck self-test must never hang a build for ever
PASSED = "SELF-TEST PASSED"
PROBLEMS = "PROBLEMS FOUND"

_ASCII = {0x2013: "-", 0x2014: "-", 0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
          0x2026: "...", 0x00A3: "GBP ", 0x00B7: "-", 0x2022: "-"}


def _plain(text) -> str:
    """One line of plain ASCII: the report is shown in consoles of every code page."""
    text = " ".join(str(text).split())
    return text.translate(_ASCII).encode("ascii", "replace").decode("ascii")


class _Skip(Exception):
    """A check that could not be run here (reported as a note, not a failure)."""


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.failures = 0
        self.notes = 0
        self.doing = "starting"
        self.started = time.time()

    def section(self, title: str):
        self.lines.append("")
        self.lines.append(title)

    def _add(self, tag: str, name: str, detail: str = ""):
        line = f"  {tag:<5} {_plain(name)}"
        if detail:
            line += " - " + _plain(detail)
        self.lines.append(line)

    def ok(self, name: str, detail: str = ""):
        self._add("ok", name, detail)

    def fail(self, name: str, detail: str = ""):
        self.failures += 1
        self._add("FAIL", name, detail)

    def note(self, name: str, detail: str = ""):
        self.notes += 1
        self._add("note", name, detail)

    def check(self, name: str, fn, *args):
        """Run one check. It returns a detail string, or raises: AssertionError
        with a message (what was wrong), _Skip (not possible here) or anything
        else (a crash, reported with where it happened)."""
        self.doing = name
        try:
            detail = fn(*args)
        except _Skip as exc:
            self.note(name, str(exc))
        except AssertionError as exc:
            self.fail(name, str(exc) or "a check did not hold" + _where(exc))
        except Exception as exc:  # noqa: BLE001 - a self-test reports everything
            self.fail(name, f"{type(exc).__name__}: {exc}{_where(exc)}")
        else:
            self.ok(name, detail or "")

    def text(self, verdict: bool = True) -> str:
        head = [f"{APP_NAME} {__version__} self-test",
                _dt.datetime.now().strftime("%d/%m/%Y %H:%M")]
        tail = []
        if verdict:
            tail = ["", f"Took {time.time() - self.started:.1f} seconds."]
            if self.notes:
                tail.append(f"{self.notes} note{'s' if self.notes != 1 else ''} above "
                            "(optional parts that are not present - not errors).")
            tail.append(PASSED if not self.failures else f"{PROBLEMS}: {self.failures}")
        return "\n".join(head + self.lines + tail) + "\n"


def _where(exc: BaseException) -> str:
    """' (file.py line N)' for the innermost frame inside this app."""
    frames = traceback.extract_tb(exc.__traceback__)
    mine = [f for f in frames if "crmbuilder" in f.filename.replace("\\", "/")] or frames
    if not mine:
        return ""
    f = mine[-1]
    return f" ({os.path.basename(f.filename)} line {f.lineno})"


def need(condition, message: str):
    if not condition:
        raise AssertionError(message)


class _Steps:
    """Names the step a multi-step check had reached when it went wrong."""

    def __init__(self):
        self.now = ""
        self.count = 0

    def __call__(self, name: str):
        self.now = name
        self.count += 1


def _stepped(fn):
    def run(*args):
        steps = _Steps()
        try:
            return fn(steps, *args)
        except _Skip:
            raise
        except AssertionError as exc:
            raise AssertionError(f"{steps.now}: {exc}") from None
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"{steps.now}: {type(exc).__name__}: {exc}{_where(exc)}") from None
    return run


# ------------------------------------------------------------------ versions
def _versions(rep: Report, strict: bool, expect_encryption: bool):
    rep.section("This copy")
    frozen = bool(getattr(sys, "frozen", False))
    rep.ok(f"{APP_NAME} {__version__}", "built app" if frozen else "running from source")
    bits = "64-bit" if sys.maxsize > 2 ** 32 else "32-bit"
    py = f"Python {platform.python_version()} ({bits}) on {platform.platform()}"
    if sys.version_info < (3, 9):
        rep.fail(py, "Python 3.9 or newer is needed")
    else:
        rep.ok(py)
    rep.ok(f"SQLite {sqlite3.sqlite_version}")
    try:  # when Tk is missing altogether, the window section says so
        import tkinter
        patch = tkinter.Tcl().eval("info patchlevel")
        if tkinter.TkVersion < 8.6:
            rep.fail(f"Tk {patch}", "Tk 8.6 or newer is needed (this is Apple's old system "
                                    "Tk - build with a Homebrew or python.org Python)")
        else:
            rep.ok(f"Tk {patch}")
    except Exception:  # noqa: BLE001
        pass
    try:
        import openpyxl
        rep.ok(f"Excel files: openpyxl {getattr(openpyxl, '__version__', '?')}")
    except Exception as exc:  # noqa: BLE001
        (rep.fail if strict else rep.note)(
            "Excel files", f"openpyxl is not installed ({type(exc).__name__}) - import and "
                           "export fall back to CSV")
    mod = dbm.sqlcipher_module()
    if mod is None:
        text = "not installed - encrypted CRM files cannot be made or opened in this copy"
        if expect_encryption:
            rep.fail("Encryption", text + " (but the build installed it, so it was not bundled)")
        else:
            rep.note("Encryption", text)
    else:
        try:
            con = mod.connect(":memory:")
            row = con.execute("PRAGMA cipher_version").fetchone()
            con.close()
            rep.ok(f"Encryption: SQLCipher {row[0] if row else '?'}")
        except Exception as exc:  # noqa: BLE001
            rep.fail("Encryption", f"the module loads but does not work: {exc}")


# ---------------------------------------------------------------- validation
def _validation():
    d = validate.parse_date
    need(d("13/11/2026") == _dt.date(2026, 11, 13), "13/11/2026 was not read as 13 November 2026")
    need(d("13112026") == _dt.date(2026, 11, 13), "13112026 was not read as a date")
    need(d("1 Feb 1985") == _dt.date(1985, 2, 1), "1 Feb 1985 was not read as a date")
    need(d("2026-11-13") == _dt.date(2026, 11, 13), "2026-11-13 was not read as a date")
    need(d("03/04/2025") == _dt.date(2025, 4, 3), "03/04/2025 was read month-first")
    need(d("31/02/2026") is None, "31/02/2026 was accepted as a date")
    need(d("banana") is None, "'banana' was accepted as a date")
    need(validate.iso_to_uk("2026-11-13") == "13/11/2026", "dates are not shown as dd/mm/yyyy")

    def good(fn, text, want):
        got, err = fn(text)
        need(err is None and got == want, f"{fn.__name__}({text!r}) gave {got!r} / {err!r}, "
                                          f"expected {want!r}")

    def bad(fn, text):
        got, err = fn(text)
        need(got is None and err, f"{fn.__name__}({text!r}) was accepted as {got!r}")

    good(validate.norm_phone, "+44 7700 900123", "07700 900123")
    good(validate.norm_phone, "07700900123", "07700 900123")
    good(validate.norm_phone, "(029) 2018 0000", "029 2018 0000")
    bad(validate.norm_phone, "12345")
    bad(validate.norm_phone, "0770 090012")
    bad(validate.norm_phone, "phone me")
    good(validate.norm_postcode, "cf484tq", "CF48 4TQ")
    good(validate.norm_postcode, " sw1a 1aa ", "SW1A 1AA")
    bad(validate.norm_postcode, "ZZ99 9Z")
    bad(validate.norm_postcode, "12345")
    good(validate.norm_ni, "ab123456c", "AB 12 34 56 C")
    bad(validate.norm_ni, "AB123456Z")
    bad(validate.norm_ni, "GB123456A")
    good(validate.norm_email, "Sam@Example.CO.UK", "Sam@example.co.uk")
    bad(validate.norm_email, "sam@")
    bad(validate.norm_email, "sam example@x.com")
    good(validate.norm_url, "Example.co.uk/page", "https://example.co.uk/page")
    bad(validate.norm_url, "not a site")

    def norm(field, raw):
        return validate.normalise(dict({"key": "f", "name": "Field"}, **field), raw)

    need(norm({"kind": "money"}, "£1,250.50") == (1250.5, None), "£1,250.50 was not read as money")
    need(norm({"kind": "number"}, "42") == (42, None), "42 was not read as a number")
    need(norm({"kind": "number"}, "forty")[1], "'forty' was accepted as a number")
    need(norm({"kind": "number", "max": 10}, "11")[1], "a number over the maximum was accepted")
    need(norm({"kind": "date"}, "13/11/2026") == ("2026-11-13", None), "dates are not stored as ISO")
    need(norm({"kind": "date", "date_rule": "past"}, "+5")[1], "a future date passed a 'past' rule")
    need(norm({"kind": "yesno"}, "yes") == (True, None), "'yes' was not read as Yes")
    need(norm({"kind": "choice", "options": ["Lead", "Customer"]}, "customer") == ("Customer", None),
         "a choice was not matched to its option")
    need(norm({"kind": "choice", "options": ["Lead"]}, "Other")[1], "an unknown choice was accepted")
    need(norm({"kind": "tags", "options": ["A", "B", "C"]}, "a; c") == (["A", "C"], None),
         "tags were not split and matched")
    need(norm({"kind": "text", "required": True}, "  ")[1], "an empty required field was accepted")
    need(norm({"kind": "text"}, "  two   words ") == ("two words", None), "text was not tidied")
    need(validate.display({"kind": "money"}, 1250.5) == "£1,250.50", "money is not shown as £1,250.50")
    return "dates, phone numbers, postcodes, NI numbers, emails, money, choices"


# ----------------------------------------------------------------- templates
@_stepped
def _template(step, tpl: dict, tmp: str, opened: list):
    key = tpl["key"]
    step("build the design")
    bp = templates.build(key, "Self-test")
    types = bpm.active_types(bp)
    need(types, "the template has no lists")
    problems = bpm.problems(bp)
    need(not problems, "the design has problems: " + "; ".join(problems))

    step("create the file")
    path = os.path.join(tmp, f"tpl-{key}.crm")
    db, recovery = dbm.Database.create(path, bp)
    opened.append(db)
    need(recovery is None and not dbm.is_encrypted(path), "a plain file came out encrypted")
    need(db.is_admin and db.can_edit, "the person who made the file is not its administrator")

    step("load the examples")
    made = templates.load_samples(db, key)
    need(made > 0, "the template has no example records")
    counts = db.counts()
    need(sum(counts.values()) == made, f"{made} examples were added but {sum(counts.values())} are stored")
    for t in types:
        for r in db.records(t["key"]):
            data, errors = db.validate_record(t["key"], r["data"])
            need(not errors, f"example {r['ref']} does not pass its own checks: {errors}")
            need(r["title"], f"example {r['ref']} has no name")

    step("add a record")
    t = next((x for x in types if db.count(x["key"])), types[0])
    tkey = t["key"]
    source = db.records(tkey)[-1]
    data, errors = db.validate_record(tkey, source["data"])
    need(not errors, f"could not copy {source['ref']}: {errors}")
    rid = db.create_record(tkey, data)
    rec = db.get_record(rid)
    need(rec and rec["data"] == data, "the new record was not stored as given")
    need(rec["ref"].startswith(t["prefix"] + "-") and rec["title"], "the new record has no reference or name")
    need(db.count(tkey) == counts.get(tkey, 0) + 1, "the list did not grow by one")

    step("change it")
    field = next((f for f in bpm.active_fields(t) if f["kind"] == "text"), None)
    need(field is not None, f"the {t['name']} list has no text field")
    fk = field["key"]
    base = dict(rec["data"])
    after = db.update_record(rid, {fk: "Selftest One"}, base=base)
    need(after["data"].get(fk) == "Selftest One", "the change was not saved")

    step("two people changing the same field")
    try:
        db.update_record(rid, {fk: "Selftest Two"}, base=base)
    except dbm.Conflict as exc:
        need(exc.theirs == {fk: "Selftest One"}, f"the conflict reported {exc.theirs!r}")
    else:
        raise AssertionError("a clashing change was saved without a warning")
    after = db.update_record(rid, {fk: "Selftest Two"}, base=base, force=True)
    need(after["data"].get(fk) == "Selftest Two", "the forced change was not saved")

    step("notes")
    nid = db.add_note(rid, "Self-test note\nsecond line", "Call")
    notes = db.notes(rid)
    need(len(notes) == 1 and notes[0]["id"] == nid and notes[0]["kind"] == "Call"
         and notes[0]["body"].startswith("Self-test note"), "the note did not come back")
    db.update_note(nid, "Self-test note, edited")
    need(db.notes(rid)[0]["body"] == "Self-test note, edited", "the note was not changed")

    step("tasks")
    before = len(db.tasks(record_id=rid))
    tid = db.add_task("Self-test task", due=dbm.today_iso(), record_id=rid,
                      assigned_to=db.user["id"])
    mine = db.tasks(record_id=rid, mine=True)
    need(len(mine) == before + 1 and any(x["id"] == tid and x["record_title"] for x in mine),
         "the task did not come back")
    db.set_task_done(tid)
    need(all(x["id"] != tid for x in db.tasks(record_id=rid)), "a finished task is still listed")
    need(any(x["id"] == tid and x["done_at"] for x in db.tasks(record_id=rid, include_done=True)),
         "the finished task was lost")

    step("attach a file")
    blob = bytes(range(256)) * 40 + "naïve – £5".encode("utf-8")
    attach = os.path.join(tmp, f"attachment {key}.bin")
    with open(attach, "wb") as fh:
        fh.write(blob)
    fid = db.add_file(rid, attach)
    got = db.file_data(fid)
    need(got is not None and got[0] == os.path.basename(attach) and got[1] == blob,
         "the attached file did not come back identical")
    listed = db.files(rid)
    need(len(listed) == 1 and listed[0]["size"] == len(blob), "the file list is wrong")

    step("search")
    need(any(r["id"] == rid for r in db.search_all("selftest two")), "search did not find the record")
    need(any(r["id"] == rid for r in db.records(tkey, search="SELFTEST two")),
         "searching inside the list did not find the record")
    need(not db.search_all("zzqqxx-no-such-thing"), "search found something that is not there")
    need(any(r["id"] == rid for r in db.recent(50)), "the record is not among the recent ones")

    step("linked records")
    links = 0
    for lt in types:
        link_fields = [f for f in bpm.active_fields(lt) if f["kind"] == "link"]
        for r in db.records(lt["key"]):
            for f in link_fields:
                dst = r["data"].get(f["key"])
                if not isinstance(dst, int):
                    continue
                found = [recs for (rt, rf, recs) in db.related(dst)
                         if rt["key"] == lt["key"] and rf["key"] == f["key"]]
                need(found and any(x["id"] == r["id"] for x in found[0]),
                     f"{r['ref']} links to record {dst} but is not listed as related to it")
                need(db.lookup("link", dst), f"the record {r['ref']} links to has no name")
                links += 1

    step("dates coming up")
    reminders = 0
    soon = (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
    for rt in types:
        for f in bpm.active_fields(rt):
            if f["kind"] == "date" and f.get("remind") and db.count(rt["key"]):
                target = db.records(rt["key"])[0]
                db.update_record(target["id"], {f["key"]: soon})
                need(any(u["record"]["id"] == target["id"] and u["field"]["key"] == f["key"]
                         and u["date"] == soon for u in db.upcoming()),
                     f"{f['name']} in three days' time is not listed as coming up")
                reminders += 1
    db.upcoming()

    step("saved view")
    spec = {"sort": ["__updated", True], "mode": "list", "search": "selftest"}
    vid = db.save_view(tkey, "Self-test view", spec)
    views = db.views(tkey)
    need(any(v["id"] == vid and v["spec"] == spec for v in views), "the saved view did not come back")

    step("delete and restore")
    need(db.delete_records([rid]) == 1, "the record was not deleted")
    need(all(r["id"] != rid for r in db.records(tkey)), "a deleted record is still in the list")
    need(any(r["id"] == rid for r in db.deleted_records()), "the deleted record is not in the recycle bin")
    need(not any(r["id"] == rid for r in db.search_all("selftest two")), "search finds a deleted record")
    need(db.restore_records([rid]) == 1, "the record was not restored")
    need(any(r["id"] == rid for r in db.records(tkey)), "the restored record is not back in the list")

    step("history of changes")
    actions = {h["action"] for h in db.history(rid)}
    missing = {"created", "changed", "note-added", "task-added", "task-done", "file-added",
               "deleted", "restored"} - actions
    need(not missing, "the history is missing: " + ", ".join(sorted(missing)))
    need(db.history(limit=5), "the overall history is empty")

    step("file is sound")
    need(db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "the file failed its integrity check")
    stats = db.stats()
    need(stats["records"] == made + 1 and stats["files"] == 1 and stats["notes"] >= 1,
         f"the totals are wrong: {stats}")

    step("back up and open the backup")
    backup = db.backup_to(os.path.join(tmp, "backups", f"tpl-{key} backup.crm"))
    copy = dbm.Database.open(backup)
    opened.append(copy)
    need(copy.login_needed() == "none", "the backup asks for a password the original did not have")
    copy.login()
    need(copy.counts() == db.counts(), "the backup holds different records")
    need(copy.get_record(rid)["data"] == db.get_record(rid)["data"], "the backup's record differs")
    need(copy.file_data(fid)[1] == blob, "the backup's attached file differs")
    copy.close()
    try:
        db.backup_to(db.path)
    except dbm.DBError:
        pass
    else:
        raise AssertionError("backing up over the live file was allowed")

    step("close and reopen")
    want = db.get_record(rid)["data"]
    want_counts = db.counts()
    db.close()
    again = dbm.Database.open(path)
    opened.append(again)
    again.login()
    need(again.name == "Self-test", "the CRM's name was not kept")
    need(again.counts() == want_counts and again.get_record(rid)["data"] == want,
         "the reopened file holds different records")
    need(any(v["id"] == vid for v in again.views(tkey)), "the saved view was not kept")
    need(bpm.problems(again.blueprint) == [], "the reopened design has problems")
    again.close()
    return (f"lists {len(types)}, examples {made}, links {links}, reminder dates {reminders}, "
            f"steps {step.count}")


# ------------------------------------------------------- users and passwords
def _small_design(name: str = "Self-test") -> dict:
    """A two-list design made the way the designer makes one (not from a template)."""
    bp = bpm.empty(name)
    org = bpm.new_type("Organisation", bp)
    bp["types"].append(org)
    person = bpm.new_type("Person", bp)
    keys: list[str] = []
    fields = []
    for fname, kind, extra in [
            ("First name", "text", {"required": True, "in_list": True}),
            ("Last name", "text", {"in_list": True}),
            ("Email", "email", {"in_list": True}),
            ("Mobile", "phone", {}),
            ("Postcode", "postcode", {}),
            ("Date of birth", "date", {"date_rule": "past"}),
            ("Status", "choice", {"options": ["Lead", "Customer"], "in_list": True}),
            ("Organisation", "link", {"link_type": org["key"]}),
            ("Value", "money", {})]:
        f = bpm.new_field(fname, kind, keys, **extra)
        keys.append(f["key"])
        fields.append(f)
    person["fields"] = fields
    person["title"] = ["first_name", "last_name"]
    person["board"] = "status"
    bp["types"].append(person)
    bp = bpm.clean(bp)
    problems = bpm.problems(bp)
    need(not problems, "a hand-made design has problems: " + "; ".join(problems))
    return bp


def _refused(fn, *args, **kw):
    """True when the database refuses the action with a plain-English DBError."""
    try:
        fn(*args, **kw)
    except dbm.DBError as exc:
        return bool(str(exc))
    return False


ADMIN_PW, EDITOR_PW, VIEWER_PW = "Admin-pass-17", "Editor-pass-17", "Viewer-pass-17"


@_stepped
def _users(step, tmp: str, opened: list):
    step("create a team CRM")
    path = os.path.join(tmp, "team.crm")
    need(_refused(dbm.Database.create, path, _small_design(), mode="team"),
         "a team CRM without a password was allowed")
    need(not os.path.exists(path), "a refused file was left behind")
    db, _code = dbm.Database.create(path, _small_design("Team test"), mode="team",
                                    name="Alex Admin", username="alex", password=ADMIN_PW)
    opened.append(db)
    step("add people")
    need(_refused(db.add_user, "ed", "Ed Editor", "editor", "short"), "a 5-letter password was allowed")
    db.add_user("ed", "Ed Editor", "editor", EDITOR_PW)
    db.add_user("vi", "Vi Viewer", "viewer", VIEWER_PW)
    need(_refused(db.add_user, "ed", "Another Ed", "viewer", EDITOR_PW), "a username was used twice")
    need(len(db.users()) == 3, "the people list is wrong")
    rid = db.create_record("person", {"first_name": "Pat", "last_name": "Example"})
    db.close()

    step("sign in is needed")
    db = dbm.Database.open(path)
    opened.append(db)
    need(db.login_needed() == "user", "a team CRM opened without asking who you are")
    for user, pw in (("ed", "wrong-password"), ("nobody", EDITOR_PW), ("ed", "")):
        try:
            db.login(user, pw)
        except dbm.LoginError:
            pass
        else:
            raise AssertionError(f"signing in as {user!r} with a wrong password worked")

    step("read-only account")
    db.login("vi", VIEWER_PW)
    need(db.user["name"] == "Vi Viewer" and not db.can_edit and not db.is_admin,
         "the read-only account has the wrong rights")
    need(db.get_record(rid)["title"] == "Pat Example", "the read-only account cannot read")
    need(_refused(db.create_record, "person", {"first_name": "No"}), "a read-only account added a record")
    need(_refused(db.update_record, rid, {"first_name": "No"}), "a read-only account changed a record")
    need(_refused(db.delete_records, [rid]), "a read-only account deleted a record")
    need(_refused(db.add_note, rid, "no"), "a read-only account added a note")
    need(_refused(db.add_user, "x", "X", "viewer", VIEWER_PW), "a read-only account added a person")
    need(db.get_record(rid)["data"]["first_name"] == "Pat", "a refused change was saved anyway")

    step("editor account")
    db.login("ed", EDITOR_PW)
    need(db.can_edit and not db.is_admin, "the editor account has the wrong rights")
    mine = db.create_record("person", {"first_name": "Sam", "last_name": "Sample"})
    db.update_record(rid, {"last_name": "Changed"})
    need(_refused(db.add_user, "x", "X", "viewer", VIEWER_PW), "an editor added a person")
    need(_refused(db.purge_records, [mine]), "an editor erased a record for good")
    need(_refused(db.update_user, db.user["id"], role="admin"), "an editor made themselves administrator")
    need(_refused(db.set_password, db.user["id"], "Another-pass-17", "not-my-password"),
         "a password was changed without the old one")
    need(db.history(rid)[0]["user_name"] == "Ed Editor", "the history does not say who made a change")

    step("administrator account")
    db.login("alex", ADMIN_PW)
    need(db.is_admin, "the administrator lost their rights")
    need(_refused(db.update_user, db.user["id"], role="viewer"), "the only administrator was demoted")
    vi = next(u for u in db.users() if u["username"] == "vi")
    db.update_user(vi["id"], active=False)
    try:
        db.login("vi", VIEWER_PW)
    except dbm.LoginError:
        pass
    else:
        raise AssertionError("a switched-off account could still sign in")
    db.login("alex", ADMIN_PW)
    need(db.purge_records([mine]) == 1 and db.get_record(mine) is None, "erasing for good did not work")
    db.close()
    return f"3 accounts, {step.count} steps"


# ---------------------------------------------------------------- encryption
SECRET_PW, NEW_PW = "Secret-pass-17", "Fresh-pass-2718"
MARKER = "Zebedee Quixotic-Marker"


@_stepped
def _encryption(step, tmp: str, opened: list):
    if dbm.sqlcipher_module() is None:
        raise _Skip("skipped - the encryption module is not installed")
    step("create an encrypted CRM")
    path = os.path.join(tmp, "locked.crm")
    need(_refused(dbm.Database.create, path, _small_design(), encrypt=True),
         "encryption without a password was allowed")
    db, code = dbm.Database.create(path, _small_design("Locked"), password=SECRET_PW, encrypt=True)
    opened.append(db)
    need(code and len(code) >= 16, "no recovery code was given")
    need(os.path.exists(path + ".keys"), "the key file was not written")
    rid = db.create_record("person", {"first_name": "Zebedee", "last_name": "Quixotic-Marker"})
    attach = os.path.join(tmp, "secret.txt")
    with open(attach, "w", encoding="utf-8") as fh:
        fh.write(MARKER * 20)
    fid = db.add_file(rid, attach)
    db.close()

    step("the file on disk is unreadable")
    need(dbm.is_encrypted(path), "the file is not recognised as encrypted")
    with open(path, "rb") as fh:
        raw = fh.read()
    need(not raw.startswith(b"SQLite format 3"), "the file has a plain SQLite header")
    need(MARKER.encode() not in raw and b"Zebedee" not in raw, "the record's text is readable in the file")
    with open(path + ".keys", "rb") as fh:
        need(SECRET_PW.encode() not in fh.read(), "the password is stored in the key file")
    con = sqlite3.connect(path)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("plain SQLite could read the encrypted file")
    finally:
        con.close()

    step("wrong password")
    db = dbm.Database.open(path)
    opened.append(db)
    need(db.login_needed() == "password", "an encrypted CRM opened without asking for the password")
    try:
        db.login(password="Wrong-pass-17")
    except dbm.LoginError:
        pass
    else:
        raise AssertionError("a wrong password opened the encrypted CRM")

    step("right password")
    db.login(password=SECRET_PW)
    need(db.get_record(rid)["title"] == "Zebedee Quixotic-Marker", "the record did not come back")
    need(db.file_data(fid)[1].decode("utf-8") == MARKER * 20, "the attached file did not come back")

    step("back up an encrypted CRM")
    backup = db.backup_to(os.path.join(tmp, "backups", "locked backup.crm"))
    need(dbm.is_encrypted(backup) and os.path.exists(backup + ".keys"),
         "the backup is not encrypted or has no key file")
    db.close()
    copy = dbm.Database.open(backup)
    opened.append(copy)
    copy.login(password=SECRET_PW)
    need(copy.get_record(rid)["title"] == "Zebedee Quixotic-Marker", "the backup does not open")
    copy.close()

    step("recovery code")
    db = dbm.Database.open(path)
    opened.append(db)
    try:
        db.recover("AAAA-BBBB-CCCC-DDDD-EEEE", "", NEW_PW)
    except dbm.LoginError:
        pass
    else:
        raise AssertionError("a made-up recovery code was accepted")
    db.recover(code, "", NEW_PW)
    need(db.get_record(rid) is not None, "the recovery code did not unlock the file")
    db.close()
    db = dbm.Database.open(path)
    opened.append(db)
    try:
        db.login(password=SECRET_PW)
    except dbm.LoginError:
        pass
    else:
        raise AssertionError("the old password still works after a recovery")
    db.login(password=NEW_PW)
    need(db.count("person") == 1, "the record was lost in the recovery")

    step("missing key file")
    db.close()
    os.rename(path + ".keys", path + ".keys.away")
    need(_refused(dbm.Database.open, path), "an encrypted CRM opened without its key file")
    os.rename(path + ".keys.away", path + ".keys")
    return f"create, reopen, wrong password, backup, recovery code ({step.count} steps)"


# ------------------------------------------------------------------ importing
CSV_TEXT = (
    "Forename,Surname,E-mail,Mobile phone,Post code,DOB,Status,Company,Amount\r\n"
    "Ann,Example,Ann@Example.CO.UK,+44 7700 900123,cf484tq,13/11/1990,Lead,Acme Ltd,\"£1,250.50\"\r\n"
    "Bob,Sample,bob@example.org,07700 900456,SW1A 1AA,1 Feb 1985,Partner,Bolt & Co,300\r\n"
    ",,,,,,,,\r\n"
    "Cat,Broken,not-an-email,07700 900789,CF48 4TQ,02/03/1970,Lead,Acme Ltd,5\r\n"
)


def _import_file(db, path: str, sheet=None, options=None) -> tuple[dict, dict, dict]:
    headers, rows, numbers = sheets.read_table(path, sheet, with_numbers=True)
    t = bpm.get_type(db.blueprint, "person")
    mapping = importing.auto_map(t, headers)
    prepared = importing.prepare(db, "person", rows, mapping, options, row_numbers=numbers)
    report = importing.run(db, "person", prepared, options)
    return mapping, prepared, report


@_stepped
def _csv_import(step, tmp: str, opened: list):
    step("create a CRM to import into")
    db, _code = dbm.Database.create(os.path.join(tmp, "import.crm"), _small_design("Import test"))
    opened.append(db)
    step("read the CSV file")
    path = os.path.join(tmp, "people.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(CSV_TEXT)
    need(sheets.sheet_names(path) == [], "a CSV file was treated as an Excel workbook")
    headers, rows, numbers = sheets.read_table(path, with_numbers=True)
    need(headers[0] == "Forename" and len(headers) == 9, f"the headings were read as {headers}")
    need(len(rows) == 3 and numbers == [2, 3, 5], f"the rows were read wrongly (row numbers {numbers})")
    need(rows[0][8] == "£1,250.50", "a quoted value with a comma and a £ sign was mangled")

    step("match columns to fields")
    t = bpm.get_type(db.blueprint, "person")
    mapping = importing.auto_map(t, headers)
    want = ["first_name", "last_name", "email", "mobile", "postcode", "date_of_birth", "status",
            "organisation", "value"]
    need([mapping.get(i) for i in range(9)] == want,
         f"the columns were matched as {[mapping.get(i) for i in range(9)]}")

    step("check the rows")
    prepared = importing.prepare(db, "person", rows, mapping, row_numbers=numbers)
    summary = prepared["summary"]
    need(summary["add"] == 2 and summary["skip"] == 1 and summary["with_errors"] == 1,
         f"the check counted {summary}")
    bad_row = [r for r in prepared["rows"] if r["action"] == "skip"][0]
    need(bad_row["n"] == 5 and "email" in bad_row["errors"], "the bad row was not pinned to row 5's email")
    need(prepared["new_options"] == {"status": ["Partner"]}, f"new options: {prepared['new_options']}")
    need(prepared["new_links"] == {"organisation": ["Acme Ltd", "Bolt & Co"]},
         f"new linked records: {prepared['new_links']}")
    need(db.count("person") == 0 and db.count("organisation") == 0, "checking the rows changed the CRM")

    step("import")
    report = importing.run(db, "person", prepared)
    need(report == {"added": 2, "updated": 0, "skipped": 1, "links_created": 2, "options_added": 1},
         f"the import reported {report}")
    people = {r["data"]["first_name"]: r for r in db.records("person")}
    need(set(people) == {"Ann", "Bob"}, f"the people imported were {sorted(people)}")
    ann = people["Ann"]["data"]
    need(ann["email"] == "Ann@example.co.uk" and ann["mobile"] == "07700 900123"
         and ann["postcode"] == "CF48 4TQ" and ann["date_of_birth"] == "1990-11-13"
         and ann["value"] == 1250.5 and ann["status"] == "Lead", f"Ann was stored as {ann}")
    need(people["Ann"]["title"] == "Ann Example", "the imported record has the wrong name")
    need(db.lookup("link", ann["organisation"]) == "Acme Ltd", "the linked organisation was not made")
    need(people["Bob"]["data"]["date_of_birth"] == "1985-02-01" and
         people["Bob"]["data"]["status"] == "Partner", "Bob was stored wrongly")
    status = bpm.get_field(bpm.get_type(db.blueprint, "person"), "status")
    need(status["options"] == ["Lead", "Customer", "Partner"], "the new option was not added to the field")
    need(db.count("organisation") == 2, "the wrong number of organisations was made")

    step("import again, updating by email")
    path2 = os.path.join(tmp, "update.tsv")
    with open(path2, "w", encoding="utf-8", newline="") as fh:
        fh.write("Forename\tE-mail\tMobile phone\nAnn\tann@example.co.uk\t07700 900999\n"
                 "Dee\tdee@example.org\t\n")
    _mapping, _prepared, report = _import_file(db, path2, options={"match_field": "email"})
    need(report["updated"] == 1 and report["added"] == 1, f"the second import reported {report}")
    need(db.count("person") == 3, "the update made a duplicate")
    need(db.get_record(people["Ann"]["id"])["data"]["mobile"] == "07700 900999",
         "the matching record was not updated")
    return "4 rows read, 2 added, 1 bad row skipped, links and options created, update by email"


@_stepped
def _xlsx_import(step, tmp: str, opened: list):
    try:
        import openpyxl
    except Exception:  # noqa: BLE001
        raise _Skip("skipped - openpyxl is not installed")
    step("write a workbook")
    path = os.path.join(tmp, "people.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "People"
    ws.append([])
    ws.append(["First name", "Last name", "Email", "Date of birth", "Value", "Postcode"])
    ws.append(["Eve", "Excel", "eve@example.org", _dt.date(1990, 11, 13), 1250.5, "cf48 4tq"])
    ws.append(["Fay", "Figures", None, _dt.datetime(1985, 2, 1), 42, None])
    wb.create_sheet("Other").append(["Nothing", "here"])
    wb.save(path)
    wb.close()

    step("read it back")
    need(sheets.have_excel(), "openpyxl is installed but not found by the app")
    need(sheets.sheet_names(path) == ["People", "Other"], "the sheet names came back wrong")
    headers, rows, numbers = sheets.read_table(path, "People", with_numbers=True)
    need(headers == ["First name", "Last name", "Email", "Date of birth", "Value", "Postcode"],
         f"the headings were read as {headers}")
    need(rows == [["Eve", "Excel", "eve@example.org", "13/11/1990", "1250.5", "cf48 4tq"],
                  ["Fay", "Figures", "", "01/02/1985", "42", ""]], f"the rows were read as {rows}")
    need(numbers == [3, 4], f"the row numbers were {numbers}")
    need(sheets.read_table(path, "Other")[0] == ["Nothing", "here"], "the second sheet was not read")

    step("import it")
    db, _code = dbm.Database.create(os.path.join(tmp, "import-xlsx.crm"), _small_design("Excel test"))
    opened.append(db)
    _mapping, _prepared, report = _import_file(db, path, "People")
    need(report["added"] == 2 and report["skipped"] == 0, f"the import reported {report}")
    eve = [r for r in db.records("person") if r["data"]["first_name"] == "Eve"][0]["data"]
    need(eve["date_of_birth"] == "1990-11-13" and eve["value"] == 1250.5
         and eve["postcode"] == "CF48 4TQ", f"Eve was stored as {eve}")
    db.close()
    return "workbook written, 2 sheets read, dates and numbers kept, 2 rows imported"


# ------------------------------------------------------------------------ GUI
def _pump(root, ms: int = 40):
    """Let Tk process events (and anything a page scheduled) for a moment."""
    end = time.time() + ms / 1000.0
    root.update()
    while time.time() < end:
        root.update()
        time.sleep(0.004)


def _trouble(app, mark: int) -> list[str]:
    """Exceptions and error boxes the shell logged since test_log[mark]."""
    out = []
    for entry in app.test_log[mark:]:
        if not entry or entry[0] not in ("exception", "error"):
            continue
        text = str(entry[1]) if len(entry) > 1 else ""
        trace = str(entry[2]) if len(entry) > 2 else ""
        if entry[0] == "exception" and "Traceback" in trace:
            last = [ln for ln in trace.strip().splitlines() if ln.strip()]
            spot = [ln.strip() for ln in last if ln.strip().startswith("File ") and "crmbuilder" in ln]
            text = last[-1].strip()
            if spot:      # File "…/crmbuilder/ui/x.py", line 12, in fn
                try:
                    file_part, line_part = spot[-1].split(",")[:2]
                    name = os.path.basename(file_part.replace("\\", "/").split('"')[1])
                    text += f" ({name}{line_part})"
                except (IndexError, ValueError):
                    pass
        elif entry[0] == "error":
            text = f"error box: {text} / {trace}"
        out.append(text)
    return out


def _needs_arguments(klass) -> bool:
    """True when a page cannot be made with just (parent, app)."""
    try:
        params = list(inspect.signature(klass.__init__).parameters.values())[3:]
    except (TypeError, ValueError):
        return False
    return any(p.default is inspect.Parameter.empty and
               p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
               for p in params)


def _gui(rep: Report, tmp: str, opened: list, strict: bool):
    rep.section("The window")
    absent = rep.fail if strict else rep.note
    rep.doing = "starting Tk"
    try:
        import tkinter as tk
    except Exception as exc:  # noqa: BLE001
        absent("Window checks", f"skipped - tkinter is not available ({exc})")
        return
    try:
        root = tk.Tk()
    except Exception as exc:  # noqa: BLE001
        absent("Window checks", f"skipped - no window could be opened ({exc})")
        return
    try:
        _gui_tour(rep, root, tmp, opened)
    except Exception as exc:  # noqa: BLE001
        rep.fail("Window checks", f"stopped early: {type(exc).__name__}: {exc}{_where(exc)}")
    finally:
        # Never leave a window open. Pending timers are cancelled first: Tcl
        # would otherwise still fire them later, into a window that has gone.
        try:
            for job in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(job)
        except Exception:  # noqa: BLE001
            pass
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def _gui_tour(rep: Report, root, tmp: str, opened: list):
    rep.doing = "loading the window's modules"
    try:
        from .ui import app as appmod
    except Exception as exc:  # noqa: BLE001
        rep.fail("The shell (crmbuilder.ui.app)", f"{type(exc).__name__}: {exc}{_where(exc)}")
        return

    # Every module the registries point at must be present and must load. In a
    # built app a missing one means PyInstaller did not bundle it.
    registry = {}
    for kind, table in (("page", appmod.PAGES), ("screen", appmod.SCREENS)):
        for name, (module, cls) in table.items():
            registry[(kind, name)] = (module, cls)
    modules = sorted({m for m, _c in registry.values()} | {"tools", "board", "picker", "widgets", "styles"})
    usable = {}
    loaded = []
    for module in modules:
        rep.doing = f"loading crmbuilder.ui.{module}"
        full = f"crmbuilder.ui.{module}"
        users = [f"{k} '{n}'" for (k, n), (m, _c) in registry.items() if m == module]
        what = f"crmbuilder.ui.{module}" + (f" ({', '.join(users)})" if users else "")
        try:
            found = importlib.util.find_spec(full) is not None
        except Exception:  # noqa: BLE001
            found = False
        if not found:
            rep.fail(what, "this part of the app is missing")
            continue
        try:
            usable[module] = importlib.import_module(full)
            loaded.append(module)
        except Exception as exc:  # noqa: BLE001
            rep.fail(what, f"does not load: {type(exc).__name__}: {exc}{_where(exc)}")
    classes = {}
    for (kind, name), (module, cls) in registry.items():
        if module not in usable:
            continue
        klass = getattr(usable[module], cls, None)
        if klass is None:
            rep.fail(f"{kind} '{name}'", f"crmbuilder.ui.{module} has no {cls}")
        else:
            classes[(kind, name)] = klass
    rep.ok(f"{len(loaded)} of {len(modules)} window modules load", " ".join(loaded))

    rep.doing = "opening the window"
    app = appmod.App(root, testing=True)
    _pump(root, 80)
    rep.ok(f"Tk {root.tk.call('info', 'patchlevel')} window opened",
           f"{root.winfo_screenwidth()}x{root.winfo_screenheight()} screen, "
           f"scaling {float(root.tk.call('tk', 'scaling')):.2f}, font {app.c.get('ui')}")

    def attempt(label: str, action) -> bool:
        """Run one window action; anything raised or logged by the shell is a failure."""
        rep.doing = label
        mark = len(app.test_log)
        try:
            result = action()
            _pump(root)
        except Exception as exc:  # noqa: BLE001
            rep.fail(label, f"{type(exc).__name__}: {exc}{_where(exc)}")
            return False
        bad = _trouble(app, mark)
        if bad:
            rep.fail(label, "; ".join(bad[:3]))
            return False
        if result is False:
            rep.fail(label, "it did not open")
            return False
        return True

    def theme() -> str:
        return "dark" if app.dark else "light"

    # ---- screens shown before a CRM is open
    locked = None
    try:
        made, _code = dbm.Database.create(os.path.join(tmp, "gui-login.crm"), _small_design("Sign-in test"),
                                          password=SECRET_PW)
        opened.append(made)
        made.close()
        locked = dbm.Database.open(os.path.join(tmp, "gui-login.crm"))
        opened.append(locked)
    except Exception as exc:  # noqa: BLE001
        rep.fail("screen 'login'", f"could not make a password-protected CRM: {exc}")
    shown = []
    for name in appmod.SCREENS:
        if ("screen", name) not in classes:
            continue
        kw = {}
        if name == "login":
            if locked is None:
                continue
            kw = {"db": locked}
        fine = True
        for _pass in (0, 1):
            label = f"screen '{name}' ({theme()})"

            def show(name=name, kw=kw):
                if _pass == 0:
                    app.show_screen(name, **kw)
                else:
                    app.toggle_dark()
                need(app.screen is not None and app.screen.screen_name == name,
                     "a different screen is showing")
                app.screen.state()
            fine = attempt(label, show) and fine
        if fine:
            shown.append(name)
    if shown:
        rep.ok(f"{len(shown)} start screens open in dark and light", " ".join(shown))
    attempt("back to the welcome screen", lambda: app.show_screen("welcome"))
    if locked is not None:
        locked.close()

    # ---- the pages, for two templates
    keys = [t["key"] for t in templates.TEMPLATES]
    # the first template, and of the others the one with the most fields (the biggest forms)
    rest = sorted(templates.TEMPLATES[1:], key=lambda t: -sum(len(x["fields"]) for x in t["types"]))
    chosen = keys[:1] + [t["key"] for t in rest[:1]]
    for key in chosen:
        rep.doing = f"window: making the {key} CRM"
        try:
            db, _code = dbm.Database.create(os.path.join(tmp, f"gui-{key}.crm"),
                                            templates.build(key, f"Self-test {key}"))
            opened.append(db)
            templates.load_samples(db, key)
        except Exception as exc:  # noqa: BLE001
            rep.fail(f"window: {key}", f"could not make the CRM: {exc}")
            continue
        entered = attempt(f"window: opening the '{key}' CRM", lambda db=db: app.enter(db))
        if app.db is not db or app.content is None:
            if entered:
                rep.fail(f"window: opening the '{key}' CRM", "the main window was not built")
            continue
        for _pass in (0, 1):
            _tour(rep, app, attempt, classes, key, theme())
            attempt(f"window: '{key}' switching to {'light' if app.dark else 'dark'}", app.toggle_dark)
    rep.doing = "closing the window"
    if app.db is not None:
        app.db.close()


def _tour(rep: Report, app, attempt, classes: dict, key: str, theme: str):
    """Open every page there is on the CRM that is open now."""
    db = app.db
    where = f"window: {key}, {theme}"
    total = failed = 0
    names = []

    def visit(label: str, name: str, **kw) -> bool:
        nonlocal total, failed
        total += 1

        def go():
            if app.go(name, **kw) is False:
                return False
            need(app.page is not None and app.page_name == name, "a different page is showing")
            if kw.get("mode") == "board":
                need(getattr(app.page, "mode", "") == "board", "the board did not show")
            state = app.page.state()
            need(isinstance(state, dict), "state() did not return a dict")
            app.page.refresh()
            return True
        fine = attempt(f"{where}: {label}", go)
        if not fine:
            failed += 1
        return fine

    for name in app_pages(classes):
        if name in ("list", "record"):
            continue
        if _needs_arguments(classes[("page", name)]):
            rep.note(f"{where}: page '{name}'", "skipped - it needs to be told what to show")
            continue
        if visit(f"page '{name}'", name):
            names.append(name)
    lists = boards = records = 0
    types = bpm.active_types(db.blueprint)
    if ("page", "list") in classes:
        for t in types:
            lists += visit(f"list '{t['plural']}'", "list", type_key=t["key"])
            if bpm.board_field(t):
                boards += visit(f"board '{t['plural']}'", "list", type_key=t["key"], mode="board")
    if ("page", "record") in classes:
        for t in types:
            rows = db.records(t["key"])
            if rows:
                records += visit(f"record '{rows[0]['ref']}'", "record", record_id=rows[0]["id"])
            if db.can_edit:
                records += visit(f"new {t['name'].lower()}", "record", type_key=t["key"], preset={})
    summary = (f"pages {' '.join(names) or '(none)'}; lists {lists}, boards {boards}, "
               f"record forms {records}")
    if failed:
        rep.note(where, f"{total - failed} of {total} opened: {summary}")
    else:
        rep.ok(where, f"all {total} opened: {summary}")


def app_pages(classes: dict) -> list[str]:
    return [name for (kind, name) in classes if kind == "page"]


# ---------------------------------------------------------------------- main
_running: Report | None = None


def run_selftest(gui: bool = True, expect_encryption: bool = False,
                 strict: bool | None = None) -> tuple[bool, str]:
    """Run every check. Returns (everything passed, the report as text).

    gui=False skips the window checks. strict (default: only in a built app)
    turns a missing Tk or openpyxl from a note into a failure - a built app
    must have both. expect_encryption does the same for the encryption module.
    """
    global _running
    if strict is None:
        strict = bool(getattr(sys, "frozen", False))
    rep = Report()
    _running = rep
    opened: list = []
    tmp = tempfile.mkdtemp(prefix="crm-builder-selftest-")
    try:
        _versions(rep, strict, expect_encryption)

        rep.section("Checking what is typed")
        rep.check("Dates, phone numbers, postcodes, NI numbers, emails", _validation)

        rep.section("Templates (a CRM is made from each and used)")
        if not templates.TEMPLATES:
            rep.fail("Templates", "there are none")
        for tpl in templates.TEMPLATES:
            rep.check(f"Template '{tpl['key']}'", _template, tpl, tmp, opened)

        rep.section("People, passwords and encryption")
        rep.check("Team CRM: accounts, sign-in and permissions", _users, tmp, opened)
        rep.check("Encrypted CRM", _encryption, tmp, opened)

        rep.section("Importing")
        rep.check("CSV import", _csv_import, tmp, opened)
        rep.check("Excel import", _xlsx_import, tmp, opened)

        if gui:
            _gui(rep, tmp, opened, strict)
        else:
            rep.section("The window")
            rep.note("Window checks", "skipped (--no-gui)")
    except Exception as exc:  # noqa: BLE001 - the report must always be produced
        rep.fail("Self-test", f"stopped early: {type(exc).__name__}: {exc}{_where(exc)}")
    finally:
        rep.doing = "tidying up"
        for db in opened:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(tmp):
            rep.section("Tidying up")
            rep.note("Temporary folder", f"could not be removed: {tmp}")
        _running = None
    return rep.failures == 0, rep.text()


def write_report(text: str) -> str | None:
    """Beside the app; if that folder cannot be written to, the settings folder."""
    for folder in (paths.app_dir(), os.path.dirname(paths.settings_path())):
        path = os.path.join(folder, REPORT_NAME)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            return path
        except OSError:
            continue
    return None


def _timed_out():
    """Called on a timer thread when the self-test has hung (for instance a
    dialog waiting for a click in a build with no one watching). Say so in the
    report and stop the process, so the build script is not left waiting."""
    rep = _running
    if rep is None:
        return
    rep.fail("Self-test", f"timed out after {TIMEOUT_SECONDS} seconds while: {rep.doing}")
    text = rep.text()
    try:
        write_report(text)
        print(text)
        sys.stdout.flush()
    finally:
        os._exit(1)


def main(argv: list[str] | None = None) -> int:
    argv = [a.lower() for a in (argv or [])]
    timer = threading.Timer(TIMEOUT_SECONDS, _timed_out)
    timer.daemon = True
    timer.start()
    try:
        ok, text = run_selftest(gui="--no-gui" not in argv,
                                expect_encryption="--expect-encryption" in argv)
    finally:
        timer.cancel()
    print(text)
    where = write_report(text)
    print(f"Report written to {where}" if where else "The report could not be written to a file.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
