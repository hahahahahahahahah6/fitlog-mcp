# Friction Log — FitLog MCP (Alexa+ track)
### Build, Ship, Shape: Amazon Developer Hackathon — due 2026-10-23 12:00 PM PDT
### Paste this into Devpost's Friction Log field at submission time (counts toward the up-to-10% judging bonus).

**What we built.** FitLog MCP — a self-hosted MCP server (MCP spec 2025-11-25, Streamable HTTP, Python stdlib only) that gives Alexa+ a memory for training: voice-log sets, PRs with estimated 1RM, a 5-day split plan, history, and protein tracking against a 160–190 g daily target. 51/51 tests pass, including the full OAuth PKCE round-trip. The official Alexa+ deploy path was blocked for a brand-new developer account, so the entry ships as a self-hosted deployment with an Echo-style web simulator that runs the *real* OAuth dance and the *real* tools.

## Friction 1 — New AWS developer account can't complete the official Alexa+ add-on path
**What I tried:** Deploy the add-on with the Alexa AI CLI per the MCP toolkit quickstart.
**What didn't work:** The flow requires an AWS assume-role that a brand-new developer account cannot complete without Solutions Architect onboarding — the CLI deploy was a dead end with no self-serve workaround documented.
**Workaround:** Pivoted to a self-hosted deployment behind a stable Cloudflare tunnel (https://mcp.haocoach.com/mcp) and built an Echo-style web simulator (https://app.haocoach.com/) that runs the genuine OAuth 2.1 + PKCE (S256) account-linking flow against the real tools. Judges can try everything with no Echo and no AWS account.
**Ask for the Alexa team:** document a dev-loop path for accounts without enterprise onboarding (e.g. a sandbox role or a dev-stage deploy that doesn't require assume-role).

## Friction 2 — The Alexa+ 401 rule is the opposite of everything I knew about OAuth
**What I tried:** Followed the standard OAuth 2.0 Bearer pattern (RFC 6750) and the default MCP server examples: return 401 *with* a `WWW-Authenticate` header.
**What didn't work:** The Alexa+ auth checklist requires 401s with *no* `WWW-Authenticate` header. Every OAuth example I knew sent the header; I only caught it because the checklist states it explicitly.
**Workaround:** Hard-coded the 401 path to omit the header and added a dedicated test asserting its absence.
**Ask:** a callout box in the auth doc — this rule is the exact inverse of the industry default, and it deserves more than a checklist line.

## Friction 3 — MCP Inspector can't test the account-linking flow
**What I tried:** Verified end-to-end with the MCP Inspector CLI (`--cli --transport http --server-url …/mcp`) — it connected first try.
**What didn't work:** The Inspector doesn't do the OAuth PKCE dance, so the authorization-code + PKCE (S256) flow — the hardest auth surface of the project — had to be exercised with raw curl against `/authorize` + `/token` and a hand-rolled PKCE exchange.
**Workaround:** The Echo-style web simulator runs the real flow (link account → typed utterance → raw `tools/call` JSON-RPC visible in the inspector panel), which became the primary auth testbed.
**Ask:** an `--oauth` mode in the MCP Inspector so builders can validate PKCE end-to-end without hand-rolling clients.

## Friction 4 — Dev-stage deploys bake a rotating tunnel URL into `addon.json`
**What I tried:** Pointed the add-on package at a quick HTTPS tunnel for iteration.
**What didn't work:** Quick tunnels rotate URLs on every restart, and the deploy expects the URL baked into `addon.json` — every restart meant edit + redeploy before anything could be re-tested.
**Workaround:** Moved to a fixed Cloudflare tunnel with stable DNS (app./mcp.haocoach.com, DNS-only) plus a 2-minute watchdog that self-heals the tunnel; the URL in `addon.json` has survived every restart since.
**Ask:** a documented dev loop — either stable dev tunnels or a URL override flag at deploy time.

## Friction 5 — Python `http.server` logging recursion killed every request
**What I tried:** Overrode `BaseHTTPRequestHandler.log_message` to route logs the way I wanted.
**What didn't work:** `log_message` → `log_error` → `log_message` recursed infinitely, killing every request until the process died.
**Workaround:** Wrote directly to stderr in the override; noted in a code comment. (Not an Amazon tool, but it was the single most confusing hour of the build.)

## Friction 6 — MCP spec 2025-11-25 version-negotiation fallback is easy to misread
**What worked:** The single-endpoint Streamable HTTP design is much simpler to self-host than the old SSE+POST split; session-id-via-header is clean.
**What didn't work:** The spec's "SHOULD assume 2025-03-26" fallback for a missing protocol-version header is easy to misread when your server only supports one version — I had to re-read it three times to be sure rejecting was correct.
**Ask:** clearer guidance for single-version servers on the 400-vs-fallback decision.

## Product feedback (tools used, would use again)
- **Alexa+ MCP toolkit docs (quickstart + auth checklist):** the single most useful page — the auth checklist is exact (401-without-WWW-Authenticate, RFC 9728 PRM, S256 PKCE all spelled out), so "support auth" was unambiguous. Nit: media-asset requirements assume you already host images somewhere; a note on acceptable hosts for dev-stage validation would help. Would use again: yes.
- **MCP spec 2025-11-25 (Streamable HTTP):** used for the entire transport; single-endpoint self-hosting is a genuine improvement over SSE+POST. Would use again: yes.
- **Python stdlib (`http.server`, `sqlite3`, `unittest`):** the whole stack — everything needed for a spec-compliant server is already in the box, no supply-chain risk. Would use again: yes.
