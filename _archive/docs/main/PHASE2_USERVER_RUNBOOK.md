# Phase 2 — userver migration runbook (native systemd)

**Audience:** an execution agent (e.g. Composer 2.5 Fast) or operator. Follow steps **in order**. Each step has an **action** and a **VERIFY** gate — do not proceed until VERIFY passes. Lines marked **[OPERATOR]** need a human (physical access, DNS, or a decision); everything else the agent can do over SSH (passwordless `sudo` is available).

> **Decisions locked for this runbook (2026-06-12):**
> - **Runtime = native systemd** (NOT Docker). Rationale: the userver AI stack already runs as systemd (`vllm-26b-card1`, `vllm-27b-card0`) on `127.0.0.1`; the whole app assumes loopback. Docker adds loopback/networking friction for no latency benefit (GPU is ~native either way). The `~/DreamCision/docker-compose.yml` + `migration_plan_final_v4.md` Docker approach is **superseded** by this runbook.
> - **Scope = minimal migration.** Clone + run DreamCision on userver, **reuse the already-running Gemma (`:8081`) and Qwen (`:8000`) vLLM** and the existing whisper.cpp build. Cut the Cloudflare tunnel over to userver. **Keep the Windows stack stopped-but-intact** for 48 h rollback. **No** Windows wipe, **no** hot-standby failover, **no** Thunderbolt (those remain a separate, later decision — see the prior `~/DreamCision/migration_plan_final_v4.md` if/when that program is revisited).
> - This document is the **source of truth** for Phase 2 and aligns with `docs/GRAND_PLAN.md`.

---

## 0. Reference values (measured from the 2026-06-12 audit — re-verify if stale)

| Item | Value |
|---|---|
| userver SSH | `eissa@100.72.189.26` (Tailscale) — LAN IP **`192.168.0.108`** |
| OS | Ubuntu 24.04.4 LTS, kernel 6.8, 24 cores, 122 GB RAM, 3.3 TB free on `/` |
| GPUs | 2× RTX PRO 6000 Blackwell (96 GB each), driver 610.43.02, CUDA 13.2 |
| Primary LLM (running, systemd) | Gemma `gemma4-26b-awq` on **`127.0.0.1:8081`** — `vllm-26b-card1.service` (GPU1) |
| Fallback LLM (running, systemd) | Qwen `qwen3.6-27b-awq` on **`127.0.0.1:8000`** — `vllm-27b-card0.service` (GPU0) |
| ASR (running, NOT systemd) | `~/whisper.cpp/build/bin/whisper-server` med-finetuned-large-v3turbo-q5_1 on **`:8097`** (started in a login shell — to be systemd-ized) |
| Repo remote | `https://github.com/Salamonti/cng.git` |
| Already installed | git, cmake, gcc/g++, make, **cloudflared 2026.5.2**, docker (unused), ffmpeg, curl, jq |
| **Missing — must install** | **Node.js**, **python3 pip/venv tooling** (`python3-venv` present but no system `pip`) |
| Free ports on userver | `3000, 3443, 7860, 8007, 8037, 8095, 8096` (8081 Gemma / 8000 Qwen / 8097 whisper are in use) |
| sudo | passwordless (`sudo -n true` works) |
| App data dir (target) | `/opt/dreamcision` (code) — owned by `eissa`; SQLite on local ext4 |

**Port reconciliation vs Windows `service_endpoints.json`:** Windows used fallback LLM on `8037`. userver's fallback Qwen is on **`8000`**. → set `LLM_*_FALLBACK` to `http://127.0.0.1:8000` (do **not** stand up a new 8037). Primary stays `http://127.0.0.1:8081`.

---

## 1. Pre-flight (on Windows workstation)

1.1 **Push the verified Phase 1 code** so userver can clone it:
```powershell
cd C:\project-root
git push origin main
```
**VERIFY:** `git status` shows "up to date with origin/main"; `git rev-parse HEAD` matches GitHub.

1.2 **Identify the gitignored host files** that must be copied separately (NOT in git): `Clinical-Note-Generator/config/config.json` (placeholders are in git, but the live file may differ), `PCHost/config/server_config.json`, `Clinical-Note-Generator/data/user_data.sqlite` (+ `-wal`/`-shm`), TLS certs, `service_endpoints.json` (committed, but Linux paths differ — edited in step 5).

