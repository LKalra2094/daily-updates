#!/usr/bin/env python3
"""One-time OAuth: mint a refresh token for the Google Health API."""

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
SCOPE = " ".join([
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
])
REDIRECT = f"http://localhost:{PORT}"

for line in (ROOT / ".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

CLIENT_ID = os.environ["GOOGLE_HEALTH_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_HEALTH_CLIENT_SECRET"]

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

env = (ROOT / ".env").read_text()
env = re.sub(r"^GOOGLE_HEALTH_REFRESH_TOKEN=.*$",
             "GOOGLE_HEALTH_REFRESH_TOKEN=" + tok["refresh_token"],
             env, flags=re.M)
(ROOT / ".env").write_text(env)
print("OK: refresh token saved. granted scope:", tok.get("scope"))
