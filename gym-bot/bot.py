#!/usr/bin/env python3
"""Gym bot.

Shows the working weight for every exercise in a split, and records it when it
goes up. `/push`, `/pull` and `/legs` print a numbered list; a plain message
like `1 150` or `bench 150` writes a new weight against that exercise.

Weights are pounds, and signed: assisted pull ups sit at -60 and progress
towards zero, so "went up" is the same arithmetic everywhere.

Nothing is ever overwritten. Every update appends a row to `lifts`, the current
weight is the newest row, and the history is the trend. Owns gym.db, shares
nothing with the habits bot but the .env file. Python standard library only.
"""

import html
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = ROOT / "gym.db"
TZ = ZoneInfo("America/Los_Angeles")
POLL_SECONDS = 50            # Telegram long-poll window
API = "https://api.telegram.org/bot{}/{}"

DAYS = ("push", "pull", "legs")
SEED = {
    "push": ["Incline Dumbbell Press", "Shoulder Press", "Chest Fly",
             "Cable Lateral Raise", "Cable Rope Tricep Extension"],
    "pull": ["Pull Ups", "Row", "Hammer Curl", "Lat Pulldown"],
    "legs": ["Leg Extension", "Hamstring Curl", "Calf Raise", "Squat"],
}

HELP = (
    "<b>Gym</b>\n<pre>"
    "/push /pull /legs   the day's weights\n"
    "1 150               update by number\n"
    "bench 150           or by name\n"
    "/add push fly 40    new exercise\n"
    "/remove fly         drop one (history kept)\n"
    "/undo               take back the last entry\n"
    "/history bench      every weight, in order"
    "</pre>"
)


def log(msg, err=False):
    stamp = datetime.now(TZ).strftime("%H:%M:%S.%f")[:-3]
    print(f"{stamp}  {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def load_env():
    for line in (ROOT.parent / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def call(method, http_timeout=None, **params):
    # Telegram's own `timeout` is the long-poll window; the socket must outlast it.
    http_timeout = http_timeout or int(params.get("timeout", 0)) + 30
    url = API.format(os.environ["GYM_BOT_TOKEN"], method)
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}
    ).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.load(e).get("description", "")
        except Exception:
            pass
        raise urllib.error.HTTPError(
            e.url, e.code, f"{e.reason}: {detail}" if detail else e.reason,
            e.headers, None) from None


def send(chat_id, text):
    return call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")


# ---------------------------------------------------------------- storage

def db():
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS exercises ("
        "  id INTEGER PRIMARY KEY,"
        "  day TEXT NOT NULL,"
        "  name TEXT NOT NULL,"
        "  position INTEGER NOT NULL,"
        "  active INTEGER NOT NULL DEFAULT 1)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS lifts ("
        "  id INTEGER PRIMARY KEY,"
        "  ts TEXT NOT NULL,"
        "  exercise_id INTEGER NOT NULL,"
        "  weight REAL NOT NULL)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS lifts_by_exercise "
                "ON lifts (exercise_id, id)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    seed(con)
    con.commit()
    return con


def seed(con):
    if con.execute("SELECT 1 FROM exercises LIMIT 1").fetchone():
        return
    for day, names in SEED.items():
        for i, name in enumerate(names):
            con.execute(
                "INSERT INTO exercises (day, name, position) VALUES (?, ?, ?)",
                (day, name, i))
    log("seeded the starting exercise list")


def meta_get(con, k):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))


def rows(con, day):
    """(id, name, current weight or None) for one day, in display order."""
    return con.execute(
        "SELECT e.id, e.name,"
        "  (SELECT weight FROM lifts WHERE exercise_id = e.id"
        "   ORDER BY id DESC LIMIT 1)"
        " FROM exercises e WHERE e.day = ? AND e.active = 1"
        " ORDER BY e.position, e.id", (day,)).fetchall()


def latest(con, ex_id):
    row = con.execute("SELECT weight FROM lifts WHERE exercise_id = ?"
                      " ORDER BY id DESC LIMIT 1", (ex_id,)).fetchone()
    return row[0] if row else None


def record(con, ex_id, weight):
    con.execute("INSERT INTO lifts (ts, exercise_id, weight) VALUES (?, ?, ?)",
                (datetime.now(TZ).isoformat(timespec="seconds"), ex_id, weight))


def tidy(name):
    """`skull crusher` typed one-handed becomes Skull Crusher; DB stays DB."""
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize()
                    for w in name.split())


def fmt(w):
    """0 when an exercise has never been set; no trailing .0 on whole numbers."""
    return "0" if w is None else f"{w:g}"


# ---------------------------------------------------------------- resolving

def ambiguous(hits):
    return "Which one?\n<pre>" + html.escape(
        "\n".join(h[1] for h in hits)) + "</pre>"


