#!/usr/bin/env python3
"""Morning briefing.

Two messages, two mindsets:

  habits  - 6:30, to Telegram. Who you have been: performance against weekly
            targets from the habits bot's database, and last night's sleep from
            Google Health. Strategic. Read in bed.
  tasks   - 7:30, by email. What today asks of you: calendar, Todoist, F1
            sessions, and the free windows left once those are subtracted.
            Operational. Read standing up.

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
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import f1
import gcal
import mail
import plan
import sleep as sleep_mod
from retry import Unavailable, retry

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT.parent / "habits-bot" / "habits.db"
TZ = ZoneInfo("America/Los_Angeles")
WINDOW = 28   # four whole weeks, so weekdays cancel and the paragraph's
              # window is the same one the table shows
TELEGRAM_LIMIT = 4096  # hard cap on a single Bot API message
# Tried in order; 3.8 intermittently 503s under load. 3.6 is skipped - it
# rejects thinkingBudget: 0.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash",
                 "gemini-3.5-flash", "gemini-2.5-flash"]


# ---------------------------------------------------------------- plumbing

def load_env():
    for line in (ROOT.parent / ".env").read_text().splitlines():
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

def today_tasks(token, today, log=None):
    """Everything due today or overdue. Habits are not here; they live in the
    habits bot's database, so nothing needs filtering out.

    `note` is Todoist's `description`, the text written on the task itself.
    `deadline` is a separate field from `due` - due is when it was planned,
    deadline is when it is actually owed - and is carried only when the two
    disagree. `postponed` counts how many times it has been pushed, which is a
    better measure of a task being avoided than merely being late.
    """
    data = http("https://api.todoist.com/api/v1/tasks", token,
                what="todoist tasks", log=log)
    tasks = data.get("results", data)
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
        deadline = (t.get("deadline") or {}).get("date")
        dur = t.get("duration") or {}
        amount = dur.get("amount")
        out.append({
            "title": t.get("content"),
            "time": at,
            "overdue": day < today.isoformat(),
            "days_late": (today - date.fromisoformat(day)).days,
            "note": (t.get("description") or "").strip() or None,
            "deadline": deadline if deadline and deadline != day else None,
            "minutes": (amount if dur.get("unit") == "minute"
                        else amount * 60 if amount else None),
            "postponed": t.get("postponed_count") or 0,
            "labels": t.get("labels") or None,
            "recurring": bool(due.get("is_recurring")),
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


def is_daily(h):
    """Does a seven-day share mean anything for this habit?

    Only for habits meant to happen more or less every day. Meditation at three
    a week would read 43% on a perfect week, which looks like failure and is
    not. Derived from the target rather than a separate flag, so it stays true
    if the target changes.
    """
    return h["kind"] == "avoid" or h.get("target_per_week", 0) >= 5


def share(con, key, kind, a, b):
    """Percentage of days between a and b that this habit held.

    For an avoid habit a logged day is a clean day, so higher is better either
    way and the two kinds are directly comparable.
    """
    days = (b - a).days
    if days <= 0:
        return None
    logged = con.execute(
        "SELECT COUNT(*) FROM completions WHERE habit=? AND day>=? AND day<?",
        (key, a.isoformat(), b.isoformat()),
    ).fetchone()[0]
    return round(100 * logged / days)


def stats(con, habits, today):
    """Two cuts per habit: the last four weeks, and the last seven days.

    Twenty-eight days is the ground - four whole weeks, so weekday effects
    cancel and one bad day barely moves it. Seven days is the momentum, and is
    the same length for the same reason: every window holds one of each weekday,
    so the number only moves when behaviour does.
    """
    window_start = max(today - timedelta(days=WINDOW), first_day(con))
    elapsed = (today - window_start).days  # whole days, excludes today
    out = []
    for h in habits:
        # Both windows clamp to the start of tracking, so both report on
        # whatever history exists rather than waiting to be full.
        recent = max(today - timedelta(days=7), window_start)
        row = {**h,
               "elapsed": elapsed,
               "long_pct": share(con, h["key"], h["kind"], window_start, today),
               "week_pct": (share(con, h["key"], h["kind"], recent, today)
                            if is_daily(h) else None)}
        row["direction"] = (
            None if row["week_pct"] is None or row["long_pct"] is None
            else "rising" if row["week_pct"] > row["long_pct"] + 5
            else "slipping" if row["week_pct"] < row["long_pct"] - 5
            else "holding")
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


# ---------------------------------------------------------------- prose

def facts_habits(rows, elapsed, sleep_data, failed):
    """What the 6:30 message reasons over: the record, and last night."""
    return {
        "days_of_history": elapsed,
        "how_to_read_daily_pattern": (
            "One character per day, oldest first, ending yesterday. "
            "X means logged, - means not."),
        "habits": [
            {"name": h["label"],
             "counts": ("clean days - higher is better, and a full record means "
                        "it was never broken" if h["kind"] == "avoid" else
                        "days done - higher is better"),
             "share_of_last_28_days": f"{h['long_pct']}%",
             **({"share_of_last_7_days": f"{h['week_pct']}%",
                 "direction": h["direction"]}
                if h["week_pct"] is not None else
                {"note": "not a daily habit - no seven-day figure"}),
             **{k: h[k] for k in
                ("daily_pattern", "clean_days_running", "days_since_last")
                if k in h}}
            for h in rows
        ],
        "sleep": sleep_data,
        "sources_unavailable_today": failed or None,
    }


def facts_tasks(today, events, tasks, races, slots, progress, week, failed):
    """What the 7:30 message reasons over: the day, and the room left in it."""
    return {
        "today": today.strftime("%A, %-d %B"),
        "day_shape": plan.day_note(week, today) or "nothing fixed",
        "how_to_use_free_windows": (
            "These are the only unclaimed stretches of today, already computed "
            "from the calendar and the fixed week. Place work inside them. Never "
            "invent a time outside them, and never overfill one."),
        "free_windows": slots,
        "todays_events": [
            {"title": e["title"], "time": am_pm(e["time"]) or "all day",
             "ends": am_pm(e.get("ends"))}
            for e in events
        ],
        "f1_today": [{**r, "time": am_pm(r["time"]), "ends": am_pm(r["ends"])}
                     for r in races] or None,
        "how_to_treat_f1": (
            "Watched the same day but not necessarily live, so the time is "
            "movable. Say it is on and roughly what it costs; do not schedule "
            "the rest of the morning as though it were fixed."
        ) if races else None,
        "todays_tasks": [
            {"title": t["title"], "time": am_pm(t["time"]),
             **{k: t[k] for k in
                ("note", "deadline", "minutes", "labels", "recurring")
                if t.get(k)},
             **({"overdue_by_days": t["days_late"]} if t["overdue"] else {}),
             **({"times_postponed": t["postponed"]} if t["postponed"] else {})}
            for t in tasks
        ],
        "habits_still_owed_this_week": progress or None,
        "gym_takes_minutes": week.get("gym_minutes"),
        "gym_usually": week.get("gym_when"),
        "sources_unavailable_today": failed or None,
    }


def narrate(facts, prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    payload = {
        "systemInstruction": {"parts": [{"text": (ROOT / prompt).read_text()}]},
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


# One grid for both tables. Two blocks that almost line up are harder to read
# than two that either match or clearly do not, and this is read half asleep.
LABEL_W, COL_A, COL_B = 12, 10, 9


def grid(label, a, b=""):
    return f"{label:<{LABEL_W}}  {a:>{COL_A}}  {b:>{COL_B}}".rstrip()


def render_habits(rows, elapsed, sleep_data, today, failed):
    """The fixed-width part of the 6:30 message: the record, and last night."""
    out = [today.strftime("%A, %-d %B"), ""]
    if "habits" in failed:
        out += [f"HABITS — database unreadable ({failed['habits']})", ""]
    elif elapsed == 0:
        out += ["HABITS", "Tracking starts today. First readout tomorrow.", ""]
    else:
        out.append("HABITS")
        out.append(grid("", "4 weeks", "this week"))
        for h in rows:
            out.append(grid(
                h["short"],
                f"{h['long_pct']}%" if h["long_pct"] is not None else "-",
                f"{h['week_pct']}%" if h["week_pct"] is not None else ""))
        out.append("")

    if "health" in failed:
        out += [f"SLEEP — Google Health unreachable ({failed['health']})", ""]
    elif sleep_data and not sleep_data.get("no_data"):
        n, base = sleep_data["most_recent_night"], sleep_data.get("baseline") or {}
        when = ("last night" if n["nights_ago"] <= 1
                else f"{n['nights_ago']} nights ago")
        # "usual" averages only nights of the same kind - a Saturday lie-in is
        # not evidence about a Tuesday - but that is arithmetic, not a caption.
        pairs = [
            ("slept",      n["asleep"],             base.get("usual_asleep")),
            ("asleep at",  am_pm(n["went_to_bed"]), am_pm(base.get("usual_bed"))),
            ("woke",       am_pm(n["woke"]),        am_pm(base.get("usual_wake"))),
        ]
        if n.get("efficiency_pct"):
            pairs.append(("efficiency", f"{n['efficiency_pct']}%",
                          f"{base['usual_efficiency_pct']}%"
                          if base.get("usual_efficiency_pct") else None))
        out.append("SLEEP" if when == "last night" else f"SLEEP · {when}")
        out.append(grid("", "last night", "usual"))
        for lab, now, usual in pairs:
            out.append(grid(lab, now, usual or ""))
        out.append("")

    return "\n".join(out).rstrip()


# ---------------------------------------------------------------- 7:30, as email

CSS = """
body{margin:0;padding:24px 16px;background:#f6f6f4;
     font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a1a}
