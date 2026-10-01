# fitlog-mcp

A self-hosted MCP server that turns Alexa+ into a gym coach. It gives your
assistant a memory for your training: log sets by voice, ask for personal
records, get today's plan and protein targets.

Built for the Alexa+ track of the Amazon **"Build, Ship, Shape" Developer
Hackathon 2026**: a working self-hosted MCP server implementing **MCP spec
2025-11-25** over **Streamable HTTP** — zero dependencies, MIT licensed.

## Run it (3 steps, no `pip install`)

Requires Python 3.9+.

```bash
git clone https://github.com/hahahahahahahahah6/fitlog-mcp.git
cd fitlog-mcp
python3 server.py
```

The server listens on `http://127.0.0.1:8765` (`/mcp` endpoint).
Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FITLOG_PORT` | `8765` | listen port |
| `FITLOG_HOST` | `127.0.0.1` | listen address |
| `FITLOG_DB` | `~/.fitlog/fitlog.db` | sqlite database path |
| `FITLOG_ALLOWED_ORIGINS` | _(empty)_ | extra trusted `Origin` values, comma-separated |

## Connect an MCP client

The endpoint is `http://127.0.0.1:8765/mcp` (Streamable HTTP). Example client
config (Claude Code / MCP Inspector style):

```json
{
  "mcpServers": {
    "fitlog": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Or verify with the MCP Inspector CLI:

```bash
npx -y @modelcontextprotocol/inspector --cli \
  --transport http --server-url http://127.0.0.1:8765/mcp \
  --method tools/list
```

## Tools

| Tool | What it does |
|---|---|
| `log_workout(exercise, sets[{reps, weight}])` | Record a session; announces new PRs |
| `get_history(exercise?, limit?)` | Past sessions, newest first |
| `get_personal_records()` | Heaviest set + estimated 1RM per lift |
| `plan_workout(day?)` | Today's plan from the 5-day split |
| `log_protein(grams)` | Log protein toward the 160–190 g target |
| `get_nutrition_targets()` | Targets vs. what's logged today |

Resources: `program://current` (the training split), `pr://all` (PR table).

## Spec compliance notes

- Streamable HTTP: `POST /mcp` for JSON-RPC, optional `GET /mcp` SSE stream,
  `DELETE /mcp` terminates the session (`Mcp-Session-Id`).
- `Origin` header is **MUST**-validated: a present-but-untrusted origin gets
  HTTP 403 (DNS-rebinding mitigation). Loopback origins are trusted by default.
- `MCP-Protocol-Version` is checked on every post-initialize request:
  unsupported versions get HTTP 400.
- Tool annotations (`readOnlyHint` / `destructiveHint` / `idempotentHint` /
  `openWorldHint`) are set per the 2025-11-25 spec.

## Tests

```bash
python3 -m unittest tests.test_server
```

## License

MIT — see [LICENSE](LICENSE).
