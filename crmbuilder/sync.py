"""Sharing a CRM through a synced folder: OneDrive, SharePoint, Dropbox,
Google Drive, iCloud and the like.

A live database cannot be shared that way - the sync client copies whole files
about and knows nothing of the locks a database relies on - so this works the
other way round. Nobody ever opens the shared file itself:

* Each copy of the app works on its own local copy of the CRM (the "work
  copy"), kept outside the synced folder. Every change made to it is also
  written to that person's own queue file, and a copy of the queue is put in
  the synced folder. Each queue file has exactly one writer, which is the one
  thing a sync service gets right.
* Now and then (on opening, every couple of minutes, on closing, or on "Sync
  now") one copy of the app takes a lock, takes the shared file, applies every
  queue it can find - everyone's, oldest change first - and puts the result
  back as the new shared file, written whole and swapped in. Then it replaces
  its own work copy with that.
* Nothing is ever dropped. When two people have changed the same thing while
  apart, the later change wins and the record is flagged in the Sync page with
  both values; an edit to a record someone else deleted brings the record back
  and is flagged; a record that would duplicate a 'must be different' value is
  still added, and flagged.

Files beside the shared CRM "team.crm":
    team.crm.sync/lock.json              who is merging at the moment
    team.crm.sync/queue-<person>.crmq    that person's changes waiting to go in
    team backups/                        the daily backups, as for any CRM
The work copy and the queues themselves live in the app's own folder.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import uuid

from . import FILE_EXT
from . import db as dbm
from . import paths, security, validate

SYNC_SUFFIX = ".sync"
QUEUE_EXT = ".crmq"
LOCK_NAME = "lock.json"
LOCK_STALE_SECONDS = 10 * 60
LOCK_SETTLE_SECONDS = 1.0       # after taking the lock: time for a rival's to arrive
ACK_DELAY_SECONDS = 60          # a merge we wrote counts once it has survived this long
MERGE_EVERY_SECONDS = 120
REFRESH_EVERY_SECONDS = 15
MIRROR_EVERY_SECONDS = 3

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ops(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, opid TEXT UNIQUE, ts TEXT, user TEXT,
  method TEXT, args TEXT, kw TEXT, blob BLOB);
"""

# Methods of Database whose calls are queued, and the keyword that carries the
# number a new row is given (so the same number is used everywhere).
JOURNALED = {
    "create_record": "rid", "update_record": None, "delete_records": None,
    "restore_records": None, "purge_records": None, "remove_examples": None,
    "add_note": "nid", "update_note": None, "delete_note": None,
    "add_task": "tid", "update_task": None, "set_task_done": None, "delete_task": None,
    "add_file": "fid", "delete_file": None,
    "save_view": "vid", "delete_view": None,
    "save_blueprint": None, "set_meta": None, "set_idle_minutes": None,
    "add_user": "uid", "update_user": None, "enable_user": None, "set_password": None,
    "recover": None, "set_key_slot": None, "resolve_sync_conflict": None,
}
# (method, index of the password among its positional arguments, its keyword)
PASSWORDS = {"add_user": (3, "password"), "enable_user": (1, "new_password"),
             "set_password": (1, "new_password"), "recover": (2, "new_password")}
# the record a method is about, for "restore it and carry on"
SUBJECT = {"update_record": 0, "add_note": 0, "add_file": 0}


class SyncError(dbm.DBError):
    pass


def wants_sync(path: str) -> bool:
    """True when a file should be opened through the synced-folder machinery:
    it sits in a synced folder (and is not a backup opened to look at), or has
    been shared that way before."""
    path = os.path.abspath(path)
    if os.path.isdir(path + SYNC_SUFFIX):
        return True
    if os.path.basename(os.path.dirname(path)).endswith(" backups"):
        return False
    return paths.looks_synced(path)


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:40] or "x"


def _machine() -> str:
    return _slug(platform.node().split(".")[0] or "pc")


def _stamp() -> str:
    return validate.now_stamp() + "." + os.urandom(4).hex()


def _jsonable(value):
    if isinstance(value, (set, frozenset, tuple)) or type(value).__name__ in ("dict_keys",
                                                                              "dict_values"):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(f"not serialisable: {type(value).__name__}")


