"""Streamable HTTP transport for fitlog-mcp (MCP spec 2025-11-25).

Single MCP endpoint with POST for client->server JSON-RPC, optional GET for
an SSE stream, and DELETE for explicit session termination.

Hard requirements implemented here:
  * Bearer auth on every /mcp request (OAuth 2.1 + PKCE issued tokens, or
    the FITLOG_API_TOKEN owner token). Unauthenticated -> HTTP 401 with NO
    WWW-Authenticate header, per the Alexa+ MCP authentication checklist.
  * OAuth discovery: RFC 9728 Protected Resource Metadata at
    /.well-known/oauth-protected-resource and authorization server metadata
    at /.well-known/oauth-authorization-server (S256 PKCE required).
  * Origin header MUST-validation on every Streamable HTTP request:
    a present-but-untrusted Origin -> HTTP 403 (DNS-rebinding mitigation).
  * MCP-Protocol-Version header on every post-initialize request:
    an unsupported version -> HTTP 400.
  * Mcp-Session-Id lifecycle: issued at initialize, required afterwards,
    revoked on DELETE (later use -> 404).
  * JSON-RPC batch (top-level array) is rejected: the 2025-11-25 spec
    removed batching -> HTTP 400 / -32600.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION
from mcp import auth as oauth
from mcp.auth import AuthState
from mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    SessionStore,
    handle_rpc,
)
from store import Store

SESSION_HEADER = "Mcp-Session-Id"
VERSION_HEADER = "MCP-Protocol-Version"

# Origins trusted in addition to loopback. Comma-separated env var.
EXTRA_ORIGINS = {
    o.strip()
    for o in os.environ.get("FITLOG_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ServerContext:
    def __init__(self, store: Store, sessions: SessionStore,
                 auth: AuthState, public_url: str,
                 owner_password: str = ""):
        self.store = store
        self.sessions = sessions
        self.auth = auth
        self.public_url = public_url
        self.canonical_resource = public_url.rstrip("/") + "/mcp"
        # Empty string = no gate (local dev). Set = /authorize requires the
        # owner password first. main() refuses public exposure without it.
        self.owner_password = owner_password


def _origin_allowed(origin: str | None) -> bool:
    # Absent Origin (curl, native MCP clients) is fine; the spec only
    # mandates rejecting a *present and invalid* Origin.
    if not origin:
        return True
    if origin in EXTRA_ORIGINS:
        return True
    try:
        host = urllib.parse.urlparse(origin).hostname or ""
    except ValueError:
        return False
    return host.lower() in LOCAL_HOSTS


def _first_values(qs: dict[str, list[str]]) -> dict[str, str]:
    return {k: v[0] for k, v in qs.items() if v}


class MCPHandler(BaseHTTPRequestHandler):
    ctx: ServerContext  # set by create_server()

    # -- helpers ---------------------------------------------------------

    def log_message(self, fmt, *args):  # keep logs one line per request
        # Write directly to stderr: BaseHTTPRequestHandler.log_error()
        # delegates back to log_message(), so calling self.log_error()
        # here would recurse forever.
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, code: int, obj, extra: dict | None = None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, html_body: str):
        body = html_body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_401(self):
        # Alexa+ REQUIRES 401 *without* a WWW-Authenticate header for
        # unauthenticated requests -- the opposite of vanilla MCP.
        body = json.dumps({
            "error": "invalid_token",
            "error_description": (
                "Missing or invalid bearer token. Authenticate with "
                "OAuth 2.1 + PKCE; see /.well-known/oauth-protected-resource."
            ),
        }).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # NOTE: no WWW-Authenticate header here, on purpose.
        self.end_headers()
        self.wfile.write(body)

    def _rpc_error_envelope(self, code: int, message: str):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message},
        }

    def _check_origin(self) -> bool:
        """True if the request may proceed; sends 403 and returns False if not."""
        if _origin_allowed(self.headers.get("Origin")):
            return True
        # Same-origin posts from our own public URL (the OAuth authorize /
        # login forms rendered in a browser) are legitimate.
        origin = self.headers.get("Origin")
        if origin:
            try:
                o = urllib.parse.urlparse(origin)
                p = urllib.parse.urlparse(self.ctx.public_url)
                if (o.scheme, o.hostname, o.port or "") == \
                   (p.scheme, p.hostname, p.port or ""):
                    return True
            except ValueError:
                pass
        self._send_json(
            403,
            self._rpc_error_envelope(INVALID_REQUEST, "Origin not allowed."),
        )
        return False

    def _owner_logged_in(self) -> bool:
        """True if the owner gate is off, or a valid owner session cookie
        is present (the /authorize password sign-in sets it)."""
        if not self.ctx.owner_password:
            return True
        cookie = self.headers.get("Cookie") or ""
        value = None
        for part in cookie.split(";"):
            name, _, v = part.strip().partition("=")
            if name == oauth.OWNER_COOKIE:
                value = v.strip().strip('"')
                break
        return oauth.owner_cookie_valid(value, self.ctx.owner_password)

    def _check_auth(self) -> bool:
        """True if the request carries a valid bearer token."""
        authz = self.headers.get("Authorization") or ""
        if authz.startswith("Bearer "):
            if self.ctx.auth.validate_token(authz[7:].strip()):
                return True
        self._send_401()
        return False

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return None, "Empty request body."
        try:
            return json.loads(raw.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError):
            return None, "Malformed JSON body."

    def _read_form_body(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        return _first_values(urllib.parse.parse_qs(raw.decode("utf-8", "replace")))

    # -- routing ----------------------------------------------------------

    def do_OPTIONS(self):
        # CORS preflight for browser-based MCP clients.
        origin = self.headers.get("Origin", "*")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            f"Content-Type, Accept, Authorization, {SESSION_HEADER}, {VERSION_HEADER}",
        )
        self.send_header("Access-Control-Expose-Headers", SESSION_HEADER)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._check_origin():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "server": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "protocol": PROTOCOL_VERSION,
                    "auth": "oauth2-pkce",
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
            )
            return
        if path in ("/.well-known/oauth-protected-resource",
                    "/.well-known/oauth-protected-resource/mcp"):
            self._send_json(
                200,
                oauth.protected_resource_metadata(
                    self.ctx.public_url, self.ctx.canonical_resource
                ),
            )
            return
        if path == "/.well-known/oauth-authorization-server":
            self._send_json(
                200, oauth.authorization_server_metadata(self.ctx.public_url)
            )
            return
        if path == "/authorize":
            q = _first_values(urllib.parse.parse_qs(parsed.query))
            params, err = oauth.validate_authorize_params(q, self.ctx.canonical_resource)
            if err:
                self._send_html(400, f"<h1>Invalid authorization request</h1><p>{err}</p>")
                return
            if not self._owner_logged_in():
                # Carry the already-validated request through the sign-in
                # round-trip (response_type / challenge method are fixed by
                # validate_authorize_params above).
                self._send_html(200, oauth.login_page({
                    **params,
                    "response_type": "code",
                    "code_challenge_method": "S256",
                }))
                return
            self._send_html(200, oauth.approval_page(params))
            return
        if path == "/mcp":
            if not self._check_auth():
                return
            self._handle_sse_stream()
            return
        self._send_json(
            404, self._rpc_error_envelope(INVALID_REQUEST, "Not found.")
        )

    def do_DELETE(self):
        if not self._check_origin():
            return
        if urllib.parse.urlparse(self.path).path != "/mcp":
            self._send_json(
                404, self._rpc_error_envelope(INVALID_REQUEST, "Not found.")
            )
            return
        if not self._check_auth():
            return
        sid = self.headers.get(SESSION_HEADER)
        if self.ctx.sessions.destroy(sid):
            self._send_json(200, {"terminated": True})
        else:
            self._send_json(
                404,
                self._rpc_error_envelope(INVALID_REQUEST, "Unknown session."),
            )

    def do_POST(self):
        if not self._check_origin():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/token":
            form = self._read_form_body()
            code, body = oauth.handle_token_request(
                form, self.ctx.auth, self.ctx.canonical_resource
            )
            self._send_json(code, body)
            return
        if path == "/authorize":
            form = self._read_form_body()
            if self.ctx.owner_password and "owner_password" in form:
                # Owner sign-in attempt from the login page.
                if not hmac.compare_digest(
                        form.get("owner_password", ""),
                        self.ctx.owner_password):
                    self._send_html(403, "<h1>Wrong password</h1>")
                    return
                params, err = oauth.validate_authorize_params(
                    form, self.ctx.canonical_resource
                )
                if err:
                    self._send_html(400, f"<h1>Invalid authorization request</h1><p>{err}</p>")
                    return
                cookie = oauth.owner_cookie_value(self.ctx.owner_password)
                secure = "; Secure" if self.ctx.public_url.startswith("https") else ""
                body = b"Signed in, redirecting..."
                self.send_response(302)
                self.send_header(
                    "Location",
                    "/authorize?" + urllib.parse.urlencode({
                        **params,
                        "response_type": "code",
                        "code_challenge_method": "S256",
                    }),
                )
                self.send_header(
                    "Set-Cookie",
                    f"{oauth.OWNER_COOKIE}={cookie}; Path=/authorize; "
                    f"HttpOnly; SameSite=Lax{secure}",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._owner_logged_in():
                self._send_html(403, "<h1>Owner sign-in required</h1>")
                return
            if form.get("approved") != "yes":
                self._send_html(400, "<h1>Not approved</h1>")
                return
            params, err = oauth.validate_authorize_params(
                form, self.ctx.canonical_resource
            )
            if err:
                self._send_html(400, f"<h1>Invalid authorization request</h1><p>{err}</p>")
                return
            issued = self.ctx.auth.issue_code(
                challenge=form["code_challenge"],
                redirect_uri=params["redirect_uri"],
                resource=params["resource"],
                scope=params["scope"],
                client_id=params["client_id"],
            )
            location = oauth.build_code_redirect(params, issued)
            body = b"Redirecting..."
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/mcp":
            self._send_json(
                404, self._rpc_error_envelope(INVALID_REQUEST, "Not found.")
            )
            return
        if not self._check_auth():
            return
        payload, err = self._read_json_body()
        if err:
            self._send_json(400, self._rpc_error_envelope(INVALID_REQUEST, err))
            return
        if isinstance(payload, list):
            # MCP 2025-11-25 removed JSON-RPC batching.
            self._send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Batch requests are not supported.",
                    },
                },
            )
            return
        if not isinstance(payload, dict):
            self._send_json(
                400, self._rpc_error_envelope(INVALID_REQUEST, "Malformed JSON-RPC message.")
            )
            return
        method = payload.get("method")

        extra_headers: dict[str, str] = {}

        if method == "initialize":
            # initialize is the one call that needs no session and no
            # version header; the server issues the session here.
            resp = handle_rpc(payload, self.ctx)
            sid = self.ctx.sessions.create()
            extra_headers[SESSION_HEADER] = sid
            self._send_json(200, resp, extra_headers)
            return

        # Every other call needs a live session.
        sid = self.headers.get(SESSION_HEADER)
        session = self.ctx.sessions.get(sid)
        if session is None:
            code = 400 if not sid else 404
            self._send_json(
                code,
                self._rpc_error_envelope(
                    INVALID_REQUEST,
                    "Missing Mcp-Session-Id header." if not sid else "Unknown or expired session.",
                ),
            )
            return

        # Protocol-version negotiation: an explicit unsupported version
        # is a hard 400. A missing header falls back to the session's
        # negotiated version (2025-11-25 from our initialize).
        client_version = self.headers.get(VERSION_HEADER)
        if client_version and client_version != PROTOCOL_VERSION:
            self._send_json(
                400,
                self._rpc_error_envelope(
                    INVALID_REQUEST,
                    f"Unsupported {VERSION_HEADER}: {client_version!r};"
                    f" server speaks {PROTOCOL_VERSION}.",
                ),
            )
            return

        if method == "notifications/initialized":
            self.ctx.sessions.mark_initialized(sid)

        try:
            r = handle_rpc(payload, self.ctx)
        except Exception:  # never leak a traceback to the client
            r = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": INTERNAL_ERROR, "message": "Internal error."},
            }

        if r is None:
            # Pure notification -> 202 Accepted, no body.
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, r, extra_headers)

    # -- SSE stream (optional GET) ----------------------------------------

    def _handle_sse_stream(self):
        sid = self.headers.get(SESSION_HEADER)
        if self.ctx.sessions.get(sid) is None:
            code = 400 if not sid else 404
            self._send_json(
                code,
                self._rpc_error_envelope(
                    INVALID_REQUEST,
                    "Missing Mcp-Session-Id header." if not sid else "Unknown or expired session.",
                ),
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # v1 sends no server-initiated messages; hold the stream open with
        # keepalive comments until the client disconnects.
        try:
            while True:
                time.sleep(15)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass


def create_server(host: str, port: int, db_path: str | None = None,
                  api_token: str | None = None, public_url: str | None = None,
                  owner_password: str | None = None):
    store = Store(db_path)
    sessions = SessionStore()
    token = api_token if api_token is not None else os.environ.get("FITLOG_API_TOKEN", "")
    base = (public_url or os.environ.get("FITLOG_PUBLIC_URL")
            or f"http://{host}:{port}").rstrip("/")
    password = (owner_password if owner_password is not None
                else os.environ.get("FITLOG_OWNER_PASSWORD", ""))
    ctx = ServerContext(store, sessions, AuthState(api_token=token), base,
                        owner_password=password)
    MCPHandler.ctx = ctx
    httpd = ThreadingHTTPServer((host, port), MCPHandler)
    return httpd, store


def public_requires_password() -> str | None:
    """Fail-fast rule: a publicly exposed server must gate /authorize.

    Returns an error message when FITLOG_PUBLIC_URL is set but no
    FITLOG_OWNER_PASSWORD is configured; None otherwise.
    """
    if os.environ.get("FITLOG_PUBLIC_URL") and not os.environ.get("FITLOG_OWNER_PASSWORD"):
        return ("FITLOG_PUBLIC_URL is set but FITLOG_OWNER_PASSWORD is not: "
                "refusing to expose the OAuth authorize page publicly without "
                "an owner password. Set FITLOG_OWNER_PASSWORD to a strong value.")
    return None


def main():
    host = os.environ.get("FITLOG_HOST", "127.0.0.1")
    port = int(os.environ.get("FITLOG_PORT", "8765"))
    err = public_requires_password()
    if err:
        print("error: " + err, file=sys.stderr)
        sys.exit(1)
    httpd, _ = create_server(host, port)
    print("fitlog-mcp listening on http://%s:%d/mcp (bearer auth required)"
          % (host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
