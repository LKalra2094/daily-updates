# Daily Updates

A morning briefing, delivered to Telegram at 6am.

Habits are logged as recurring tasks in Todoist. Sleep comes off a Fitbit.
Every morning a cron job on a small VM reads what happened, measures it against
targets, pulls the day's calendar, and asks a language model to write a short
paragraph about what the record shows. The paragraph is the briefing; the tables
underneath are the evidence.

## Design

**Every number is computed in Python.** The model receives finished statistics
and writes prose over them - it never does arithmetic, and it is told explicitly
that it may not state a figure it was not given. If the model is unreachable the
briefing still sends, without the paragraph.

**Targets, not raw rates.** A once-a-week habit done once a week is met, not 14%
complete. Streaks were deliberately rejected: one missed day zeroing a month is
the opposite of evidence.

**No week-sized buckets.** The model is handed the raw 30-day daily pattern per
habit - one character a day, `X` or `-` - and finds the trend itself rather than
being fed a this-week-vs-last-week comparison.

**Sleep is a target, not an excuse.** Missing it never produces advice to do less
today. It produces a concrete bedtime for tonight, read off when the last
calendar event ends.

**Nothing is allowed to cost the morning message.** Every external call is
wrapped; a dead calendar feed, a 503 from the model, an expired health token all
degrade the briefing rather than cancel it.

## Layout

| File | Role |
|---|---|
| `briefing.py` | Fetch, store, compute, render, send |
| `gcal.py` | Google Calendar iCal feeds; answers "does this event fall today" |
| `sleep.py` | Google Health API: sleep stages and resting heart rate |
| `authorize.py` | One-time OAuth flow to mint the Google Health refresh token |
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

**Todoist** - the activity log at `/api/v1/activities`. Recurring completions
never appear in the completed-tasks endpoints (completing a recurring task just
advances its due date), so the activity log is the only source that records
them. The free tier keeps one week of activity, so each run backfills 7 days
into local SQLite, which is the permanent record.

**Google Calendar** - the calendar's *secret iCal address*, fetched directly. No
OAuth. Recurrence rules are not expanded into a series; `occurs_on` answers only
whether a given event falls on a given day.

**Google Health API** (`health.googleapis.com/v4`) - sleep and resting heart
rate. This replaces the Fitbit Web API, retired September 2026. Google computes
daily resting heart rate *from sleep*, so it is a sleeping-HR figure rather than
a daytime average.

**Gemini** free tier writes the paragraph, with a fallback chain across models.
**Telegram** Bot API delivers it, splitting at 4096 characters.

## Google Health setup

Needed once, and only for sleep. Everything else uses a plain key or URL.

1. Google Cloud project, enable **Google Health API**.
2. **Google Auth Platform** - External user type; fill in Branding (app name,
   support email, home page, privacy policy link) and add `github.com` to
   Authorized domains.
3. **Data Access** - add both scopes:
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
4. **Audience** - Publish app. An app left in Testing issues refresh tokens that
   expire after 7 days, which would silently kill an unattended cron job.
5. **Clients** - create a Desktop app client; put the ID and secret in `.env`.
6. `python3 authorize.py`, open the URL it prints, accept the unverified-app
   warning. The refresh token is written to `.env`.

The Fitbit account must be migrated to a Google account; legacy Fitbit accounts
cannot reach this API at all.

## Deployment

An Ubuntu VM, one crontab entry, no service:

```
CRON_TZ=America/Los_Angeles
0 6 * * * cd ~/daily-updates && /usr/bin/python3 briefing.py >> briefing.log 2>&1
```

`CRON_TZ` is not optional - cloud VMs run on UTC, and without it the briefing
arrives at the wrong hour and shifts twice a year with daylight saving.
