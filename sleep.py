#!/usr/bin/env python3
"""Sleep from the Google Health API (Fitbit).

The Fitbit Web API is being retired; this reads its replacement. Stdlib only.
A night is filed under the date you woke up on, in local time.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "https://health.googleapis.com/v4"
FAMILY = "users/me/dataSourceFamilies/google-wearables"

# Targets. Weekend nights - those you wake from on Sat or Sun - slide an hour later.
TARGET_HOURS = (7, 8)
TARGET_BED_WEEKDAY = "23:00"
TARGET_WAKE_WEEKDAY = "07:00"
TARGET_BED_WEEKEND = "00:00"
TARGET_WAKE_WEEKEND = "08:00"


def access_token():
    body = urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_HEALTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_HEALTH_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_HEALTH_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    return json.load(urllib.request.urlopen(req, timeout=300))["access_token"]


def _dt(block, which):
    """Absolute instant, shifted into the wearer's own local time."""
    t = datetime.fromisoformat(block[f"{which}Time"].replace("Z", "+00:00"))
    offset = int(block.get(f"{which}UtcOffset", "0s").rstrip("s"))
    return t.astimezone(timezone(timedelta(seconds=offset)))


def nights(since, token=None):
    """Sleep sessions ending on or after `since` (a date), newest first."""
    token = token or access_token()
    q = urllib.parse.urlencode({
        "dataSourceFamily": FAMILY,
        "filter": f'sleep.interval.civil_end_time >= "{since.isoformat()}"',
    })
    req = urllib.request.Request(
        f"{BASE}/users/me/dataTypes/sleep/dataPoints:reconcile?{q}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=300))

    out = []
    for point in data.get("dataPoints", []):
        s = point.get("sleep") or {}
        interval = s.get("interval")
        if not interval:
            continue
        start, end = _dt(interval, "start"), _dt(interval, "end")
        in_bed = (end - start).total_seconds() / 60

        by_stage, awake = {}, 0.0
        for stage in s.get("stages", []):
            mins = (_dt(stage, "end") - _dt(stage, "start")).total_seconds() / 60
            kind = stage.get("type", "UNKNOWN")
            by_stage[kind] = by_stage.get(kind, 0) + mins
            if kind == "AWAKE":
                awake += mins

        asleep = in_bed - awake
        out.append({
            "date": end.date().isoformat(),   # the morning you woke up
            "went_to_bed": start.strftime("%H:%M"),
            "woke": end.strftime("%H:%M"),
            "asleep_minutes": round(asleep),
            "in_bed_minutes": round(in_bed),
            "efficiency_pct": round(100 * asleep / in_bed) if in_bed else None,
            "deep_minutes": round(by_stage.get("DEEP", 0)),
            "rem_minutes": round(by_stage.get("REM", 0)),
        })
    out.sort(key=lambda n: n["date"], reverse=True)
    return out


def resting_hr(token=None):
    """Daily resting heart rate. Google derives it from sleep, so it is the
    sleeping-HR figure rather than a daytime average."""
    token = token or access_token()
    q = urllib.parse.urlencode({"dataSourceFamily": FAMILY})
    req = urllib.request.Request(
        f"{BASE}/users/me/dataTypes/daily-resting-heart-rate/dataPoints:reconcile?{q}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=300))
    out = []
    for point in data.get("dataPoints", []):
        r = point.get("dailyRestingHeartRate") or {}
        d = r.get("date") or {}
        if not d or not r.get("beatsPerMinute"):
            continue
        out.append({
            "date": f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}",
            "bpm": int(r["beatsPerMinute"]),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def _minutes(hhmm_str):
    return int(hhmm_str[:2]) * 60 + int(hhmm_str[3:5])


def _clock(minutes):
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _bed_minutes(hhmm_str):
    """Bedtimes past midnight sort after 23:00, not before 00:00."""
    m = _minutes(hhmm_str)
    return m + 24 * 60 if m < 12 * 60 else m


def targets_for(wake_date):
    """Targets for a night you wake from on `wake_date`."""
    weekend = wake_date.weekday() >= 5
    return {
        "bed": TARGET_BED_WEEKEND if weekend else TARGET_BED_WEEKDAY,
        "wake": TARGET_WAKE_WEEKEND if weekend else TARGET_WAKE_WEEKDAY,
        "is_weekend": weekend,
    }


def hhmm(minutes):
    return f"{int(minutes) // 60}h{int(minutes) % 60:02d}"


def summarise(rows, hr_rows, today):
    """Most recent night, the baseline it sits against, and tonight's target."""
    tonight = targets_for(today + timedelta(days=1))
    out = {"tonight_target_bed": tonight["bed"],
           "tonight_target_wake": tonight["wake"],
           "target_hours": f"{TARGET_HOURS[0]}-{TARGET_HOURS[1]}"}

    recent = [r for r in rows if r["date"] <= today.isoformat()]
    if not recent:
        out["no_data"] = True
        return out

    last = recent[0]
    nights_ago = (today - date.fromisoformat(last["date"])).days
    window = recent[:30]
    hours = last["asleep_minutes"] / 60
    tgt = targets_for(date.fromisoformat(last["date"]))

    out.update({
        "most_recent_night": {
            "date": last["date"],
            "nights_ago": nights_ago,      # 0 means this morning
            "is_stale": nights_ago > 1,
            "asleep": hhmm(last["asleep_minutes"]),
            "asleep_hours": round(hours, 1),
            "hit_target": hours >= TARGET_HOURS[0],
            "went_to_bed": last["went_to_bed"],
            "target_bed_that_night": tgt["bed"],
            "woke": last["woke"],
            "target_wake_that_night": tgt["wake"],
            "deep_minutes": last["deep_minutes"],
            "rem_minutes": last["rem_minutes"],
            "awake_minutes": last["in_bed_minutes"] - last["asleep_minutes"],
        },
        "baseline": {
            "nights": len(window),
            "usual_asleep": hhmm(sum(r["asleep_minutes"] for r in window) / len(window)),
            "usual_bed": _clock(sum(_bed_minutes(r["went_to_bed"]) for r in window) / len(window)),
            "usual_wake": _clock(sum(_minutes(r["woke"]) for r in window) / len(window)),
            "nights_hitting_target": sum(
                1 for r in window if r["asleep_minutes"] >= TARGET_HOURS[0] * 60),
        },
    })

    # Bedtime spread: consistency outranks duration in the evidence.
    if len(window) > 1:
        beds = [_bed_minutes(r["went_to_bed"]) for r in window]
        out["baseline"]["bedtime_spread_minutes"] = round(max(beds) - min(beds))

    if hr_rows:
        recent_hr = [h for h in hr_rows if h["date"] <= today.isoformat()]
        if recent_hr:
            latest = recent_hr[0]
            others = [h["bpm"] for h in recent_hr[1:31]] or [latest["bpm"]]
            usual = sum(others) / len(others)
            delta = latest["bpm"] - usual
            out["resting_heart_rate"] = {
                "last": latest["bpm"],
                "usual": round(usual, 1),
                "above_usual_by": round(delta, 1),
                # Only worth mentioning when it has actually moved.
                "is_elevated": delta >= 3,
            }
    return out
