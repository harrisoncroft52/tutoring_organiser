"""Reading dates, times, calendar pastes and bank-statement pastes out of text and file names."""
import os
import re
from datetime import date as Date, datetime, timedelta
from pathlib import Path


DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-_./ ](\d{1,2})[-_./ ](\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[-_./ ](\d{1,2})[-_./ ](\d{2}|\d{4})"), ("d", "m", "y")),
]


TEXT_DATE_RX = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[-_. ]*([A-Za-z]{3,9})\.?(?:[-_. ]*(\d{4}))?(?!\d)")


def date_from_filename(path):
    """Try to read a date out of a file name; fall back to the file's modified time."""
    name = Path(path).stem
    for rx, order in DATE_PATTERNS:
        m = rx.search(name)
        if m:
            parts = dict(zip(order, map(int, m.groups())))
            try:
                if parts["y"] < 100:
                    parts["y"] += 2000 if parts["y"] < 70 else 1900
                return datetime(parts["y"], parts["m"], parts["d"]), "filename"
            except ValueError:
                pass
    for m in TEXT_DATE_RX.finditer(name):
        mon = month_num(m.group(2))
        if not mon:
            continue
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            when = datetime(year, mon, int(m.group(1)))
        except ValueError:
            continue
        if not m.group(3) and when > datetime.now() + timedelta(days=90):
            when = when.replace(year=year - 1)        # "20 Dec" seen in March means last December
        return when, "filename"
    return datetime.fromtimestamp(os.path.getmtime(path)).replace(hour=0, minute=0, second=0, microsecond=0), "modified"


def pdfs_in(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def month_num(word):
    w = word.lower().rstrip(".")
    for i, name in enumerate(_MONTHS, 1):
        if w in (name, name[:3]) or (i == 9 and w == "sept"):
            return i
    return None


def weekday_ok(d, word):
    w = (word or "")[:3].lower()
    return w not in _WEEKDAYS or _WEEKDAYS.index(w) == d.weekday()


def clean_line(s):
    s = s.replace("\u202f", " ").replace("\u00a0", " ")
    for d in "\u2010\u2011\u2012\u2013\u2014\u2212":
        s = s.replace(d, "-")
    return s.strip()


_T = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
RANGE_RE = re.compile(rf"^{_T}\s*(?:-|to)\s*{_T}$", re.I)


def parse_time_range(text):
    """'3 - 4pm', '4:30 - 6pm', '11am - 12pm', '16:00 - 17:00' -> (start_minutes, duration_minutes)."""
    m = RANGE_RE.match(text)
    if not m:
        return None
    h1, m1, a1, h2, m2, a2 = m.groups()
    h1, h2, m1, m2 = int(h1), int(h2), int(m1 or 0), int(m2 or 0)
    a1 = a1.replace(".", "").lower() if a1 else None
    a2 = a2.replace(".", "").lower() if a2 else None
    if m1 > 59 or m2 > 59 or h1 > 24 or h2 > 24:
        return None

    def to_min(h, mi, ap):
        if ap:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if ap == "pm" else 0)
        return h * 60 + mi

    if a1 is None and a2 is not None:          # '3 - 4pm': the start borrows the end's am/pm
        s, e = to_min(h1, m1, a2), to_min(h2, m2, a2)
        if s is not None and e is not None and s >= e:
            alt = to_min(h1, m1, "am")         # '11 - 1pm' means 11am
            if alt is not None and alt < e:
                s = alt
    elif a1 is not None and a2 is None:        # '3pm - 4': the end borrows the start's am/pm
        s, e = to_min(h1, m1, a1), to_min(h2, m2, a1)
        if s is not None and e is not None and e <= s:
            alt = to_min(h2, m2, "pm" if a1 == "am" else "am")
            if alt is not None and alt > s:
                e = alt
    else:                                      # both given, or plain 24-hour times
        s, e = to_min(h1, m1, a1), to_min(h2, m2, a2)
    if s is None or e is None:
        return None
    dur = (e - s) % 1440
    return (s, dur) if dur else None


def fmt_clock(minutes):
    h, m = divmod(minutes % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


MY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})(?:\s*,\s*([A-Za-z]{3,9}))?$")
TITLE_RE = re.compile(r"^\s*tutoring\b\s*[-:]?\s*(.*)$", re.I)


def student_from_title(title):
    m = TITLE_RE.match(title)
    if not m:
        return None
    name = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
    return name or None


def parse_calendar_paste(text):
    """
    Parses Google Calendar search results:
        30 / Oct 2026, Fri / 3 - 4pm / Tutoring Lorenzo / 4:30 - 6pm / Tutoring Noah / (blank) / 2 / Nov 2026, Mon ...
    Returns (items, warnings). Each item: start (datetime), duration (min), title, name (None if not 'Tutoring X').
    """
    lines = [l for l in (clean_line(x) for x in text.splitlines()) if l]
    items, warnings = [], []
    cur = pending = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        my = MY_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
        if re.fullmatch(r"\d{1,2}", ln) and my:
            mon = month_num(my.group(1))
            try:
                cur = Date(int(my.group(2)), mon, int(ln))
            except (TypeError, ValueError):
                warnings.append(f"Couldn't read the date '{ln} {lines[i + 1]}'.")
                cur = None
            else:
                if not weekday_ok(cur, my.group(3)):
                    warnings.append(f"{ln} {lines[i + 1]}: that day of the week doesn't match the date. Please check it.")
            pending = None
            i += 2
            continue
        tr = parse_time_range(ln)
        if tr:
            if cur is None:
                warnings.append(f"The time '{ln}' has no date above it.")
            else:
                pending = tr
            i += 1
            continue
        if pending and cur:
            start = datetime(cur.year, cur.month, cur.day) + timedelta(minutes=pending[0])
            items.append({"start": start, "duration": pending[1], "title": ln, "name": student_from_title(ln)})
            pending = None
        else:
            warnings.append(f"Couldn't read the line: {ln}")
        i += 1
    return items, warnings


