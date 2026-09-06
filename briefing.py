#!/usr/bin/env python3
"""Morning briefing.

Modules, in the order they appear in the message:
  prose   - Gemini narrates the verified stats below it
  habits  - performance against weekly targets, from the Todoist activity log
  sleep   - last night against target, from Google Health (Fitbit)
  today   - calendar events (Google iCal feed) and Todoist tasks due

Every number is computed here. The model only ever writes prose over numbers it
was handed.
"""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import gcal
import sleep as sleep_mod
from retry import Unavailable, retry

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "habits.db"
TZ = ZoneInfo("America/Los_Angeles")
WINDOW = 30
TELEGRAM_LIMIT = 4096  # hard cap on a single Bot API message
# Tried in order; 3.8 intermittently 503s under load. 3.6 is skipped - it
# rejects thinkingBudget: 0.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash",
                 "gemini-3.5-flash", "gemini-2.5-flash"]


# ---------------------------------------------------------------- plumbing

def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def http(url, token=None, data=None, headers=None, timeout=300,
         what=None, log=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)

    def once():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    return retry(once, what or url, log=log)


def db():
    """The habits bot owns this file. Opened read-only so that stays true.

    Resolved at call time, not import time - the path comes from .env.
    """
    path = os.environ.get("HABITS_DB") or DEFAULT_DB
    # The path can contain spaces, so it has to be URI-escaped.
    return sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro",
                           uri=True)


# ---------------------------------------------------------------- todoist

def today_tasks(token, habits, today, log=None):
    """Everything due today that is not one of the habits."""
    data = http("https://api.todoist.com/api/v1/tasks", token,
                what="todoist tasks", log=log)
    tasks = data.get("results", data)
    habit_names = {h["key"] for h in habits}
    habit_projects = {
        t.get("project_id") for t in tasks if t.get("content") in habit_names
    }
    out = []
    for t in tasks:
        due = t.get("due") or {}
        raw = due.get("date")
        if not raw:
            continue
        day = raw[:10]
        if day > today.isoformat():
            continue  # future
        at = raw[11:16] if len(raw) > 10 else None
        out.append({
            "title": t.get("content"),
            "time": at,
            "overdue": day < today.isoformat(),
        })
    out.sort(key=lambda x: (x["time"] is None, x["time"] or ""))
    return out


# ---------------------------------------------------------------- habits

def first_day(con):
    row = con.execute("SELECT v FROM meta WHERE k='start_day'").fetchone()
    if row:
        return date.fromisoformat(row[0])
    today = datetime.now(TZ).date()
    con.execute("INSERT INTO meta (k, v) VALUES ('start_day', ?)", (today.isoformat(),))
    con.commit()
    return today


def stats(con, habits, today):
    """Per-habit performance against its weekly target, over the window."""
    window_start = max(today - timedelta(days=WINDOW), first_day(con))
    elapsed = (today - window_start).days  # whole days, excludes today
    weeks = elapsed / 7 if elapsed else 0
    out = []
    for h in habits:
        logged = con.execute(
            "SELECT COUNT(*) FROM completions WHERE habit=? AND day>=? AND day<?",
            (h["key"], window_start.isoformat(), today.isoformat()),
        ).fetchone()[0]
        if h["kind"] == "avoid":
            # Target is a clean day every day; what matters is lapses.
            actual = (elapsed - logged) / weeks if weeks else 0
            lo = hi = 0.0
            met = actual <= 0.001
        else:
            actual = logged / weeks if weeks else 0
            lo = h["target_per_week"]
            hi = h.get("target_max", lo)
            met = actual >= lo - 0.001
        row = {**h, "logged": logged, "elapsed": elapsed,
               "per_week": round(actual, 1), "target_lo": lo,
               "target_hi": hi, "met": met}
        row.update(trend(con, h, today, window_start))
        out.append(row)
    return out, elapsed


