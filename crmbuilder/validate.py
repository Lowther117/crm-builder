"""Field kinds: what each one accepts, how it is tidied, stored and shown.

Every value goes through normalise() before it is saved, so wrong information
cannot get into the database: a date is a real date, a postcode is a real UK
postcode shape, a phone number has the right number of digits.

Stored forms (what lives in a record's JSON):
  text, longtext, email, phone, postcode, ni, url, choice   str
  number                                                    int or float
  money, percent                                            float
  date                                                      'YYYY-MM-DD'
  tags                                                      list of str
  yesno                                                     True / False
  link, user                                                int (record id / user id)
An empty value is never stored: the key is simply absent.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

# key -> (label, one-line description, example)
KINDS = {
    "text":     ("Text", "A short piece of text: a name, a job title, a reference.", "Acme Ltd"),
    "longtext": ("Long text", "Several lines: an address, a description, background.", ""),
    "number":   ("Number", "A plain number you might add up or sort by.", "42"),
    "money":    ("Money (£)", "An amount in pounds. Shown with a £ sign and pence.", "£1,250.00"),
    "percent":  ("Percentage", "A number shown with a % sign.", "75%"),
    "date":     ("Date", "A UK date. Type 13112026 and the slashes are added for you.", "13/11/2026"),
    "choice":   ("Choice (pick one)", "A dropdown with the options you list.", "Lead / Customer"),
    "tags":     ("Tags (pick several)", "Tick boxes: any number of the options you list.", "VIP, Newsletter"),
    "yesno":    ("Yes / No", "A simple tick box.", "Yes"),
    "email":    ("Email address", "Checked for the right shape. Click to write an email.", "sam@example.co.uk"),
    "phone":    ("Phone number", "UK numbers are checked and spaced for you; +44 becomes 0.", "07700 900123"),
    "postcode": ("Postcode", "Checked against the UK format and tidied.", "CF48 4TQ"),
    "url":      ("Website", "A web address. Click to open it.", "https://example.co.uk"),
    "ni":       ("National Insurance no.", "Checked against the NI number format.", "AB 12 34 56 C"),
    "link":     ("Link to another record", "Connects this record to one in another list, "
                 "e.g. a contact to their organisation.", ""),
    "user":     ("Team member", "One of the people who use this CRM, e.g. an owner.", ""),
}
KIND_ORDER = ["text", "longtext", "choice", "tags", "yesno", "date", "number", "money",
              "percent", "email", "phone", "postcode", "url", "ni", "link", "user"]
# Kinds whose stored value is plain text, so one can be changed to another
# without touching the data already entered.
TEXTY = {"text", "longtext", "email", "phone", "postcode", "url", "ni", "choice"}


def kind_label(kind: str) -> str:
    return KINDS.get(kind, (kind,))[0]


def kind_from_label(label: str) -> str:
    for k, v in KINDS.items():
        if v[0] == label:
            return k
    return "text"


# ------------------------------------------------------------------ dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
                "december": 12})


def _year(y: int) -> int:
    if y < 100:
        pivot = (_dt.date.today().year % 100) + 20
        return 2000 + y if y <= pivot else 1900 + y
    return y


def parse_date(text: str) -> _dt.date | None:
    """UK-first date parsing. Returns None when it is not a real date."""
    s = (text or "").strip().lower()
    if not s:
        return None
    today = _dt.date.today()
    if s == "today":
        return today
    if s == "tomorrow":
        return today + _dt.timedelta(days=1)
    if s == "yesterday":
        return today - _dt.timedelta(days=1)
    m = re.fullmatch(r"([+-])\s*(\d{1,4})\s*([dwmy]?)", s)
    if m:
        n = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        unit = m.group(3) or "d"
        if unit == "d":
            return today + _dt.timedelta(days=n)
        if unit == "w":
            return today + _dt.timedelta(weeks=n)
        months = n if unit == "m" else n * 12
        y, mo = divmod(today.year * 12 + today.month - 1 + months, 12)
        for day in range(today.day, 27, -1):
            try:
                return _dt.date(y, mo + 1, day)
            except ValueError:
                continue
        try:
            return _dt.date(y, mo + 1, min(today.day, 28))
        except ValueError:
            return None
    # a time after the date is ignored (spreadsheets export "01/02/2026 00:00")
    s = re.sub(r"[t ]+\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*([ap]m)?\s*(z|[+-]\d{2}:?\d{2})?$", "", s)
    try:
        m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[t ].*)?", s)
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})\s*[/\-. ]\s*(\d{1,2})\s*[/\-. ]\s*(\d{2}|\d{4})", s)
        if m:
            return _dt.date(_year(int(m.group(3))), int(m.group(2)), int(m.group(1)))
        m = re.fullmatch(r"(\d{2})(\d{2})(\d{4}|\d{2})", s)
        if m:
            return _dt.date(_year(int(m.group(3))), int(m.group(2)), int(m.group(1)))
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/]*([a-z]+)[\s\-/,]*(\d{2}|\d{4})", s)
        if m and m.group(2) in _MONTHS:
            return _dt.date(_year(int(m.group(3))), _MONTHS[m.group(2)], int(m.group(1)))
        m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
        if m and m.group(1) in _MONTHS:
            return _dt.date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None
    return None


def iso_to_uk(iso: str) -> str:
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    except (ValueError, AttributeError):
        return str(iso or "")


def format_stamp(stamp: str) -> str:
    """'2026-10-07T13:05:00' (UTC) -> '07/10/2026 14:05' in local time."""
    try:
        t = _dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=_dt.timezone.utc).astimezone()
        return t.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return str(stamp or "")


def now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def friendly_date(iso: str) -> str:
    """Today / Tomorrow / Mon 12 Oct / 12/10/2027 - for task lists."""
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    delta = (d - _dt.date.today()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if -7 < delta < 0:
        return f"{-delta} days ago"
    if d.year == _dt.date.today().year:
        return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return iso_to_uk(iso)


# ------------------------------------------------------------- normalisers
_POSTCODE = re.compile(
    r"^(GIR0AA|[A-PR-UWYZ]([0-9]{1,2}|([A-HK-Y][0-9]([0-9ABEHMNPRV-Y])?)|[0-9][A-HJKPS-UW])"
    r"[0-9][ABD-HJLNP-UW-Z]{2})$")
_NI = re.compile(r"^(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z][0-9]{6}[A-D]$")
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")


def norm_postcode(text: str):
    s = re.sub(r"\s+", "", text).upper()
    if not _POSTCODE.match(s):
        return None, "This is not a UK postcode (for example CF48 4TQ)."
    return s[:-3] + " " + s[-3:], None


def norm_ni(text: str):
    s = re.sub(r"[\s-]+", "", text).upper()
    if not _NI.match(s):
        return None, "This is not a National Insurance number (for example AB 12 34 56 C)."
    return f"{s[0:2]} {s[2:4]} {s[4:6]} {s[6:8]} {s[8]}", None


def norm_email(text: str):
    s = text.strip()
    if " " in s or not _EMAIL.match(s) or ".." in s or len(s) > 254:
        return None, "This is not an email address (for example sam@example.co.uk)."
    local, _, domain = s.rpartition("@")
    if local.startswith(".") or local.endswith("."):
        return None, "This is not an email address (for example sam@example.co.uk)."
    return local + "@" + domain.lower(), None


def _space_uk(d: str) -> str:
    """Space an 0-prefixed UK number the way people write it."""
    if d.startswith("02"):
        return f"{d[:3]} {d[3:7]} {d[7:]}"
    if d.startswith("07") or d.startswith("05"):
        return f"{d[:5]} {d[5:]}"
    if d.startswith(("03", "08", "09")):
        if len(d) == 10:
            return f"{d[:4]} {d[4:]}"
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    if d.startswith("011") or re.match(r"01\d1", d):
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    return f"{d[:5]} {d[5:]}"


def norm_phone(text: str):
    s = text.strip()
    ext = ""
    m = re.search(r"\s*(?:ext\.?|x|extension)\s*(\d{1,6})\s*$", s, re.I)
    if m:
        ext = " ext " + m.group(1)
        s = s[:m.start()]
    if re.search(r"[^\d\s()+.\-]", s):
        return None, "A phone number can only contain digits, spaces and + ( ) -."
    plus = s.lstrip().startswith("+")
    d = re.sub(r"\D", "", s)
    if plus or d.startswith("00"):
        if d.startswith("00"):
            d = d[2:]
        if d.startswith("44"):
            d = "0" + d[2:].lstrip("0")
        else:
            if not 7 <= len(d) <= 15:
                return None, "This international number has the wrong number of digits."
            return "+" + d + ext, None
    if not d.startswith("0"):
        return None, "A UK phone number starts with 0 (or use +country code)."
    if d[1:2] not in "1235789":
        return None, "This is not a UK phone number."
    if d == "08001111":       # Childline: the one 8-digit number still in use
        return "0800 1111" + ext, None
    if len(d) not in (10, 11):
        return None, f"A UK phone number has 10 or 11 digits - this has {len(d)}."
    if d.startswith("07") and len(d) != 11:
        return None, f"A UK mobile number has 11 digits - this has {len(d)}."
    if d.startswith(("02", "03")) and len(d) != 11:
        return None, f"A UK number starting {d[:2]} has 11 digits - this has {len(d)}."
    return _space_uk(d) + ext, None


def norm_url(text: str):
    s = text.strip()
    if " " in s:
        return None, "A web address cannot contain spaces."
    bare = not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I)
    if bare:
        s = "https://" + s
    m = re.match(r"^(https?)://([^/?#]+)(.*)$", s, re.I)
    if not m:
        return None, "This is not a web address (for example example.co.uk)."
    host = m.group(2).lower()
    if bare and "@" in host:
        return None, "This looks like an email address, not a web address."
    name = host.split("@")[-1].split(":")[0]
    if "." not in name or not re.match(r"^[a-z0-9.-]+$", name) or name.startswith(".") \
            or name.endswith(".") or ".." in name:
        return None, "This is not a web address (for example example.co.uk)."
    return m.group(1).lower() + "://" + host + m.group(3), None


def _number_text(text: str):
    """What was typed as a plain decimal ('-1250.5'), or None if it is not a number."""
    s = text.strip().replace("£", "").replace("%", "").replace(" ", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if "," in s:
        # commas only between thousands: "1,250" is fine, "12,50" is a slip that
        # must not quietly become 1250
        if not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d*)?", s):
            return None
        s = s.replace(",", "")
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)", s):
        return None
    if neg:
        s = s[1:] if s.startswith("-") else "-" + s.lstrip("+")
    return s


def parse_number(text: str):
    s = _number_text(text)
    if s is None:
        return None
    v = float(s)
    return v if v - v == 0 else None      # too long to be a number at all


def _plain(v: float) -> str:
    """A number written out in full with no exponent, so that typing it back
    gives exactly the same number."""
    r = repr(float(v))
    if "e" in r or "E" in r:
        r = format(Decimal(r), "f")
    return r[:-2] if r.endswith(".0") else r


def _fmt_num(v: float, decimals) -> str:
    if decimals is None:
        if float(v).is_integer():
            return f"{int(v):,}"
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    return f"{v:,.{int(decimals)}f}"


def options_of(field: dict) -> list[str]:
    return [str(o) for o in field.get("options") or [] if str(o).strip()]


def _match_option(field: dict, text: str):
    for o in options_of(field):
        if o.lower() == text.strip().lower():
            return o
    return None


TRUE_WORDS = {"yes", "y", "true", "1", "x", "on", "tick", "ticked", "✓", "✔"}
FALSE_WORDS = {"no", "n", "false", "0", "off", ""}


def normalise(field: dict, raw, resolve=None):
    """Turn what was typed (or imported) into the stored value.

    Returns (value, error). value is None for 'empty'. error is a sentence fit
    to show under the field, or None. resolve(kind, text, field) is used for
    link and user fields given as text (imports); the UI passes ids directly.
    """
    kind = field.get("kind", "text")
    name = field.get("name", "This field")
    if raw is None:
        raw = ""
    if kind == "yesno":
        if isinstance(raw, bool):
            return raw, None
        s = str(raw).strip().lower()
        if s in TRUE_WORDS:
            return True, None
        if s in FALSE_WORDS:
            return (None if s == "" else False), None
        return None, "Use Yes or No."
    if kind == "tags":
        if isinstance(raw, (list, tuple)):
            items = raw
        else:       # (an option may itself contain a comma)
            whole = _match_option(field, str(raw))
            items = [whole] if whole is not None else re.split(r"[;,|\n]", str(raw))
        out, bad = [], []
        for item in items:
            t = str(item).strip()
            if not t:
                continue
            o = _match_option(field, t)
            if o is None:
                bad.append(t)
            elif o not in out:
                out.append(o)
        if bad:
            return None, "Not one of the options: " + ", ".join(bad) + "."
        if not out:
            return (None, f"{name} is needed.") if field.get("required") else (None, None)
        return out, None
    if kind in ("link", "user"):
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw, None
        s = str(raw).strip()
        if not s:
            return (None, f"{name} is needed.") if field.get("required") else (None, None)
        if resolve is not None:
            got = resolve(kind, s, field)
            if got is not None:
                return got, None
        if re.fullmatch(r"[0-9]+", s) and resolve is None:
            return int(s), None
        what = "team member" if kind == "user" else "record"
        return None, f"No matching {what} found for “{s}”."

    s = str(raw).strip() if kind != "longtext" else str(raw).strip("\n\r ").rstrip()
    if not s:
        return (None, f"{name} is needed.") if field.get("required") else (None, None)

    if kind in ("text", "longtext"):
        if kind == "text":
            s = re.sub(r"\s+", " ", s)
        mx = field.get("maxlen")
        if mx and len(s) > int(mx):
            return None, f"Too long: {len(s)} characters, the most allowed is {int(mx)}."
        return s, None
    if kind == "choice":
        o = _match_option(field, s)
        if o is None:
            return None, f"“{s}” is not one of the options."
        return o, None
    if kind == "date":
        d = parse_date(s)
        if d is None:
            return None, "This is not a real date. Use day/month/year, e.g. 13/11/2026."
        if not 1800 <= d.year <= 2200:
            return None, "That year does not look right."
        rule = field.get("date_rule")
        today = _dt.date.today()
        if rule == "past" and d > today:
            return None, "This date cannot be in the future."
        if rule == "future" and d < today:
            return None, "This date cannot be in the past."
        return d.isoformat(), None
    if kind in ("number", "money", "percent"):
        v = parse_number(s)
        if v is None:
            return None, "This is not a number."
        v += 0.0                  # ("-0" is just 0)
        if kind == "money":
            v = round(v, 2) + 0.0
        dec = field.get("decimals")
        if kind == "number" and dec is not None and str(dec) != "":
            if round(v, int(dec)) != v:
                d = int(dec)
                return None, ("Whole numbers only." if d == 0
                              else f"No more than {d} decimal place{'s' if d != 1 else ''}.")
        mn, mx = field.get("min"), field.get("max")
        if mn not in (None, "") and v < float(mn):
            return None, f"The smallest allowed is {_fmt_num(float(mn), None)}."
        if mx not in (None, "") and v > float(mx):
            return None, f"The largest allowed is {_fmt_num(float(mx), None)}."
        if kind == "number" and float(v).is_integer():
            # whole numbers are kept exactly however long they are (as a float
            # a 17-digit number would silently change its last digits)
            return (int(v) if abs(v) < 1e15 else int(Decimal(_number_text(s)))), None
        return v, None
    if kind == "email":
        return norm_email(s)
    if kind == "phone":
        return norm_phone(s)
    if kind == "postcode":
        return norm_postcode(s)
    if kind == "ni":
        return norm_ni(s)
    if kind == "url":
        return norm_url(s)
    return s, None


def to_edit(field: dict, value) -> str:
    """Stored value -> the text shown in an edit box."""
    if value is None:
        return ""
    kind = field.get("kind", "text")
    if kind == "date":
        return iso_to_uk(value)
    if kind == "money":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
    if kind in ("number", "percent"):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        try:
            return _plain(value)
        except (TypeError, ValueError, ArithmeticError):
            return str(value)
    if kind == "tags":
        return ", ".join(value) if isinstance(value, (list, tuple)) else str(value)
    if kind == "yesno":
        return "Yes" if value else "No"
    return str(value)


def display(field: dict, value, lookup=None) -> str:
    """Stored value -> text for lists, exports and print-outs.
    lookup(kind, id) gives the title of a linked record or a user's name."""
    if value is None or value == "" or value == []:
        return ""
    kind = field.get("kind", "text")
    try:
        if kind == "date":
            return iso_to_uk(value)
        if kind == "money":
            v = float(value)
            return ("-£" if v < 0 else "£") + f"{abs(v):,.2f}"
        if kind == "percent":
            return _fmt_num(float(value), None) + "%"
        if kind == "number":
            dec = field.get("decimals")
            dec = int(dec) if dec not in (None, "") else None
            if isinstance(value, int) and not isinstance(value, bool):    # exact, however long
                return f"{value:,}" if dec is None else format(Decimal(value), f",.{dec}f")
            return _fmt_num(float(value), dec)
        if kind == "yesno":
            return "Yes" if value else "No"
        if kind == "tags":
            return ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
        if kind in ("link", "user"):
            if lookup is not None:
                got = lookup(kind, value)
                if got:
                    return got
            return "" if kind == "link" else str(value)
    except (TypeError, ValueError):
        pass
    return str(value)