.wrap{max-width:560px;margin:0 auto}
h1{font-size:19px;margin:0 0 4px;font-weight:600}
.sub{color:#6b6b6b;font-size:14px;margin:0 0 20px}
.prose{font-size:16px;margin:0 0 24px}
h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:#8a8a8a;
   font-weight:600;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid #e3e3e0}
.row{display:flex;gap:12px;padding:7px 0;border-bottom:1px solid #ececea}
.row:last-child{border-bottom:0}
.when{flex:0 0 72px;color:#6b6b6b;font-variant-numeric:tabular-nums;font-size:14px}
.what{flex:1}
.note{color:#6b6b6b;font-size:14px;margin-top:3px}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:10px;
     background:#ececea;color:#5a5a5a;margin-left:6px;vertical-align:1px}
.late{background:#f6dcd8;color:#8d2b18}
.free{background:#e2eee4;color:#26603a}
.empty{color:#8a8a8a}
.fail{background:#f6dcd8;color:#8d2b18;padding:9px 12px;border-radius:5px;
      font-size:14px;margin:14px 0}
"""


def _rows(items):
    return "".join(
        f'<div class="row"><div class="when">{escape(w)}</div>'
        f'<div class="what">{body}</div></div>'
        for w, body in items) or '<div class="row empty">Nothing.</div>'


def render_tasks_html(today, events, tasks, races, slots, prose, failed):
    """The 7:30 message. Email, because this is a document, not a glance."""
    p = [f'<style>{CSS}</style><div class="wrap">',
         f'<h1>{escape(today.strftime("%A, %-d %B"))}</h1>',
         '<p class="sub">What today asks of you.</p>']
    if prose:
        p.append(f'<p class="prose">{escape(prose)}</p>')
    for src, label in (("todoist", "Todoist"), ("calendar", "Calendar"),
                       ("f1", "F1 calendar")):
        if src in failed:
            p.append(f'<div class="fail">{label} unreachable — '
                     f'{escape(str(failed[src]))}</div>')

    if races:
        p.append("<h2>Racing</h2>")
        p.append(_rows([
            (am_pm(r["time"]),
             f'{escape(r["session"])}<span class="tag">{escape(r["race"])}</span>'
             f'<div class="note">about {plan.span(r["minutes"])}</div>')
            for r in races]))

    if "calendar" not in failed:
        p.append("<h2>Today</h2>")
        p.append(_rows([(am_pm(e["time"]) or "all day", escape(e["title"]))
                        for e in events])
                 if events else '<div class="row empty">Nothing scheduled.</div>')

    if "todoist" not in failed:
        p.append("<h2>To do</h2>")
        if not tasks:
            p.append('<div class="row empty">No tasks for you today.</div>')
        else:
            items = []
            for t in tasks:
                tags = ""
                if t["overdue"]:
                    d = t["days_late"]
                    tags += (f'<span class="tag late">{d} day'
                             f'{"s" if d != 1 else ""} late</span>')
                if t["postponed"]:
                    tags += f'<span class="tag">moved {t["postponed"]}×</span>'
                if t["minutes"]:
                    tags += f'<span class="tag">{plan.span(t["minutes"])}</span>'
                if t["deadline"]:
                    tags += f'<span class="tag">due {escape(t["deadline"])}</span>'
                body = escape(t["title"]) + tags
                if t["note"]:
                    body += f'<div class="note">{escape(t["note"])}</div>'
                items.append((am_pm(t["time"]) or "—", body))
            p.append(_rows(items))

    if slots:
        p.append("<h2>Free</h2>")
        p.append(_rows([
            (s["from"], f'until {escape(s["to"])}'
                        f'<span class="tag free">{plan.span(s["minutes"])}</span>')
            for s in slots]))
    p.append("</div>")
    return "\n".join(p)


def send(text):
    """text is already HTML-safe."""
    return http(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
              "parse_mode": "HTML"},
    )


def log(msg):
    print(msg, file=sys.stderr)


def run_habits(dry, prose_wanted):
    """6:30. Who you have been, and how you slept. Strategic, not operational."""
    habits = json.loads((ROOT.parent / "habits.json").read_text())
    today = datetime.now(TZ).date()
    failed = {}

    # Each source retries on its own. Whatever still fails is named in the
    # message rather than papered over with yesterday's numbers.
    rows, elapsed = [], 0
    try:
        rows, elapsed = stats(db(), habits, today)
    except sqlite3.Error as e:
        failed["habits"] = str(e)
        log(f"habits database unreadable: {e}")

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

    prose = None
    if prose_wanted and not (elapsed == 0 and "habits" not in failed):
        try:
            prose = narrate(facts_habits(rows, elapsed, sleep_data, failed),
                            "prompt-habits.md")
        except Unavailable as e:
            log(f"narration unavailable: {e}")

    block = render_habits(rows, elapsed, sleep_data, today, failed)
    if dry:
        print("\n\n".join(x for x in (prose, block) if x))
        return 0

    # Prose is plain text so it reflows; the table needs fixed width.
    head = escape(prose) + "\n\n" if prose else ""
    table = "<pre>" + escape(block) + "</pre>"
    parts = ([head + table] if len(head) + len(table) <= TELEGRAM_LIMIT
             else [p for p in (head.strip(), table) if p])
    try:
        print("sent" if all(send(p).get("ok") for p in parts) else "FAILED")
    except Unavailable as e:
        log(f"telegram unavailable: {e}")
        return 1
    return 0


def run_tasks(dry, prose_wanted):
    """7:30. What today asks of you, and where it actually fits."""
    habits = json.loads((ROOT.parent / "habits.json").read_text())
    week = plan.load(ROOT.parent / "week.json")
    today = datetime.now(TZ).date()
    failed = {}

    tasks = []
    try:
        tasks = today_tasks(os.environ["TODOIST_TOKEN"], today, log=log)
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

    races = []
    try:
        races = f1.sessions_on(today, TZ, log=log)
    except Unavailable as e:
        failed["f1"] = str(e).split(": ", 1)[-1]
        log(f"f1 unavailable: {e}")

    progress = []
    try:
        progress = plan.week_progress(db(), habits, today)
    except sqlite3.Error as e:
        failed["habits"] = str(e)
        log(f"habits database unreadable: {e}")

    # Free time is arithmetic over the week shape and the calendar, so it is
    # computed here and handed over finished. The model places work in it.
    targets = sleep_mod.targets_for(today)
    slots = plan.free_slots(week, today, events, targets["wake"], targets["bed"])

    prose = None
    if prose_wanted:
        try:
            prose = narrate(
                facts_tasks(today, events, tasks, races, slots, progress,
                            week, failed),
                "prompt-tasks.md")
        except Unavailable as e:
            log(f"narration unavailable: {e}")

    html = render_tasks_html(today, events, tasks, races, slots, prose, failed)
    if dry:
        print(html)
        return 0
    subject = today.strftime("%A, %-d %B")
    try:
        mail.send(os.environ["MAIL_TO"], subject, html, log=log)
        print("sent")
    except (Unavailable, KeyError) as e:
        log(f"mail unavailable: {e}")
        return 1
    return 0


def main():
    load_env()
    mode = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if mode not in ("habits", "tasks"):
        print("usage: briefing.py habits|tasks [--dry-run] [--no-prose]",
              file=sys.stderr)
        return 2
    runner = run_habits if mode == "habits" else run_tasks
    return runner("--dry-run" in sys.argv, "--no-prose" not in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
