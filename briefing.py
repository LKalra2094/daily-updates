#!/usr/bin/env python3
"""Morning briefing.

Modules, in the order they appear in the message:
  prose   - Gemini narrates the verified stats below it
  habits  - performance against weekly targets, from the Todoist activity log
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

ROOT = Path(__file__).resolve().parent
DB = ROOT / "habits.db"
TZ = ZoneInfo("America/Los_Angeles")
WINDOW = 30
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


def http(url, token=None, data=None, headers=None, timeout=30):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def db():
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS completions ("
        "  day TEXT NOT NULL, habit TEXT NOT NULL, PRIMARY KEY (day, habit))"
    )
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return con


# ---------------------------------------------------------------- todoist

def backfill(con, token, days=7):
    """Pull recent completions from the Todoist activity log.

    Recurring tasks never appear in the completed-tasks endpoints - completing
    one just advances its due date - so the activity log is the only source
    that records them.
    """
    cutoff = (datetime.now(TZ).date() - timedelta(days=days)).isoformat()
    cursor, stored = None, 0
    for _ in range(20):
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = http(
            "https://api.todoist.com/api/v1/activities?" + urllib.parse.urlencode(params),
            token,
        )
        results = data.get("results", [])
        if not results:
            break
        for r in results:
            day = (
                datetime.fromisoformat(r["event_date"].replace("Z", "+00:00"))
                .astimezone(TZ).date().isoformat()
            )
            if day < cutoff:
                con.commit()
                return stored
            if r.get("event_type") != "completed":
                continue
            content = (r.get("extra_data") or {}).get("content")
            if content:
                con.execute(
                    "INSERT OR IGNORE INTO completions (day, habit) VALUES (?, ?)",
                    (day, content),
                )
                stored += 1
        cursor = data.get("next_cursor")
        if not cursor:
            break
    con.commit()
    return stored


def today_tasks(token, habits, today):
    """Everything due today that is not one of the habits."""
    data = http("https://api.todoist.com/api/v1/tasks", token)
    tasks = data.get("results", data)
    habit_names = {h["todoist"] for h in habits}
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
        if t.get("content") in habit_names or t.get("project_id") in habit_projects:
            continue
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
            (h["todoist"], window_start.isoformat(), today.isoformat()),
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
    """This week against the week before, and time since the last lapse."""
    def count(a, b):
        return con.execute(
            "SELECT COUNT(*) FROM completions WHERE habit=? AND day>=? AND day<?",
            (h["todoist"], a.isoformat(), b.isoformat()),
        ).fetchone()[0]

    w1_start = today - timedelta(days=7)
    w2_start = today - timedelta(days=14)
    out = {}
    if w1_start >= window_start:
        out["last_7_days"] = count(w1_start, today)
    if w2_start >= window_start:
        out["prior_7_days"] = count(w2_start, w1_start)

    # Days since the most recent lapse (avoid habits) or completion (do habits).
    if h["kind"] == "avoid":
        d, gap = today - timedelta(days=1), 0
        while d >= window_start and count(d, d + timedelta(days=1)):
            gap += 1
            d -= timedelta(days=1)
        out["clean_days_running"] = gap
    else:
        row = con.execute(
            "SELECT MAX(day) FROM completions WHERE habit=? AND day<?",
            (h["todoist"], today.isoformat()),
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

def narrate(rows, elapsed, tasks, events):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    facts = {
        "days_of_history": elapsed,
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
                ("last_7_days", "prior_7_days", "clean_days_running", "days_since_last")
                if k in h}}
            for h in rows
        ],
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
        # thinkingBudget 0: reasoning tokens otherwise eat the output budget and
        # truncate the paragraph mid-sentence.
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 1000,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    for model in GEMINI_MODELS:
        try:
            r = http(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent",
                data=payload, headers={"x-goog-api-key": key}, timeout=60,
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
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
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


def render_block(rows, elapsed, tasks, events, today):
    """The fixed-width part: habit table and the day's schedule."""
    out = [today.strftime("%A, %-d %B"), ""]
    if elapsed == 0:
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
    habits = json.loads((ROOT / "habits.json").read_text())
    token = os.environ["TODOIST_TOKEN"]
    con = db()
    backfill(con, token)
    today = datetime.now(TZ).date()
    rows, elapsed = stats(con, habits, today)
    tasks = today_tasks(token, habits, today)
    events = gcal.events_on(
        os.environ.get("GOOGLE_CALENDAR_ICS", "").split(","), today, TZ)
    # With no completed day yet every stat is zero, which reads as "you did
    # nothing" rather than "nothing has been measured". Skip the prose.
    skip = "--no-prose" in sys.argv or elapsed == 0
    prose = None if skip else narrate(rows, elapsed, tasks, events)
    block = render_block(rows, elapsed, tasks, events, today)
    if "--dry-run" in sys.argv:
        print("\n\n".join(x for x in (prose, block) if x))
        return
    # Prose is plain text so it reflows; the table needs fixed width.
    html = ""
    if prose:
        html += escape(prose) + "\n\n"
    html += "<pre>" + escape(block) + "</pre>"
    print("sent" if send(html).get("ok") else "FAILED")


if __name__ == "__main__":
    main()
