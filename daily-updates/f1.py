#!/usr/bin/env python3
"""Formula 1 sessions, from the Jolpica API.

Only the four sessions that are actually watched: qualifying, the race, and on a
sprint weekend the sprint and its qualifying. Practice is not reported.

Sessions are treated as soft: they are watched the same day, but not always
live, because a European race can land before six in the morning here. So they
are named and sized, and left out of the busy windows - the reader decides when
to watch, and the model is told the time is movable.

Ergast, which this replaces, was retired. Jolpica is its successor: same shape,
same absence of authentication.
"""

import json
from datetime import datetime, timedelta, timezone

from retry import retry

API = "https://api.jolpi.ca/ergast/f1/{}/races.json?limit=100"

# What each session costs you, roughly, including build-up. Used for planning,
# not as a claim about broadcast length.
WATCHED = {
    "Qualifying": ("Qualifying", 60),
    "Race": ("Race", 120),
    "Sprint": ("Sprint", 45),
    "SprintQualifying": ("Sprint qualifying", 45),
}


def fetch(year, log=None):
    import urllib.request

    def once():
        req = urllib.request.Request(
            API.format(year), headers={"User-Agent": "healthy-life/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    return retry(once, "f1 calendar", log=log)["MRData"]["RaceTable"]["Races"]


def _local(day, clock, tz):
    """Jolpica gives UTC. Everything downstream is local."""
    stamp = datetime.fromisoformat(f"{day}T{clock.replace('Z', '+00:00')}")
    return stamp.astimezone(tz)


def sessions_on(target, tz, races=None, log=None):
    """The watched sessions falling on `target`, in local time, in order."""
    races = races if races is not None else fetch(target.year, log=log)
    out = []
    for race in races:
        for key, (label, mins) in WATCHED.items():
            block = race if key == "Race" else race.get(key)
            if not block or not block.get("time"):
                continue
            start = _local(block["date"], block["time"], tz)
            if start.date() != target:
                continue
            out.append({
                "session": label,
                "race": race["raceName"],
                "time": start.strftime("%H:%M"),
                "ends": (start + timedelta(minutes=mins)).strftime("%H:%M"),
                "minutes": mins,
            })
    out.sort(key=lambda s: s["time"])
    return out


def next_session(after, tz, races=None, log=None):
    """The next watched session from `after` onward, or None once the season ends.

    Lets a quiet day still say when the next one is, which is the question that
    otherwise gets googled.
    """
    races = races if races is not None else fetch(after.year, log=log)
    now = datetime.now(timezone.utc)
    best = None
    for race in races:
        for key, (label, _) in WATCHED.items():
            block = race if key == "Race" else race.get(key)
            if not block or not block.get("time"):
                continue
            start = _local(block["date"], block["time"], tz)
            if start <= now:
                continue
            if best is None or start < best[0]:
                best = (start, label, race["raceName"])
    if not best:
        return None
    start, label, name = best
    return {"session": label, "race": name,
            "date": start.date().isoformat(),
            "day": start.strftime("%A"),
            "time": start.strftime("%H:%M"),
            "days_away": (start.date() - after).days}