def trend(con, h, today, window_start):
    """The shape of the window, day by day, plus time since the last event.

    No week-sized buckets: the model is handed the raw daily pattern and reads
    the trend out of it.
    """
    def count(a, b):
        return con.execute(
            "SELECT COUNT(*) FROM completions WHERE habit=? AND day>=? AND day<?",
            (h["key"], a.isoformat(), b.isoformat()),
        ).fetchone()[0]

    days, d = [], window_start
    while d < today:
        days.append("X" if count(d, d + timedelta(days=1)) else "-")
        d += timedelta(days=1)
    out = {"daily_pattern": "".join(days)}

    if h["kind"] == "avoid":
        d, gap = today - timedelta(days=1), 0
        while d >= window_start and count(d, d + timedelta(days=1)):
            gap += 1
            d -= timedelta(days=1)
        out["clean_days_running"] = gap
    else:
        row = con.execute(
            "SELECT MAX(day) FROM completions WHERE habit=? AND day<?",
            (h["key"], today.isoformat()),
        ).fetchone()[0]
        if row:
            out["days_since_last"] = (today - date.fromisoformat(row)).days
    return out


def target_str(h):
    if h["kind"] == "avoid":
        return "0"
    if h["target_hi"] != h["target_lo"]:
        return f"{h['target_lo']:g}-{h['target_hi']:g}"
    return f"{h['target_lo']:g}"


# ---------------------------------------------------------------- prose

