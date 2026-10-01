# Devpost submission draft — fitlog-mcp (Alexa+ track)

> DRAFT. Do not submit yet. The add-on has not been deployed yet (public
> tunnel URL not live, so no external round-trip has been measured); demo
> video still needs recording; package due Oct 23, 2026 12:00 PM PT.

## Text description

FitLog MCP is a self-hosted MCP server (MCP spec 2025-11-25, Streamable HTTP,
zero dependencies, MIT) that gives Alexa+ a memory for your training. Log
sets by voice ("log my bench press: 5 reps at 185 pounds" — lb supported),
ask for personal records with estimated 1RMs, get today's plan from the
built-in 5-day split, and track protein against a 160–190 g daily target.

How it works: a single Python process (stdlib only: `http.server`, `json`,
`sqlite3`) serves one `/mcp` endpoint, exposed to Alexa+ through an HTTPS
tunnel and registered as an Alexa+ add-on (`addon-package/addon.json`,
to be deployed with the Alexa AI CLI to the development stage once the
public tunnel URL is live). Every `/mcp`
request needs a bearer token: account linking runs OAuth 2.1
authorization-code + PKCE (S256), with RFC 9728 Protected Resource Metadata
and authorization-server metadata served from `/.well-known/`.
Unauthenticated requests get 401 with no `WWW-Authenticate` header, exactly
as the Alexa+ checklist requires. The server issues a `Mcp-Session-Id` at
`initialize`, MUST-validates the `Origin` header on every request (403 on
untrusted origins), rejects unsupported `MCP-Protocol-Version` values with
400, and rejects JSON-RPC batches (removed in 2025-11-25) with -32600.
Workouts, PRs, and protein logs live in sqlite; six tools and two resources
(`program://current`, `pr://all`) expose them. Local request handling is
lightweight (stdlib `http.server` + sqlite); end-to-end latency over the
public tunnel has not been measured yet.

Run instructions: `python3 server.py` (Python 3.9+, no `pip install`);
set `FITLOG_API_TOKEN` for local access, `FITLOG_PUBLIC_URL` to your public
URL for account linking. Tests: `python3 -m unittest` (31 tests, incl. the
full OAuth PKCE flow).

## Product feedback

- **Alexa+ MCP toolkit docs (quickstart + auth checklist):** the single
  most useful page. What worked: the authentication checklist is exact —
  401-without-WWW-Authenticate, RFC 9728 PRM, S256 PKCE all spelled out, so
  there was no guessing what "support auth" means. What needs work: media
  asset requirements assume you already host images somewhere; a note on
  acceptable hosts for dev-stage validation would help. Would use again: yes.
- **MCP spec 2025-11-25 (Streamable HTTP):** used for the entire transport.
  What worked: single-endpoint design is much simpler to self-host than the
  old SSE+POST split; session-id-via-header is clean. What needs work: the
  spec's "SHOULD assume 2025-03-26" fallback for a missing version header is
  easy to misread — clearer guidance on single-version servers would help.
  Would use again: yes.
- **MCP Inspector (CLI mode):** used for early end-to-end verification
  before the Alexa+ add-on existed. What worked: `--cli --transport http
  --server-url …/mcp` connected first try. Note: it doesn't exercise the
  OAuth flow, so the auth checklist had to be verified with curl + a
  hand-rolled PKCE exchange instead. Would use again: yes.
- **Python stdlib (`http.server`, `sqlite3`, `unittest`):** the whole stack.
  What worked: everything needed for a spec-compliant server is already in
  the box; no supply-chain risk. What needs work: `http.server` logging
  hooks recurse if overridden naively (hit once, fixed).

## Friction log

1. `BaseHTTPRequestHandler.log_message` → `log_error` → `log_message`
   recursion when overriding logging: infinite recursion killed every
   request until the override wrote to stderr directly. (Fixed; noted in a
   code comment.)
2. The Alexa+ 401 rule (no `WWW-Authenticate` header) is the opposite of
   both RFC 6750 and the default MCP behavior — every OAuth example I knew
   sent the header. Caught only because the checklist states it explicitly.
   Worth a callout box in the auth doc.
3. The MCP Inspector CLI can't do the OAuth PKCE dance, so the account-
   linking flow had to be tested with raw curl against `/authorize` +
   `/token`. An `--oauth` mode in the Inspector would close that gap.
4. Dev-stage deploys need the tunnel URL baked into `addon.json`, and quick
   tunnels rotate URLs — every restart means edit + redeploy. A documented
   "dev loop" (stable tunnel or URL override at deploy time) would help.