1.3 **Fresh DB backup** (already have one from Phase 1, take a current one right before transfer to minimize drift):
```powershell
Copy-Item C:\project-root\Clinical-Note-Generator\data\user_data.sqlite "C:\project-root\Clinical-Note-Generator\data\backups\user_data.pre-migrate.sqlite" -Force
```
**VERIFY:** backup file exists and is non-zero.

> **[OPERATOR]** Decide the migration window. While the tunnel is cut over, `notes.ieissa.com` briefly serves from userver; clinicians should not be mid-encounter.

---

## 2. userver base dependencies

2.1 Install Node.js 22 (NodeSource) + Python venv/pip + build deps:
```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
```
**VERIFY:**
```bash
node --version    # expect v22.x
npm --version
python3 -m pip --version
python3 -m venv --help >/dev/null && echo venv-ok
```

2.2 Create app directory:
```bash
sudo mkdir -p /opt/dreamcision
sudo chown -R eissa:eissa /opt/dreamcision
```
**VERIFY:** `ls -ld /opt/dreamcision` shows owner `eissa`.

---

## 3. Get code + host files onto userver

3.1 Clone the repo (run **on userver**):
```bash
cd /opt/dreamcision
git clone https://github.com/Salamonti/cng.git .
git checkout main && git pull
git rev-parse HEAD     # must equal the Windows HEAD from step 1.1
```
**VERIFY:** HEAD matches; `ls` shows `Clinical-Note-Generator/`, `PCHost/`, `RAG/`, `service_endpoints.json`, `startup/`.

3.2 Copy gitignored host files from Windows → userver (run **on Windows**; `scp`). The live config has real secrets in **env**, not the file, but copy it anyway to match the running host:
```powershell
scp C:\project-root\Clinical-Note-Generator\config\config.json eissa@100.72.189.26:/opt/dreamcision/Clinical-Note-Generator/config/config.json
scp C:\project-root\PCHost\config\server_config.json eissa@100.72.189.26:/opt/dreamcision/PCHost/config/server_config.json
scp C:\project-root\Clinical-Note-Generator\data\user_data.sqlite eissa@100.72.189.26:/opt/dreamcision/Clinical-Note-Generator/data/user_data.sqlite
```
(Do **not** copy `-wal`/`-shm`; with the app stopped, checkpoint first or copy only the main DB after a clean shutdown to avoid a torn WAL. Safest: on Windows stop OfficeStack, run `PRAGMA wal_checkpoint(TRUNCATE)`, then copy the single `.sqlite`.)
**VERIFY (on userver):** `sqlite3 /opt/dreamcision/Clinical-Note-Generator/data/user_data.sqlite "PRAGMA integrity_check;"` → `ok`.

> **[OPERATOR / decision]** Secrets: `JWT_SECRET`, `JWT_REFRESH_SECRET`, `ADMIN_API_KEY` are read from the **environment** (config.json holds `SET_IN_ENV_*` placeholders). They must be set on userver via the systemd unit `Environment=`/`EnvironmentFile=` (step 6.0). Copy the real values from the Windows host's environment — never commit them.

---

## 4. Python venvs + Node deps

4.1 FastAPI venv:
```bash
cd /opt/dreamcision/Clinical-Note-Generator
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt    # confirm the requirements filename in the repo
deactivate
```
**VERIFY:** `/opt/dreamcision/Clinical-Note-Generator/.venv/bin/python -c "import fastapi, sqlalchemy, uvicorn; print('ok')"`.

4.2 RAG venv:
```bash
cd /opt/dreamcision/RAG
python3 -m venv venv
. venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt ; deactivate
```
**VERIFY:** RAG venv imports its entrypoint module (`query_api`).

4.3 PCHost Node deps:
```bash
cd /opt/dreamcision/PCHost
npm ci --omit=dev || npm install --omit=dev
```
**VERIFY:** `node --check server.js` exits 0.

---

## 5. Linuxize `service_endpoints.json`

