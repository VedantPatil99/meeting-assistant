"""Turn the due dates people say in meetings ("by Friday", "end of month") into calendar dates.

Deterministic on purpose: the model copies the phrase, this module does the date arithmetic,
so "next Friday" means the same thing every time and is easy to test.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "ten": 10, "fourteen": 14}

FILLER_RE = re.compile(r"\b(by|before|on|until|due|no later than|the|of|at|latest)\b")


def _weekday(name: str) -> int | None:
    for i, day in enumerate(WEEKDAYS):
        if name == day or (len(name) >= 3 and day.startswith(name)):
            return i
    return None


def _month(name: str) -> int | None:
    for i, month in enumerate(MONTHS, start=1):
        if name == month or (len(name) >= 3 and month.startswith(name)):
            return i
    return None


def _week_day(today: date, weekday: int, next_week: bool = False) -> date:
    """"Friday" is the coming Friday (said on a Friday, it means next week's).
    "Next Friday" is the Friday of next calendar week."""
    if next_week:
        return today + timedelta(days=7 - today.weekday() + weekday)
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def _future_date(today: date, month: int, day: int) -> date | None:
    """`day month` this year, or next year if that's already well past."""
    for year in (today.year, today.year + 1):
        if day > monthrange(year, month)[1]:
            return None
        candidate = date(year, month, day)
        if candidate >= today - timedelta(days=30):
            return candidate
    return None


def resolve_due(text: str | None, meeting_date: date | datetime | str | None = None) -> date | None:
    """The calendar date a spoken due date refers to, counted from the meeting day. None if unclear."""
    if not text:
        return None
    if meeting_date is None:
        today = date.today()
    elif isinstance(meeting_date, str):
        today = datetime.fromisoformat(meeting_date).date()
    elif isinstance(meeting_date, datetime):
        today = meeting_date.date()
    else:
        today = meeting_date

    raw = text.lower().strip()
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    t = FILLER_RE.sub(" ", raw.replace(",", " ").replace(".", " "))
    t = re.sub(r"\s+", " ", t).strip()

    if re.search(r"\b(today|tonight|eod|end day|asap|immediately)\b", t):
        return today
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2)
    if re.search(r"\b(tomorrow|tmrw)\b", t):
        return today + timedelta(days=1)
    if m := re.search(r"\bin (\d+|[a-z]+) (day|week|month)s?\b", t):
        n = int(m[1]) if m[1].isdigit() else NUMBERS.get(m[1])
        if n:
            if m[2] == "day":
                return today + timedelta(days=n)
            if m[2] == "week":
                return today + timedelta(weeks=n)
            month = today.month - 1 + n
            year = today.year + month // 12
            month = month % 12 + 1
            return date(year, month, min(today.day, monthrange(year, month)[1]))
    if m := re.search(r"\bend (next )?week\b|\beow\b", t):
        return _week_day(today, 4, next_week=bool(m.group(1)))
    if re.search(r"\bend month\b|\beom\b|\bmonth end\b", t):
        return date(today.year, today.month, monthrange(today.year, today.month)[1])
    if re.search(r"\bnext week\b", t):
        return _week_day(today, 0, next_week=True)
    if re.search(r"\bnext month\b", t):
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return date(year, month, 1)

    words = t.split()
    # "15 december", "15th dec", "december 15", "dec 15th"
    for i, word in enumerate(words):
        month = _month(word)
        if month is None:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(words) and (d := re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", words[j])):
                return _future_date(today, month, int(d[1]))
    # "15/12" or "15/12/2026", read day first
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        day, month = int(m[1]), int(m[2])
        if 1 <= month <= 12:
            if m[3]:
                year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                try:
                    return date(year, month, day)
                except ValueError:
                    return None
            return _future_date(today, month, day)
    # "friday", "this friday", "next friday"
    for i, word in enumerate(words):
        weekday = _weekday(word)
        if weekday is not None:
            return _week_day(today, weekday, next_week=i > 0 and words[i - 1] == "next")
    return None


def describe_due(due: date | None, today: date | None = None) -> str:
    """Short label for task lists: 'Today', 'Tomorrow', 'Fri 30 Oct', 'Overdue 2 days'."""
    if due is None:
        return "No date"
    today = today or date.today()
    delta = (due - today).days
    if delta < 0:
        return f"Overdue {-delta} day{'s' if delta < -1 else ''}"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{due:%a} {due.day} {due:%b}" + (f" {due.year}" if due.year != today.year else "")
