# FitLog Alexa+ web simulator

A web page that simulates the Alexa+ experience against the **real**
self-hosted FitLog MCP server — the route the hackathon rules explicitly
allow ("simulate an Alexa+ experience using your preferred agentic tools
via a web app").

What it does:

- Echo-style UI: glowing ring, conversation transcript, typed "voice" input.
- **Account linking** runs the server's genuine OAuth 2.1 + PKCE flow:
  browser → `/authorize` → owner-password sign-in → Approve →
  authorization code → `/token` → bearer token.
- Every chat turn is routed by a small keyword/regex NLU to one of the 6
  FitLog tools and executed as a real MCP `tools/call` (Streamable HTTP,
  bearer token, session id). The inspector panel shows the live JSON-RPC
  request/response — proof of a working MCP integration on camera.

## Run it

Requires Python 3.9+, no `pip install`. Two terminals.

**Terminal 1 — the MCP server** (fresh DB so PRs trigger on camera):

```bash
cd ~/workspace/fitlog-mcp   # or wherever you cloned the repo
FITLOG_OWNER_PASSWORD='pick-a-strong-password' \
FITLOG_DB=/tmp/fitlog-demo.db \
python3 server.py
# -> fitlog-mcp listening on http://127.0.0.1:8765/mcp
```

**Terminal 2 — the simulator:**

```bash
cd ~/workspace/fitlog-mcp/simulator
./start.sh
# -> FitLog Alexa+ simulator on http://127.0.0.1:8799/
```

Then open http://127.0.0.1:8799/ in a browser.

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FITLOG_MCP_URL` | `http://127.0.0.1:8765` | FitLog MCP server base URL (no trailing `/mcp`) |
| `SIMULATOR_HOST` | `127.0.0.1` | interface the simulator listens on |
| `SIMULATOR_PORT` | `8799` | simulator port |

The owner password is **never hardcoded** — the MCP server reads
`FITLOG_OWNER_PASSWORD` from its own environment, and the simulator just
drives the server's real `/authorize` page.

## Demo click path (for the video)

1. Browser: http://127.0.0.1:8799/
2. Click **Link FitLog account** → the real `/authorize` page appears →
   enter the owner password → **Approve** → redirected back, pill flips to
   **Linked ✓**.
3. Click a suggestion chip or type:
   - “Log my bench press: 3 sets of 5 at 185 pounds” → sets logged,
     pounds auto-converted, “New PR” callout.
   - “What's my bench press personal record?” → heaviest set + est. 1RM.
   - “What's today's workout plan?” → today's split.
   - “Log 45 grams of protein” → total vs 160–190 g target.
   - “Show my workout history” / “What are my nutrition targets?”
4. Expand the 🔍 inspector to show the raw `tools/call` JSON-RPC on screen.

## Files

- `server.py` — stdlib-only backend: static UI, OAuth callback + token
  exchange, NLU router, MCP client (initialize → tools/call).
- `index.html` — the Echo-style UI (vanilla HTML/CSS/JS, no build step).
- `start.sh` — one-command launcher.