def sort_key(field: dict, value):
    """A key that sorts stored values sensibly, blanks last."""
    kind = field.get("kind", "text")
    if value is None or value == "" or value == []:
        return (1, 0, "")
    if kind in ("number", "money", "percent"):
        try:
            return (0, float(value), "")
        except (TypeError, ValueError):
            return (0, 0, str(value))
    if kind == "yesno":
        return (0, 0 if value else 1, "")
    if kind == "choice":
        opts = options_of(field)
        return (0, opts.index(value) if value in opts else len(opts), str(value).lower())
    if kind == "tags":
        return (0, 0, ", ".join(value).lower() if isinstance(value, (list, tuple)) else str(value))
    return (0, 0, str(value).lower())


# ------------------------------------------------- typing help (live filters)
def allowed_chars(kind: str):
    """Characters that may be typed into a field of this kind (None = anything)."""
    return {
        "number": "0123456789.,-",
        "money": "0123456789.,-£",
        "percent": "0123456789.,-%",
        "phone": "0123456789 +()-.extEXT",
        "postcode": None, "ni": None,
    }.get(kind)


def live_date(text: str) -> str:
    """Add the slashes while a date is typed: 1311 -> 13/11/ , 13112026 -> 13/11/2026."""
    if not re.fullmatch(r"[\d/]*", text):
        return text
    if re.fullmatch(r"\d{2}", text):
        return text + "/"
    if re.fullmatch(r"\d{2}/\d{2}", text):
        return text + "/"
    if re.fullmatch(r"\d{3,8}", text):
        d = text
        out = d[:2] + "/" + d[2:4]
        if len(d) >= 4:
            out += "/" + d[4:]
        return out
    return text


