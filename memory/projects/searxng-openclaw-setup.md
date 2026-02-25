# SearXNG ↔ OpenClaw Setup (Workstation + VPS)

Last updated: 2026-02-25 (UTC)
Owner: Islam

## Goal
Replace Brave API dependency for search with a self-hosted SearXNG endpoint routed through workstation Node proxy, consumed from VPS/OpenClaw workflows.

## Topology
- Workstation runs SearXNG in Docker.
- SearXNG container binding:
  - `127.0.0.1:8083 -> 8080` (local-only on workstation)
- Public app/proxy entrypoint remains on port `3443` (do not blanket-block).
- Node proxy exposes protected route:
  - `https://ieissa.com:3443/searxng/search`

## Security Model
- Route-level protection in `PCHost/server.js`:
  1) IP allowlist (`51.79.53.179`, loopback)
  2) `X-API-Key` check against `SEARXNG_API_KEY`
- App/OpenWebUI remains publicly reachable on shared port.
- Sensitive search route is locked; avoid whole-port firewall blocks that break user app access.

## Workstation Configuration
### Node proxy file
- Runtime proxy file: `C:\PCHost\server.js` (repo mirror under `cng/PCHost/server.js`)
- Loads env from:
  - `C:/project-root/Clinical-Note-Generator/.env`

### Required env in workstation `.env`
- `SEARXNG_API_KEY=<secret>`
- Optional:
  - `SEARXNG_URL=http://127.0.0.1:8083`

### Route behavior
- Endpoint: `GET /searxng/search?q=<query>`
- Required header: `X-API-Key: <SEARXNG_API_KEY>`
- Forces upstream `format=json`.
- Returns `403` for disallowed IPs, `401` for bad/missing key.

## VPS/OpenClaw Configuration
### Secrets file
- Path: `/home/solom/.openclaw/secrets.env`
- Contents:
  - `SEARXNG_PROXY_URL=https://ieissa.com:3443/searxng/search`
  - `SEARXNG_API_KEY=<secret>`
- Permissions: `chmod 600 /home/solom/.openclaw/secrets.env`

### Gateway systemd override
- Drop-in dir:
  - `/home/solom/.config/systemd/user/openclaw-gateway.service.d/`
- File: `searx.conf`
- Content:
  - `[Service]`
  - `EnvironmentFile=-/home/solom/.openclaw/secrets.env`

### Apply changes
- `systemctl --user daemon-reload`
- `systemctl --user restart openclaw-gateway.service`

## Helper Scripts (VPS workspace)
- `/home/solom/.openclaw/workspace/scripts/searx_search.py`
- `/home/solom/.openclaw/workspace/scripts/searx_search.sh`

Usage:
- Human-readable:
  - `./scripts/searx_search.sh "openclaw heartbeat" 5`
- JSON mode:
  - `./scripts/searx_search.sh --json "openclaw heartbeat"`

## Validation Results
- Workstation direct proxy test with key returned valid JSON results.
- Non-allowed IP returned `403 Forbidden`.
- VPS allowed IP + valid key returned successful results.

## Operational Notes
- Built-in `web_search` tool still requires Brave API key and remains unused for this flow.
- Preferred search path is SearXNG route/helper scripts.
- If search fails, check in order:
  1) gateway service running,
  2) secrets file present and loaded,
  3) key rotation mismatch,
  4) workstation proxy running,
  5) IP allowlist contains current VPS IP.