Edit `/opt/dreamcision/service_endpoints.json`:
- `office_stack_processes.*.working_dir` / `executable`: change Windows paths (`C:\\project-root\\...`, `C:\\Program Files\\nodejs\\node.exe`) → Linux (`/opt/dreamcision/PCHost`, `/usr/bin/node`, venv pythons).
- **`env` block** — keep all `http://127.0.0.1:<port>` targets; change only the **fallback**:
  - `LLM_NOTE_GEN_URL_FALLBACK`, `LLM_OCR_URL_FALLBACK`, `LLM_QA_TEXT_URL_FALLBACK`, `LLM_QA_VISION_URL_FALLBACK`, `LLM_RAG_COMMENT_URL_FALLBACK`, `LLM_ORDER_REQUEST_URL_FALLBACK`: `http://127.0.0.1:8037` → **`http://127.0.0.1:8000`** (existing Qwen).
  - `NOTEGEN_URL_PRIMARY` etc stay `http://127.0.0.1:8081` (existing Gemma).
  - `ASR_URLS`: set to `http://127.0.0.1:8095,http://127.0.0.1:8096` (pool created in step 7); `ASR_URL`=8095, `ASR_URL_FALLBACK`=8096.
- `pchost.backend_url` stays `http://127.0.0.1:7860`; `http_port` 3000, `https_port` 3443.

**Note:** Windows process supervision (`office_stack_launcher.py` / NSSM) is **replaced by systemd** (step 6) — the launcher's `kind: uvicorn_clinical`/`node` logic is not used on Linux. `service_endpoints.json` remains the config/URL source of truth only.

**VERIFY:** `jq . /opt/dreamcision/service_endpoints.json` parses; no `C:\\` paths remain (`grep -i 'C:' service_endpoints.json` empty); no `8037` remains.

---

## 6. systemd units (replace NSSM office stack)

Create these under `/etc/systemd/system/`. They mirror the existing `vllm-26b-card1.service` conventions (circuit breaker, `Restart=on-failure`). **Do NOT** create LLM units — reuse the running `vllm-26b-card1` (8081) and `vllm-27b-card0` (8000).

6.0 **Secrets env file** (root-only):
```bash
sudo install -m 600 /dev/null /etc/dreamcision.env
sudo tee /etc/dreamcision.env >/dev/null <<'EOF'
JWT_SECRET=__set_real_value__
JWT_REFRESH_SECRET=__set_real_value__
ADMIN_API_KEY=__set_real_value__
EOF
sudo chmod 600 /etc/dreamcision.env
```

6.1 `dreamcision-fastapi.service` (port 7860):
```ini
[Unit]
Description=DreamCision FastAPI (/api)
After=network-online.target vllm-26b-card1.service
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
User=eissa
WorkingDirectory=/opt/dreamcision/Clinical-Note-Generator
EnvironmentFile=/etc/dreamcision.env
ExecStart=/opt/dreamcision/Clinical-Note-Generator/.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 7860 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1,::1 --log-level info
Restart=on-failure
RestartSec=10s
TimeoutStopSec=60s

[Install]
WantedBy=multi-user.target
```
> ASGI path `server.app:app` is **confirmed** from `start_fastapi_server_external.bat`. The `--proxy-headers --forwarded-allow-ips 127.0.0.1,::1` flags are **required** (PCHost forwards `X-Forwarded-Proto: https`; without them the app mis-builds redirect URLs — same class of bug as Regression Guard R6). LLM/ASR URLs are read from `service_endpoints.json` by the app's config loader at startup; if the app instead expects them as process env, add an `EnvironmentFile` derived from the JSON `env` block.

6.2 `dreamcision-rag.service` (port 8007):
```ini
[Unit]
Description=DreamCision RAG API
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3
[Service]
User=eissa
WorkingDirectory=/opt/dreamcision/RAG
ExecStart=/opt/dreamcision/RAG/venv/bin/uvicorn query_api:app --host 127.0.0.1 --port 8007
Restart=on-failure
RestartSec=10s
[Install]
WantedBy=multi-user.target
```
> Confirm RAG ASGI path (`query_api:app`).

6.3 `dreamcision-pchost.service` (ports 3000/3443):
```ini
[Unit]
Description=DreamCision PCHost proxy
After=network-online.target dreamcision-fastapi.service
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3
[Service]
User=eissa
WorkingDirectory=/opt/dreamcision/PCHost
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=10s
[Install]
WantedBy=multi-user.target
```

