# Daily Updates

The morning half. A briefing delivered to Telegram at 6am.

Every number is computed in Python; the model only writes prose over finished
statistics. What it does and why is in the [root README](../README.md) and
[DECISIONS.md](../DECISIONS.md).

## Layout

| File | Role |
|---|---|
| `briefing.py` | Fetch, compute, render, send |
| `gcal.py` | Google Calendar iCal feeds; answers "does this event fall today" |
| `sleep.py` | Google Health API: sleep stages and resting heart rate |
| `authorize.py` | One-time OAuth flow to mint the Google Health refresh token |
| `prompt.md` | The voice. Edit this, not the code |
| `../habits.json` | Habits and their weekly targets - shared with the bot |
| `../habits-bot/habits.db` | Not written here; owned by the habits bot, opened read-only |
| `../.env` | Credentials, shared with the bot |

## Running

```sh
python3 briefing.py --dry-run    # print, send nothing
python3 briefing.py --no-prose   # skip the model
python3 briefing.py              # send
```

Python standard library only - no dependencies to install.

Habits come from `habits.json` in the repo root. `kind: "avoid"` inverts the
metric - the number becomes days the habit was broken, and the target is zero,
so an avoid habit needs no `target_per_week`.

## Sources

**Habits** - the SQLite file written by the habits bot in `../habits-bot/`,
opened read-only here.

**Todoist** - today's tasks, for the TO DO block.

**Google Calendar** - the calendar's *secret iCal address*, fetched directly. No
OAuth. Recurrence rules are not expanded into a series; `occurs_on` answers only
whether a given event falls on a given day.

**Google Health API** (`health.googleapis.com/v4`) - sleep and resting heart
rate. This replaces the Fitbit Web API, retired September 2026.

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
5. **Clients** - create a Desktop app client; put the ID and secret in `../.env`.
6. `python3 authorize.py`, open the URL it prints, accept the unverified-app
   warning. The refresh token is written to `../.env`.

The Fitbit account must be migrated to a Google account; legacy Fitbit accounts
cannot reach this API at all.

## Deployment

An Ubuntu VM, four crontab entries, no service. Weekends shift an hour, because
the sleep targets do - up at 8 rather than 7.

```
CRON_TZ=America/Los_Angeles
30 6 * * 1-5 cd ~/healthy-life/daily-updates && /usr/bin/python3 briefing.py habits >> ~/healthy-life/briefing.log 2>&1
30 7 * * 6,0 cd ~/healthy-life/daily-updates && /usr/bin/python3 briefing.py habits >> ~/healthy-life/briefing.log 2>&1
30 7 * * 1-5 cd ~/healthy-life/daily-updates && /usr/bin/python3 briefing.py tasks  >> ~/healthy-life/briefing.log 2>&1
30 8 * * 6,0 cd ~/healthy-life/daily-updates && /usr/bin/python3 briefing.py tasks  >> ~/healthy-life/briefing.log 2>&1
```

`CRON_TZ` is not optional - cloud VMs run on UTC, and without it the messages
arrive at the wrong hour and shift twice a year with daylight saving.

The log path is absolute on purpose: it sits at the repo root, above both
halves, because it is output rather than code.
