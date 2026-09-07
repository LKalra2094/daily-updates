#!/usr/bin/env python3
"""Habits bot.

Sends one message each evening with a button per habit. Tapping a button toggles
that habit for the day the message belongs to, so scrolling back to an older
message repairs an older day.

Owns habits.db. The morning briefing reads that file and knows nothing about
this process. Python standard library only.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = ROOT / "habits.db"
TZ = ZoneInfo("America/Los_Angeles")
SEND_HOUR = 20               # 8pm local
POLL_SECONDS = 50            # Telegram long-poll window
API = "https://api.telegram.org/bot{}/{}"


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
    url = API.format(os.environ["HABITS_BOT_TOKEN"], method)
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


def db():
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS completions ("
        "  day TEXT NOT NULL, habit TEXT NOT NULL, PRIMARY KEY (day, habit))"
    )
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return con


def habits():
    return json.loads((ROOT.parent / "habits.json").read_text())


def logged(con, day):
    return {r[0] for r in con.execute(
        "SELECT habit FROM completions WHERE day=?", (day,))}


def keyboard(con, day):
    done = logged(con, day)
    rows = []
    for h in habits():
        mark = "✅ " if h["key"] in done else ""
        want = 0 if h["key"] in done else 1
        rows.append([{"text": f"{mark}{h['label']}",
                      "callback_data": f"{day}:{h['key']}:{want}"}])
    return {"inline_keyboard": rows}


def card_text(day):
    d = date.fromisoformat(day)
    return d.strftime("%A, %-d %B")


def send_card(con, day):
    r = call("sendMessage",
             chat_id=os.environ["HABITS_CHAT_ID"],
             text=card_text(day),
             reply_markup=keyboard(con, day))
    if r.get("ok"):
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('last_card', ?)",
                    (day,))
        con.commit()
    return r


def handle_callback(con, cb):
    data = cb.get("data") or ""
    parts = data.split(":")
    if len(parts) != 3:
        return
    day, key, want = parts[0], parts[1], parts[2] == "1"

    # Acknowledge first: this is what stops the spinner on the phone, and it
    # must not wait on a database write. A rejection here means the tap is
    # stale, so it is discarded rather than applied.
    started = time.time()
    try:
        call("answerCallbackQuery", callback_query_id=cb["id"])
    except Exception as e:
        log(f"discarded stale tap {day} {key}: {e}", err=True)
        return
    ack_ms = (time.time() - started) * 1000

    if want:
        con.execute("INSERT OR IGNORE INTO completions (day, habit) VALUES (?, ?)",
                    (day, key))
        note = "logged"
    else:
        con.execute("DELETE FROM completions WHERE day=? AND habit=?", (day, key))
        note = "removed"
    con.commit()

    log(f"{day} {key} {note}  (ack {ack_ms:.0f}ms, "
        f"total {(time.time() - started) * 1000:.0f}ms)")

    # Redrawing is deferred to the end of the batch: one edit per message
    # instead of one per tap, and never on the path that logs the habit.
    msg = cb.get("message") or {}
    return (msg.get("chat", {}).get("id"), msg.get("message_id"), day)


def redraw(con, targets):
    """One edit per message, with a short timeout - display must never block."""
    for chat_id, message_id, day in targets:
        if not message_id:
            continue
        started = time.time()
        try:
            call("editMessageReplyMarkup", http_timeout=120,
                 chat_id=chat_id, message_id=message_id,
                 reply_markup=keyboard(con, day))
            log(f"  redrew {message_id} in {(time.time() - started) * 1000:.0f}ms")
        except Exception as e:
            log(f"  redraw {message_id} failed after "
                f"{(time.time() - started) * 1000:.0f}ms: {e}", err=True)


def handle_update(con, u):
    if "callback_query" in u:
        return handle_callback(con, u["callback_query"])
    if "message" in u:
        text = (u["message"].get("text") or "").strip().lower()
        if text in ("/today", "/start"):
            send_card(con, datetime.now(TZ).date().isoformat())
        elif text == "/yesterday":
            send_card(con, (datetime.now(TZ).date() - timedelta(days=1)).isoformat())


def due_card(con, now):
    """The day whose card should exist by now, or None."""
    if now.hour < SEND_HOUR:
        return None
    today = now.date().isoformat()
    row = con.execute("SELECT v FROM meta WHERE k='last_card'").fetchone()
    return None if row and row[0] >= today else today


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
    log(f"habits bot up; sending at {SEND_HOUR}:00 {TZ.key}"
        f"{'; discarded backlog' if offset else ''}")
    while True:
        try:
            day = due_card(con, datetime.now(TZ))
            if day:
                send_card(con, day)
                log(f"sent card for {day}")

            r = call("getUpdates", offset=offset, timeout=POLL_SECONDS,
                     allowed_updates=["callback_query", "message"])
            pending = {}
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try:
                    target = handle_update(con, u)
                    if target and target[1]:
                        pending[target[1]] = target      # last one per message
                except Exception as e:
                    log(f"update {u['update_id']} failed: {e}", err=True)
            if pending:
                redraw(con, pending.values())
        except urllib.error.HTTPError as e:
            log(f"http {e.code}; backing off", err=True)
            time.sleep(10)
        except Exception as e:                      # never die on one bad poll
            log(f"error: {e}; backing off", err=True)
            time.sleep(10)


def main():
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "send":
        con = db()
        day = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ).date().isoformat()
        print(send_card(con, day).get("ok"))
    elif cmd == "show":
        con = db()
        for r in con.execute("SELECT * FROM completions ORDER BY day, habit"):
            print(r)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