6.4 Enable + start (FastAPI → RAG → PCHost order):
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dreamcision-fastapi dreamcision-rag dreamcision-pchost
```
**VERIFY:**
```bash
systemctl is-active dreamcision-fastapi dreamcision-rag dreamcision-pchost   # all "active"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7860/docs           # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7860/api/workspace/version  # 401 (route live)
curl -sk -o /dev/null -w '%{http_code}\n' -H 'x-forwarded-proto: https' http://127.0.0.1:3000/   # 200
journalctl -u dreamcision-fastapi -n 50 --no-pager    # no tracebacks
```

---

## 7. whisper.cpp ASR pool → systemd (ports 8095, 8096)

The existing build is at `~/whisper.cpp/build/bin/whisper-server` (model `~/whisper.cpp/models/med-finetuned-large-v3turbo-q5_1.bin`). Replace the fragile login-shell instance with a templated systemd unit. Bind **127.0.0.1** (not 0.0.0.0 — backends stay loopback-only).

7.1 `whisper-server@.service` (template; instance = port):
```ini
[Unit]
Description=whisper.cpp ASR server (port %i)
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3
[Service]
User=eissa
Environment=CUDA_VISIBLE_DEVICES=1
ExecStart=/home/eissa/whisper.cpp/build/bin/whisper-server -m /home/eissa/whisper.cpp/models/med-finetuned-large-v3turbo-q5_1.bin --host 127.0.0.1 --port %i -t 4 --convert
Restart=on-failure
RestartSec=10s
[Install]
WantedBy=multi-user.target
```
7.2 Enable two instances, retire the manual 8097:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now whisper-server@8095 whisper-server@8096
# stop the old manual login-shell instance on 8097 (find PID via: pgrep -af whisper-server)
```
**VERIFY:**
```bash
for p in 8095 8096; do curl -s -o /dev/null -w "$p:%{http_code}\n" http://127.0.0.1:$p/ ; done   # 200 each
```
> GPU note: both pinned to GPU1 (37 GB free). If contention appears under load, pin `whisper-server@8096` to `CUDA_VISIBLE_DEVICES=0` via a systemd drop-in. whisper.cpp clinical policy: **no VAD** (`--convert` only; do not add `--vad`).

---

## 8. RAG weekly updates → systemd timer (replaces Windows Task Scheduler)

The Windows `RAG Weekly` task ran `RAG/scripts/weekly_run.ps1` — a **6-step pipeline**, not a single script. Port it to a bash script and drive it from a timer. (Step 6, `summarize_recent_updates.py`, needs the primary LLM on `127.0.0.1:8081` — reachable on userver.)

8.0 Create `/opt/dreamcision/RAG/scripts/weekly_run.sh` (mirror of `weekly_run.ps1`):
```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/dreamcision/RAG
PY=/opt/dreamcision/RAG/venv/bin/python
DAYS_BACK=${DAYS_BACK:-7}; PMC_DAYS=${PMC_DAYS:-30}; GUIDE_DAYS=${GUIDE_DAYS:-30}; PDF_MAX_MB=${PDF_MAX_MB:-8}; SUM_MAX=${SUM_MAX:-800}
STAMP=$(date +%Y%m%d_%H%M%S); RUNDIR="runs/$STAMP"; mkdir -p "$RUNDIR"
"$PY" fetch_sources.py --days "$DAYS_BACK"            2>&1 | tee "$RUNDIR/fetch_sources.log"
"$PY" pmc_fetcher.py --oa-subset --days "$PMC_DAYS" --max 2000 2>&1 | tee "$RUNDIR/pmc_fetcher.log"
"$PY" guidelines_fetcher.py --days "$GUIDE_DAYS" --limit-per-source 120 --depth 2 --timeout 45 --fetch-pdf --pdf-max-mb "$PDF_MAX_MB" 2>&1 | tee "$RUNDIR/guidelines.log"
mkdir -p clean_corpus chunks embeddings
for f in $(find raw_docs -maxdepth 1 -type f \( -name '*.json' -o -name '*.jsonl' \) -mtime -"$DAYS_BACK"); do
  out="clean_corpus/$(basename "${f%.*}").processed.jsonl"
  "$PY" process_clinical_corpus.py --in "$f" --out "$out" --fulltext 2>&1 | tee -a "$RUNDIR/process.log"
done
"$PY" chunking_pipeline.py --input ./clean_corpus --pattern '*.processed.jsonl' --output ./chunks 2>&1 | tee "$RUNDIR/chunking.log"
"$PY" embed_chunks.py --input ./chunks --output ./embeddings --batch 64 2>&1 | tee "$RUNDIR/embed.log"
"$PY" update_index.py --emb-dir ./embeddings --chunk-dir ./chunks --snapshots both 2>&1 | tee "$RUNDIR/update_index.log"
# best-effort summary (literature panel); LLM on 127.0.0.1:8081
"$PY" summarize_recent_updates.py --max-docs "$SUM_MAX" 2>&1 | tee "$RUNDIR/summarize.log" || echo "summarize failed (non-fatal)"
echo "weekly run done: $RUNDIR"
```
```bash
chmod +x /opt/dreamcision/RAG/scripts/weekly_run.sh
```
> If the raw-docs `-mtime` filter yields nothing, the `.ps1` fell back to processing all files — add that fallback if a week has no new raw files.

