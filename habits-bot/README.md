# Habits Bot

The evening half. A Telegram message at 8pm with one button per habit. Tap what
you did.

Each button carries the date of the message it belongs to, so scrolling back to
an older evening and tapping there repairs that day.

Why it exists and how it behaves under load is in the
[root README](../README.md) and [DECISIONS.md](../DECISIONS.md).

## Layout

| File | Role |
|---|---|
| `bot.py` | The whole thing |
| `habits.db` | SQLite completion history, written only here (not in git) |
| `habits-bot.service` | systemd unit |
| `../habits.json` | Habits and their labels - shared with the briefing |
| `../.env` | Bot token and chat id, shared with the briefing |

## Running

```sh
python3 bot.py run              # the listener
python3 bot.py send             # send today's card now
python3 bot.py send 2026-09-01  # or an older day's
python3 bot.py show             # dump the database
```

Python standard library only. Long polling, so no inbound ports and no webhook.

`/today` and `/yesterday` in the chat send a card for that day.

## Deployment

An Ubuntu VM, running as a systemd service from `~/healthy-life/habits-bot`:

```sh
sudo cp habits-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now habits-bot
journalctl -u habits-bot -f
```
