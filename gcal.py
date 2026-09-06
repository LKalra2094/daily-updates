#!/usr/bin/env python3
"""Read today's events from Google Calendar secret iCal feeds.

Stdlib only. Rather than expanding every recurrence rule into a full series,
this answers the only question the briefing asks: does this event happen on
the given day?
"""

import urllib.request
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "healthy-life/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def unfold(text):
    """iCal wraps long lines by starting the continuation with a space/tab."""
    out = []
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_events(text):
    """Split into VEVENT blocks of {PROP: (params, value)}."""
    events, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *raw = head.split(";")
            params = dict(
                p.split("=", 1) for p in raw if "=" in p
            )
            # Repeated props (EXDATE, RDATE) accumulate.
            if name in cur:
                cur[name] = (cur[name][0], cur[name][1] + "," + value)
            else:
                cur[name] = (params, value)
    return events


def as_datetime(params, value, tz):
    """Return (date, time_or_None). Time is local to tz."""
    value = value.split(",")[0]
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), None
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        local = naive.replace(tzinfo=timezone.utc).astimezone(tz)
    else:
        # TZID given: trust the feed's own zone, else treat as already local.
        try:
            from zoneinfo import ZoneInfo
            local = naive.replace(tzinfo=ZoneInfo(params["TZID"])).astimezone(tz)
        except Exception:
            local = naive
    return local.date(), local.strftime("%H:%M")


def occurs_on(ev, target, tz):
    """Does this VEVENT land on `target`? Returns the time, "" for all-day, or None."""
    if "DTSTART" not in ev:
        return None
    if ev.get("STATUS", ({}, ""))[1] == "CANCELLED":
        return None

    start_date, start_time = as_datetime(*ev["DTSTART"], tz)
    slot = start_time if start_time is not None else ""

    if "EXDATE" in ev:
        for raw in ev["EXDATE"][1].split(","):
            if as_datetime(ev["EXDATE"][0], raw, tz)[0] == target:
                return None

    if "RRULE" not in ev:
        return slot if start_date == target else None

    rule = dict(
        p.split("=", 1) for p in ev["RRULE"][1].split(";") if "=" in p
    )
    if target < start_date:
        return None

    until = rule.get("UNTIL")
    if until and target > as_datetime({}, until, tz)[0]:
        return None

    freq = rule.get("FREQ")
    step = int(rule.get("INTERVAL", 1))
    byday = [d[-2:] for d in rule["BYDAY"].split(",")] if "BYDAY" in rule else None
    n = None  # occurrence index, for COUNT

    if freq == "DAILY":
        delta = (target - start_date).days
        if delta % step:
            return None
        n = delta // step
    elif freq == "WEEKLY":
        days = byday or [WEEKDAYS[start_date.weekday()]]
        if WEEKDAYS[target.weekday()] not in days:
            return None
        # Weeks elapsed since the week containing DTSTART.
        anchor = start_date - timedelta(days=start_date.weekday())
        weeks = ((target - timedelta(days=target.weekday())) - anchor).days // 7
        if weeks % step:
            return None
        n = weeks // step * len(days)
    elif freq == "MONTHLY":
        months = (target.year - start_date.year) * 12 + target.month - start_date.month
        if months % step:
            return None
        if byday:
            if WEEKDAYS[target.weekday()] not in byday:
                return None
            ordinal = rule["BYDAY"].split(",")[0][:-2]
            if ordinal and ordinal.lstrip("-").isdigit():
                nth = (target.day - 1) // 7 + 1
                if int(ordinal) > 0 and nth != int(ordinal):
                    return None
        elif target.day != start_date.day:
            return None
        n = months // step
    elif freq == "YEARLY":
        years = target.year - start_date.year
        if years % step or (target.month, target.day) != (start_date.month, start_date.day):
            return None
        n = years // step
    else:
        return None

    if "COUNT" in rule and n is not None and n >= int(rule["COUNT"]):
        return None
    return slot


def events_on(urls, target, tz):
    out, seen = [], set()
    for url in [u.strip() for u in urls if u.strip()]:
        try:
            events = parse_events(fetch(url))
        except Exception as e:  # a dead feed must not cost you the briefing
            print(f"calendar feed failed: {e}")
            continue
        for ev in events:
            slot = occurs_on(ev, target, tz)
            if slot is None:
                continue
            title = ev.get("SUMMARY", ({}, "(untitled)"))[1].replace("\\,", ",")
            key = (title, slot)
            if key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "time": slot or None, "all_day": not slot})
    out.sort(key=lambda e: (e["time"] is None, e["time"] or ""))
    return out
