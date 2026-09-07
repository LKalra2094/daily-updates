# Healthy Life

Two programs that share one habit list.

**`habits-bot/`** sends a Telegram message every evening at 8pm with one button
per habit. Tapping a button records that habit for the day the message belongs
to, so scrolling back to an older evening repairs an older day.

**`daily-updates/`** sends a different Telegram message at 6am. It reads the
habit history, measures it against weekly targets, adds last night's sleep and
today's calendar, and asks a language model to write a short paragraph about
what the record shows.

The evening half records. The morning half reflects it back.

## The two contracts between them

**`habits.json` at the repo root is the single source of truth for what a habit
is.** Both halves read it. The bot uses `key` and `label` to draw the buttons;
the briefing also reads `kind`, `target_per_week` and `target_max` to measure
against. Each half ignores the fields it does not need. There is one list, so a
habit cannot be renamed in one place and not the other.

**`habits-bot/habits.db` is written only by the bot.** The briefing opens it
read-only, by URI, and knows nothing about the bot process - not whether it is
running, not how it writes. Two writers on one SQLite file is the failure mode
this avoids.

Neither file is in git. `habits.json` is personal; `habits.db` is history.
Copy `habits.example.json` to `habits.json` to start.

## Layout

| Path | Role |
|---|---|
| `habits.json` | The habit list, shared (not in git - see `habits.example.json`) |
| `.env` | Every credential for both halves (not in git) |
| `habits-bot/` | The evening bot; owns `habits.db` |
| `daily-updates/` | The morning briefing; reads `habits.db` |
| `DECISIONS.md` | Why it is built this way, including the paths not taken |

Each half has its own README covering its setup and how it runs.

Python standard library only, both halves. Nothing to install.

## Deployment

One Ubuntu VM holds the whole tree at `~/healthy-life`. The bot runs as a
systemd service; the briefing is a crontab entry. See each half's README.
