#!/usr/bin/env python3
"""Retry transient network failures.

The briefing runs once a day with nothing waiting on it, so it can afford to be
patient. What it cannot afford is showing yesterday's numbers as though they
were today's - so callers that exhaust their retries report the failure rather
than falling back.
"""

import time
import urllib.error

ATTEMPTS = 4
BACKOFF = (5, 20, 60)          # seconds between attempts
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class Unavailable(Exception):
    """A source could not be reached after every attempt."""


def retry(fn, what, attempts=ATTEMPTS, log=None):
    """Call fn(), retrying only failures that stand a chance of healing."""
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            # 401/403/404 will not fix themselves; do not burn 85 seconds on them.
            if e.code not in RETRY_STATUS:
                raise Unavailable(f"{what}: HTTP {e.code}") from e
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(getattr(e, "reason", e))

        if attempt < attempts - 1:
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            if log:
                log(f"{what}: {last} - retrying in {delay}s")
            time.sleep(delay)

    raise Unavailable(f"{what}: {last}")