PAY_MONTH_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{4})$")
PAY_DAY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})$")
SECTION_RE = re.compile(r"^(upcoming|past)\s+(?:lessons?|payments?|sessions?)$", re.I)
DOLLAR_RE = re.compile(r"^(?:[A-Za-z]{1,3})?\s*([-+]?)\s*\$\s*([\d,]+(?:\.\d{1,2})?)$")
PLAIN_RE = re.compile(r"^([-+]?)\s*([\d,]+(?:\.\d{1,2})?)$")          # bare '116.00' (bank-style lines only)
BANK_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$")  # '12 Oct 2026'


def parse_payments_paste(text):
    """
    Two layouts are understood, and they can be mixed:

    1. Lesson list:   October2026 / Tue27 / 16:00 - 17:00 / $45, or month, year,
       weekday, and day each on their own line before the time and amount.
       (with optional 'Upcoming lessons' / 'Past lessons' dividers)
    2. Bank style:    12 Oct 2026 / D T & K E Carpenter Monday Carpenter Ben / 116.00 / 17 Sep 2026 / ...
       (date line, then the payer/description, then the amount)

    Returns (items, warnings). Each item: date, amount, note, upcoming (bool).
    """
    lines = [l for l in (clean_line(x) for x in text.splitlines()) if l]
    items, warnings = [], []
    month = year = cur = pending = mode = None
    pending_weekday = None
    bank, desc = False, ""

    def money_match(ln):
        am = DOLLAR_RE.match(ln) or (PLAIN_RE.match(ln) if bank else None)
        if not am:
            return None
        return float(am.group(2).replace(",", "")) * (-1 if am.group(1) == "-" else 1)

    for ln in lines:
        mk = SECTION_RE.match(ln)
        if mk:
            if mk.group(1).lower() == "past":
                if mode is None:                      # everything above a 'Past lessons' divider is upcoming
                    for it in items:
                        if not it.get("bank"):        # bank-style lines are always real, past payments
                            it["upcoming"] = True
                mode = "past"
            else:
                mode = "upcoming"
            month = year = cur = pending = None       # a new month header follows the divider
            bank, desc = False, ""
            continue
        bm = BANK_DATE_RE.match(ln)
        if bm and month_num(bm.group(2)):
            try:
                cur = Date(int(bm.group(3)), month_num(bm.group(2)), int(bm.group(1)))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                bank = False
                continue
            bank, desc, pending = True, "", None
            continue
        m = PAY_MONTH_RE.match(ln)
        if m and month_num(m.group(1)):
            month, year, cur, pending = month_num(m.group(1)), int(m.group(2)), None, None
            pending_weekday = None
            bank, desc = False, ""
            continue
        # Some copied lesson lists put the month and year on separate lines.
        standalone_month = re.fullmatch(r"([A-Za-z]{3,9})\.?", ln)
        if standalone_month and month_num(standalone_month.group(1)):
            month, year, cur, pending = month_num(standalone_month.group(1)), None, None, None
            pending_weekday = None
            bank, desc = False, ""
            continue
        if month is not None and year is None and re.fullmatch(r"\d{4}", ln):
            year = int(ln)
            continue
        m = PAY_DAY_RE.match(ln)
        if m and m.group(1)[:3].lower() in _WEEKDAYS:
            bank, desc = False, ""
            if month is None:
                warnings.append(f"'{ln}' has no month above it.")
                continue
            try:
                cur = Date(year, month, int(m.group(2)))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                continue
            if not weekday_ok(cur, m.group(1)):
                warnings.append(f"{ln}: that day of the week doesn't match the date. Please check it.")
            pending = None
            pending_weekday = None
            continue
        # Also accept a weekday and day number on separate lines (e.g. Fri / 21).
        if re.fullmatch(r"[A-Za-z]{3,9}\.?", ln) and ln[:3].lower() in _WEEKDAYS:
            pending_weekday = ln
            continue
        if month is not None and year is not None and re.fullmatch(r"\d{1,2}", ln):
            try:
                cur = Date(year, month, int(ln))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                pending_weekday = None
                continue
            if pending_weekday and not weekday_ok(cur, pending_weekday):
                warnings.append(f"{pending_weekday} {ln}: that day of the week doesn't match the date. Please check it.")
            pending_weekday = None
            pending = None
            bank, desc = False, ""
            continue
        tr = parse_time_range(ln)
        if tr and not bank:
            pending = tr
            continue
        amount = money_match(ln)
        if amount is not None and cur:
            if bank:
                note = desc
            else:
                note = (f"Lesson {fmt_clock(pending[0])} - {fmt_clock(pending[0] + pending[1])}" if pending else "")
            items.append({"date": cur, "amount": amount, "note": note, "upcoming": mode == "upcoming", "bank": bank})
            pending, desc = None, ""
            continue
        if bank and cur:
            desc = (desc + " " + ln).strip()          # payer / description line(s)
            continue
        warnings.append(f"Couldn't read the line: {ln}")
    return items, warnings
