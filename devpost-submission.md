# Devpost submission — FitLog MCP
### Build, Ship, Shape: Amazon Developer Hackathon (Alexa+ track)
### Deadline: 2026-10-23 12:00 PM PDT — DO NOT SUBMIT YET (needs browser login as haoli5933)

## Title
FitLog MCP — Give Alexa+ a Memory for Your Training

## Tagline
A self-hosted MCP server that turns Alexa+ into your fitness coach: log sets, track PRs, and hit protein goals by voice.

## One-liner
FitLog MCP is a zero-dependency Python MCP server (MCP 2025-11-25, Streamable HTTP) that lets Alexa+ log workouts, look up personal records, plan training days, and track protein — with real OAuth 2.1 + PKCE account linking.

## Detailed description (for the Devpost "About" / demo story)

**Inspiration.** I lift five days a week and track 160–190 g of protein a day. Logging sets mid-workout on a phone is friction I never wanted — so I gave Alexa+ a memory for my training instead.

**What it does.** FitLog MCP is a self-hosted Model Context Protocol server that plugs into Alexa+ as a custom add-on. Six tools cover the whole loop:

- `log_workout` — "log my bench press: 3 sets of 5 at 185 pounds" (lb/kg auto-converted, PR callouts)
- `get_personal_records` — heaviest set per lift plus estimated 1RM
- `plan_workout` — today's session from a built-in 5-day split
- `get_history` — recent sessions per exercise
- `log_protein` — "log 45 grams of protein" against the daily target
- `get_nutrition_targets` — daily protein/calorie goals

Two resources (`program://current`, `pr://all`) expose the training program and all-time PRs.

**How it's built.** One Python process, stdlib only (`http.server`, `sqlite3`, `json`) — no `pip install`, no supply-chain risk. A single `/mcp` endpoint speaks Streamable HTTP per MCP spec 2025-11-25, with `Mcp-Session-Id` lifecycle, `Origin` header validation, and JSON-RPC batch rejection. Auth follows the Alexa+ checklist exactly: OAuth 2.1 authorization-code + PKCE (S256), RFC 9728 Protected Resource Metadata at `/.well-known/`, 401s with no `WWW-Authenticate` header. Account linking is the genuine flow, not a mock. 42/42 unit tests pass, including the full PKCE round-trip.

**Try it live.** The demo is deployed at a fixed public URL:

- 🎙️ **Live demo (Echo-style web simulator):** https://app.haocoach.com/
- 🔌 **MCP endpoint:** https://mcp.haocoach.com/mcp

The simulator drives the real server: click "Link FitLog account" to run the actual OAuth dance, then type a phrase like "log my bench press: 3 sets of 5 at 185 pounds" and expand the inspector to watch the raw `tools/call` JSON-RPC. A 54-second demo video is in `demo/fitlog-demo-54s.mp4`.

**What's next.** Alexa+ add-on packaging (`addon-package/addon.json`) is ready for the dev stage; the public tunnel URL above is the stable endpoint it will point at.

## Links
- GitHub: https://github.com/hahahahahahahahah6/fitlog-mcp (MIT)
- Live demo: https://app.haocoach.com/
- MCP server: https://mcp.haocoach.com/mcp

## Submission checklist (for the person clicking Submit in the browser)
- [ ] Log in to Devpost as `haoli5933` (registered for this hackathon on 2026-09-30 — confirmed)
- [ ] Create submission under Build, Ship, Shape → title/tagline/description above
- [ ] Demo URL: https://app.haocoach.com/
- [ ] Code repo: https://github.com/hahahahahahahahah6/fitlog-mcp
- [ ] Upload demo video: `demo/fitlog-demo-54s.mp4` (re-record against the live URL if time permits)
- [ ] Fill hackathon's required questions (built with: Alexa+ MCP toolkit; track: Alexa+)
- [ ] Submit before 2026-10-23 12:00 PM PDT
