# fitlog-mcp — Demo Video Script (< 3 min, English)

Target: public on YouTube/Vimeo, English narration, screen recording.
Total budget: ~2:30.

## Shot list & narration

**0:00–0:15 — Hook (terminal)**
Show: empty terminal.
Say: "Meet FitLog MCP — a self-hosted MCP server that turns Alexa+ into a
gym coach. Zero dependencies, just Python's standard library."

**0:15–0:40 — Start the server (terminal)**
Show: `python3 server.py` → "fitlog-mcp listening on http://127.0.0.1:8765/mcp".
Say: "One command and it's live. It speaks MCP spec 2025-11-25 over Streamable
HTTP — the same open standard that powers Alexa+ integrations."

**0:40–1:20 — Log a workout (MCP Inspector, split screen with terminal)**
Show: Inspector `tools/call log_workout` with exercise "Bench Press",
sets 8x80, 6x85. Response: "New PR for Bench Press: 85 kg!"
Say: "Log sets by voice through Alexa+, and the server remembers. It even
detects personal records automatically — no spreadsheet, no app to open."

**1:20–1:50 — Ask for records + today's plan (Inspector)**
Show: `get_personal_records` → PR table; `plan_workout` → today's split.
Say: "Ask for your records any time, or get today's plan from your stored
five-day split. It knows it's leg day without being told."

**1:50–2:10 — Nutrition (Inspector)**
Show: `log_protein` 45 g → `get_nutrition_targets` → "45 g logged, 115 g
short of the 160–190 target".
Say: "Protein gets the same treatment — log it through the day and always
know where you stand against your target."

**2:10–2:30 — Under the hood (code, brief)**
Show: quick scroll of `mcp/transport.py` (Origin validation, session
lifecycle) and the test run `python3 -m unittest` → OK.
Say: "Under the hood: hand-rolled Streamable HTTP with mandatory Origin
validation and session lifecycle, fully tested. Self-host it on anything
that runs Python. That's FitLog MCP — your training, remembered."

## Recording notes
- 1080p screen capture; zoom terminal to ≥18pt font.
- Use a fresh DB (`FITLOG_DB=/tmp/demo.db`) so PRs trigger on camera.
- Keep each Inspector call snappy; pre-type the JSON arguments.
