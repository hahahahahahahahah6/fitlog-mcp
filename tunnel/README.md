# Exposing the server publicly (Cloudflare Tunnel)

The FitLog MCP server binds to loopback only. To make it reachable by Alexa+,
expose it through a Cloudflare Tunnel. Do not use any other reverse proxy:
the login rate limiter honors only Cloudflare's CF-Connecting-IP header, so
a different proxy in front would break per-client rate limiting.

## Quick start

1. Create a tunnel: `cloudflared tunnel create <name>`
2. Route a hostname to it, e.g. `mcp.example.com` → `http://127.0.0.1:8765`
   (the port your server listens on).
3. Start the server with `FITLOG_PUBLIC_URL=https://mcp.example.com` so the
   OAuth metadata and redirect URIs use the public origin.
4. Run the tunnel:
   - named tunnel: `cloudflared tunnel run <name>`
   - throwaway quick tunnel: `cloudflared tunnel --url http://127.0.0.1:8765`

The MCP endpoint is then `https://mcp.example.com/mcp`.

## Restricted networks: DNS stub

`tunnel/dnsstub.py` is a minimal DNS stub for environments where UDP DNS is
blocked or IPv6 resolution is broken. It answers the Cloudflare tunnel
control-plane SRV record directly and forwards everything else to an upstream
resolver over TCP. You only need it if `cloudflared` cannot resolve DNS on
its own:

```bash
nohup python3 tunnel/dnsstub.py > /tmp/dnsstub.log 2>&1 &
# then run cloudflared with 127.0.0.1 configured as its resolver
```
