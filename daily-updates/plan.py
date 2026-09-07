#!/usr/bin/env python3
"""Free time, computed.

The shape of a week is fixed and known - office days, a work-from-home Friday,
when the day starts and ends. The calendar is what varies. Subtract one from the
other and what is left is the time actually available tonight.

That subtraction happens here, in Python, for the same reason every habit number
does: the model is handed finished slots and writes over them. Asked to invent
times it produces plausible ones, and a plausible schedule that double-books you
is worse than no schedule at all.
"""

import json
from datetime import timedelta
from pathlib import Path

MIN_SLOT = 30       # minutes; anything shorter is not a slot, it is a gap
ASSUMED_EVENT = 60  # an event with no end time is treated as an hour

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def minutes(hhmm):
    """"17:30" -> 1050. Midnight as an end time means the end of the day."""
    if not hhmm:
        return None
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return 24 * 60 if h == 0 and m == 0 else h * 60 + m


def clock(mins):
    """1050 -> "5:30pm". The inverse of minutes(), for display."""
    if mins >= 24 * 60:
        return "midnight"     # the weekend bedtime; "11:59pm" reads as a glitch
    h, m = divmod(int(mins), 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}" if m else f"{h12}{suffix}"


def span(mins):
    """A duration, spoken the way a person would: 90 -> "1h30"."""
    h, m = divmod(int(mins), 60)
    if not h:
        return f"{m}m"
    return f"{h}h{m:02d}" if m else f"{h}h"


def load(path):
    return json.loads(Path(path).read_text())


def merge(windows):
    """Overlapping busy windows collapse into one."""
    out = []
    for start, end in sorted(windows):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def busy_windows(week, day, events):
    """Everything spoken for: the fixed week shape plus today's calendar."""
    shape = week["days"][DAYS[day.weekday()]]
    windows = [[minutes(a), minutes(b)] for a, b in shape.get("busy", [])]
    for e in events:
        if not e.get("time"):
            continue  # all-day events block nothing in particular
        start = minutes(e["time"])
        end = minutes(e.get("ends")) or start + ASSUMED_EVENT
        windows.append([start, max(end, start + 1)])
    return merge(windows)


def free_slots(week, day, events, wake, bed):
    """What is left between waking and the target bedtime, longest first.

    Slots are returned in clock order, which is how they will be read; the
    caller sorts differently if it wants to.
    """
    lo, hi = minutes(wake), minutes(bed)
    out, cursor = [], lo
    for start, end in busy_windows(week, day, events):
        if start > cursor:
            out.append((cursor, min(start, hi)))
        cursor = max(cursor, end)
        if cursor >= hi:
            break
    if cursor < hi:
        out.append((cursor, hi))
    return [{"from": clock(a), "to": clock(b), "minutes": b - a}
            for a, b in out if b - a >= MIN_SLOT and a < hi]


def day_note(week, day):
    return week["days"][DAYS[day.weekday()]].get("note")


def week_progress(con, habits, today):
    """How each habit stands this week, Monday to now.

    Distinct from the 30-day rate in the morning message: that one is identity,
    this one is what is still outstanding before Sunday.
    """
    monday = today - timedelta(days=today.weekday())
    out = []
    for h in habits:
        if h["kind"] != "do":
            continue
        done = con.execute(
            "SELECT COUNT(*) FROM completions WHERE habit=? AND day>=? AND day<=?",
            (h["key"], monday.isoformat(), today.isoformat()),
        ).fetchone()[0]
        target = h["target_per_week"]
        out.append({
            "name": h["label"],
            "done_this_week": done,
            "target_this_week": target,
            "still_needed": max(0, target - done),
            "days_left_including_today": 7 - today.weekday(),
        })
    return out