def no_match(target):
    return (f"Nothing matching “{html.escape(target)}”. "
            "/push /pull /legs to see the lists.")


def resolve(con, target, inactive=False):
    """Exercises a user's word (or list number) could mean. 0, 1 or many.

    Graded, because a plain "pull" has to reach Pull Ups without Lat Pulldown
    also putting its hand up: an exact name beats a name that starts with the
    word, which beats a word-prefix anywhere, which beats a bare substring.
    Only the best grade that matched anything is offered.
    """
    target = target.strip()
    if target.isdigit():
        last = json.loads(meta_get(con, "last_list") or "{}")
        ids = last.get("ids") or []
        i = int(target)
        if not 1 <= i <= len(ids):
            return []
        row = con.execute("SELECT id, name FROM exercises"
                          " WHERE id = ? AND active = 1", (ids[i - 1],)).fetchone()
        return [row] if row else []

    low = target.lower()
    words = low.split()
    grades = ([], [], [], [])
    for eid, name in con.execute(
            "SELECT id, name FROM exercises"
            + ("" if inactive else " WHERE active = 1")
            + " ORDER BY day, position, id"):
        n = name.lower()
        parts = n.split()
        if n == low:
            g = 0
        elif n.startswith(low):
            g = 1
        elif all(any(p.startswith(w) for p in parts) for w in words):
            g = 2
        elif low in n:
            g = 3
        else:
            continue
        grades[g].append((eid, name))
    hits = next((g for g in grades if g), [])

    # A bare "row" still matches on more than one day. The list last printed
    # says which day he is standing in, so prefer that before asking.
    if len(hits) > 1:
        seen = set(json.loads(meta_get(con, "last_list") or "{}").get("ids") or [])
        narrowed = [h for h in hits if h[0] in seen]
        if len(narrowed) == 1:
            return narrowed
    return hits


def pick(con, target, inactive=False):
    """One exercise, or the message explaining why there isn't one."""
    hits = resolve(con, target, inactive)
    if len(hits) == 1:
        return hits[0], None
    target = target.strip()
    if not hits:
        if target.isdigit():
            return None, (f"There's no {html.escape(target)} on the last list. "
                          "Send /push, /pull or /legs first.")
        return None, no_match(target)
    return None, ambiguous(hits)


# ---------------------------------------------------------------- commands

def day_list(con, day):
    rs = rows(con, day)
    if not rs:
        return f"No exercises on {day} day yet. /add {day} &lt;name&gt; &lt;weight&gt;"
    meta_set(con, "last_list", json.dumps({"day": day, "ids": [r[0] for r in rs]}))
    con.commit()
    width = max(len(r[1]) for r in rs)
    lines = "\n".join(f"{i:>2}. {r[1]:<{width}}  {fmt(r[2]):>5}"
                      for i, r in enumerate(rs, 1))
    return f"<b>{day.title()} day</b>\n<pre>{html.escape(lines)}</pre>"


def update(con, text):
    """`1 150` or `bench 150`. None if this isn't an update at all."""
    try:
        target, raw = text.rsplit(None, 1)
        weight = float(raw)
    except ValueError:
        return None
    hit, problem = pick(con, target)
    if problem:
        return problem
    eid, name = hit
    was = latest(con, eid)
    record(con, eid, weight)
    con.commit()
    log(f"{name}: {fmt(was)} -> {fmt(weight)}")
    return f"<pre>{html.escape(f'{name}  {fmt(was)} → {fmt(weight)}')}</pre>"


def add(con, rest):
    parts = rest.split()
    if len(parts) < 2 or parts[0].lower() not in DAYS:
        return "Usage: /add push skull crusher 60"
    day, parts = parts[0].lower(), parts[1:]
    weight = None
    try:
        weight = float(parts[-1])
        parts = parts[:-1]
    except (ValueError, IndexError):
        pass
    name = tidy(" ".join(parts))
    if not name:
        return "Usage: /add push skull crusher 60"
    pos = con.execute("SELECT COALESCE(MAX(position), -1) + 1"
                      " FROM exercises WHERE day = ?", (day,)).fetchone()[0]
    # A name that was here before is woken up rather than inserted again, so
    # adding back something dropped in March rejoins its old history.
    prior = con.execute("SELECT id, active FROM exercises WHERE day = ?"
                        " AND lower(name) = lower(?)", (day, name)).fetchone()
    if prior and prior[1]:
        return f"{html.escape(name)} is already on {day} day."
    if prior:
        eid = prior[0]
        con.execute("UPDATE exercises SET active = 1, position = ? WHERE id = ?",
                    (pos, eid))
    else:
        eid = con.execute("INSERT INTO exercises (day, name, position)"
                          " VALUES (?, ?, ?)", (day, name, pos)).lastrowid
    if weight is not None:
        record(con, eid, weight)
    con.commit()
    log(f"added {name} to {day}"
        + (f" at {fmt(weight)}" if weight is not None else ""))
    shown = fmt(weight if weight is not None else latest(con, eid))
    line = html.escape(f"{day.title()} day  +{name}  {shown}")
    return f"<pre>{line}</pre>" + (" History rejoined." if prior else "")


