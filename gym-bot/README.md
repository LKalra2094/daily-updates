# Gym Bot

What weight am I on. Send `/push`, `/pull` or `/legs` and the day's exercises
come back with the weight you were last at. Send `1 150` and that becomes the
new weight.

It exists to save the two minutes at the start of every set spent trying to
remember, and to leave a record of every increase so the trend is readable
later.

Its own bot, its own token, its own database. It shares nothing with the habits
bot but the `.env` file, so neither can take the other down.

## The chat

```
/push                       Push day
                             1. Incline Dumbbell Press   145
                             2. Shoulder Press            85
                             3. Chest Fly                 40
                             4. Cable Lateral Raise       20
                             5. Cable Rope Tricep Ext     60

1 150                       Incline Dumbbell Press  145 → 150
bench 150                   the same thing, by name
```

| Command | |
|---|---|
| `/push` `/pull` `/legs` | the day's exercises and their current weights |
| `<number> <weight>` | update by position in the list just printed |
| `<name> <weight>` | update by name; partial is fine |
| `/add push skull crusher 60` | new exercise, starting weight optional |
| `/remove skull crusher` | drop it from the list; its history is kept |
| `/undo` | take back the last weight entered |
| `/history bench` | every weight for one exercise, in order |

Names match loosely, best match first: an exact name beats a name starting with
what you typed, which beats a word prefix, which beats a substring. That is what
lets a bare `pull` reach Pull Ups without Lat Pulldown also answering. When two
are genuinely equal it asks which, preferring exercises on the day last printed.

## Weights

Pounds, and signed. Assisted pull ups sit at `-60` and climb towards zero, so
"the number went up" means progress on every exercise without a special case
anywhere in the code.

## Storage

Nothing is ever overwritten. Every update appends a row to `lifts`; the current
weight is just the newest row for that exercise, and the history is the trend.
`/undo` is a single `DELETE` of the last row.

`/remove` deactivates rather than deletes, so a lift you drop keeps its past —
and adding the same name back later rejoins it to that history rather than
starting a second, parallel record.

| Table | |
|---|---|
| `exercises` | id, day, name, position, active |
| `lifts` | id, timestamp, exercise id, weight |
| `meta` | the list last printed, so `1 150` knows what 1 was |

## Layout

| File | Role |
|---|---|
| `bot.py` | The whole thing |
| `gym.db` | Exercises and every weight ever entered (not in git) |
| `gym-bot.service` | systemd unit |
| `../.env` | `GYM_BOT_TOKEN` and `GYM_CHAT_ID` |

## Running

```sh
python3 bot.py run          # the listener
python3 bot.py list         # print all three days
python3 bot.py list push    # or one
python3 bot.py show         # dump every entry ever made
```

Python standard library only. Long polling, so no inbound ports and no webhook.

The exercise list is seeded on first run and lives in the database from then on,
because `/add` writes to it from the phone.

## Setup

Make the bot with BotFather, then put its token in `../.env`:

```
GYM_BOT_TOKEN=...
GYM_CHAT_ID=
```

Leave `GYM_CHAT_ID` empty and start it. Message the bot; it replies with the id
of the chat. Put that in `.env` and restart. Until it is set the bot refuses to
write anything, and once set it ignores every other chat — this is a bot that
takes writes, so it should only take them from you.

## Deployment

The same Ubuntu VM as the habits bot, alongside it at `~/healthy-life/gym-bot`:

```sh
sudo cp gym-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gym-bot
journalctl -u gym-bot -f
```