8.1 `dreamcision-rag-weekly.service`:
```ini
[Unit]
Description=DreamCision RAG weekly corpus/index pipeline
After=network-online.target dreamcision-rag.service vllm-26b-card1.service
[Service]
Type=oneshot
User=eissa
WorkingDirectory=/opt/dreamcision/RAG
ExecStart=/opt/dreamcision/RAG/scripts/weekly_run.sh
TimeoutStartSec=4h
```

8.2 `dreamcision-rag-weekly.timer`:
```ini
[Unit]
Description=Weekly RAG update (Mon 03:00)
[Timer]
OnCalendar=Mon *-*-* 03:00:00
Persistent=true
[Install]
WantedBy=timers.target
```
8.3 Enable:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dreamcision-rag-weekly.timer
```
**VERIFY:** `systemctl list-timers | grep rag` shows next run; `sudo systemctl start dreamcision-rag-weekly.service && journalctl -u dreamcision-rag-weekly -n 30 --no-pager` runs clean.

---

## 9. Cloudflare tunnel cutover (Net-P1 on userver)

cloudflared **2026.5.2** is installed but unconfigured. The `office-prod` tunnel currently runs on **Windows** (NSSM service `cloudflare`). **A tunnel ID runs in one place at a time** — this is a coordinated cutover.

9.1 Move tunnel credentials + config to userver (copy from Windows `C:\Windows\System32\config\systemprofile\.cloudflared\`):
```powershell
# on Windows (elevated to read systemprofile)
scp C:\Windows\System32\config\systemprofile\.cloudflared\451a4852-065d-4004-9bce-3104875287ac.json eissa@100.72.189.26:/tmp/tunnel-creds.json
```
9.2 On userver, install config:
```bash
sudo mkdir -p /etc/cloudflared
sudo mv /tmp/tunnel-creds.json /etc/cloudflared/451a4852-065d-4004-9bce-3104875287ac.json
sudo tee /etc/cloudflared/config.yml >/dev/null <<'EOF'
tunnel: 451a4852-065d-4004-9bce-3104875287ac
credentials-file: /etc/cloudflared/451a4852-065d-4004-9bce-3104875287ac.json
ingress:
  - hostname: openclaw.ieissa.ca
    service: http://127.0.0.1:18789
  - hostname: notes.ieissa.com
    service: http://127.0.0.1:3000
    originRequest:
      connectTimeout: 10s
      tcpKeepAlive: 30s
      keepAliveConnections: 100
      keepAliveTimeout: 90s
  - hostname: app.ieissa.com
    service: http://127.0.0.1:3000
    originRequest:
      connectTimeout: 10s
      tcpKeepAlive: 30s
      keepAliveConnections: 100
      keepAliveTimeout: 90s
  - hostname: webui.ieissa.com
    service: https://127.0.0.1:8443
    originRequest:
      noTLSVerify: true
  - hostname: office.ieissa.ca
    service: https://192.168.0.210:443
    originRequest:
      noTLSVerify: true
  - hostname: hospital.ieissa.ca
    service: https://192.168.0.210:9445
    originRequest:
      noTLSVerify: true
  - hostname: nemotron-asr.ieissa.com
    service: http://127.0.0.1:8765
  - service: http_status:404
EOF
```
> Net-P1 origin = `http://127.0.0.1:3000` (no `http2Origin` — PCHost is HTTP/1.1). `openclaw`, `webui`, `office`, `hospital`, `nemotron-asr` ingress only valid if those services also exist on userver — **prune any hostname whose service is not on userver** (e.g. openclaw 18789, webui 8443) to avoid 502s. Confirm before cutover.

9.3 Validate, then **CUTOVER** (ordering matters):
```bash
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate    # OK
```
```powershell
# [OPERATOR, on Windows, elevated] stop the Windows tunnel FIRST (single-tunnel constraint)
Stop-Service cloudflare
```
```bash
# then on userver, install + start cloudflared as a service
sudo cloudflared --config /etc/cloudflared/config.yml service install || true
sudo systemctl enable --now cloudflared
systemctl is-active cloudflared
```
**VERIFY:** from any external network — `curl -s -o /dev/null -w '%{http_code}' https://notes.ieissa.com/` → 200; login + open a note in-app; `curl https://notes.ieissa.com/api/workspace/version` → 401.
**Rollback:** `sudo systemctl stop cloudflared` on userver, then `Start-Service cloudflare` on Windows (reverts to Windows origin).