def narrate(rows, elapsed, tasks, events, sleep_data, failed):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    facts = {
        "days_of_history": elapsed,
        "how_to_read_daily_pattern": (
            "One character per day, oldest first, ending yesterday. "
            "X means logged, - means not."),
        "habits": [
            {"name": h["label"],
             "what_the_number_counts": (
                 "days this habit was BROKEN - lower is better, 0 is perfect"
                 if h["kind"] == "avoid" else
                 "sessions completed - higher is better"),
             "actual_per_week": h["per_week"],
             "target_per_week": target_str(h),
             "at_or_above_target": h["met"],
             **({"lapses_in_period": h["elapsed"] - h["logged"],
                 "clean_days_in_period": h["logged"]}
                if h["kind"] == "avoid" else
                {"times_logged": h["logged"]}),
             **{k: h[k] for k in
                ("daily_pattern", "clean_days_running", "days_since_last")
                if k in h}}
            for h in rows
        ],
        "sleep": sleep_data,
        "sources_unavailable_today": failed or None,
        "last_event_ends_today": (
            max((e["ends"] for e in events if e.get("ends")), default=None)),
        "todays_events": [
            {"title": e["title"], "time": am_pm(e["time"]) or "all day"}
            for e in events
        ],
        "todays_tasks": [
            {"title": t["title"], "time": am_pm(t["time"]), "overdue": t["overdue"]}
            for t in tasks
        ],
    }
    payload = {
        "systemInstruction": {"parts": [{"text": (ROOT / "prompt.md").read_text()}]},
        "contents": [{"role": "user",
                      "parts": [{"text": json.dumps(facts, indent=2)}]}],
        "generationConfig": {
            "temperature": 0.8,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    for model in GEMINI_MODELS:
        try:
            r = http(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent",
                data=payload, headers={"x-goog-api-key": key}, timeout=300,
                what=model,
            )
            cand = r["candidates"][0]
            if cand.get("finishReason") not in (None, "STOP"):
                print(f"{model}: finish={cand['finishReason']}", file=sys.stderr)
                continue
            parts = cand["content"]["parts"]
            text = " ".join(
                p["text"].strip() for p in parts
                if p.get("text") and not p.get("thought")
            ).strip()
            if text:
                return text
        except (Unavailable, urllib.error.URLError, KeyError,
                IndexError, TimeoutError) as e:
            print(f"{model}: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------- render

def am_pm(hhmm):
    """24h "17:00" -> "5pm" / "9:30am". Returns None unchanged."""
    if not hhmm:
        return hhmm
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}" if m else f"{h12}{suffix}"


def render_block(rows, elapsed, tasks, events, sleep_data, today, failed):
    """The fixed-width part: habit table and the day's schedule."""
    out = [today.strftime("%A, %-d %B"), ""]
    if "habits" in failed:
        out += [f"HABITS — database unreadable ({failed['habits']})", ""]
    elif elapsed == 0:
        out += ["HABITS", "Tracking starts today. First readout tomorrow.", ""]
    else:
        out.append(f"HABITS · last {elapsed} day{'s' if elapsed != 1 else ''}")
        w = max(len(h["label"]) for h in rows)
        out.append(f"{'':<{w}}   per wk   target")
        for h in rows:
            tail = "  lapses" if h["kind"] == "avoid" else ""
            mark = "ok" if h["met"] else "--"
            out.append(
                f"{h['label']:<{w}}   {h['per_week']:>5.1f}   {target_str(h):>6}"
                f"   {mark}{tail}"
            )
        out.append("")

    if "health" in failed:
        out += [f"SLEEP — Google Health unreachable ({failed['health']})", ""]
    elif sleep_data and not sleep_data.get("no_data"):
        n = sleep_data["most_recent_night"]
        when = ("last night" if n["nights_ago"] <= 1
                else f"{n['nights_ago']} nights ago")
        out.append(f"SLEEP · {when}")
        out.append(f"{'asleep':<8} {n['asleep']:>6}   target {sleep_data['target_hours']}h")
        out.append(f"{'bed':<8} {am_pm(n['went_to_bed']):>6}   target "
                   f"{am_pm(n['target_bed_that_night'])}")
        out.append(f"{'woke':<8} {am_pm(n['woke']):>6}   target "
                   f"{am_pm(n['target_wake_that_night'])}")
        out.append("")

    if "calendar" in failed:
        out.append(f"TODAY — calendar unreachable ({failed['calendar']})")
    else:
        out.append("TODAY")
        if not events:
            out.append("Nothing on the calendar.")
        for e in events:
            out.append(f"{am_pm(e['time']) or 'all day':>7}  {e['title']}")

    if tasks:
        out += ["", "TO DO"]
        for t in tasks:
            when = am_pm(t["time"]) or ("late" if t["overdue"] else "·")
            out.append(f"{when:>7}  {t['title']}")
    return "\n".join(out).rstrip()


def send(text):
    """text is already HTML-safe."""
    return http(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
              "parse_mode": "HTML"},
    )


def main():
    load_env()
    dry = "--dry-run" in sys.argv
    habits = json.loads((ROOT / "habits.json").read_text())
    token = os.environ["TODOIST_TOKEN"]
    con = db()
    today = datetime.now(TZ).date()
    failed = {}

    def log(msg):
        print(msg, file=sys.stderr)

    # Each source retries on its own. Whatever still fails is named in the
    # message rather than papered over with yesterday's numbers.
    rows, elapsed = [], 0
    try:
        rows, elapsed = stats(con, habits, today)
    except sqlite3.Error as e:
        failed["habits"] = str(e)
        log(f"habits database unreadable: {e}")

    tasks = []
    try:
        tasks = today_tasks(token, habits, today, log=log)
    except Unavailable as e:
        failed["todoist"] = str(e).split(": ", 1)[-1]
        log(f"todoist unavailable: {e}")

    events = []
    try:
        events = gcal.events_on(
            os.environ.get("GOOGLE_CALENDAR_ICS", "").split(","), today, TZ, log=log)
    except Unavailable as e:
        failed["calendar"] = str(e).split(": ", 1)[-1]
        log(f"calendar unavailable: {e}")

    sleep_data = None
    if os.environ.get("GOOGLE_HEALTH_REFRESH_TOKEN"):
        try:
            tok = sleep_mod.access_token(log=log)
            sleep_data = sleep_mod.summarise(
                sleep_mod.nights(today - timedelta(days=WINDOW), tok, log=log),
                sleep_mod.resting_hr(tok, log=log), today)
        except Unavailable as e:
            failed["health"] = str(e).split(": ", 1)[-1]
            log(f"health unavailable: {e}")

    skip = "--no-prose" in sys.argv or (elapsed == 0 and "habits" not in failed)
    prose = None
    if not skip:
        try:
            prose = narrate(rows, elapsed, tasks, events, sleep_data, failed)
        except Unavailable as e:
            log(f"narration unavailable: {e}")

    block = render_block(rows, elapsed, tasks, events, sleep_data, today, failed)
    if dry:
        print("\n\n".join(x for x in (prose, block) if x))
        return

    # Prose is plain text so it reflows; the table needs fixed width.
    head = escape(prose) + "\n\n" if prose else ""
    table = "<pre>" + escape(block) + "</pre>"
    if len(head) + len(table) <= TELEGRAM_LIMIT:
        parts = [head + table]
    else:
        # Two messages rather than a truncated one.
        parts = [p for p in (head.strip(), table) if p]
    try:
        print("sent" if all(send(p).get("ok") for p in parts) else "FAILED")
    except Unavailable as e:
        log(f"telegram unavailable: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
