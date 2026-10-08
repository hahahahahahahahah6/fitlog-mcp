#!/usr/bin/env python3
"""Web-based simulated Alexa+ experience for fitlog-mcp.

A stdlib-only HTTP server that:
  * serves the simulator UI (index.html),
  * runs the REAL OAuth 2.1 + PKCE account-linking dance against the FitLog
    MCP server (browser -> /authorize -> owner password -> Approve ->
    /api/link/callback -> /token),
  * proxies chat turns to the real MCP server over Streamable HTTP
    (initialize -> tools/call with a real bearer token),
  * maps typed "voice" phrases to the 6 FitLog tools with a small
    keyword/regex router.

Run:  FITLOG_OWNER_PASSWORD=... python3 server.py   (see README.md)
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# -- configuration ---------------------------------------------------------
FITLOG_MCP_URL = os.environ.get("FITLOG_MCP_URL", "http://127.0.0.1:8765").rstrip("/")
# Browser-facing base for the MCP server's /authorize page. Server-side calls
# always use FITLOG_MCP_URL (loopback); the browser must be sent to the public
# URL when the demo is exposed through a tunnel.
FITLOG_MCP_PUBLIC_URL = os.environ.get(
    "FITLOG_MCP_PUBLIC_URL", "").rstrip("/") or FITLOG_MCP_URL
CANONICAL_RESOURCE = FITLOG_MCP_URL + "/mcp"
SIM_HOST = os.environ.get("SIMULATOR_HOST", "127.0.0.1")
SIM_PORT = int(os.environ.get("SIMULATOR_PORT", "8799"))
# When the simulator is exposed publicly (e.g. through a tunnel), the OAuth
# redirect_uri must be the public https URL, not http://host:port.
SIM_PUBLIC_URL = os.environ.get("SIMULATOR_PUBLIC_URL", "").rstrip("/")
PROTOCOL_VERSION = "2025-11-25"
CLIENT_ID = "alexa-plus-simulator"
SCOPES = "fitlog.read fitlog.write"

# -- in-memory state --------------------------------------------------------
LINKED: dict = {"token": None, "linked_at": None}
PENDING: dict[str, dict] = {}   # state -> {verifier, redirect_uri}
MCP_SESSION: dict = {"id": None}
RPC_ID = [0]


# ==================================================================== mcp client

def _http(method: str, url: str, body: bytes | None = None,
          headers: dict | None = None, timeout: int = 15):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: int = 15):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    h.update(headers or {})
    return _http("POST", url, data, h, timeout)


def _mcp_endpoint() -> str:
    """The /mcp JSON-RPC endpoint of the configured upstream server."""
    return FITLOG_MCP_URL + "/mcp"


def _rpc(method: str, params: dict | None = None,
         notification: bool = False):
    """One JSON-RPC round trip; returns (request_obj, response_obj|None)."""
    RPC_ID[0] += 1
    req_obj: dict = {"jsonrpc": "2.0", "method": method}
    if not notification:
        req_obj["id"] = RPC_ID[0]
    if params is not None:
        req_obj["params"] = params
    headers = {
        "Authorization": "Bearer " + LINKED["token"],
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if MCP_SESSION["id"]:
        headers["Mcp-Session-Id"] = MCP_SESSION["id"]
    status, resp_headers, raw = _post_json(_mcp_endpoint(), req_obj,
                                           headers)
    if notification:
        return req_obj, None
    resp_obj = json.loads(raw.decode("utf-8"))
    new_sid = resp_headers.get("Mcp-Session-Id") or resp_headers.get(
        "mcp-session-id")
    if new_sid:
        MCP_SESSION["id"] = new_sid
    return req_obj, resp_obj


def _ensure_session():
    if MCP_SESSION["id"]:
        return
    req, resp = _rpc("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": CLIENT_ID, "version": "0.1.0"},
    })
    if not resp or "error" in resp:
        raise RuntimeError("initialize failed: %r" % (resp,))
    _rpc("notifications/initialized", notification=True)


def mcp_tools_call(name: str, arguments: dict):
    """Call a tool; returns (request_obj, response_obj, text, is_error,
    elapsed_ms). Re-initializes once on a dead session."""
    if not LINKED["token"]:
        raise RuntimeError("not_linked")
    t0 = time.monotonic()
    for attempt in (0, 1):
        _ensure_session()
        req, resp = _rpc("tools/call",
                         {"name": name, "arguments": arguments})
        err = (resp or {}).get("error") or {}
        msg = str(err.get("message", ""))
        if attempt == 0 and ("session" in msg.lower()
                             or "Mcp-Session-Id" in msg):
            MCP_SESSION["id"] = None
            continue
        break
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    text, is_error = "", True
    result = (resp or {}).get("result") or {}
    if isinstance(result, dict):
        parts = result.get("content") or []
        text = "\n".join(p.get("text", "") for p in parts
                         if isinstance(p, dict))
        is_error = bool(result.get("isError"))
    return req, resp, text, is_error, elapsed_ms


def mcp_health() -> bool:
    try:
        status, _, _ = _http("GET", FITLOG_MCP_URL + "/health", timeout=5)
        return status == 200
    except Exception:
        return False


_CANONICAL_CACHE: dict = {"value": None, "at": 0.0}


def canonical_resource() -> str:
    """The server's canonical resource URI, discovered from its RFC 9728
    metadata. This matters when the server is publicly exposed: with
    FITLOG_PUBLIC_URL set, the canonical resource is the public URL, and
    the OAuth flow must use exactly that value."""
    now = time.monotonic()
    if _CANONICAL_CACHE["value"] and now - _CANONICAL_CACHE["at"] < 60:
        return _CANONICAL_CACHE["value"]
    value = FITLOG_MCP_URL + "/mcp"
    try:
        status, _, raw = _http(
            "GET", FITLOG_MCP_URL + "/.well-known/oauth-protected-resource",
            timeout=5)
        if status == 200:
            doc = json.loads(raw.decode("utf-8"))
            if isinstance(doc, dict) and doc.get("resource"):
                value = doc["resource"]
    except Exception:
        pass
    _CANONICAL_CACHE.update(value=value, at=now)
    return value


# ==================================================================== oauth

def _pkce_pair():
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(host: str, port: int) -> str:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    if SIM_PUBLIC_URL:
        redirect_uri = SIM_PUBLIC_URL + "/api/link/callback"
    else:
        redirect_uri = f"http://{host}:{port}/api/link/callback"
    resource = canonical_resource()
    PENDING[state] = {"verifier": verifier, "redirect_uri": redirect_uri,
                      "resource": resource, "created": time.time()}
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_uri,
        "resource": resource,
        "scope": SCOPES,
        "state": state,
    })
    # The browser must reach the MCP server's public origin; server-side
    # calls (token exchange, tools/call) keep using FITLOG_MCP_URL.
    return FITLOG_MCP_PUBLIC_URL + "/authorize?" + q


def exchange_code(code: str, state: str):
    rec = PENDING.pop(state, None)
    if rec is None:
        raise RuntimeError("unknown or expired login state")
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": rec["verifier"],
        "redirect_uri": rec["redirect_uri"],
        "resource": rec["resource"],
    }).encode()
    status, _, raw = _http(
        "POST", FITLOG_MCP_URL + "/token", form,
        {"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    body = json.loads(raw.decode("utf-8"))
    if status != 200:
        raise RuntimeError("token exchange failed: %r" % (body,))
    LINKED["token"] = body["access_token"]
    LINKED["linked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    MCP_SESSION["id"] = None  # fresh session for the new token


# ==================================================================== NLU router

DAY_WORDS = {
    "monday": "monday", "mon": "monday",
    "tuesday": "tuesday", "tue": "tuesday", "tues": "tuesday",
    "wednesday": "wednesday", "wed": "wednesday",
    "thursday": "thursday", "thu": "thursday", "thur": "thursday",
    "thurs": "thursday", "friday": "friday", "fri": "friday",
    "saturday": "saturday", "sat": "saturday",
    "sunday": "sunday", "sun": "sunday",
}

_UNIT_RE = r"(lb|lbs|pound|pounds|kg|kilo|kilos)?"


def _unit(word: str | None) -> str:
    if word and word.startswith(("lb", "pound")):
        return "lb"
    return "kg"


def _strip_wake(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^(alexa[, ]+)?(ask|tell)\s+fitlog\s+(to\s+)?", "", t,
               flags=re.I).strip()
    t = re.sub(r"^alexa[, ]+", "", t, flags=re.I).strip()
    return t


def route(text: str):
    """Return (tool_name, arguments) or (None, None) for the help fallback."""
    t = _strip_wake(text)
    low = t.lower()

    # -- log protein (check before log_workout: "log 45 grams of protein")
    m = re.search(r"log\s+(\d+(?:\.\d+)?)\s*(?:g|grams?)(?:\s+of)?\s+protein",
                  low)
    if not m:
        m = re.search(r"(?:i\s+had|i\s+ate)\s+(\d+(?:\.\d+)?)\s*(?:g|grams?)"
                      r"(?:\s+of)?\s+protein", low)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*g(?:rams?)?\s+(?:of\s+)?protein", low)
    if m:
        return "log_protein", {"grams": float(m.group(1))}

    # -- log workout: "log my bench press: 3 sets of 5 at 185 pounds"
    m = re.search(
        r"log\s+(?:my\s+)?(.+?)\s*:\s*(\d+)\s*sets?\s*(?:of\s*)?(\d+)\s*"
        r"(?:reps?\s*)?(?:at\s*)?(\d+(?:\.\d+)?)\s*" + _UNIT_RE, low)
    if m:
        n_sets, reps, weight = int(m.group(2)), int(m.group(3)), float(m.group(4))
        return "log_workout", {
            "exercise": m.group(1).strip(),
            "sets": [{"reps": reps, "weight": weight}] * n_sets,
            "unit": _unit(m.group(5)),
        }
    # -- log workout: "log my bench press: 5 reps at 185 pounds"
    m = re.search(
        r"log\s+(?:my\s+)?(.+?)\s*:\s*(\d+)\s*(?:reps?\s*)?(?:x|at)\s*"
        r"(\d+(?:\.\d+)?)\s*" + _UNIT_RE, low)
    if m:
        return "log_workout", {
            "exercise": m.group(1).strip(),
            "sets": [{"reps": int(m.group(2)), "weight": float(m.group(3))}],
            "unit": _unit(m.group(4)),
        }

    # -- personal records
    if re.search(r"personal records?|\bpr\b|my best|my max|one.?rep.?max|\b1rm\b",
                 low):
        return "get_personal_records", {}

    # -- workout plan
    if re.search(r"\bplan\b|workout plan|train today|today'?s workout|"
                 r"what should i (train|do)|leg day|chest day|back day|"
                 r"shoulder day|arm day", low):
        args: dict = {}
        for w, day in DAY_WORDS.items():
            if re.search(r"\b" + w + r"\b", low):
                args["day"] = day
                break
        return "plan_workout", args

    # -- history
    if re.search(r"\bhistory\b|workouts? (i|you|my)|my workouts|"
                 r"what did i do|sessions|show my|last week", low):
        args = {}
        m = re.search(r"(?:history|workouts?)\s+(?:for|of)\s+([a-z ]+)", low)
        if m:
            args["exercise"] = m.group(1).strip()
        m = re.search(r"last\s+(\d+)", low)
        if m:
            args["limit"] = int(m.group(1))
        return "get_history", args

    # -- nutrition targets
    if re.search(r"\btargets?\b|how much protein should|daily (protein|nutrition)|"
                 r"protein goal", low):
        return "get_nutrition_targets", {}

    return None, None


HELP_TEXT = (
    "I can do six things — try one of these:\n"
    "• “Log my bench press: 3 sets of 5 at 185 pounds”\n"
    "• “Log 45 grams of protein”\n"
    "• “What's my squat personal record?”\n"
    "• “What's today's workout plan?”\n"
    "• “Show my workout history”\n"
    "• “What are my nutrition targets?”\n"
    "Link your FitLog account first with the button above."
)


# ==================================================================== http server

class Handler(BaseHTTPRequestHandler):
    server_version = "FitLogSimulator/0.1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers ------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj, extra: dict | None = None):
        self._send(code, json.dumps(obj).encode(), "application/json", extra)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    # -- routes --------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return

        if path == "/api/status":
            self._json(200, {
                "linked": LINKED["token"] is not None,
                "linked_at": LINKED["linked_at"],
                "mcp_up": mcp_health(),
                "mcp_url": CANONICAL_RESOURCE,
            })
            return

        if path == "/api/link":
            # Start the real OAuth dance: hand the browser to the MCP
            # server's /authorize page.
            host = self.headers.get("Host", "").split(":")[0] or SIM_HOST
            self._redirect(authorize_url(host, SIM_PORT))
            return

        if path == "/api/link/callback":
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            err = (qs.get("error") or [None])[0]
            if err or not code or not state:
                self._send(400,
                            f"<h1>Link failed</h1><p>{html.escape(str(err or 'missing code/state'))}</p>".encode(),
                            "text/html; charset=utf-8")
                return
            try:
                exchange_code(code, state)
            except Exception as e:  # noqa: BLE001 - show it, demo server
                self._send(400,
                            f"<h1>Link failed</h1><p>{html.escape(str(e))}</p>".encode(),
                            "text/html; charset=utf-8")
                return
            self._redirect("/?linked=1")
            return

        if path == "/api/link/reset":
            LINKED["token"] = None
            LINKED["linked_at"] = None
            MCP_SESSION["id"] = None
            self._json(200, {"linked": False})
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/chat":
            self._json(404, {"error": "not_found"})
            return
        body = self._read_json()
        message = str(body.get("message", "") or "")
        if not message.strip():
            self._json(400, {"error": "empty message"})
            return
        if not LINKED["token"]:
            self._json(409, {"error": "not_linked",
                             "hint": "Link your FitLog account first."})
            return
        tool, args = route(message)
        if tool is None:
            self._json(200, {
                "reply": HELP_TEXT,
                "tool": None,
                "arguments": {},
                "request": None,
                "response": None,
                "elapsed_ms": 0,
            })
            return
        try:
            req, resp, text, is_error, ms = mcp_tools_call(tool, args)
        except RuntimeError as e:
            self._json(502, {"error": "mcp_error", "detail": str(e)})
            return
        reply = ("Hmm, that didn't work: " if is_error else "") + text
        self._json(200, {
            "reply": reply,
            "tool": tool,
            "arguments": args,
            "request": req,
            "response": resp,
            "elapsed_ms": ms,
        })


def main():
    # Fail fast with a useful message if the MCP server is unreachable.
    if not mcp_health():
        print(f"warning: MCP server not reachable at {FITLOG_MCP_URL} "
              f"(/health). Start it first: python3 ../server.py",
              file=sys.stderr)
    httpd = ThreadingHTTPServer((SIM_HOST, SIM_PORT), Handler)
    print(f"FitLog Alexa+ simulator on http://{SIM_HOST}:{SIM_PORT}/",
          flush=True)
    print(f"  MCP upstream: {CANONICAL_RESOURCE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
