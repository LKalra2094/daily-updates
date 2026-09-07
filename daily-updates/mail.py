#!/usr/bin/env python3
"""Sending, over the Gmail API.

Oracle blocks outbound SMTP on free-tier tenancies, so mail leaves over HTTPS
like everything else here. Gmail rather than a third-party sender because this
goes from the reader's own account to the reader's own account: no domain to
verify, no shared sending reputation, and nothing to land it in spam. A daily
message that arrives in the spam folder is a broken product.

Its own refresh token, not the health one. Adding a scope to a working token
means re-running that consent and risking a service that already works, for no
gain - the two grants are independent. The OAuth *client* is shared, though:
one client can back several grants, so setting this up is adding a scope in the
Cloud Console rather than making a second client.
"""

import base64
import json
import os
import urllib.parse
import urllib.request
from email.message import EmailMessage

from retry import Unavailable, retry

SCOPE = "https://www.googleapis.com/auth/gmail.send"
SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
TOKEN = "https://oauth2.googleapis.com/token"


def client(part):
    """GMAIL_CLIENT_* if set, else the health client - they can be the same one."""
    return (os.environ.get(f"GMAIL_CLIENT_{part}")
            or os.environ[f"GOOGLE_HEALTH_CLIENT_{part}"])


def access_token(log=None):
    body = urllib.parse.urlencode({
        "client_id": client("ID"),
        "client_secret": client("SECRET"),
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()

    def once():
        req = urllib.request.Request(TOKEN, data=body)
        return json.load(urllib.request.urlopen(req, timeout=120))["access_token"]

    return retry(once, "gmail token", log=log)


def build(to, subject, html):
    """An HTML mail with a plain-text part, so it degrades rather than breaks."""
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = os.environ.get("MAIL_FROM", to)
    msg["Subject"] = subject
    msg.set_content("This message is HTML. If you are reading this, your "
                    "client did not render it.")
    msg.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def send(to, subject, html, log=None):
    token = access_token(log=log)
    payload = json.dumps({"raw": build(to, subject, html)}).encode()

    def once():
        req = urllib.request.Request(
            SEND, data=payload,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=120))

    return retry(once, "gmail send", log=log)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for line in (root / ".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    try:
        r = send(os.environ["MAIL_TO"], "Test from healthy-life",
                 "<p>If you can read this, the Gmail API path works.</p>",
                 log=lambda m: print(m, file=sys.stderr))
        print("sent:", r.get("id"))
    except (Unavailable, KeyError) as e:
        print("FAILED:", e)
        sys.exit(1)
