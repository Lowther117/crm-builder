"""The database: one file holding the design, the records and everything on them.

SQLite (or SQLCipher when encryption is on). Records keep their values as JSON
so the design can change without rebuilding tables. Safe on a shared network
drive for a small team: short transactions, a busy timeout, rollback journal.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import unicodedata

from . import blueprint as bpm
from . import security, validate

SCHEMA_VERSION = 2
ROLES = {"admin": "Administrator", "editor": "Editor", "viewer": "Read only"}
NOTE_KINDS = ["Note", "Call", "Email", "Meeting"]
MAX_FAILED = 5
LOCK_MINUTES = 15
BACKUPS_KEPT = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, name TEXT, role TEXT,
  pw TEXT, active INTEGER DEFAULT 1, failed INTEGER DEFAULT 0, locked_until TEXT,
  created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY, type TEXT, seq INTEGER, ref TEXT, title TEXT, data TEXT,
  search TEXT, example INTEGER DEFAULT 0, created_at TEXT, created_by INTEGER,
  updated_at TEXT, updated_by INTEGER, deleted_at TEXT, deleted_by INTEGER);
CREATE INDEX IF NOT EXISTS records_type ON records(type, deleted_at);
CREATE TABLE IF NOT EXISTS links(src INTEGER, field TEXT, dst INTEGER);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst);
CREATE INDEX IF NOT EXISTS links_src ON links(src);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, record_id INTEGER, kind TEXT, body TEXT, at TEXT,
  created_at TEXT, created_by INTEGER, edited_at TEXT);
CREATE INDEX IF NOT EXISTS notes_record ON notes(record_id);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, record_id INTEGER, title TEXT, detail TEXT, due TEXT,
  assigned_to INTEGER, done_at TEXT, done_by INTEGER, created_at TEXT, created_by INTEGER);
CREATE INDEX IF NOT EXISTS tasks_record ON tasks(record_id);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, record_id INTEGER, name TEXT, size INTEGER, data BLOB,
  added_at TEXT, added_by INTEGER);
CREATE INDEX IF NOT EXISTS files_record ON files(record_id);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, at TEXT, user_id INTEGER, user_name TEXT, action TEXT,
  record_id INTEGER, type TEXT, ref TEXT, field TEXT, old TEXT, new TEXT);
CREATE INDEX IF NOT EXISTS audit_record ON audit(record_id);
CREATE TABLE IF NOT EXISTS views(
  id INTEGER PRIMARY KEY, type TEXT, name TEXT, spec TEXT, user_id INTEGER);
"""


class DBError(Exception):
    """Something the person should be told about, in plain English."""


class LoginError(DBError):
    pass


class Conflict(DBError):
    """Someone else changed the same fields. .theirs = {field key: their value}."""

    def __init__(self, theirs: dict, who: str):
        super().__init__("Someone else changed this record while you had it open.")
        self.theirs = theirs
        self.who = who


def sqlcipher_module():
    try:
        from sqlcipher3 import dbapi2  # type: ignore
        return dbapi2
    except Exception:
        try:
            from pysqlcipher3 import dbapi2  # type: ignore
            return dbapi2
        except Exception:
            return None


