"""OAuth 2.1 authorization-code + PKCE (S256) for Alexa+ account linking.

Implements exactly what the Alexa+ MCP authentication checklist requires:
  * 401 Unauthorized (with NO WWW-Authenticate header) for unauthenticated
    MCP requests -- the transport enforces this.
  * RFC 9728 Protected Resource Metadata at
    /.well-known/oauth-protected-resource
  * Authorization server metadata at /.well-known/oauth-authorization-server
    with code_challenge_methods_supported including "S256".
  * /authorize + /token implementing the authorization-code flow with
    PKCE S256. The `resource` parameter must equal the server's canonical
    URI on both requests.

Single-user demo posture: the approval page performs no login -- whoever
loads it consents as the server owner. When FITLOG_OWNER_PASSWORD is set
(REQUIRED whenever FITLOG_PUBLIC_URL exposes the server publicly), the
/authorize page first requires the owner password; a successful sign-in
sets a short-lived HMAC-signed session cookie before the approval page
is shown.

Stdlib only. Codes and tokens persist in SQLite (same DB file as the
workout store when a db_path is given); a restart no longer invalidates
outstanding codes/tokens. Tokens carry their granted scope, and the
transport enforces fitlog.read / fitlog.write on every tool call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import sqlite3
import threading
import time
import urllib.parse

# Scopes advertised in the PRM document and accepted on authorize/token.
SCOPES = ("fitlog.read", "fitlog.write")

CODE_TTL = 600          # authorization codes live 10 minutes, single use
TOKEN_TTL = 30 * 86400  # bearer tokens live 30 days

OAUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_codes (
    code TEXT PRIMARY KEY,
    challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope TEXT NOT NULL,
    client_id TEXT NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
    token TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    expires REAL NOT NULL
);
"""


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class AuthState:
    """OAuth codes/tokens in SQLite plus an optional static owner token.

    db_path=None keeps everything in memory (used by unit tests); a real
    path persists codes and tokens across restarts.
    """

    def __init__(self, api_token: str | None = None,
                 db_path: str | None = None):
        self._lock = threading.Lock()
        self.api_token = api_token or ""
        # check_same_thread=False: the HTTP server is threaded, all access
        # is serialized with the lock.
        self._conn = sqlite3.connect(db_path or ":memory:",
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(OAUTH_SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # -- issuance ------------------------------------------------------

    def issue_code(self, *, challenge: str, redirect_uri: str, resource: str,
                   scope: str, client_id: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._prune_locked()
            self._conn.execute(
                "INSERT INTO oauth_codes "
                "(code, challenge, redirect_uri, resource, scope, client_id,"
                " expires) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code, challenge, redirect_uri, resource, scope, client_id,
                 time.time() + CODE_TTL),
            )
            self._conn.commit()
        return code

    def redeem_code(self, *, code: str, verifier: str, redirect_uri: str,
                    resource: str) -> tuple[str | None, str | None, str | None]:
        """Validate a code + PKCE verifier; return (token, scope, error)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT challenge, redirect_uri, resource, scope, expires"
                " FROM oauth_codes WHERE code = ?",
                (code,),
            ).fetchone()
            # Single use: delete the code whether or not validation passes.
            self._conn.execute("DELETE FROM oauth_codes WHERE code = ?",
                               (code,))
            self._conn.commit()
            if row is None:
                return None, None, "invalid_grant: unknown or reused code"
            if time.time() > row["expires"]:
                return None, None, "invalid_grant: code expired"
            if not hmac.compare_digest(row["redirect_uri"], redirect_uri):
                return None, None, "invalid_grant: redirect_uri mismatch"
            if not hmac.compare_digest(row["resource"], resource):
                return None, None, "invalid_target: resource mismatch"
            try:
                expect = pkce_challenge(verifier)
            except (UnicodeEncodeError, ValueError):
                return None, None, "invalid_request: bad code_verifier"
            if not hmac.compare_digest(expect, row["challenge"]):
                return None, None, "invalid_grant: PKCE verification failed"
            token = secrets.token_urlsafe(32)
            self._conn.execute(
                "INSERT INTO oauth_tokens (token, scope, expires)"
                " VALUES (?, ?, ?)",
                (token, row["scope"], time.time() + TOKEN_TTL),
            )
            self._conn.commit()
            return token, row["scope"], None

    # -- validation ----------------------------------------------------

    def token_scopes(self, token: str) -> set[str] | None:
        """Return the granted scope set for a token, or None if invalid."""
        if not token:
            return None
        if self.api_token and hmac.compare_digest(token, self.api_token):
            return set(SCOPES)  # the owner API token has full access
        with self._lock:
            row = self._conn.execute(
                "SELECT scope, expires FROM oauth_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return None
            if time.time() > row["expires"]:
                self._conn.execute("DELETE FROM oauth_tokens WHERE token = ?",
                                   (token,))
                self._conn.commit()
                return None
            return set(row["scope"].split())

    def validate_token(self, token: str) -> bool:
        return self.token_scopes(token) is not None

    def _prune_locked(self) -> None:
        now = time.time()
        self._conn.execute("DELETE FROM oauth_codes WHERE expires <= ?", (now,))
        self._conn.execute("DELETE FROM oauth_tokens WHERE expires <= ?", (now,))
        self._conn.commit()


# -- metadata documents ---------------------------------------------------

def protected_resource_metadata(public_url: str, canonical: str) -> dict:
    return {
        "resource": canonical,
        "authorization_servers": [public_url],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(public_url: str) -> dict:
    return {
        "issuer": public_url,
        "authorization_endpoint": public_url + "/authorize",
        "token_endpoint": public_url + "/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPES),
    }


# -- /authorize ------------------------------------------------------------

def _redirect_ok(uri: str) -> bool:
    try:
        p = urllib.parse.urlparse(uri)
    except ValueError:
        return False
    if p.scheme == "https" and p.hostname:
        return True
    # OAuth 2.1 allows http loopback redirect URIs for native apps.
    return p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")


def validate_authorize_params(q: dict[str, str], canonical: str
                              ) -> tuple[dict | None, str | None]:
    """Return (clean params, error). `q` maps names to first values."""
    if q.get("response_type") != "code":
        return None, "response_type must be 'code'"
    challenge = q.get("code_challenge", "")
    if not challenge:
        return None, "code_challenge is required"
    if q.get("code_challenge_method", "plain") != "S256":
        return None, "code_challenge_method must be 'S256'"
    redirect_uri = q.get("redirect_uri", "")
    if not redirect_uri or not _redirect_ok(redirect_uri):
        return None, "redirect_uri must be an https URL (or http loopback)"
    resource = q.get("resource") or canonical
    if resource != canonical:
        return None, f"resource must be {canonical}"
    scope = q.get("scope", " ".join(SCOPES))
    unknown = [s for s in scope.split() if s not in SCOPES]
    if unknown:
        return None, f"unknown scope(s): {' '.join(unknown)}"
    return {
        "client_id": q.get("client_id", ""),
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": q.get("state", ""),
        "code_challenge": challenge,
        "resource": resource,
    }, None


# -- owner sign-in gate --------------------------------------------------------

OWNER_COOKIE = "fitlog_owner"
OWNER_SESSION_TTL = 2 * 3600  # an owner sign-in lasts 2 hours


def _owner_key(password: str) -> bytes:
    return hashlib.sha256(
        ("fitlog-owner-session:" + password).encode("utf-8")).digest()


def owner_cookie_value(password: str, now: float | None = None) -> str:
    """Build a signed owner-session cookie value: '<unix-ts>.<hex-sig>'."""
    ts = str(int(now if now is not None else time.time()))
    sig = hmac.new(
        _owner_key(password), ("owner:" + ts).encode("utf-8"),
        hashlib.sha256).hexdigest()
    return ts + "." + sig


def owner_cookie_valid(cookie_value: str | None, password: str,
                       now: float | None = None) -> bool:
    """Check a cookie value's signature and 2-hour expiry."""
    if not cookie_value or not password:
        return False
    try:
        ts, sig = cookie_value.split(".", 1)
        ts_f = float(ts)
    except ValueError:
        return False
    expect = owner_cookie_value(password, ts_f).split(".", 1)[1]
    if not hmac.compare_digest(sig, expect):
        return False
    now_f = now if now is not None else time.time()
    return 0 <= now_f - ts_f <= OWNER_SESSION_TTL


def login_page(params: dict) -> str:
    """Password form shown before the approval page when the owner gate is on.

    Carries the OAuth request params as hidden fields so the redirect back
    to /authorize after sign-in preserves them.
    """
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
        for k, v in params.items()
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>FitLog — owner sign-in</title></head>
<body style="font-family:sans-serif;max-width:36em;margin:4em auto">
<h1>FitLog owner sign-in</h1>
<p>This is a single-user demo server. Enter the owner password to continue
linking Alexa+ to <strong>your</strong> training log.</p>
<form method="post" action="/authorize">
{hidden}
<label>Owner password:
<input type="password" name="owner_password" autofocus
 style="font-size:1.1em;padding:.3em"></label>
<button type="submit" style="font-size:1.1em;padding:.4em 1.5em">Sign in</button>
</form></body></html>"""


def approval_page(params: dict) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
        for k, v in {
            **params,
            "response_type": "code",
            "code_challenge_method": "S256",
        }.items()
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>FitLog — authorize</title></head>
<body style="font-family:sans-serif;max-width:36em;margin:4em auto">
<h1>Connect Alexa+ to FitLog?</h1>
<p>Alexa+ is asking for access to your training log with scopes:
<strong>{html.escape(params["scope"])}</strong>.</p>
<p><small>Demo server: approving links whoever you sign in with on Alexa+ as
the owner of this FitLog database.</small></p>
<form method="post" action="/authorize">
{hidden}
<button type="submit" name="approved" value="yes"
 style="font-size:1.2em;padding:.5em 2em">Approve</button>
</form></body></html>"""


def build_code_redirect(params: dict, code: str) -> str:
    q = {"code": code}
    if params["state"]:
        q["state"] = params["state"]
    sep = "&" if "?" in params["redirect_uri"] else "?"
    return params["redirect_uri"] + sep + urllib.parse.urlencode(q)


# -- /token -----------------------------------------------------------------

def handle_token_request(form: dict[str, str], auth: AuthState,
                         canonical: str) -> tuple[int, dict]:
    """Return (http_status, json_body) for POST /token."""
    if form.get("grant_type") != "authorization_code":
        return 400, {"error": "unsupported_grant_type",
                     "error_description": "only authorization_code is supported"}
    code = form.get("code", "")
    verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")
    resource = form.get("resource", "")
    if not all([code, verifier, redirect_uri, resource]):
        return 400, {"error": "invalid_request",
                     "error_description": "code, code_verifier, redirect_uri and "
                                          "resource are all required"}
    token, scope, err = auth.redeem_code(code=code, verifier=verifier,
                                          redirect_uri=redirect_uri, resource=resource)
    if err:
        kind, _, desc = err.partition(": ")
        return 400, {"error": kind, "error_description": desc}
    return 200, {"access_token": token, "token_type": "Bearer",
                 "expires_in": TOKEN_TTL, "scope": scope}
