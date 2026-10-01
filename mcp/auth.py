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
loads it consents as the server owner. This is documented in the README;
for a multi-user service this page would authenticate the user first.

Stdlib only. Tokens and codes live in memory; restart invalidates them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import threading
import time
import urllib.parse

# Scopes advertised in the PRM document and accepted on authorize/token.
SCOPES = ("fitlog.read", "fitlog.write")

CODE_TTL = 600          # authorization codes live 10 minutes, single use
TOKEN_TTL = 30 * 86400  # bearer tokens live 30 days


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class AuthState:
    """In-memory OAuth codes/tokens plus an optional static owner token."""

    def __init__(self, api_token: str | None = None):
        self._lock = threading.Lock()
        self._codes: dict[str, dict] = {}
        self._tokens: dict[str, float] = {}
        self.api_token = api_token or ""

    # -- issuance ------------------------------------------------------

    def issue_code(self, *, challenge: str, redirect_uri: str, resource: str,
                   scope: str, client_id: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._prune_locked()
            self._codes[code] = {
                "challenge": challenge,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "scope": scope,
                "client_id": client_id,
                "expires": time.time() + CODE_TTL,
            }
        return code

    def redeem_code(self, *, code: str, verifier: str, redirect_uri: str,
                    resource: str) -> tuple[str | None, str | None]:
        """Validate a code + PKCE verifier; return (token, error)."""
        with self._lock:
            rec = self._codes.pop(code, None)
            if rec is None:
                return None, "invalid_grant: unknown or reused code"
            if time.time() > rec["expires"]:
                return None, "invalid_grant: code expired"
            if not hmac.compare_digest(rec["redirect_uri"], redirect_uri):
                return None, "invalid_grant: redirect_uri mismatch"
            if not hmac.compare_digest(rec["resource"], resource):
                return None, "invalid_target: resource mismatch"
            try:
                expect = pkce_challenge(verifier)
            except (UnicodeEncodeError, ValueError):
                return None, "invalid_request: bad code_verifier"
            if not hmac.compare_digest(expect, rec["challenge"]):
                return None, "invalid_grant: PKCE verification failed"
            token = secrets.token_urlsafe(32)
            self._tokens[token] = time.time() + TOKEN_TTL
            return token, None

    # -- validation ----------------------------------------------------

    def validate_token(self, token: str) -> bool:
        if not token:
            return False
        if self.api_token and hmac.compare_digest(token, self.api_token):
            return True
        with self._lock:
            exp = self._tokens.get(token)
            if exp is None:
                return False
            if time.time() > exp:
                del self._tokens[token]
                return False
            return True

    def _prune_locked(self) -> None:
        now = time.time()
        for code in [c for c, r in self._codes.items() if now > r["expires"]]:
            del self._codes[code]
        for tok in [t for t, e in self._tokens.items() if now > e]:
            del self._tokens[tok]


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


def approval_page(params: dict) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
        for k, v in params.items()
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
    token, err = auth.redeem_code(code=code, verifier=verifier,
                                  redirect_uri=redirect_uri, resource=resource)
    if err:
        kind, _, desc = err.partition(": ")
        return 400, {"error": kind, "error_description": desc}
    return 200, {"access_token": token, "token_type": "Bearer",
                 "expires_in": TOKEN_TTL, "scope": " ".join(SCOPES)}