# ----------------------------------------------------- guessing from a sheet
def guess_kind(name: str, values: list[str]) -> tuple[str, list[str]]:
    """Guess the kind of a spreadsheet column from its heading and contents.
    Returns (kind, options) - options only for choice."""
    vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
    low = (name or "").lower()
    if not vals:
        for word, kind in (("email", "email"), ("e-mail", "email"), ("phone", "phone"),
                           ("mobile", "phone"), ("tel", "phone"), ("postcode", "postcode"),
                           ("post code", "postcode"), ("date", "date"), ("dob", "date"),
                           ("website", "url"), ("url", "url"), ("notes", "longtext"),
                           ("address", "longtext")):
            if word in low:
                return kind, []
        return "text", []

    def share(test) -> float:
        return sum(1 for v in vals if test(v)) / len(vals)

    ok = 0.9
    # a heading that says what the column is lowers the bar: one or two bad
    # values in an "Email" column should not turn the whole column into text
    hinted = {"email": ("email", "e-mail"), "phone": ("phone", "mobile", "tel", "fax"),
              "postcode": ("postcode", "post code"), "date": ("date", "dob", "birthday")}

    def bar(kind):
        return 0.6 if any(w in low for w in hinted.get(kind, ())) else ok

    if share(lambda v: norm_email(v)[1] is None) >= bar("email"):
        return "email", []
    all01 = all(v in ("0", "1") for v in vals)
    words = {v.lower() for v in vals}
    if words <= (TRUE_WORDS | {"no", "n", "false", "0"}) and (len(words) >= 2 or len(vals) >= 3) \
            and (not all01 or re.search(r"\b(is|has|consent|active|opt)", low)) \
            and not any(w in low for w in ("note", "comment")):
        return "yesno", []
    if share(lambda v: norm_postcode(v)[1] is None) >= bar("postcode"):
        return "postcode", []
    if share(lambda v: norm_ni(v)[1] is None) >= ok:
        return "ni", []
    phoneish = any(w in low for w in ("phone", "mobile", "tel", "fax"))
    # "Member ID", "Order number", "Ref" hold labels, not amounts - but whole
    # words only: "Bid value", "Paid" and "Number of rooms" are real numbers
    labelish = "#" in low or bool(re.search(r"\b(ref|reference|id|code|no)\b|\bnumber$",
                                            re.sub(r"[\W_]+", " ", low).strip()))
    if share(lambda v: norm_phone(v)[1] is None and len(re.sub(r"\D", "", v)) >= 10) >= bar("phone") and (
            phoneish or share(lambda v: v.lstrip("+( ").startswith(("0", "44"))) >= ok):
        return "phone", []
    if share(lambda v: bool(re.match(r"^(https?://|www\.)", v, re.I))) >= ok:
        return "url", []
    if share(lambda v: parse_date(v) is not None and not re.fullmatch(r"\d{1,5}", v)
             and not re.fullmatch(r"[+-]\d+[dwmy]?", v)) >= bar("date") \
            and not share(lambda v: bool(re.fullmatch(r"\d{6,8}", v))) >= 0.5:
        return "date", []
    if share(lambda v: v.startswith("£") or v.startswith("-£")) >= 0.5 and \
            share(lambda v: parse_number(v) is not None) >= ok:
        return "money", []
    if share(lambda v: v.endswith("%") and parse_number(v) is not None) >= ok:
        return "percent", []
    if share(lambda v: parse_number(v) is not None) >= 0.97 and not phoneish \
            and not labelish \
            and not any(v.startswith("0") and len(v) > 1 and "." not in v for v in vals):
        if any(w in low for w in ("price", "cost", "amount", "value", "fee", "salary",
                                  "budget", "total", "£")):
            return "money", []
        return "number", []
    if any("\n" in v for v in vals) or max(len(v) for v in vals) > 120:
        return "longtext", []
    distinct = []
    for v in vals:
        if v.lower() not in [d.lower() for d in distinct]:
            distinct.append(v)
            if len(distinct) > 12:
                break
    if len(vals) >= 6 and len(distinct) <= 12 and len(distinct) <= max(2, len(vals) // 3) \
            and max(len(d) for d in distinct) <= 40 \
            and not any(w in low for w in ("name", "town", "city", "county")):
        return "choice", distinct
    return "text", []