def is_encrypted(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return len(head) == 16 and head != b"SQLite format 3\x00"


def today_iso() -> str:
    return _dt.date.today().isoformat()


def _db_errors() -> tuple:
    """The exception classes the database engine raises (SQLCipher has its own)."""
    mod = sqlcipher_module()
    return (sqlite3.Error, mod.Error) if mod is not None else (sqlite3.Error,)


def _ulower(value):
    """lower() for SQL that knows every alphabet (SQLite's own only knows a-z)."""
    return value.lower() if isinstance(value, str) else value


def _like(word: str) -> str:
    """A LIKE pattern (ESCAPE '\\') that finds the word anywhere, taken literally."""
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _only_adds_options(old: dict, new: dict) -> bool:
    """True when the new design is the old one with, at most, extra choices
    added to the end of choice fields."""
    a, b = json.loads(json.dumps(old)), json.loads(json.dumps(new))
    if len(a.get("types") or []) != len(b.get("types") or []):
        return False
    for ta, tb in zip(a.get("types") or [], b.get("types") or []):
        fa, fb = ta.get("fields") or [], tb.get("fields") or []
        if len(fa) != len(fb):
            return False
        for f, g in zip(fa, fb):
            have, want = f.pop("options", None) or [], g.pop("options", None) or []
            if want[:len(have)] != have:
                return False
    return a == b


class Database:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.conn = None
        self.encrypted = False
        self._key: bytes | None = None
        self.keyfile: security.KeyFile | None = None
        self.blueprint: dict = bpm.empty()
        self.user: dict | None = None
        self.mode = "solo"
        self._titles: dict[int, str] = {}
        self._users: dict[int, dict] | None = None
        self._seen_version = None       # data_version the two caches above were filled at
        self._keys_before = None        # (key file as it was,) while a change to it is open
        self._bp_stamp = None

    # ------------------------------------------------------------ opening
    @classmethod
    def create(cls, path: str, bp: dict, mode: str = "solo", name: str = "Me",
               username: str = "me", password: str | None = None,
               encrypt: bool = False) -> tuple["Database", str | None]:
        """Make a new database file. Returns (db signed in as its first
        administrator, recovery code or None)."""
        path = os.path.abspath(path)
        if os.path.exists(path):
            raise DBError("A file with that name already exists there. Choose another name.")
        if encrypt and not password:
            raise DBError("Encryption needs a password.")
        if encrypt and sqlcipher_module() is None:
            raise DBError("Encryption is not available in this copy of the app.")
        if mode == "team" and not password:
            raise DBError("A team CRM needs a password for the administrator.")
        db = cls(path)
        db.mode = mode
        recovery = None
        try:
            if encrypt:
                db.encrypted = True
                db._key = security.new_db_key()
                kf = security.KeyFile(path)
                kf.data["mode"] = mode
                kf.set_user(username, password, db._key)
                recovery = kf.set_recovery(db._key)
                db.keyfile = kf
            db._connect()
            db.conn.executescript(SCHEMA)
            now = validate.now_stamp()
            with db.tx():
                for k, v in (("schema", str(SCHEMA_VERSION)), ("mode", mode),
                             ("created_at", now), ("idle_minutes", "15")):
                    db.conn.execute("INSERT INTO meta VALUES(?,?)", (k, v))
                db.conn.execute(
                    "INSERT INTO users(username,name,role,pw,created_at) VALUES(?,?,?,?,?)",
                    (username, name, "admin",
                     security.hash_password(password) if password else None, now))
            if encrypt:
                db.keyfile.save()
            db._load_user(username)
            db.save_blueprint(bpm.clean(bp), note="Created")
            return db, recovery
        except Exception as exc:
            db.close()
            for p in (path, path + ".keys"):
                with contextlib.suppress(OSError):
                    os.remove(p)
            if isinstance(exc, (OSError,) + _db_errors()):
                raise DBError("The CRM could not be made there. Check that the folder exists "
                              "and that you are allowed to save in it, or choose another "
                              f"place.\n\n({exc})") from exc
            raise

    @classmethod
    def open(cls, path: str) -> "Database":
        """Open a file. Check .login_needed() next; sign in with .login()."""
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise DBError("That file cannot be found. It may have been moved or renamed.")
        db = cls(path)
        if is_encrypted(path):
            db.encrypted = True
            if not os.path.exists(path + ".keys"):
                # (anything that is not a plain database looks "encrypted")
                raise DBError("This is not a CRM Builder file – or it is an encrypted CRM "
                              "whose key file is missing.\n\n"
                              f"If it is an encrypted CRM, put “{os.path.basename(path)}.keys” "
                              "back in the same folder as the CRM file. Without it the file "
                              "cannot be opened.")
            if sqlcipher_module() is None:
                raise DBError("This CRM is encrypted, and this copy of the app was built "
                              "without encryption support. Rebuild the app with the build "
                              "script, which installs it.")
            db.keyfile = security.KeyFile.load(path)
            if db.keyfile is None:
                raise DBError("This CRM is encrypted, but its key file is missing or cannot "
                              "be read.\n\n"
                              f"Put a good copy of “{os.path.basename(path)}.keys” (there is "
                              "one beside each backup) back in the same folder as the CRM "
                              "file. Without it the file cannot be opened.")
            db.mode = db.keyfile.mode
            return db
        try:
            db._connect()
            db._after_connect()
        except DBError:
            db.close()
            raise
        except sqlite3.DatabaseError as exc:
            db.close()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DBError("This CRM is busy: someone else is saving a lot at once. "
                              "Wait a few seconds and open it again.") from exc
            raise DBError(f"This is not a CRM Builder file, or it is damaged.\n\n({exc})")
        return db

    def _connect(self):
        if self.encrypted:
            mod = sqlcipher_module()
            self.conn = mod.connect(self.path, timeout=10, isolation_level=None)
            self.conn.execute(f"PRAGMA key = \"x'{self._key.hex()}'\"")
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        else:
            self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row if not self.encrypted else sqlcipher_module().Row
        self.conn.create_function("ulower", 1, _ulower)
        self.conn.execute("PRAGMA busy_timeout = 10000")
        self.conn.execute("PRAGMA journal_mode = DELETE")
        self.conn.execute("PRAGMA synchronous = FULL")

    def _after_connect(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"meta", "users", "records"} <= tables:
            raise DBError("This is not a CRM Builder file (or it was made by version 1, "
                          "which this version cannot open).")
        # Nothing is written to a file until it is known to be one of ours.
        schema = str(self.get_meta("schema") or "")
        if not schema.isdigit():
            raise DBError("This is not a CRM Builder file (or it was made by version 1, "
                          "which this version cannot open).")
        if int(schema) > SCHEMA_VERSION:
            raise DBError("This CRM was made with a newer version of CRM Builder. "
                          "Update the app to open it.")
        self.conn.executescript(SCHEMA)
        self.mode = self.get_meta("mode", "solo")
        self._load_blueprint()

    def close(self):
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        self.conn = None

    @contextlib.contextmanager
    def tx(self):
        """A write transaction. Nested use joins the outer one."""
        if self.conn.in_transaction:
            yield
            return
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except Exception as exc:
            # (SQLCipher raises its own errors, not sqlite3's)
            if not isinstance(exc, _db_errors()):
                raise
            raise DBError(self._explain(exc)) from exc
        try:
            yield
            self.conn.execute("COMMIT")
            self._keys_before = None
        except BaseException as exc:
            # Never leave it open: an open transaction would take in every
            # later save and none of them would ever reach the file.
            with contextlib.suppress(Exception):
                self.conn.execute("ROLLBACK")
            self._forget()
            self._restore_keys()
            if isinstance(exc, _db_errors()):
                raise DBError(self._explain(exc)) from exc
            raise

    def _explain(self, exc) -> str:
        """Why a save failed, for the person at the keyboard."""
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return "Someone else is saving at the moment. Wait a few seconds and try again."
        if not os.path.exists(self.path):
            return ("The CRM file can no longer be reached: it has been moved or renamed, or "
                    "the drive or network connection has dropped. Nothing was saved. Close "
                    "the CRM and open it again.")
        if "readonly" in text or "read-only" in text or "unable to open" in text:
            return ("Nothing was saved: this CRM cannot be changed from here. The file or its "
                    "folder is read only, or you are not allowed to save in that folder.")
        if "full" in text:
            return "Nothing was saved: the disk the CRM is on is full."
        if "malformed" in text or "not a database" in text or "corrupt" in text:
            return ("Nothing was saved: the CRM file is damaged. Close it and open the newest "
                    "copy from its backups folder instead.")
        return f"Nothing was saved: the CRM file could not be written to.\n\n({exc})"

    def _forget(self) -> None:
        """After a rollback: drop what was remembered from the abandoned changes."""
        self._titles.clear()
        self._users = None
        with contextlib.suppress(Exception):
            if self.get_meta("blueprint_at") != self._bp_stamp:
                self._load_blueprint()

    # ------------------------------------------------------------ signing in
    def login_needed(self) -> str:
        """'none' (open straight away), 'password' (one person, has a password)
        or 'user' (a team: username and password)."""
        if self.mode == "team":
            return "user"
        if self.encrypted:
            return "password"
        row = self.conn.execute("SELECT pw FROM users ORDER BY id LIMIT 1").fetchone()
        return "password" if row and row["pw"] else "none"

    def solo_username(self) -> str:
        if self.encrypted and self.conn is None:
            names = self.keyfile.usernames()
            return names[0] if names else "me"
        row = self.conn.execute("SELECT username FROM users ORDER BY id LIMIT 1").fetchone()
        return row["username"] if row else "me"

    def login(self, username: str | None = None, password: str = "") -> dict:
        username = (username or "").strip() or self.solo_username()
        if self.encrypted and self.conn is None:
            self._unlock_file(username, password)
            try:
                return self._login(username, password)
            except LoginError:
                # nothing stays unlocked for someone who is not signed in
                self.close()
                self._key = None
                raise
        return self._login(username, password)

    def _unlock_file(self, username: str, password: str) -> None:
        """Encrypted file, not open yet: get the key with this person's password.
        Wrong passwords are counted in the key file (the database cannot be
        written to until it is unlocked)."""
        self.keyfile = security.KeyFile.load(self.path) or self.keyfile
        kf = self.keyfile
        self.mode = kf.mode
        now = validate.now_stamp()
        if kf.locked(username, now):
            raise LoginError(f"Too many wrong passwords. This account is locked for "
                             f"{LOCK_MINUTES} minutes; an administrator can unlock it sooner.")
        key = kf.unlock(username, password)
        if key is None:
            if kf.has_user(username):
                until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
                    minutes=LOCK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
                # Checking a password takes a moment, and an administrator may
                # have changed the key file meanwhile: count in the newest copy
                # so that their change is not undone by saving this one.
                kf = self.keyfile = security.KeyFile.load(self.path) or kf
                kf.note_failure(username, MAX_FAILED, until)
                with contextlib.suppress(OSError):
                    kf.save()
            raise LoginError("That username or password is not right."
                             if self.mode == "team" else "That password is not right.")
        self._key = key
        try:
            self._connect()
            self._after_connect()
        except DBError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise DBError(f"The file could not be unlocked.\n\n({exc})")
        wrong = kf.clear_failures(username)
        if wrong:
            with contextlib.suppress(Exception):
                kf = self.keyfile = security.KeyFile.load(self.path) or kf    # (newest, as above)
                kf.clear_failures(username)
                kf.save()
                row = self._user_row(username)
                if row:
                    with self.tx():
                        self._audit("signin-failed", None, "", "", user=(row["id"], row["name"]),
                                    new=f"{wrong} wrong password{'s' if wrong != 1 else ''} "
                                        "before this sign-in")

    def _user_row(self, username: str):
        row = self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None and not username.isascii():
            # SQLite only ignores the case of plain a-z
            for r in self.conn.execute("SELECT * FROM users").fetchall():
                if r["username"].lower() == username.lower():
                    return r
        return row

    def _login(self, username: str, password: str) -> dict:
        row = self._user_row(username)
        generic = ("That username or password is not right." if self.mode == "team"
                   else "That password is not right.")
        if row is None or not row["active"]:
            raise LoginError(generic)
        now = validate.now_stamp()
        if row["locked_until"] and row["locked_until"] > now:
            raise LoginError(f"Too many wrong passwords. This account is locked for "
                             f"{LOCK_MINUTES} minutes; an administrator can unlock it sooner.")
        if row["pw"]:
            if not security.verify_password(password, row["pw"]):
                failed = (row["failed"] or 0) + 1
                until = None
                if failed >= MAX_FAILED:
                    until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
                        minutes=LOCK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
                    failed = 0
                with contextlib.suppress(Exception), self.tx():
                    self.conn.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?",
                                      (failed, until, row["id"]))
                    self._audit("signin-failed", None, "", "", user=(row["id"], row["name"]))
                raise LoginError(generic)
        with contextlib.suppress(Exception), self.tx():
            self.conn.execute("UPDATE users SET failed=0, locked_until=NULL, last_login=? "
                              "WHERE id=?", (now, row["id"]))
        self._load_user(row["username"])
        return self.user

    def recover(self, code: str, username: str, new_password: str) -> None:
        """Unlock an encrypted file with the recovery code and set a new password."""
        if not (self.encrypted and self.keyfile):
            raise DBError("This CRM is not encrypted, so there is no recovery code.")
        self.keyfile = security.KeyFile.load(self.path) or self.keyfile
        key = self.keyfile.unlock_recovery(code)
        if key is None:
            raise LoginError("That recovery code is not right.")
        opened_here = self.conn is None
        self._key = key
        try:
            if opened_here:
                self._connect()
                self._after_connect()
            username = (username or "").strip() or self.solo_username()
            row = self._user_row(username)
            if row is None:
                raise LoginError("There is no account with that username.")
            problem = security.password_problem(new_password, row["username"])
            if problem:
                raise DBError(problem)
            with self.tx():
                self.conn.execute("UPDATE users SET pw=?, failed=0, locked_until=NULL, active=1 "
                                  "WHERE id=?", (security.hash_password(new_password), row["id"]))
                self._audit("password-recovered", None, "", "", user=(row["id"], row["name"]),
                            new=row["name"])
                self._edit_keys(lambda kf: kf.set_user(row["username"], new_password, key))
        except Exception:
            if opened_here:
                self.close()
                self._key = None
            raise
        self._load_user(row["username"])

    def check_password(self, password: str) -> bool:
        row = self.conn.execute("SELECT pw FROM users WHERE id=?", (self.user["id"],)).fetchone()
        if not row or not row["pw"]:
            return True
        if security.verify_password(password, row["pw"]):
            return True
        # (the lock screen asks here: wrong guesses belong in the history too)
        with contextlib.suppress(Exception), self.tx():
            self._audit("signin-failed", None, "", "")
        return False

    def _load_user(self, username: str):
        row = self._user_row(username)
        self.user = {"id": row["id"], "username": row["username"], "name": row["name"],
                     "role": row["role"]}
        self._users = None

    @property
    def can_edit(self) -> bool:
        return bool(self.user) and self.user["role"] in ("admin", "editor")

    @property
    def is_admin(self) -> bool:
        return bool(self.user) and self.user["role"] == "admin"

    @property
    def has_password(self) -> bool:
        row = self.conn.execute("SELECT pw FROM users WHERE id=?", (self.user["id"],)).fetchone()
        return bool(row and row["pw"])

    # --------------------------------------------------------------- meta
    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value) -> None:
        with self.tx():
            self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, None if value is None else str(value)))

    @property
    def name(self) -> str:
        return self.blueprint.get("name") or "My CRM"

    def data_version(self) -> int:
        """Changes whenever another person's copy of the app writes to the file."""
        return self.conn.execute("PRAGMA data_version").fetchone()[0]

    # ------------------------------------------------------------- design
    def _load_blueprint(self):
        raw = self.get_meta("blueprint")
        self._bp_stamp = self.get_meta("blueprint_at")
        try:
            self.blueprint = bpm.clean(json.loads(raw)) if raw else bpm.empty()
        except ValueError:
            self.blueprint = bpm.empty()

    def refresh_design(self) -> bool:
        """Pick up a design change made by someone else. True if it changed."""
        if self.get_meta("blueprint_at") != self._bp_stamp:
            self._load_blueprint()
            self._titles.clear()
            return True
        return False

    def save_blueprint(self, bp: dict, note: str = "Design changed") -> None:
        bp = bpm.clean(bp)
        if self.user is not None:
            self._need_edit()
            # The design belongs to administrators. The one thing an editor's
            # work may do to it is add choices to a field (an import does).
            if not self.is_admin and not _only_adds_options(self.blueprint, bp):
                raise DBError("Only an administrator can change the design.")
        with self.tx():
            if self.user is not None and not self.is_admin:
                # ...and that is judged against the design in the file: this
                # copy of the app may not have seen an administrator's latest
                # change yet, and saving over it would undo it.
                try:
                    now = bpm.clean(json.loads(self.get_meta("blueprint") or "{}"))
                except ValueError:
                    now = bpm.empty()
                if not _only_adds_options(now, bp):
                    raise DBError("The design has just been changed by someone else, so "
                                  "nothing was saved. Try again.")
            self.set_meta("blueprint", json.dumps(bp))
            # (the random part tells apart two changes made in the same second)
            self.set_meta("blueprint_at", validate.now_stamp() + "." + os.urandom(4).hex())
            self._audit("design", None, "", "", new=note)
            self.blueprint = bp
            self._bp_stamp = self.get_meta("blueprint_at")
            self._retitle_all()

    def _retitle_all(self):
        """Recompute titles and search text after a design change (two passes so
        titles built from links pick up their targets' new titles)."""
        for _ in range(4):      # (until nothing changes; titles can be built from titles)
            self._titles.clear()
            changed = False
            rows = self.conn.execute("SELECT id,type,ref,data,title,search FROM records").fetchall()
            for r in rows:
                t = bpm.get_type(self.blueprint, r["type"])
                if t is None:
                    continue
                data = json.loads(r["data"] or "{}")
                title = bpm.title_for(t, data, self.lookup) or r["ref"]
                search = self._search_text(t, r["ref"], title, data)
                if title != r["title"] or search != r["search"]:
                    self.conn.execute("UPDATE records SET title=?, search=? WHERE id=?",
                                      (title, search, r["id"]))
                    changed = True
            if not changed:
                break
        self._titles.clear()

    # -------------------------------------------------------------- users
    def users(self, active_only: bool = False) -> list[dict]:
        rows = self.conn.execute("SELECT id,username,name,role,active,locked_until,last_login,"
                                 "pw IS NOT NULL AS has_pw FROM users ORDER BY name COLLATE NOCASE")
        out = [dict(r) for r in rows]
        return [u for u in out if u["active"]] if active_only else out

    def _fresh(self) -> None:
        """Forget remembered titles and names once someone else has saved
        anything, so that what is shown is never older than the file."""
        version = self.conn.execute("PRAGMA data_version").fetchone()[0]
        if version != self._seen_version:
            self._seen_version = version
            self._titles.clear()
            self._users = None

    def _user_map(self) -> dict[int, dict]:
        self._fresh()
        if self._users is None:
            self._users = {u["id"]: u for u in self.users()}
        return self._users

    def user_name(self, uid) -> str:
        u = self._user_map().get(uid)
        return u["name"] if u else ""

    def refresh_account(self) -> bool:
        """Pick up a change an administrator made to the signed-in account (its
        role, or switching it off) since it signed in. False = switched off."""
        if not self.user or self.conn is None:
            return False
        row = self.conn.execute("SELECT name, role, active FROM users WHERE id=?",
                                (self.user["id"],)).fetchone()
        if row is None or not row["active"]:
            self.user["role"] = "off"
            return False
        self.user["role"], self.user["name"] = row["role"], row["name"]
        return True

    def _need_admin(self):
        if not self.refresh_account():
            raise DBError("Your account has been switched off. Speak to an administrator.")
        if not self.is_admin:
            raise DBError("Only an administrator can do that.")

    def _need_edit(self):
        if not self.refresh_account():
            raise DBError("Your account has been switched off. Speak to an administrator.")
        if not self.can_edit:
            raise DBError("Your account is read only.")

    def _edit_keys(self, change) -> None:
        """Change the key file of an encrypted CRM: change(keyfile) is applied to
        the newest copy on disk (other people may have changed it since this
        copy of the app read it) and saved. Call inside tx(), last, so that the
        database and the key file change together or not at all."""
        if not self.keyfile:
            return
        if self._keys_before is None:
            try:
                with open(self.keyfile.path, "rb") as fh:
                    self._keys_before = (fh.read(),)
            except OSError:
                self._keys_before = (None,)
        self.keyfile = security.KeyFile.load(self.path) or self.keyfile
        change(self.keyfile)
        try:
            self.keyfile.save()
        except OSError as exc:
            raise DBError("The key file beside the CRM could not be saved, so nothing was "
                          f"changed.\n\n({exc})") from exc

    def _restore_keys(self) -> None:
        """The database change was abandoned, so put the key file back as it was:
        a password the key file accepts and the database does not (or the other
        way round) would lock that person out."""
        before, self._keys_before = self._keys_before, None
        if not before or before[0] is None or not self.keyfile:
            return
        with contextlib.suppress(OSError):
            tmp = self.keyfile.path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(before[0])
            os.replace(tmp, self.keyfile.path)
        self.keyfile = security.KeyFile.load(self.path) or self.keyfile

    def add_user(self, username: str, name: str, role: str, password: str) -> int:
        self._need_admin()
        username, name = username.strip(), name.strip()
        if not username or not name:
            raise DBError("Give a username and a name.")
        if role not in ROLES:
            raise DBError("Choose a role.")
        problem = security.password_problem(password, username)
        if problem:
            raise DBError(problem)
        if any(u["username"].lower() == username.lower() for u in self.users()):
            raise DBError("That username is already taken.")
        if not self.has_password:
            # otherwise the first account could be signed in to with any password
            raise DBError("Set a password for yourself first. Once other people use "
                          "this CRM, everyone signs in.")
        was = self.mode
        try:
            with self.tx():
                cur = self.conn.execute(
                    "INSERT INTO users(username,name,role,pw,created_at) VALUES(?,?,?,?,?)",
                    (username, name, role, security.hash_password(password), validate.now_stamp()))
                self._audit("user-added", None, "", "", new=f"{name} ({ROLES[role]})")
                if self.mode != "team":
                    self.mode = "team"
                    self.set_meta("mode", "team")

                def change(kf):
                    kf.data["mode"] = "team"
                    kf.set_user(username, password, self._key)
                self._edit_keys(change)
        except BaseException:
            self.mode = was
            raise
        self._users = None
        return cur.lastrowid

    def update_user(self, uid: int, name: str | None = None, role: str | None = None,
                    active: bool | None = None, unlock: bool = False) -> None:
        self._need_admin()
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise DBError("That account no longer exists.")
        new_role = role if role is not None else row["role"]
        new_active = int(active) if active is not None else row["active"]
        if new_role not in ROLES:
            raise DBError("Choose a role.")
        if uid == self.user["id"] and not new_active:
            raise DBError("You cannot switch off your own account. Ask another "
                          "administrator to do it.")
        if new_active and not row["active"] and self.encrypted:
            # their copy of the key was removed when they were switched off
            raise DBError("Choose a new password for this person to switch them back on.")
        if row["role"] == "admin" and row["active"] and (new_role != "admin" or not new_active):
            others = self.conn.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1 "
                                       "AND id<>?", (uid,)).fetchone()[0]
            if not others:
                raise DBError("There has to be at least one active administrator.")
        with self.tx():
            self.conn.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?",
                              ((name or "").strip() or row["name"], new_role, new_active, uid))
            if unlock:
                self.conn.execute("UPDATE users SET failed=0, locked_until=NULL WHERE id=?", (uid,))
            self._audit("user-changed", None, "", "",
                        new=f"{(name or '').strip() or row['name']} ({ROLES[new_role]}"
                            f"{'' if new_active else ', switched off'})")
            if self.keyfile and not new_active:
                # switched off: they must no longer be able to unlock the file
                self._edit_keys(lambda kf: kf.remove_user(row["username"]))
            elif self.keyfile and unlock:
                self._edit_keys(lambda kf: kf.clear_failures(row["username"]))
        self._users = None
        if uid == self.user["id"]:
            self._load_user(row["username"])

    def enable_user(self, uid: int, new_password: str) -> None:
        """Switch a switched-off account back on, with a new password (in an
        encrypted CRM their copy of the key has to be made again from one)."""
        self._need_admin()
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise DBError("That account no longer exists.")
        problem = security.password_problem(new_password, row["username"])
        if problem:
            raise DBError(problem)
        with self.tx():
            self.conn.execute("UPDATE users SET pw=?, active=1, failed=0, locked_until=NULL "
                              "WHERE id=?", (security.hash_password(new_password), uid))
            self._audit("user-changed", None, "", "",
                        new=f"{row['name']} ({ROLES.get(row['role'], row['role'])}, "
                            "switched back on with a new password)")
            self._edit_keys(lambda kf: kf.set_user(row["username"], new_password, self._key))
        self._users = None

    def set_password(self, uid: int, new_password: str, old_password: str | None = None) -> None:
        """Change a password. Your own needs the old one; an administrator can
        set anyone's. An empty new password removes it (one-person CRMs only)."""
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise DBError("That account no longer exists.")
        own = uid == self.user["id"]
        if not own:
            self._need_admin()
        elif row["pw"] and not security.verify_password(old_password or "", row["pw"]):
            raise DBError("Your current password is not right.")
        if not row["active"]:
            raise DBError("This account is switched off. Switch it back on to give it "
                          "a new password.")
        if new_password == "":
            if self.mode == "team" or self.encrypted:
                raise DBError("A password is required here.")
            hashed = None
        else:
            problem = security.password_problem(new_password, row["username"])
            if problem:
                raise DBError(problem)
            hashed = security.hash_password(new_password)
        with self.tx():
            self.conn.execute("UPDATE users SET pw=?, failed=0, locked_until=NULL WHERE id=?",
                              (hashed, uid))
            self._audit("password-changed", None, "", "", new=row["name"])
            if new_password:
                self._edit_keys(lambda kf: kf.set_user(row["username"], new_password, self._key))
        self._users = None

    def new_recovery_code(self) -> str:
        self._need_admin()
        if not self.keyfile:
            raise DBError("This CRM is not encrypted.")
        made = []
        with self.tx():
            self._audit("recovery-code", None, "", "", new="New recovery code made")
            self._edit_keys(lambda kf: made.append(kf.set_recovery(self._key)))
        return made[0]

    def set_idle_minutes(self, minutes: int) -> None:
        """Lock the app after this many minutes without use (0 = never).
        Administrators only; recorded in the history."""
        self._need_admin()
        minutes = max(0, int(minutes))
        old = self.get_meta("idle_minutes", "15")
        if str(minutes) == str(old):
            return
        with self.tx():
            self.set_meta("idle_minutes", minutes)
            self._audit("setting", None, "Lock when not used", f"{old} minutes" if old != "0" else "Never",
                        f"{minutes} minutes" if minutes else "Never")

    # -------------------------------------------------------------- audit
    def _audit(self, action, record, field, old, new="", user=None):
        uid, uname = user or ((self.user["id"], self.user["name"]) if self.user else (None, ""))
        rid = record["id"] if record else None
        self.conn.execute(
            "INSERT INTO audit(at,user_id,user_name,action,record_id,type,ref,field,old,new) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (validate.now_stamp(), uid, uname, action, rid,
             record["type"] if record else "", record["ref"] if record else "",
             field or "", "" if old is None else str(old)[:2000],
             "" if new is None else str(new)[:2000]))

    def history(self, record_id: int | None = None, limit: int = 500, search: str = "") -> list[dict]:
        sql = "SELECT * FROM audit"
        args: list = []
        where = []
        if record_id is not None:
            where.append("record_id=?")
            args.append(record_id)
        if search:
            fn = "lower" if search.isascii() else "ulower"
            where.append("(" + " OR ".join(
                f"{fn}({col}) LIKE ? ESCAPE '\\'"
                for col in ("user_name", "ref", "field", "old", "new", "action")) + ")")
            args += [_like(search.lower())] * 6
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    # ------------------------------------------------------------ lookups
    def lookup(self, kind: str, value):
        """Title of a linked record / name of a user, for display."""
        if kind == "user":
            return self.user_name(value)
        try:
            rid = int(value)
        except (TypeError, ValueError):
            return ""
        self._fresh()
        if rid not in self._titles:
            row = self.conn.execute("SELECT title, deleted_at FROM records WHERE id=?",
                                    (rid,)).fetchone()
            if row is None:
                self._titles[rid] = ""
            else:
                self._titles[rid] = row["title"] + (" (deleted)" if row["deleted_at"] else "")
        return self._titles[rid]

    def resolve(self, kind: str, text: str, field: dict):
        """Find a record or user from text (imports). None when nothing matches."""
        low = " ".join(text.split()).lower()
        if kind == "user":
            for u in self.users():
                if low in (u["name"].lower(), u["username"].lower()):
                    return u["id"]
            return None
        fn = "lower" if low.isascii() else "ulower"
        rows = self.conn.execute(
            "SELECT id FROM records WHERE type=? AND deleted_at IS NULL AND "
            f"({fn}(title)=? OR {fn}(ref)=?) ORDER BY id LIMIT 1",
            (field.get("link_type"), low, low)).fetchone()
        return rows["id"] if rows else None

    def _search_text(self, t: dict, ref: str, title: str, data: dict) -> str:
        bits = [ref, title]
        for f in bpm.active_fields(t):
            text = validate.display(f, data.get(f["key"]), self.lookup)
            if text:
                bits.append(text)
                if f["kind"] in ("phone", "postcode", "ni"):
                    bits.append(text.replace(" ", ""))
        text = " \n".join(bits).lower()
        if not text.isascii():
            # findable without the accents too: "sian" finds Siân
            plain = "".join(c for c in unicodedata.normalize("NFKD", text)
                            if not unicodedata.combining(c))
            if plain != text:
                text += " \n" + plain
        return text

    # ------------------------------------------------------------ records
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["data"] = json.loads(d.get("data") or "{}")
        d.pop("search", None)
        return d

    def get_record(self, rid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return self._row(row) if row else None

    def records(self, type_key: str, search: str = "", deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM records WHERE type=? AND deleted_at IS " + ("NOT NULL" if deleted else "NULL")
        args: list = [type_key]
        for word in search.lower().split():
            sql += " AND search LIKE ? ESCAPE '\\'"
            args.append(_like(word))
        sql += " ORDER BY id DESC"
        return [self._row(r) for r in self.conn.execute(sql, args)]

    def count(self, type_key: str) -> int:
        return self.conn.execute("SELECT count(*) FROM records WHERE type=? AND deleted_at IS NULL",
                                 (type_key,)).fetchone()[0]

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT type, count(*) FROM records WHERE deleted_at IS NULL GROUP BY type")}

    def search_all(self, query: str, limit: int = 60) -> list[dict]:
        words = query.lower().split()
        if not words:
            return []
        keys = [t["key"] for t in bpm.active_types(self.blueprint)]
        if not keys:
            return []
        sql = ("SELECT * FROM records WHERE deleted_at IS NULL AND type IN (%s)"
               % ",".join("?" * len(keys)))
        args: list = list(keys)
        for word in words:
            sql += " AND search LIKE ? ESCAPE '\\'"
            args.append(_like(word))
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, args)]

    def recent(self, limit: int = 8) -> list[dict]:
        keys = [t["key"] for t in bpm.active_types(self.blueprint)]
        if not keys:
            return []
        sql = ("SELECT * FROM records WHERE deleted_at IS NULL AND type IN (%s) "
               "ORDER BY updated_at DESC, id DESC LIMIT ?" % ",".join("?" * len(keys)))
        return [self._row(r) for r in self.conn.execute(sql, keys + [limit])]

    def validate_record(self, type_key: str, raw: dict, partial: bool = False,
                        base: dict | None = None):
        """Check and tidy values. raw maps field key -> typed text / value.
        Returns (data without empties, {field key: error sentence}). With
        partial=True only the keys present in raw are checked.

        base is the data the record already holds (when editing one). A value
        left exactly as it was is kept even if a rule would refuse it today -
        a 'not in the past' date that has since gone by, a choice removed from
        the design - so that the rest of the record can still be saved."""
        t = bpm.get_type(self.blueprint, type_key)
        data, errors = {}, {}
        for f in bpm.active_fields(t):
            if partial and f["key"] not in raw:
                continue
            value, err = validate.normalise(f, raw.get(f["key"]), self.resolve)
            if err and base is not None and self._untouched(f, raw.get(f["key"]), base.get(f["key"])):
                value, err = base[f["key"]], None
            if err:
                errors[f["key"]] = err
            elif value is not None:
                if f["kind"] == "link":
                    target = self.conn.execute("SELECT type FROM records WHERE id=?",
                                               (value,)).fetchone()
                    if target is None or target["type"] != f.get("link_type"):
                        errors[f["key"]] = "That record no longer exists."
                        continue
                data[f["key"]] = value
        return data, errors

    @staticmethod
    def _untouched(f: dict, raw, old) -> bool:
        if old is None:
            return False
        if isinstance(raw, str):
            return raw.strip() == validate.to_edit(f, old).strip()
        if isinstance(raw, (list, tuple)):
            return list(raw) == old
        return type(raw) is type(old) and raw == old

    def find_duplicates(self, type_key: str, data: dict, exclude_id: int | None = None):
        """[(field, record)] where another record already has the same value in a
        'must be unique' field, an email or an NI number."""
        t = bpm.get_type(self.blueprint, type_key)
        checks = [f for f in bpm.active_fields(t)
                  if data.get(f["key"]) not in (None, "", []) and
                  (f.get("unique") or f["kind"] in ("email", "ni"))]
        if not checks:
            return []
        out = []
        # A record's search text holds each of its values as it is shown, so the
        # database can pick out the few rows worth a proper look. (Reading and
        # unpacking a whole long list on every save is slow on a shared drive.)
        sql = "SELECT * FROM records WHERE type=? AND deleted_at IS NULL AND ("
        args: list = [type_key]
        for f in checks:
            sql += "instr(search, ?) > 0 OR "
            args.append(validate.display(f, data[f["key"]], self.lookup).lower())
        rows = self.conn.execute(sql[:-4] + ") ORDER BY id DESC", args)
        for r in [self._row(x) for x in rows]:
            if r["id"] == exclude_id:
                continue
            for f in checks:
                a, b = data.get(f["key"]), r["data"].get(f["key"])
                if b is not None and str(a).strip().lower() == str(b).strip().lower():
                    out.append((f, r))
        return out

    def unique_clashes(self, type_key: str, data: dict, exclude_id: int | None = None):
        return [(f, r) for f, r in self.find_duplicates(type_key, data, exclude_id)
                if f.get("unique")]

    def _check_links(self, t: dict, values: dict) -> None:
        """Inside the transaction that stores them: the records these values
        link to must still be there (someone else may have erased one since
        the form was checked)."""
        for f in t.get("fields", []):
            v = values.get(f["key"])
            if f["kind"] == "link" and isinstance(v, int) and not isinstance(v, bool):
                if self.conn.execute("SELECT 1 FROM records WHERE id=?", (v,)).fetchone() is None:
                    raise DBError(f"The record chosen for “{f['name']}” no longer exists - "
                                  "someone else has erased it. Choose another and save again.")

    def _set_links(self, rid: int, t: dict, data: dict):
        self.conn.execute("DELETE FROM links WHERE src=?", (rid,))
        for f in t.get("fields", []):
            if f["kind"] == "link" and isinstance(data.get(f["key"]), int):
                self.conn.execute("INSERT INTO links VALUES(?,?,?)", (rid, f["key"], data[f["key"]]))

    def create_record(self, type_key: str, data: dict, example: bool = False) -> int:
        """Store a new record. data must already be validated."""
        self._need_edit()
        t = bpm.get_type(self.blueprint, type_key)
        if t is None:
            raise DBError("That list no longer exists.")
        data = {k: v for k, v in data.items() if v not in (None, "", [])}
        now = validate.now_stamp()
        with self.tx():
            self._check_links(t, data)
            seq = int(self.get_meta("seq:" + type_key, "0")) + 1
            self.set_meta("seq:" + type_key, seq)
            ref = f"{t['prefix']}-{seq:04d}"
            title = bpm.title_for(t, data, self.lookup) or ref
            cur = self.conn.execute(
                "INSERT INTO records(type,seq,ref,title,data,search,example,created_at,created_by,"
                "updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (type_key, seq, ref, title, json.dumps(data),
                 self._search_text(t, ref, title, data), int(example), now, self.user["id"],
                 now, self.user["id"]))
            rid = cur.lastrowid
            self._set_links(rid, t, data)
            self._audit("created", {"id": rid, "type": type_key, "ref": ref}, "", "", title)
        return rid

    def update_record(self, rid: int, changes: dict, base: dict | None = None,
                      force: bool = False) -> dict:
        """Apply changes ({field key: validated value, or None to clear}).

        base is the data the person started from. If someone else has since
        changed one of the same fields to something different, Conflict is
        raised (unless force); their changes to other fields are kept."""
        self._need_edit()
        changes = {k: (None if v in (None, "", []) else v) for k, v in changes.items()}
        with self.tx():
            cur = self.get_record(rid)
            if cur is None:
                raise DBError("This record no longer exists.")
            if cur["deleted_at"]:
                raise DBError("This record has been deleted by someone else.")
            t = bpm.get_type(self.blueprint, cur["type"])
            if t is None:
                raise DBError("That list no longer exists.")
            data = dict(cur["data"])
            if base is not None and not force:
                theirs = {k: data.get(k) for k in changes
                          if data.get(k) != base.get(k) and data.get(k) != changes[k]}
                if theirs:
                    raise Conflict(theirs, self.user_name(cur["updated_by"]) or "Someone else")
            changed = []
            for k, v in changes.items():
                old = data.get(k)
                if old == v:
                    continue
                f = bpm.get_field(t, k) or {"key": k, "name": k, "kind": "text"}
                changed.append((f, old, v))
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            if not changed:
                return cur
            self._check_links(t, {f["key"]: new for f, _old, new in changed})
            title = bpm.title_for(t, data, self.lookup) or cur["ref"]
            now = validate.now_stamp()
            # (example=0: once someone has changed an example record it is theirs,
            # and "remove the example records" must not erase it)
            self.conn.execute(
                "UPDATE records SET data=?, title=?, search=?, updated_at=?, updated_by=?, "
                "example=0 WHERE id=?",
                (json.dumps(data), title, self._search_text(t, cur["ref"], title, data), now,
                 self.user["id"], rid))
            self._set_links(rid, t, data)
            for f, old, new in changed:
                self._audit("changed", cur, f["name"], validate.display(f, old, self.lookup),
                            validate.display(f, new, self.lookup))
            self._titles.pop(rid, None)
            if title != cur["title"]:
                self._retitle_dependants(rid)
        return self.get_record(rid)

    def _retitle_dependants(self, rid: int):
        """Bring up to date the records that show this one's title in their own
        title or search text - and, where their title changed, theirs in turn."""
        self._retitle(self._linking_to(rid), {rid})

    def _linking_to(self, rid: int) -> list[int]:
        return [row["src"] for row in self.conn.execute(
            "SELECT DISTINCT src FROM links WHERE dst=?", (rid,)).fetchall()]

    def _retitle(self, ids: list[int], seen: set | None = None):
        seen = set() if seen is None else seen      # (two records can link to each other)
        todo = list(ids)
        while todo:
            rid = todo.pop()
            if rid in seen:
                continue
            seen.add(rid)
            r = self.get_record(rid)
            t = bpm.get_type(self.blueprint, r["type"]) if r else None
            if not t:
                continue
            title = bpm.title_for(t, r["data"], self.lookup) or r["ref"]
            self.conn.execute("UPDATE records SET title=?, search=? WHERE id=?",
                              (title, self._search_text(t, r["ref"], title, r["data"]), rid))
            self._titles.pop(rid, None)
            if title != r["title"]:
                todo += self._linking_to(rid)

    def delete_records(self, ids) -> int:
        """Move records to the recycle bin."""
        self._need_edit()
        n = 0
        with self.tx():
            for rid in ids:
                r = self.get_record(rid)
                if r and not r["deleted_at"]:
                    self.conn.execute("UPDATE records SET deleted_at=?, deleted_by=? WHERE id=?",
                                      (validate.now_stamp(), self.user["id"], rid))
                    self._audit("deleted", r, "", r["title"])
                    self._titles.pop(rid, None)
                    self._retitle_dependants(rid)     # they now say "(deleted)"
                    n += 1
        return n

    def restore_records(self, ids) -> int:
        self._need_edit()
        n = 0
        with self.tx():
            for rid in ids:
                r = self.get_record(rid)
                if r and r["deleted_at"]:
                    self.conn.execute("UPDATE records SET deleted_at=NULL, deleted_by=NULL "
                                      "WHERE id=?", (rid,))
                    self._audit("restored", r, "", "", r["title"])
                    self._titles.pop(rid, None)
                    self._retitle_dependants(rid)
                    n += 1
        return n

    def purge_records(self, ids) -> int:
        """Delete for good, with notes, tasks and files. Administrators only."""
        self._need_admin()
        return self._purge(ids)

    def _purge(self, ids) -> int:
        n = 0
        with self.tx():
            for rid in ids:
                r = self.get_record(rid)
                if not r:
                    continue
                for table, col in (("notes", "record_id"), ("tasks", "record_id"),
                                   ("files", "record_id"), ("links", "src")):
                    self.conn.execute(f"DELETE FROM {table} WHERE {col}=?", (rid,))
                # records that pointed at this one lose the link - and its name,
                # which must not live on in their titles and search text
                pointed = []
                for row in self.conn.execute("SELECT src, field FROM links WHERE dst=?",
                                             (rid,)).fetchall():
                    other = self.get_record(row["src"])
                    if other:
                        other["data"].pop(row["field"], None)
                        self.conn.execute("UPDATE records SET data=? WHERE id=?",
                                          (json.dumps(other["data"]), other["id"]))
                        pointed.append(other["id"])
                self.conn.execute("DELETE FROM links WHERE dst=?", (rid,))
                self.conn.execute("DELETE FROM records WHERE id=?", (rid,))
                self._titles.pop(rid, None)
                self._retitle(pointed)
                self._audit("erased", r, "", r["title"])
                n += 1
            self._titles.clear()
        return n

    def deleted_records(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM records WHERE deleted_at IS NOT NULL "
                                 "ORDER BY deleted_at DESC")
        return [self._row(r) for r in rows]

    def has_examples(self) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM records WHERE example=1 LIMIT 1").fetchone())

    def remove_examples(self) -> int:
        """Erase the example records the setup wizard added."""
        self._need_edit()
        ids = [r[0] for r in self.conn.execute("SELECT id FROM records WHERE example=1")]
        return self._purge(ids)

    def related(self, rid: int) -> list[tuple[dict, dict, list[dict]]]:
        """Records in other lists that link to this one: [(type, field, [record])].
        Every (type, field) that could link here is included, even when empty."""
        rec = self.get_record(rid)
        if rec is None:
            return []
        out = []
        for t, f in bpm.links_to(self.blueprint, rec["type"]):
            rows = self.conn.execute(
                "SELECT r.* FROM links l JOIN records r ON r.id=l.src WHERE l.dst=? AND l.field=? "
                "AND r.type=? AND r.deleted_at IS NULL ORDER BY r.updated_at DESC",
                (rid, f["key"], t["key"])).fetchall()
            out.append((t, f, [self._row(r) for r in rows]))
        return out

    def upcoming(self, days: int = 14, overdue_days: int = 30) -> list[dict]:
        """Dates flagged 'remind me' that fall soon (or have just passed):
        [{date, record, field, type}] soonest first."""
        today = _dt.date.today()
        lo = (today - _dt.timedelta(days=overdue_days)).isoformat()
        hi = (today + _dt.timedelta(days=days)).isoformat()
        out = []
        for t in bpm.active_types(self.blueprint):
            fields = [f for f in bpm.active_fields(t) if f["kind"] == "date" and f.get("remind")]
            if not fields:
                continue
            for r in self.records(t["key"]):
                for f in fields:
                    d = r["data"].get(f["key"])
                    if d and lo <= d <= hi:
                        out.append({"date": d, "record": r, "field": f, "type": t})
        out.sort(key=lambda x: x["date"])
        return out

    # -------------------------------------------------------------- notes
    def notes(self, record_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM notes WHERE record_id=? ORDER BY at DESC, id DESC",
                                 (record_id,))
        return [dict(r) for r in rows]

    def add_note(self, record_id: int, body: str, kind: str = "Note", at: str | None = None) -> int:
        self._need_edit()
        body = body.strip()
        if not body:
            raise DBError("Write something first.")
        now = validate.now_stamp()
        with self.tx():
            rec = self._existing(record_id)
            cur = self.conn.execute(
                "INSERT INTO notes(record_id,kind,body,at,created_at,created_by) VALUES(?,?,?,?,?,?)",
                (record_id, kind if kind in NOTE_KINDS else "Note", body, at or now, now,
                 self.user["id"]))
            self._audit("note-added", rec, kind, "", body[:200])
            self.conn.execute("UPDATE records SET updated_at=? WHERE id=?",
                              (now, record_id))
        return cur.lastrowid

    def _existing(self, record_id: int) -> dict:
        """The record something is about to be attached to. If someone else has
        erased it meanwhile, say so rather than save what could never be seen."""
        rec = self.get_record(record_id)
        if rec is None:
            raise DBError("This record no longer exists.")
        return rec

    def update_note(self, note_id: int, body: str) -> None:
        self._need_edit()
        row = self.conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise DBError("That note no longer exists.")
        if row["created_by"] != self.user["id"] and not self.is_admin:
            raise DBError("Only the person who wrote a note (or an administrator) can change it.")
        body = body.strip()
        if not body:
            raise DBError("A note cannot be empty. Delete it instead.")
        with self.tx():
            self.conn.execute("UPDATE notes SET body=?, edited_at=? WHERE id=?",
                              (body, validate.now_stamp(), note_id))
            self._audit("note-changed", self.get_record(row["record_id"]), row["kind"],
                        row["body"][:200], body[:200])

    def delete_note(self, note_id: int) -> None:
        self._need_edit()
        row = self.conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            return
        if row["created_by"] != self.user["id"] and not self.is_admin:
            raise DBError("Only the person who wrote a note (or an administrator) can delete it.")
        with self.tx():
            self.conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            self._audit("note-deleted", self.get_record(row["record_id"]), row["kind"],
                        row["body"][:200])

    # -------------------------------------------------------------- tasks
    def tasks(self, record_id: int | None = None, mine: bool = False,
              include_done: bool = False) -> list[dict]:
        """Tasks, soonest first (no date last). Each has record_title/record_type."""
        sql = ("SELECT t.*, r.title AS record_title, r.type AS record_type, r.ref AS record_ref "
               "FROM tasks t LEFT JOIN records r ON r.id=t.record_id "
               "WHERE (t.record_id IS NULL OR r.deleted_at IS NULL)")
        args: list = []
        if record_id is not None:
            sql += " AND t.record_id=?"
            args.append(record_id)
        if mine:
            sql += " AND (t.assigned_to=? OR t.assigned_to IS NULL)"
            args.append(self.user["id"])
        if not include_done:
            sql += " AND t.done_at IS NULL"
        sql += " ORDER BY t.done_at IS NOT NULL, t.due IS NULL, t.due, t.id"
        return [dict(r) for r in self.conn.execute(sql, args)]

    def add_task(self, title: str, due: str | None = None, record_id: int | None = None,
                 assigned_to: int | None = None, detail: str = "") -> int:
        self._need_edit()
        title = title.strip()
        if not title:
            raise DBError("Say what needs doing.")
        with self.tx():
            rec = self._existing(record_id) if record_id else None
            cur = self.conn.execute(
                "INSERT INTO tasks(record_id,title,detail,due,assigned_to,created_at,created_by) "
                "VALUES(?,?,?,?,?,?,?)",
                (record_id or None, title, detail.strip(), due or None, assigned_to,
                 validate.now_stamp(), self.user["id"]))
            if rec:
                self._audit("task-added", rec, "", "", title)
        return cur.lastrowid

    def update_task(self, task_id: int, **fields) -> None:
        self._need_edit()
        allowed = {k: v for k, v in fields.items()
                   if k in ("title", "detail", "due", "assigned_to", "record_id")}
        if "title" in allowed and not str(allowed["title"]).strip():
            raise DBError("Say what needs doing.")
        if not allowed:
            return
        with self.tx():
            sets = ", ".join(f"{k}=?" for k in allowed)
            self.conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", list(allowed.values()) + [task_id])

    def set_task_done(self, task_id: int, done: bool = True) -> None:
        self._need_edit()
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return
        with self.tx():
            self.conn.execute("UPDATE tasks SET done_at=?, done_by=? WHERE id=?",
                              (validate.now_stamp() if done else None,
                               self.user["id"] if done else None, task_id))
            if row["record_id"]:
                self._audit("task-done" if done else "task-reopened",
                            self.get_record(row["record_id"]), "", "", row["title"])

    def delete_task(self, task_id: int) -> None:
        self._need_edit()
        row = self.conn.execute("SELECT record_id, title FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return
        with self.tx():
            self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            rec = self.get_record(row["record_id"]) if row["record_id"] else None
            if rec:
                self._audit("task-removed", rec, "", row["title"])

    # -------------------------------------------------------------- files
    def files(self, record_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT id,record_id,name,size,added_at,added_by FROM files "
                                 "WHERE record_id=? ORDER BY id DESC", (record_id,))
        return [dict(r) for r in rows]

    def add_file(self, record_id: int, path: str) -> int:
        self._need_edit()
        try:
            if os.path.getsize(path) > 100 * 1024 * 1024:
                raise DBError("That file is over 100 MB, which is too big to keep inside the "
                              "CRM file.")
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            raise DBError(f"That file could not be read.\n\n({exc})") from exc
        name = os.path.basename(path)
        with self.tx():
            rec = self._existing(record_id)
            cur = self.conn.execute(
                "INSERT INTO files(record_id,name,size,data,added_at,added_by) VALUES(?,?,?,?,?,?)",
                (record_id, name, len(blob), blob, validate.now_stamp(), self.user["id"]))
            self._audit("file-added", rec, "", "", name)
        return cur.lastrowid

    def file_data(self, file_id: int) -> tuple[str, bytes] | None:
        row = self.conn.execute("SELECT name, data FROM files WHERE id=?", (file_id,)).fetchone()
        return (row["name"], bytes(row["data"] or b"")) if row else None

    def delete_file(self, file_id: int) -> None:
        self._need_edit()
        row = self.conn.execute("SELECT record_id, name FROM files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            return
        with self.tx():
            self.conn.execute("DELETE FROM files WHERE id=?", (file_id,))
            self._audit("file-removed", self.get_record(row["record_id"]), "", row["name"])

    # -------------------------------------------------------------- views
    def views(self, type_key: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM views WHERE type=? AND (user_id IS NULL OR user_id=?) "
                                 "ORDER BY name COLLATE NOCASE", (type_key, self.user["id"]))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["spec"] = json.loads(d["spec"] or "{}")
            except ValueError:
                d["spec"] = {}
            out.append(d)
        return out

    def save_view(self, type_key: str, name: str, spec: dict) -> int:
        name = name.strip()
        if not name:
            raise DBError("Give the view a name.")
        with self.tx():
            self.conn.execute("DELETE FROM views WHERE type=? AND ulower(name)=? AND user_id=?",
                              (type_key, name.lower(), self.user["id"]))
            cur = self.conn.execute("INSERT INTO views(type,name,spec,user_id) VALUES(?,?,?,?)",
                                    (type_key, name, json.dumps(spec), self.user["id"]))
        return cur.lastrowid

    def delete_view(self, view_id: int) -> None:
        with self.tx():     # (views are personal: only your own)
            self.conn.execute("DELETE FROM views WHERE id=? AND (user_id IS NULL OR user_id=?)",
                              (view_id, self.user["id"]))

    # ------------------------------------------------------------ backups
    def backup_dir(self) -> str:
        base = os.path.splitext(os.path.basename(self.path))[0]
        return os.path.join(os.path.dirname(self.path), base + " backups")

    def backup_to(self, dest: str) -> str:
        """Write a complete copy of the CRM to dest (and its key file, if any)."""
        dest = os.path.abspath(dest)
        if dest == self.path:
            raise DBError("Choose a different place from the CRM file itself.")
        # (its own name: two people's copies of the app may make today's backup at once)
        tmp = f"{dest}.{os.urandom(3).hex()}.part"
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if self.encrypted:
                # Holding the write lock guarantees the file on disk is complete.
                with self.tx():
                    shutil.copyfile(self.path, tmp)
            else:
                out = sqlite3.connect(tmp)
                try:
                    self.conn.backup(out)
                finally:
                    out.close()
            # the key file first: a copy without it could never be opened
            if self.encrypted and os.path.exists(self.path + ".keys"):
                shutil.copyfile(self.path + ".keys", dest + ".keys")
            os.replace(tmp, dest)
        except (OSError,) + _db_errors() as exc:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise DBError("The copy could not be written there. Check the folder still exists "
                          "and that you are allowed to save in it, or choose another place."
                          f"\n\n({exc})") from exc
        return dest

    def _dated_backups(self, folder: str) -> list[str]:
        """The automatic copies in the backups folder, oldest first - and only
        those: anything else kept there is the person's own and is left alone."""
        base, ext = os.path.splitext(os.path.basename(self.path))
        made = re.compile(re.escape(base) + r" \d{4}-\d{2}-\d{2}" + re.escape(ext))
        return sorted(f for f in os.listdir(folder) if made.fullmatch(f))

    def daily_backup(self) -> str | None:
        """One automatic copy per day, keeping the newest few. Never raises."""
        try:
            folder = self.backup_dir()
            base, ext = os.path.splitext(os.path.basename(self.path))
            dest = os.path.join(folder, f"{base} {today_iso()}{ext}")
            if os.path.exists(dest):
                return None
            self.backup_to(dest)
            for old in self._dated_backups(folder)[:-BACKUPS_KEPT]:
                for p in (os.path.join(folder, old), os.path.join(folder, old + ".keys")):
                    with contextlib.suppress(OSError):
                        os.remove(p)
            return dest
        except Exception:
            return None

    def last_backup(self) -> str | None:
        try:
            mine = self._dated_backups(self.backup_dir())
            base = os.path.splitext(os.path.basename(self.path))[0]
            return mine[-1][len(base) + 1:len(base) + 11] if mine else None
        except OSError:
            return None

    def stats(self) -> dict:
        c = self.conn
        return {
            "records": c.execute("SELECT count(*) FROM records WHERE deleted_at IS NULL").fetchone()[0],
            "deleted": c.execute("SELECT count(*) FROM records WHERE deleted_at IS NOT NULL").fetchone()[0],
            "notes": c.execute("SELECT count(*) FROM notes").fetchone()[0],
            "tasks": c.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "files": c.execute("SELECT count(*) FROM files").fetchone()[0],
            "file_bytes": c.execute("SELECT coalesce(sum(size),0) FROM files").fetchone()[0],
            "size": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
            "users": c.execute("SELECT count(*) FROM users WHERE active=1").fetchone()[0],
        }
