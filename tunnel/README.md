# Cloudflare Tunnel setup for haocoach.com

## Status (2026-10-01)
- Domain haocoach.com purchased ($10.46/yr, expires 2027-09-30).
- Tunnel `fitlog` created (ID 082c55ab-932b-4614-b27d-2ef9d2b0e285).
- DNS: mcp.haocoach.com and app.haocoach.com CNAME to the tunnel.
- BLOCKER: This sandbox blocks outbound TCP except via the egress proxy
  (which only allows CONNECT to port 443). cloudflared needs port 7844.
  The proxy says: "ask the user to open Muse settings -> Permissions" to allow TCP.

## To start the tunnel (once TCP is allowed)
```bash
# 1. Start the DNS stub (works around broken IPv6 DNS in sandbox)
nohup python3 ~/workspace/fitlog-mcp/tunnel/dnsstub.py > /tmp/dnsstub.log 2>&1 &
# 2. Run cloudflared with the stub as resolver (mount namespace)
unshare -m bash -c '
  mount --bind ~/workspace/fitlog-mcp/tunnel/stub-resolv.conf /etc/resolv.conf &&
  exec cloudflared tunnel --config ~/.cloudflared/config.yml run \
    --token-file ~/.config/cloudflare/tunnel-token
'
```

## DNS stub notes
The sandbox blocks:
- UDP sendto()/sendmsg() (EPERM) — use connect()+send() instead.
- Re-connect() of a UDP socket to a different peer — use a fresh socket per reply.
- Replies must come from 127.0.0.1:53 (clients use connected sockets).
The stub answers `_v2-origintunneld._tcp.argotunnel.com` SRV directly and
forwards everything else via TCP to 198.19.0.1:53.