def remove(con, rest):
    """Deactivates rather than deletes: the weight history outlives the list."""
    if not rest:
        return "Usage: /remove skull crusher"
    hit, problem = pick(con, rest)
    if problem:
        return problem
    eid, name = hit
    con.execute("UPDATE exercises SET active = 0 WHERE id = ?", (eid,))
    con.commit()
    log(f"removed {name}")
    return f"Dropped {html.escape(name)}. Its history is kept."


def undo(con):
    row = con.execute(
        "SELECT l.id, l.exercise_id, l.weight, e.name FROM lifts l"
        " JOIN exercises e ON e.id = l.exercise_id"
        " ORDER BY l.id DESC LIMIT 1").fetchone()
    if not row:
        return "Nothing to undo."
    lift_id, eid, weight, name = row
    con.execute("DELETE FROM lifts WHERE id = ?", (lift_id,))
    con.commit()
    log(f"undid {name} {fmt(weight)}")
    return (f"<pre>{html.escape(f'{name}  {fmt(weight)} → {fmt(latest(con, eid))}')}"
            "</pre>")


def history(con, rest):
    if not rest:
        return "Usage: /history bench"
    hit, problem = pick(con, rest, inactive=True)   # a dropped lift still has a past
    if problem:
        return problem
    eid, name = hit
    entries = con.execute("SELECT ts, weight FROM lifts WHERE exercise_id = ?"
                          " ORDER BY id", (eid,)).fetchall()
    if not entries:
        return f"{html.escape(name)} has no entries yet."
    lines = "\n".join(f"{ts[:10]}  {fmt(w):>5}" for ts, w in entries[-30:])
    return f"<b>{html.escape(name)}</b>\n<pre>{html.escape(lines)}</pre>"


# ---------------------------------------------------------------- the loop

def handle_message(con, msg):
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not text or chat_id is None:
        return

    # Until the chat is pinned down in .env, the bot reads but never writes -
    # otherwise anyone who finds the bot can set his weights. It hands back the
    # id it saw, which is the easiest way to learn it in the first place.
    want = os.environ.get("GYM_CHAT_ID", "").strip()
    if not want:
        log(f"GYM_CHAT_ID unset; ignoring chat {chat_id}", err=True)
        send(chat_id, f"This chat is <code>{chat_id}</code>. Put "
                      f"<code>GYM_CHAT_ID={chat_id}</code> in .env and restart me.")
        return
    if str(chat_id) != want:
        log(f"ignored message from chat {chat_id}", err=True)
        return

    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        cmd, rest = cmd.split("@")[0].lower(), rest.strip()
        if cmd[1:] in DAYS:
            send(chat_id, day_list(con, cmd[1:]))
        elif cmd == "/add":
            send(chat_id, add(con, rest))
        elif cmd == "/remove":
            send(chat_id, remove(con, rest))
        elif cmd == "/undo":
            send(chat_id, undo(con))
        elif cmd == "/history":
            send(chat_id, history(con, rest))
        else:
            send(chat_id, HELP)
        return

    send(chat_id, update(con, text) or HELP)


def drain():
    """Confirm any backlog without acting on it, and return the next offset."""
    r = call("getUpdates", offset=-1, timeout=0)
    result = r.get("result", [])
    if not result:
        return None
    last = result[-1]["update_id"]
    call("getUpdates", offset=last + 1, timeout=0)
    return last + 1


def run():
    con = db()
    offset = drain()
    log(f"gym bot up{'; discarded backlog' if offset else ''}")
    while True:
        try:
            r = call("getUpdates", offset=offset, timeout=POLL_SECONDS,
                     allowed_updates=["message"])
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        handle_message(con, u["message"])
                except Exception as e:
                    log(f"update {u['update_id']} failed: {e}", err=True)
        except urllib.error.HTTPError as e:
            log(f"http {e.code}; backing off", err=True)
            time.sleep(10)
        except Exception as e:                      # never die on one bad poll
            log(f"error: {e}; backing off", err=True)
            time.sleep(10)


def strip_tags(s):
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def main():
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "list":
        con = db()
        for day in ([sys.argv[2]] if len(sys.argv) > 2 else DAYS):
            print(strip_tags(day_list(con, day)), "\n")
    elif cmd == "show":
        con = db()
        for r in con.execute(
                "SELECT l.ts, e.day, e.name, l.weight FROM lifts l"
                " JOIN exercises e ON e.id = l.exercise_id ORDER BY l.id"):
            print(*r)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
