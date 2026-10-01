# Devpost submission draft — fitlog-mcp (Alexa+ track)

> DRAFT. Do not submit yet. Video still needs recording; package due
> Oct 23, 2026 12:00 PM PT.

## Text description

FitLog MCP is a self-hosted MCP server (MCP spec 2025-11-25, Streamable HTTP,
zero dependencies, MIT) that gives Alexa+ a memory for your training. Log
sets by voice, ask for personal records, get today's plan from your stored
5-day split, and track protein against a 160–190 g daily target. It shows
what an Alexa+ integration looks like when the assistant actually remembers
your gym life.

How it works: a single Python process (stdlib only: `http.server`, `json`,
`sqlite3`) serves one `/mcp` endpoint. `POST` carries JSON-RPC, an optional
`GET` holds an SSE stream, and `DELETE` terminates the session. The server
issues a `Mcp-Session-Id` at `initialize`, MUST-validates the `Origin`
header on every request (403 on untrusted origins — DNS-rebinding
mitigation), and rejects unsupported `MCP-Protocol-Version` values with
400. Workouts, PRs (with Epley 1RM estimates), and protein logs live in
sqlite; six tools and two resources (`program://current`, `pr://all`)
expose them to any MCP client — including Alexa+.

Run instructions: `python3 server.py` (Python 3.9+, no `pip install`);
point any Streamable-HTTP MCP client at `http://127.0.0.1:8765/mcp`.

## Product feedback

- **MCP spec 2025-11-25 (Streamable HTTP):** used for the entire transport.
  What worked: single-endpoint design is much simpler to self-host than the
  old SSE+POST split; session-id-via-header is clean. What needs work: the
  spec's "SHOULD assume 2025-03-26" fallback for a missing version header is
  easy to misread — clearer guidance on single-version servers would help.
  Onboarding: readable in one sitting. Would use again: yes.
- **MCP Inspector (CLI mode):** used for end-to-end verification. What
  worked: `--cli --transport http --server-url …/mcp` connected first try,
  `tools/list` and `tools/call` round-tripped with zero config. Would use
  again: yes — it caught nothing here, which is the point.
- **Python stdlib (`http.server`, `sqlite3`, `unittest`):** the whole stack.
  What worked: everything needed for a spec-compliant server is already in
  the box; no supply-chain risk for judges. What needs work: `http.server`
  logging hooks recurse if overridden naively (hit once, fixed).

## Friction log

1. `BaseHTTPRequestHandler.log_message` → `log_error` → `log_message`
   recursion when overriding logging: infinite recursion killed every
   request until the override wrote to stderr directly. (Fixed; noted in
   README-adjacent code comment.)
2. Shell quoting when capturing the `Mcp-Session-Id` response header in a
   `curl -D` walkthrough: wrote a small bash helper script instead.
3. No live Alexa+ device to test against (Preview program) — verified with
   the official MCP Inspector CLI over Streamable HTTP instead, which the
   rules accept as "showing the MCP server in action."
