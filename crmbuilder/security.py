"""Passwords and the key file for encrypted databases. Standard library only.

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes.

An encrypted database has a random 256-bit key. That key is never stored in the
clear: the '<file>.keys' file beside the database holds one wrapped copy per
person (wrapped with a key derived from their password) and one wrapped with
the recovery code that is shown once when encryption is switched on.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

PW_ITERS = 600_000    # OWASP's current figure for PBKDF2-HMAC-SHA256
KEY_ITERS = 600_000
MAX_ITERS = 20_000_000
MIN_PASSWORD = 8


def hash_password(password: str, iters: int = PW_ITERS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, iters, salt, want = (stored or "").split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), want)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if username and password.lower() == username.lower():
        return "The password cannot be the same as the username."
    if password.lower() in ("password", "password1", "12345678", "qwertyui", "letmein1"):
        return "That password is too easy to guess."
    return None


# ------------------------------------------------------------ key wrapping
def _wrap(secret: bytes, passphrase: str, iters: int) -> dict:
    salt = secrets.token_bytes(16)
    stream = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iters, 64)
    ct = bytes(a ^ b for a, b in zip(secret, stream[:32]))
    tag = hmac.new(stream[32:], salt + ct, hashlib.sha256).hexdigest()
    return {"salt": salt.hex(), "ct": ct.hex(), "tag": tag, "iters": iters}


def _unwrap(slot: dict, passphrase: str, iters: int) -> bytes | None:
    try:
        if not 1 <= iters <= MAX_ITERS:     # a tampered file must not hang the sign-in
            return None
        salt, ct = bytes.fromhex(slot["salt"]), bytes.fromhex(slot["ct"])
        stream = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iters, 64)
        tag = hmac.new(stream[32:], salt + ct, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag, slot["tag"]):
            return None
        return bytes(a ^ b for a, b in zip(ct, stream[:32]))
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def new_db_key() -> bytes:
    return secrets.token_bytes(32)


def new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _norm_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class KeyFile:
    """The '<database>.keys' file: wrapped copies of the database key."""

    def __init__(self, db_path: str):
        self.path = db_path + ".keys"
        self.data = {"v": 1, "iters": KEY_ITERS, "mode": "solo", "slots": {}}

    @classmethod
    def load(cls, db_path: str) -> "KeyFile | None":
        kf = cls(db_path)
        try:
            with open(kf.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not isinstance(data.get("slots"), dict):
                return None
            if not isinstance(data.get("fails"), dict):
                data.pop("fails", None)
            kf.data = data
            return kf
        except (OSError, ValueError):
            return None

    def save(self) -> None:
        # Without this file the database cannot be opened at all, so it is
        # written in full beside the old one and only then swapped in.
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)

    @property
    def mode(self) -> str:
        return self.data.get("mode", "solo")

    def usernames(self) -> list[str]:
        return sorted(k[5:] for k in self.data["slots"] if k.startswith("user:"))

    def _iters(self, slot: dict) -> int:
        # Each wrapped copy records the work factor it was made with. New copies
        # never use fewer than KEY_ITERS, whatever the (unprotected) file says,
        # so editing the file cannot make the next password change a weak one.
        try:
            return int(slot.get("iters") or self.data.get("iters") or KEY_ITERS)
        except (TypeError, ValueError, AttributeError, OverflowError):
            return KEY_ITERS

    def set_user(self, username: str, password: str, key: bytes) -> None:
        self.data["slots"]["user:" + username.lower()] = _wrap(key, password, KEY_ITERS)
        self.clear_failures(username)

    def remove_user(self, username: str) -> None:
        self.data["slots"].pop("user:" + username.lower(), None)
        self.clear_failures(username)

    def has_user(self, username: str) -> bool:
        return "user:" + username.lower() in self.data["slots"]

    # Wrong passwords typed before the file is unlocked cannot be counted inside
    # the (still encrypted) database, so they are counted here instead.
    def locked(self, username: str, now: str) -> bool:
        entry = (self.data.get("fails") or {}).get(username.lower())
        until = entry.get("until") if isinstance(entry, dict) else None
        return isinstance(until, str) and until > now

    def note_failure(self, username: str, max_failed: int, until: str) -> None:
        """Count a wrong password; after max_failed in a row lock until 'until'."""
        fails = self.data.setdefault("fails", {})
        entry = fails.get(username.lower())
        if not isinstance(entry, dict):
            entry = fails[username.lower()] = {"n": 0, "total": 0}
        entry["n"] = _count(entry.get("n")) + 1
        entry["total"] = _count(entry.get("total")) + 1
        if entry["n"] >= max_failed:
            entry["n"], entry["until"] = 0, until

    def clear_failures(self, username: str) -> int:
        """Forget the count. Returns how many wrong passwords there had been."""
        entry = (self.data.get("fails") or {}).pop(username.lower(), None)
        return _count(entry.get("total")) if isinstance(entry, dict) else 0

    def rename_user(self, old: str, new: str) -> None:
        slot = self.data["slots"].pop("user:" + old.lower(), None)
        if slot is not None:
            self.data["slots"]["user:" + new.lower()] = slot

    def set_recovery(self, key: bytes) -> str:
        code = new_recovery_code()
        self.data["slots"]["recovery"] = _wrap(key, _norm_code(code), KEY_ITERS)
        return code

    def unlock(self, username: str, password: str) -> bytes | None:
        slot = self.data["slots"].get("user:" + username.lower())
        return _unwrap(slot, password, self._iters(slot)) if isinstance(slot, dict) else None

    def unlock_recovery(self, code: str) -> bytes | None:
        slot = self.data["slots"].get("recovery")
        return _unwrap(slot, _norm_code(code), self._iters(slot)) if isinstance(slot, dict) else None
