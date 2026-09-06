# Healthy Life

A morning briefing, delivered to Telegram at 6am.

Habits are logged as recurring tasks in Todoist. Every morning a cron job on a
small VM reads what was completed, measures it against a weekly target, pulls
the day's calendar, and asks a language model to write a short paragraph about
what the record shows. The paragraph is the briefing; the table underneath is
the evidence.

## Design

Every number is computed in Python. The model receives finished statistics and
writes prose over them - it never does arithmetic, and it is told explicitly
that it may not state a figure it was not given. If the model is unreachable the
briefing still sends, without the paragraph.

Habits are measured against a weekly target rather than as raw rates or streaks.
A once-a-week habit done once a week is met, not 14% complete. Streaks were
deliberately rejected: one missed day zeroing a month is the opposite of
evidence.

## Layout

| File | Role |
|---|---|
| `briefing.py` | Fetch, store, compute, render, send |
| `gcal.py` | Reads Google Calendar iCal feeds; answers "does this event fall today" |
| `habits.json` | Habits and their weekly targets (not in git - see `habits.example.json`) |
| `prompt.md` | The voice. Edit this, not the code |
| `habits.db` | SQLite completion history (not in git) |
| `.env` | Credentials (not in git) |

## Running

```sh
python3 briefing.py --dry-run    # print, send nothing
python3 briefing.py --no-prose   # skip the model
python3 briefing.py              # send
```

Python standard library only - no dependencies to install.

Copy `habits.example.json` to `habits.json` and set your own habits and weekly
targets. `kind: "avoid"` inverts the metric - the number becomes days the habit
was broken, and the target is zero.

## Sources

- **Todoist** activity log. Recurring completions never appear in the
  completed-tasks endpoints, so `/api/v1/activities` is the only source that
  records them.
- **Google Calendar** secret iCal feed, read directly.
- **Gemini** free tier writes the paragraph, with a fallback chain across models.
- **Telegram** Bot API delivers it.