---

## 10. Firewall (backends stay loopback-only)

```bash
sudo ufw allow 22/tcp
sudo ufw allow from 192.168.0.0/24 to any port 3443 proto tcp   # only for Net-P2 LAN bypass; tunnel itself is outbound
sudo ufw deny 7860/tcp; sudo ufw deny 8007/tcp; sudo ufw deny 8081/tcp; sudo ufw deny 8000/tcp; sudo ufw deny 8095/tcp; sudo ufw deny 8096/tcp
sudo ufw --force enable
```
**VERIFY:** `sudo ufw status numbered`; from another LAN host, `curl http://192.168.0.108:7860/docs` must **fail** (refused), while `127.0.0.1:7860` works on userver.

---

## 11. Net-P2 — split-horizon DNS + cert (LAN bypass) — [OPERATOR, optional, later]

Lets on-site staff hit `notes.ieissa.com` over the LAN without the tunnel. Requires:
- Internal DNS: `notes.ieissa.com` → `192.168.0.108` (router/Pi-hole/AD).
- A **real TLS cert** for `notes.ieissa.com` on userver (none found in audit) so PCHost can serve `0.0.0.0:3443` directly. Options: copy the existing cert from Windows, or issue via Cloudflare Origin CA / Let's Encrypt DNS-01. Set `SSL_KEY_PATH`/`SSL_CERT_PATH` for PCHost.
- PCHost listens `0.0.0.0:3443`; firewall allows 3443 from the hospital VLAN only (step 10).
**Deferred** — not required for the tunnel path to work. Track as Net-P2.

---

## 12. Smoke + acceptance (T25–T30)

Run **after** cutover, from a real browser via `https://notes.ieissa.com`:
- [ ] Login + workspace loads/syncs
- [ ] Generate a note (uses Gemma `:8081`) — no "failed to fetch"
- [ ] Record → Stop → transcript attaches (whisper `:8095/8096`)
- [ ] RAG/literature query returns evidence (`:8007`)
- [ ] Anti-stomp (edit + reload persists; cross-tab) still holds
- [ ] `journalctl` clean for all 3 dreamcision units + whisper instances
- [ ] Reboot test: `sudo reboot`, then confirm all units auto-start and the tunnel reconnects

**Phase 2 acceptance:** DreamCision serving production via userver on systemd, reusing Gemma + Qwen vLLM + whisper.cpp; Windows stack stopped-but-intact for rollback.

---

## 13. Rollback

| Failure point | Rollback |
|---|---|
| App units won't start | `systemctl stop dreamcision-*`; leave tunnel on Windows (don't cut over) |
| Tunnel cutover bad | `systemctl stop cloudflared` (userver) → `Start-Service cloudflare` (Windows) |
| Data issue | restore `user_data.pre-migrate.sqlite` |
| Total | Windows stack is untouched for 48 h — restart `OfficeStack` + `cloudflare` services on Windows |

**Do not** delete or wipe the Windows stack until 48 h of clean userver operation.

---

## 14. Open items for the operator (decide before/at execution)
1. **Secrets** values for `/etc/dreamcision.env` (JWT + admin key) — from Windows env.
2. ~~ASGI entrypoints~~ **confirmed**: FastAPI `server.app:app`, RAG `query_api:app`; requirements at `Clinical-Note-Generator/requirements.txt` + `RAG/requirements.txt`; RAG weekly ported to `weekly_run.sh` (§8.0). Still verify a clean `pip install` resolves on Linux (no Windows-only wheels).
3. Confirm which **cloudflared ingress hostnames** have real services on userver (real config confirmed — tunnel `451a4852-…`; `office`/`hospital` point at NAS `192.168.0.210` reachable from userver; **prune** `openclaw:18789` / `webui:8443` / `nemotron-asr:8765` unless those run on userver).
4. **Net-P2** cert source for the LAN-bypass path (deferred).
5. Whether/when to pursue the larger **wipe + hot-standby failover + Thunderbolt** program from the older `~/DreamCision/migration_plan_final_v4.md` (out of scope here).
