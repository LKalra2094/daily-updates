#!/usr/bin/env python3
"""One-time OAuth: mint a refresh token.

    python3 authorize.py health   sleep and resting heart rate
    python3 authorize.py gmail    sending the 7:30 message

Two separate grants against two separate client ids. Bundling gmail.send onto
the health token would mean re-running that consent, and re-consenting a working
service to add an unrelated scope is how a working service stops working.
"""

import http.server
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
REDIRECT = f"http://localhost:{PORT}"
GRANTS = {
    "health": {
        "prefix": "GOOGLE_HEALTH",
        "scope": " ".join([
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth."
            "health_metrics_and_measurements.readonly",
        ]),
    },
    "gmail": {
        "prefix": "GMAIL",
        "scope": "https://www.googleapis.com/auth/gmail.send",
    },
}

which = sys.argv[1] if len(sys.argv) > 1 else ""
if which not in GRANTS:
    print(f"usage: authorize.py {'|'.join(GRANTS)}")
    sys.exit(2)
grant = GRANTS[which]
SCOPE = grant["scope"]
PREFIX = grant["prefix"]

for line in (ROOT.parent / ".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# One client can back both grants; only the refresh tokens differ.
CLIENT_ID = (os.environ.get(f"{PREFIX}_CLIENT_ID")
             or os.environ["GOOGLE_HEALTH_CLIENT_ID"])
CLIENT_SECRET = (os.environ.get(f"{PREFIX}_CLIENT_SECRET")
                 or os.environ["GOOGLE_HEALTH_CLIENT_SECRET"])

auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT,
    "response_type": "code",
    "scope": SCOPE,
    "access_type": "offline",
    "prompt": "consent",
})
(ROOT / ".auth_url").write_text(auth_url)

result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        result.update({k: v[0] for k, v in q.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in result
        self.wfile.write(
            b"<h2>Authorized. You can close this tab.</h2>" if ok
            else b"<h2>Authorization failed. Back to the terminal.</h2>")
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("localhost", PORT), Handler)
server.timeout = 300
server.serve_forever()

if "code" not in result:
    print("FAILED:", result.get("error", "no code returned"))
    sys.exit(1)

body = urllib.parse.urlencode({
    "code": result["code"],
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT,
    "grant_type": "authorization_code",
}).encode()
req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
tok = json.load(urllib.request.urlopen(req, timeout=30))

if "refresh_token" not in tok:
    print("NO REFRESH TOKEN:", json.dumps(tok)[:400])
    sys.exit(1)

env = (ROOT.parent / ".env").read_text()
key = f"{PREFIX}_REFRESH_TOKEN"
if re.search(rf"^{key}=", env, flags=re.M):
    env = re.sub(rf"^{key}=.*$", f"{key}=" + tok["refresh_token"], env, flags=re.M)
else:
    env = env.rstrip("\n") + f"\n{key}=" + tok["refresh_token"] + "\n"
(ROOT.parent / ".env").write_text(env)
print("OK: refresh token saved. granted scope:", tok.get("scope"))