def _copy_whole(src: str, dest: str, retries: int = 6) -> None:
    """Copy a file so that the destination is only ever seen whole (written
    beside it, then swapped in). A sync client may hold the destination open
    for a moment on Windows, so the swap is retried."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = f"{dest}.{os.urandom(3).hex()}.part"
    try:
        shutil.copyfile(src, tmp)
        for attempt in range(retries):
            try:
                os.replace(tmp, dest)
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.5 * (attempt + 1))
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _connect(path: str, key: bytes | None, readonly: bool = False):
    if key is not None:
        mod = dbm.sqlcipher_module()
        if mod is None:
            raise SyncError("This CRM is encrypted, and this copy of the app was built without "
                            "encryption support.")
        con = mod.connect(path, timeout=10, isolation_level=None)
        con.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        con.row_factory = mod.Row
        if readonly:
            con.execute("PRAGMA query_only = 1")
    elif readonly:
        con = sqlite3.connect("file:" + urllib.parse.quote(path) + "?mode=ro", uri=True,
                              timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
    else:
        con = sqlite3.connect(path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return con


# ----------------------------------------------------------------- the queue
class Queue:
    """One person's changes waiting to be merged: a small database of its own,
    encrypted like the CRM when the CRM is."""

    def __init__(self, path: str, key: bytes | None):
        self.path, self.key = path, key
        self.conn = None

    def open(self) -> "Queue":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = _connect(self.path, self.key)
        self.conn.execute("PRAGMA journal_mode = DELETE")
        self.conn.executescript(QUEUE_SCHEMA)
        return self

    def close(self):
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        self.conn = None

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                          "SET value=excluded.value", (key, None if value is None else str(value)))

    def client(self) -> int:
        """This queue's own space of numbers (see db.LOW_IDS): chosen at random
        once, which is as good as unique for a team."""
        c = self.get("client")
        if not c:
            c = int.from_bytes(os.urandom(4), "big") % ((1 << 31) - 2) + 1
            self.set("client", c)
        return int(c)

    def next_id(self) -> int:
        n = int(self.get("next_id", "0")) + 1
        self.set("next_id", n)
        return (self.client() << 32) | n

    def add(self, ts: str, user: str, method: str, args: list, kw: dict,
            blob: bytes | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO ops(opid,ts,user,method,args,kw,blob) VALUES(?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, ts, user, method, json.dumps(args, default=_jsonable),
             json.dumps(kw, default=_jsonable), blob))
        return cur.lastrowid

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM ops").fetchone()[0]

    def pending(self, after: int = 0) -> list[dict]:
        return _read_ops(self.conn, after)

    def truncate(self, upto: int) -> None:
        """Forget the changes up to this number: they are in the shared file."""
        if upto <= 0:
            return
        n = self.conn.execute("DELETE FROM ops WHERE seq<=?", (upto,)).rowcount
        if n:
            with contextlib.suppress(Exception):
                self.conn.execute("VACUUM")

    def mirror_to(self, dest: str) -> None:
        """Put a whole copy in the synced folder (never the live file itself)."""
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.{os.urandom(3).hex()}.part"
        try:
            if self.key is None:
                out = sqlite3.connect(tmp)
                try:
                    self.conn.backup(out)
                finally:
                    out.close()
            else:
                # (holding the write lock, the file on disk is whole)
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    shutil.copyfile(self.path, tmp)
                finally:
                    self.conn.execute("COMMIT")
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise


def _read_ops(conn, after: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM ops WHERE seq>? ORDER BY seq", (after,)):
        out.append({"seq": r["seq"], "opid": r["opid"], "ts": r["ts"], "user": r["user"],
                    "method": r["method"], "args": json.loads(r["args"] or "[]"),
                    "kw": json.loads(r["kw"] or "{}"),
                    "blob": bytes(r["blob"]) if r["blob"] is not None else None})
    return out


def read_mirror(path: str, key: bytes | None, after: int) -> list[dict]:
    """Another person's queue, as last mirrored. It is copied first: the sync
    client may be replacing it at this very moment."""
    folder = tempfile.mkdtemp(prefix="crm-builder-queue-")
    try:
        copy = os.path.join(folder, "q" + QUEUE_EXT)
        shutil.copyfile(path, copy)
        con = _connect(copy, key, readonly=True)
        try:
            return _read_ops(con, after)
        finally:
            con.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- the store
class SyncStore:
    """The shared file, the work copy, the queue and the merge."""

    def __init__(self, shared: str, local_root: str | None = None):
        self.shared = os.path.abspath(shared)
        self.sync_dir = self.shared + SYNC_SUFFIX
        root = local_root or os.path.join(os.path.dirname(paths.settings_path()), "sync")
        self.local_dir = os.path.join(root, hashlib.sha1(self.shared.encode("utf-8")).hexdigest()[:12])
        self.work = os.path.join(self.local_dir, "work" + FILE_EXT)
        self.merged = os.path.join(self.local_dir, "merge" + FILE_EXT)
        self.state_path = os.path.join(self.local_dir, "state.json")
        self.state = self._load_state()
        self.queue: Queue | None = None
        self.slug = ""
        self.key: bytes | None = None
        self.last_merge: float = 0.0
        self.last_refresh: float = 0.0
        self.last_error = ""
        self._seen = None            # (mtime, size) of the shared file when last looked at
        self._mirror_dirty = False
        self._lock: dict = {}
        self._beat_at = 0.0
        self._ack_stamp = None
        self._ack_since = 0.0
        self._mirrored_at = 0.0

    # ----------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(self.local_dir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.state_path)

    @property
    def mirror_path(self) -> str:
        return os.path.join(self.sync_dir, f"queue-{self.slug}{QUEUE_EXT}")

    @property
    def lock_path(self) -> str:
        return os.path.join(self.sync_dir, LOCK_NAME)

    def shared_readable(self) -> bool:
        try:
            with open(self.shared, "rb") as fh:
                return len(fh.read(16)) == 16
        except OSError:
            return False

    # --------------------------------------------------------- preparing
    def prepare(self) -> None:
        """Make sure there is a work copy to open. The first time (or if the
        work copy has gone) it is a copy of the shared file."""
        os.makedirs(self.local_dir, exist_ok=True)
        if os.path.isfile(self.work) and self.state.get("shared") == self.shared:
            return
        if not os.path.isfile(self.shared):
            raise SyncError("That file cannot be found. It may have been moved or renamed, or "
                            "your sync service has not downloaded it yet.")
        if not self.shared_readable():
            raise SyncError("That file cannot be read yet. If it is in a cloud folder, wait for "
                            "it to finish downloading and try again.")
        self._fetch_shared(self.work)
        self.state = {"shared": self.shared, "base_stamp": self._stamp_of(self.work, None)}
        self._save_state()

    def _fetch_shared(self, dest: str) -> None:
        """A whole copy of the shared file (and its key file) to dest."""
        _copy_whole(self.shared, dest)
        keys = self.shared + ".keys"
        if os.path.isfile(keys):
            _copy_whole(keys, dest + ".keys")

    def _stamp_of(self, path: str, key: bytes | None) -> str | None:
        """The merge stamp a file carries (None: not a readable CRM right now)."""
        try:
            con = _connect(path, key, readonly=True)
        except Exception:
            return None
        try:
            row = con.execute("SELECT value FROM meta WHERE key='sync_stamp'").fetchone()
            return row[0] if row else ""
        except Exception:
            return None
        finally:
            con.close()

    def attach(self, db: "SyncDatabase") -> None:
        """Signed in: open this person's queue."""
        self.key = db._key if db.encrypted else None
        self.slug = f"{_slug(db.user['username'])}-{_machine()}"
        self.queue = Queue(os.path.join(self.local_dir, f"queue-{self.slug}{QUEUE_EXT}"),
                           self.key).open()
        self.queue.set("slug", self.slug)
        self.queue.set("user", db.user["username"])
        self.queue.client()
        self.mirror()

    def mirror(self, now: bool = True) -> None:
        """Copy the queue to the shared folder. now=False: only if the last
        copy was a few seconds ago (a queue holding a big file is not copied
        again for every keystroke-sized change; the tick catches up)."""
        if self.queue is None:
            return
        if not now and time.time() - self._mirrored_at < MIRROR_EVERY_SECONDS:
            self._mirror_dirty = True
            return
        try:
            self.queue.mirror_to(self.mirror_path)
            self._mirror_dirty = False
            self._mirrored_at = time.time()
        except Exception as exc:   # (the drive is gone for the moment: tried again later)
            self._mirror_dirty = True
            self.last_error = f"The queue could not be copied to the shared folder ({exc})."

    def pending_count(self) -> int:
        return self.queue.count() if self.queue is not None else 0

    # --------------------------------------------------------- the lock
    def lock_holder(self) -> dict | None:
        try:
            with open(self.lock_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return None

    def _lock_stale(self, data: dict) -> bool:
        beat = data.get("beat") or data.get("since") or ""
        try:
            when = _dt.datetime.strptime(beat, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return True
        return (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds() > LOCK_STALE_SECONDS

    def take_lock(self, who: str) -> bool:
        os.makedirs(self.sync_dir, exist_ok=True)
        mine = {"by": who, "slug": self.slug, "machine": _machine(), "token": os.urandom(8).hex(),
                "since": validate.now_stamp(), "beat": validate.now_stamp()}
        for _attempt in range(2):
            try:
                fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                held = self.lock_holder()
                if held is None or self._lock_stale(held):
                    with contextlib.suppress(OSError):
                        os.remove(self.lock_path)
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(mine, fh)
            time.sleep(LOCK_SETTLE_SECONDS)
            now = self.lock_holder()
            if now and now.get("token") == mine["token"]:
                self._lock = mine
                self._beat_at = time.time()
                return True
            return False       # (somebody else's arrived at the same moment: they have it)
        return False

    def release_lock(self) -> None:
        held = self.lock_holder()
        if held and self._lock and held.get("token") == self._lock.get("token"):
            with contextlib.suppress(OSError):
                os.remove(self.lock_path)
        self._lock = {}

    def _beat(self) -> None:
        self._beat_at = time.time()
        if self._lock:
            self._lock["beat"] = validate.now_stamp()
            with contextlib.suppress(OSError):
                with open(self.lock_path, "w", encoding="utf-8") as fh:
                    json.dump(self._lock, fh)

    # ------------------------------------------------------------ merge
    def _open_copy(self, path: str) -> dbm.Database:
        """Open a copy of the CRM for merging, without signing in."""
        db = dbm.Database(path)
        if self.key is not None:
            db.encrypted = True
            db._key = self.key
            db.keyfile = security.KeyFile.load(path)
        db._connect()
        db._after_connect()
        return db

    @staticmethod
    def watermark(db: dbm.Database, slug: str) -> int:
        try:
            return int(db.get_meta("sync:applied:" + slug, "0") or 0)
        except ValueError:
            return 0

    def _others(self) -> list[str]:
        try:
            names = sorted(os.listdir(self.sync_dir))
        except OSError:
            return []
        mine = os.path.basename(self.mirror_path)
        return [os.path.join(self.sync_dir, n) for n in names
                if n.startswith("queue-") and n.endswith(QUEUE_EXT) and n != mine]

    def _others_waiting(self) -> bool:
        """Is there a queue in the shared folder newer than the shared file?"""
        try:
            shared_at = os.stat(self.shared).st_mtime
        except OSError:
            return False
        for path in self._others():
            with contextlib.suppress(OSError):
                if os.stat(path).st_mtime > shared_at:
                    return True
        return False

    def merge(self, db: "SyncDatabase") -> dict:
        """Take the lock, apply every queue to the shared file, put it back, and
        bring the work copy up to date. Returns a summary; {"busy": who} when
        someone else is merging at the moment."""
        if self.queue is None:
            raise SyncError("Sign in first.")
        if not self.shared_readable():
            raise SyncError("The shared file cannot be reached at the moment, so nothing was "
                            "synced. Your changes are safe here and will go in later.")
        if self._mirror_dirty:
            self.mirror()
        if not self.take_lock(db.user["name"]):
            held = self.lock_holder() or {}
            return {"busy": held.get("by") or "someone else"}
        summary = {"sent": 0, "received": 0, "conflicts": 0, "skipped": 0, "busy": ""}
        try:
            self._fetch_shared(self.merged)
            mdb = self._open_copy(self.merged)
            try:
                ops = []
                mine_after = self.watermark(mdb, self.slug)
                for op in self.queue.pending(mine_after):
                    op["slug"] = self.slug
                    ops.append(op)
                for path in self._others():
                    slug = os.path.basename(path)[len("queue-"):-len(QUEUE_EXT)]
                    try:
                        theirs = read_mirror(path, self.key, self.watermark(mdb, slug))
                    except Exception:
                        continue          # (half-synced: picked up next time)
                    for op in theirs:
                        op["slug"] = slug
                        ops.append(op)
                ops.sort(key=lambda o: (o["ts"], o["slug"], o["seq"]))
                tops: dict[str, int] = {}
                with mdb.tx():
                    for op in ops:
                        tops[op["slug"]] = max(tops.get(op["slug"], 0), op["seq"])
                        if mdb.conn.execute("SELECT 1 FROM sync_applied WHERE opid=?",
                                            (op["opid"],)).fetchone():
                            continue
                        result = apply_op(mdb, op)
                        mdb.conn.execute("INSERT OR IGNORE INTO sync_applied VALUES(?,?)",
                                         (op["opid"], validate.now_stamp()))
                        if result == "skipped":
                            summary["skipped"] += 1
                        elif result:
                            summary["conflicts"] += 1
                        summary["sent" if op["slug"] == self.slug else "received"] += 1
                        if time.time() - self._beat_at > 30:
                            self._beat()
                    stamp = mdb.get_meta("sync_stamp", "")
                    if ops or not stamp:
                        for slug, top in tops.items():
                            mdb.set_meta("sync:applied:" + slug, top)
                        stamp = _stamp()
                        mdb.set_meta("sync_stamp", stamp)
                        mdb.set_meta("sync_at", validate.now_stamp())
                        mdb.set_meta("sync_by", db.user["name"])
                        changed = True
                    else:
                        changed = False
            finally:
                mdb.close()
            if changed:
                _copy_whole(self.merged, self.shared)
                if os.path.isfile(self.merged + ".keys"):
                    _copy_whole(self.merged + ".keys", self.shared + ".keys")
            self.state.update({"shared": self.shared, "base_stamp": stamp})
            self._save_state()
        finally:
            self.release_lock()
        db.swap_in(self.merged)
        self.last_merge = time.time()
        self._seen = None
        self.last_error = ""
        return summary

    # ---------------------------------------------------------- refresh
    def shared_changed(self) -> bool:
        """Cheap: has the shared file been replaced since it was last looked at?"""
        try:
            st = os.stat(self.shared)
            seen = (st.st_mtime, st.st_size)
        except OSError:
            return False
        if seen == self._seen:
            return False
        self._seen = seen
        return True

    def refresh(self, db: "SyncDatabase") -> bool:
        """Without the lock: if someone else has merged, start again from the
        new shared file and put this person's own waiting changes back on top.
        True when the work copy changed."""
        if self.queue is None or not self.shared_readable():
            return False
        stamp = self._stamp_of(self.shared, self.key)
        if stamp is None:
            return False
        if stamp == self.state.get("base_stamp"):
            self._acknowledge()
            return False
        self._fetch_shared(self.merged)
        mdb = self._open_copy(self.merged)
        try:
            after = self.watermark(mdb, self.slug)
            with mdb.tx():
                for op in self.queue.pending(after):
                    op["slug"] = self.slug
                    if mdb.conn.execute("SELECT 1 FROM sync_applied WHERE opid=?",
                                        (op["opid"],)).fetchone():
                        continue
                    apply_op(mdb, op, quiet=True)
                    mdb.conn.execute("INSERT OR IGNORE INTO sync_applied VALUES(?,?)",
                                     (op["opid"], validate.now_stamp()))
        finally:
            mdb.close()
        self.state.update({"shared": self.shared, "base_stamp": stamp})
        self._save_state()
        db.swap_in(self.merged)
        self.last_refresh = time.time()
        self._acknowledge()
        return True

    def _acknowledge(self) -> None:
        """Forget queued changes once a shared file that has stayed put for a
        while (so it is not a copy the sync service is about to throw away in
        favour of another) says they are in."""
        if self.queue is None or not self.queue.count():
            return
        try:
            con = _connect(self.shared, self.key, readonly=True)
        except Exception:
            return
        try:
            rows = dict(con.execute("SELECT key, value FROM meta WHERE key IN (?, 'sync_stamp')",
                                    ("sync:applied:" + self.slug,)).fetchall())
        except Exception:
            return
        finally:
            con.close()
        stamp = rows.get("sync_stamp", "")
        if stamp != self._ack_stamp:
            self._ack_stamp, self._ack_since = stamp, time.time()
            return
        if time.time() - self._ack_since < ACK_DELAY_SECONDS:
            return
        got = rows.get("sync:applied:" + self.slug, "0")
        upto = int(got) if str(got).isdigit() else 0
        if upto > 0:
            self.queue.truncate(upto)
            self.mirror()

    # ------------------------------------------------------------- info
    def conflict_copies(self) -> list[str]:
        """Files the sync service made when two copies collided ("team (1).crm",
        "team-LAPTOP.crm"...). Everything in them is already in the queues, so
        they are safe to delete."""
        folder = os.path.dirname(self.shared)
        base, ext = os.path.splitext(os.path.basename(self.shared))
        out = []
        try:
            for name in sorted(os.listdir(folder)):
                if not name.lower().endswith(ext.lower()) or name == os.path.basename(self.shared):
                    continue
                stem = name[:-len(ext)]
                if stem.startswith(base) and stem != base and (
                        "conflict" in stem.lower()
                        or re.fullmatch(re.escape(base) + r"[ \-_(].*", stem)):
                    out.append(os.path.join(folder, name))
        except OSError:
            pass
        return out

    def info(self) -> dict:
        """What the Sync page shows."""
        held = self.lock_holder() or {}
        if held and self._lock_stale(held):
            held = {}
        try:
            con = _connect(self.shared, self.key, readonly=True)
            try:
                rows = dict(con.execute("SELECT key, value FROM meta WHERE key IN "
                                        "('sync_at','sync_by')").fetchall())
            finally:
                con.close()
        except Exception:
            rows = {}
        return {"shared": self.shared, "pending": self.pending_count(),
                "merging": held.get("by", ""), "last_at": rows.get("sync_at", ""),
                "last_by": rows.get("sync_by", ""), "error": self.last_error,
                "online": self.shared_readable(), "copies": self.conflict_copies()}

    def close(self) -> None:
        if self.queue is not None:
            self.queue.close()
            self.queue = None


# --------------------------------------------------------- applying a change
def _decode(value):
    if isinstance(value, dict) and "$pw" in value:
        pw = value["$pw"]
        return dbm.PasswordRef(pw.get("hash", ""), pw.get("plain"))
    return value


def _flag(db: dbm.Database, op: dict, note: str, record=None, field: str = "",
          mine="", theirs="", other: str = "", quiet: bool = False) -> None:
    if quiet:
        return
    rec = record if isinstance(record, dict) else (db.get_record(record) if record else None)
    rid = rec["id"] if rec else None
    db.conn.execute(
        "INSERT INTO sync_conflicts(at,record_id,type,ref,field,mine,theirs,who,other,note) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (validate.now_stamp(), rid, rec["type"] if rec else "", rec["ref"] if rec else "",
         field, str(mine)[:2000], str(theirs)[:2000], op.get("user", ""), other, note[:500]))
    db._audit("sync-conflict", rec, field, mine, note)


def apply_op(db: dbm.Database, op: dict, quiet: bool = False):
    """Replay one queued change on db as the person who made it, when they
    made it. Returns None (fine), a word (applied, but flagged) or "skipped".
    quiet: just apply (the work copy is being rebuilt; the real merge flags)."""
    row = db._user_row(op.get("user") or "")
    if row is None or not row["active"]:
        _flag(db, op, "Not applied: the account that made this change no longer exists "
                      "or is switched off.", quiet=quiet)
        return "skipped"
    db.user = {"id": row["id"], "username": row["username"], "name": row["name"], "role": row["role"]}
    db._users = None
    db._now_override = op.get("ts") or None
    try:
        return _apply(db, op, quiet)
    finally:
        db._now_override = None


def _apply(db: dbm.Database, op: dict, quiet: bool):
    method = op["method"]
    args = [_decode(a) for a in op["args"]]
    kw = {k: _decode(v) for k, v in op["kw"].items()}
    fn = getattr(dbm.Database, method, None) if method in JOURNALED else None
    if fn is None:
        _flag(db, op, f"Not applied: this copy of the app does not know how to “{method}”. "
                      "Update the app.", quiet=quiet)
        return "skipped"
    if method == "add_file":
        folder = tempfile.mkdtemp(prefix="crm-builder-sync-")
        try:
            path = os.path.join(folder, os.path.basename(str(args[1])) or "file")
            with open(path, "wb") as fh:
                fh.write(op.get("blob") or b"")
            args[1] = path
            return _apply_call(db, op, fn, args, kw, quiet)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    note = None
    if method == "save_blueprint":
        base = kw.pop("base_stamp", None)
        if base is not None and base != db.get_meta("blueprint_at"):
            note = "design"
            _flag(db, op, "The design was changed by two people while apart. This change was "
                          "applied over the other one: check the design.", quiet=quiet)
    return _apply_call(db, op, fn, args, kw, quiet) or note


def _subject(method: str, args: list, kw: dict):
    """The record a change is about (to restore it if it has been binned)."""
    if method in SUBJECT:
        return args[SUBJECT[method]] if len(args) > SUBJECT[method] else None
    if method == "add_task":
        return kw.get("record_id") or (args[2] if len(args) > 2 else None)
    return None


def _apply_call(db, op, fn, args, kw, quiet):
    """Call fn(db, *args, **kw) under the merge rules: restore what an edit
    needs, add a duplicate but say so, keep the later change in a clash."""
    method = op["method"]
    note = None

    def flag(text, **extra):
        _flag(db, op, text, quiet=quiet, **extra)

    for _attempt in range(5):
        try:
            fn(db, *args, **kw)
            return note
        except dbm.Conflict as exc:
            rec = db.get_record(args[0]) if args else None
            for field, theirs in exc.theirs.items():
                flag("Changed by two people while apart. This value won; "
                     f"{exc.who} had set it to the other one.", record=rec, field=field,
                     mine=(args[1] if len(args) > 1 else {}).get(field), theirs=theirs,
                     other=exc.who)
            note = "conflict"
            kw["force"] = True
        except dbm.Binned as exc:
            target = getattr(exc, "target", None)
            field = getattr(exc, "field", "")
            if target is None:
                target = _subject(method, args, kw)
            if target is None:
                flag(f"Not applied: {exc}")
                return "skipped"
            r = db.get_record(int(target))
            if r and r["deleted_at"]:
                db.restore_records([int(target)])
            if field:
                flag("This change links to a record someone else had deleted, so that record "
                     "has been restored.", record=r, field=field)
            else:
                flag("This record had been deleted by someone else, but was changed or written "
                     "on afterwards, so it has been restored.", record=r)
            note = "restored"
        except dbm.UniqueClash as exc:
            if method in ("create_record", "update_record"):
                kw["check_unique"] = False
                rid = kw.get("rid") if method == "create_record" else args[0]
                flag(f"Duplicate: {exc} It was still saved so that nothing is lost - decide "
                     "which to keep.", record=db.get_record(rid) if rid else None)
                note = "duplicate"
            else:
                flag(f"Not applied: {exc}")
                return "skipped"
        except dbm.Gone as exc:
            field = getattr(exc, "field", None)
            if field and method in ("create_record", "update_record") and len(args) > 1:
                args[1].pop(field, None)
                rid = kw.get("rid") if method == "create_record" else args[0]
                flag(f"A link was left out: {exc}", record=db.get_record(rid) if rid else None,
                     field=field)
                note = "dropped link"
                continue
            flag(f"Not applied: {exc}")
            return "skipped"
        except dbm.DBError as exc:
            if "already been added" in str(exc):
                return note          # (the same record, twice: fine)
            flag(f"Not applied: {exc}")
            return "skipped"
    flag("Not applied: it could not be made to fit.")
    return "skipped"


# ------------------------------------------------------------ the database
class SyncDatabase(dbm.Database):
    """A Database on the work copy that writes every change to the queue."""

    def __init__(self, path: str):
        super().__init__(path)
        self.store: SyncStore | None = None
        self._jdepth = 0
        self._buffer: list = []

    @property
    def shown_path(self) -> str:
        return self.store.shared if self.store else self.path

    @property
    def synced(self) -> bool:
        return self.store is not None

    @classmethod
    def open_shared(cls, shared: str, local_root: str | None = None) -> "SyncDatabase":
        """Open a CRM in a synced folder. Sign in with .login() as usual."""
        store = SyncStore(shared, local_root)
        store.prepare()
        db = cls.open(store.work)
        db.store = store
        return db

    @classmethod
    def adopt(cls, db: dbm.Database, local_root: str | None = None) -> "SyncDatabase":
        """A CRM that was just made (or opened directly) in a synced folder,
        signed in: carry on with it the shared way."""
        username, key, encrypted, shared = db.user["username"], db._key, db.encrypted, db.path
        store = SyncStore(shared, local_root)
        store.prepare()
        new = cls(store.work)
        new.store = store
        new.encrypted = encrypted
        if encrypted:
            new._key = key
            new.keyfile = security.KeyFile.load(store.work)
            new.mode = new.keyfile.mode if new.keyfile else db.mode
        new._connect()
        new._after_connect()
        new._load_user(username)
        store.attach(new)
        db.close()          # (only once the shared way is known to work)
        return new

    def login(self, username: str | None = None, password: str = "") -> dict:
        user = super().login(username, password)
        self.store.attach(self)
        return user

    def recover(self, code: str, username: str, new_password: str) -> None:
        if self.user is not None:
            return self._journaled("recover", [code, username, new_password], {})
        super().recover(code, username, new_password)
        self.store.attach(self)
        _args, _kw, record, blob = self._prepare("recover", [code, username, new_password], {})
        self._journal("recover", record[0], record[1], blob)

    def close(self):
        super().close()
        if self.store is not None:
            self.store.close()

    def swap_in(self, merged: str) -> None:
        """Replace the work copy with a freshly merged one and reconnect."""
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
            self.conn = None
        _copy_whole(merged, self.path)
        if os.path.isfile(merged + ".keys"):
            _copy_whole(merged + ".keys", self.path + ".keys")
            self.keyfile = security.KeyFile.load(self.path)
        self._connect()
        self._after_connect()
        self._titles.clear()
        self._users = None
        self._seen_version = None
        if self.user:
            row = self._user_row(self.user["username"])
            if row is not None:
                self._load_user(row["username"])

    # ----------------------------------------------------------- queue
    def _on_rollback(self) -> None:
        self._buffer.clear()

    def _on_commit(self) -> None:
        self._flush()

    def _flush(self) -> None:
        if not self._buffer or self.store is None or self.store.queue is None:
            self._buffer.clear()
            return
        q = self.store.queue
        # one queue transaction for the lot (an import queues a change per row)
        own = not q.conn.in_transaction
        if own:
            q.conn.execute("BEGIN IMMEDIATE")
        try:
            for ts, method, args, kw, blob in self._buffer:
                q.add(ts, self.user["username"] if self.user else "", method, args, kw, blob)
            if own:
                q.conn.execute("COMMIT")
        except BaseException:
            if own:
                with contextlib.suppress(Exception):
                    q.conn.execute("ROLLBACK")
            raise
        self._buffer.clear()
        self.store.mirror(now=False)

    def _journal(self, method: str, args: list, kw: dict, blob) -> None:
        self._buffer.append((validate.now_stamp(), method, args, kw, blob))
        if self.conn is None or not self.conn.in_transaction:
            self._flush()

    def _prepare(self, method: str, args: list, kw: dict):
        """What to call, and what to queue: (args, kw, [queued args, queued
        kw], file contents)."""
        args, kw = list(args), dict(kw)
        idkw = JOURNALED.get(method)
        if idkw and kw.get(idkw) is None:
            kw[idkw] = self.store.queue.next_id()
        blob = None
        qargs, qkw = list(args), dict(kw)
        if method == "add_file":
            try:
                with open(args[1], "rb") as fh:
                    blob = fh.read()
            except OSError as exc:
                raise dbm.DBError(f"That file could not be read.\n\n({exc})") from exc
            qargs = [args[0], os.path.basename(args[1])]
        elif method == "update_record":
            rid, changes = args[0], args[1]
            base = args[2] if len(args) > 2 else kw.get("base")
            force = args[3] if len(args) > 3 else kw.get("force", False)
            check = args[4] if len(args) > 4 else kw.get("check_unique", True)
            if base is None:
                cur = self.get_record(rid)
                base = cur["data"] if cur else None
            args, kw = [rid, changes], {"base": base, "force": force, "check_unique": check}
            qargs, qkw = list(args), dict(kw)
        elif method == "save_blueprint":
            qkw["base_stamp"] = self.get_meta("blueprint_at")
        if method in PASSWORDS:
            i, name = PASSWORDS[method]
            pw = args[i] if len(args) > i else kw.get(name)
            if isinstance(pw, str) and pw != "":
                ref = {"$pw": {"hash": security.hash_password(pw),
                               "plain": pw if self.encrypted else None}}
                if len(qargs) > i:
                    qargs[i] = ref
                else:
                    qkw[name] = ref
            if method == "set_password":
                # (the old password is checked here, not again when merged)
                qargs = qargs[:2]
                qkw.pop("old_password", None)
        return args, kw, [qargs, qkw], blob

    def _journaled(self, method: str, args: list, kw: dict):
        """Run a Database method and queue the call (top-level calls only: a
        method's own calls to other methods are part of it)."""
        fn = getattr(dbm.Database, method)
        if self._jdepth or self.store is None or self.store.queue is None:
            self._jdepth += 1
            try:
                return fn(self, *args, **kw)
            finally:
                self._jdepth -= 1
        args, kw, record, blob = self._prepare(method, args, kw)
        self._jdepth += 1
        try:
            result = fn(self, *args, **kw)
        finally:
            self._jdepth -= 1
        self._journal(method, json.loads(json.dumps(record[0], default=_jsonable)),
                      json.loads(json.dumps(record[1], default=_jsonable)), blob)
        return result

    def new_recovery_code(self) -> str:
        """The code is made here; the shared file gets the slot it unlocks (a
        replay would make a different code from the one that was shown)."""
        code = self._journaled_quiet("new_recovery_code")
        slot = (self.keyfile.data.get("slots") or {}).get("recovery") if self.keyfile else None
        if slot:
            self._journal("set_key_slot", ["recovery", dict(slot)], {}, None)
        return code

    def _journaled_quiet(self, method: str, *args, **kw):
        self._jdepth += 1
        try:
            return getattr(dbm.Database, method)(self, *args, **kw)
        finally:
            self._jdepth -= 1

    # ------------------------------------------------------------ sync
    def sync_now(self) -> dict:
        """Merge, or if someone else is merging, at least pick up their result."""
        try:
            out = self.store.merge(self)
        except dbm.DBError as exc:
            self.store.last_error = str(exc)
            raise
        if out.get("busy"):
            with contextlib.suppress(Exception):
                self.store.refresh(self)
        return out

    def auto_sync(self) -> str | None:
        """Called every few seconds by the app. Returns "refreshed" when the
        work copy changed quietly, a sentence worth showing, or None."""
        store = self.store
        if store is None or store.queue is None or self.conn is None:
            return None
        now = time.time()
        try:
            changed = False
            if store._mirror_dirty:
                store.mirror()
            if store.shared_changed() and now - store.last_refresh > REFRESH_EVERY_SECONDS:
                changed = store.refresh(self)
            if not changed and store.pending_count():
                store._acknowledge()
            due = now - store.last_merge > MERGE_EVERY_SECONDS
            if due and (store.pending_count() or store._others_waiting()):
                out = store.merge(self)
                if not out.get("busy"):
                    if out["conflicts"]:
                        n = out["conflicts"]
                        return f"Synced. {n} change{'s' if n != 1 else ''} clashed - see the Sync page."
                    if out["received"]:
                        n = out["received"]
                        return f"Synced: {n} change{'s' if n != 1 else ''} from other people."
                    return "refreshed"
            return "refreshed" if changed else None
        except dbm.DBError as exc:
            store.last_error = str(exc)
            return None
        except Exception as exc:  # noqa: BLE001 - syncing must never take the window down
            store.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def conflicts(self, include_resolved: bool = False) -> list[dict]:
        sql = "SELECT * FROM sync_conflicts"
        if not include_resolved:
            sql += " WHERE resolved=0"
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id DESC")]

    def conflict_count(self) -> int:
        try:
            return self.conn.execute("SELECT count(*) FROM sync_conflicts WHERE resolved=0").fetchone()[0]
        except Exception:
            return 0


def _install_journaling():
    for name in JOURNALED:
        if name == "recover":
            continue

        def make(method):
            def call(self, *args, **kw):
                return self._journaled(method, list(args), kw)
            call.__name__ = method
            call.__doc__ = getattr(dbm.Database, method).__doc__
            return call
        setattr(SyncDatabase, name, make(name))


_install_journaling()
