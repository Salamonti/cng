# DreamCision operator runbook (Windows)

**P14 operator notebook (v1):** how this PC runs the stack — NSSM (including **production `AppEnvironmentExtra`**), venv, **admin gates**, **checklist**, **ports**, **backups**, **bootstrap admin**, **password rotation**, and **recovery**.  
Roadmap (archived): [`planning-archive/MASTER_PLAN_DREAMCISION.md`](./planning-archive/MASTER_PLAN_DREAMCISION.md).

---

## 1. Paths (adjust to your checkout)

| Item | Typical path |
|------|----------------|
| **Project root** | `C:\project-root` (contains `Clinical-Note-Generator`, `PCHost`, `RAG`, `service_endpoints.json`) |
| **App directory (FastAPI / Python cwd)** | `C:\project-root\Clinical-Note-Generator` |
| **Venv Python** (first match wins in scripts) | `Clinical-Note-Generator\.venv\Scripts\python.exe` → `venv\` → `cenv\` → system `python` |

---

## 2. NSSM: office stack (recommended single service)

One Windows service can start **Node (PCHost)**, **FastAPI**, **RAG**, and **AI listeners** in order, using [`service_endpoints.json`](../service_endpoints.json) as the only command list.

| NSSM field | Value |
|------------|--------|
| **Application** | `C:\project-root\Clinical-Note-Generator\.venv\Scripts\python.exe` |
| **Arguments** | `-m server.core.office_stack_supervisor` |
| **App directory** | `C:\project-root\Clinical-Note-Generator` |

Optional env on the service (NSSM **Environment** tab or `AppEnvironmentExtra`):

- `FASTAPI_PORT` — if not set, read from `service_endpoints.json` (`fastapi_port`).
- `OFFICE_STACK_START_DELAY_SEC` — delay before starting children (default `30` in supervisor).

Thin wrapper (same behavior): [`startup/start-office-stack.ps1`](../startup/start-office-stack.ps1).

### 2a. Production environment — NSSM **AppEnvironmentExtra**

Set these on the **same** Windows service that runs the Python process which ultimately hosts FastAPI (office supervisor **or** standalone FastAPI). Child processes inherit this environment.

**GUI:** NSSM → service → *Environment* tab → add each variable.

**CLI (example service name `DreamCisionOffice`):** each extra var is often set with repeated `nssm set ... AppEnvironmentExtra` calls depending on NSSM build; the GUI is least error-prone.

**Template (replace secrets; never commit real values):**

| Variable | Production example | Notes |
|----------|--------------------|--------|
| `JWT_SECRET` | *(long random)* | Required. |
| `JWT_REFRESH_SECRET` | *(different long random)* | Required. |
| `DATABASE_URL` | `sqlite:///data/user_data.sqlite` | Override only if you use another DB path/URL. |
| `ADMIN_BOOTSTRAP_EMAIL` | `ops@yourorg.example` | Optional; **first boot only** — creates admin if email not present (see §9). |
| `ADMIN_BOOTSTRAP_PASSWORD` | *(strong one-time password)* | Optional; remove from NSSM after admin exists if policy requires (see §10). |
| `ADMIN_PROCESS_CONTROL_ENABLED` | `0` | Recommended prod — no admin UI start/stop of office + AI. |
| `ADMIN_SERVICE_CONTROL_ENABLED` | `0` | Recommended prod — no admin UI Windows service control. |
| `ADMIN_MUTATIONS_LOCALHOST_ONLY` | `1` | **Recommended** when FastAPI binds `0.0.0.0` — blocks `/api/admin` writes from non-loopback (see `server/app.py` middleware). |
| `ENV` | `production` | Optional label for `/api/version`. |
| `FASTAPI_PORT` | *(optional)* | If set, overrides port read from `service_endpoints.json` for some launch paths. |

**Whisper / LLM binaries and model paths** normally live in [`service_endpoints.json`](../service_endpoints.json) (`ai_binary_defaults`, `llama_instances`, `whisper_instances`), not duplicated here.

---

## 3. NSSM: FastAPI only (alternative)

If you do **not** use the office supervisor, install FastAPI as its own service, e.g.:

| NSSM field | Example |
|------------|--------|
| **Application** | `...\Clinical-Note-Generator\.venv\Scripts\python.exe` |
| **Arguments** | `-m uvicorn server.app:app --host 0.0.0.0 --port 7860 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1,::1 --log-level info` |
| **App directory** | `...\Clinical-Note-Generator` |

Set **`JWT_SECRET`**, **`JWT_REFRESH_SECRET`**, and other secrets via NSSM env (never commit them).

---

## 4. Three admin control mechanisms (do not confuse)

These are **independent** toggles in DreamCision admin (`admin.html`):

| Mechanism | What it does | Gate (FastAPI env) | Config |
|-----------|----------------|-------------------|--------|
| **A. Windows / NSSM** | Start/stop/restart **Windows services** by name (e.g. office stack, FastAPI) | `ADMIN_SERVICE_CONTROL_ENABLED=1` | `windows_services` in `service_endpoints.json` (allowlisted names only) |
| **B. Office stack processes** | Start/stop **PCHost / FastAPI / RAG** child processes from JSON (`office_stack_processes`) | `ADMIN_PROCESS_CONTROL_ENABLED=1` (or legacy `ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED=1`) | `office_stack_processes` + `office_stack_order` |
| **C. AI native processes** | Start/stop **`llama-server`** / **`whisper-server`** instances | `ADMIN_PROCESS_CONTROL_ENABLED=1` (or legacy `ADMIN_AI_PROCESS_CONTROL_ENABLED=1`) | `llama_instances` / `whisper_instances` |

**Typical production:** run **A** via NSSM / Services.msc; put **`ADMIN_SERVICE_CONTROL_ENABLED=0`** and **`ADMIN_PROCESS_CONTROL_ENABLED=0`** on the FastAPI process so the web UI cannot stop your services. **Typical dev:** set process control to `1` on a trusted workstation only.

---

## 5. Recommended defaults

| Variable | Production / unattended | Trusted dev console |
|----------|-------------------------|----------------------|
| `ADMIN_PROCESS_CONTROL_ENABLED` | **`0`** | **`1`** if you use admin Start/Stop for office + AI |
| `ADMIN_SERVICE_CONTROL_ENABLED` | **`0`** (or `1` only with strict allowlist + VPN/firewall) | **`1`** if you use NSSM buttons in admin |
| `ADMIN_MUTATIONS_LOCALHOST_ONLY` | **`1`** when FastAPI listens on `0.0.0.0` (blocks POST/PUT/PATCH/DELETE under `/api/admin` unless the **direct** TCP client is loopback) | usually `0` |

`start_fastapi_server_external.bat` defaults **`ADMIN_PROCESS_CONTROL_ENABLED`** to **`0`**. Uncomment or set `set ADMIN_PROCESS_CONTROL_ENABLED=1` for local operator consoles.

Details: [`Clinical-Note-Generator/docs/ENV_VARIABLES.md`](../Clinical-Note-Generator/docs/ENV_VARIABLES.md).

---

## 6. Optional hardening

- **IP / network:** Prefer terminating HTTPS at PCHost and **not** exposing FastAPI `:7860` to the LAN. If it is exposed, use `ADMIN_MUTATIONS_LOCALHOST_ONLY=1` and/or a firewall rule so only loopback or admin IPs reach **`/api/admin`** (reverse proxy path rules are even better).
- **Destructive UI:** The admin console asks for browser confirmation before bulk office **Stop all**, per-service **Stop/Restart**, AI **Stop**, and Windows **Stop/Restart**.

---

## 7. Whisper / complex CLI

Copy/paste **`launch.arguments`** patterns for `whisper_instances` in [`Clinical-Note-Generator/docs/WHISPER_LAUNCH_ARGUMENTS.md`](../Clinical-Note-Generator/docs/WHISPER_LAUNCH_ARGUMENTS.md).

---

## 8. Environment checklist (before go-live)

Use this when commissioning a machine or after a restore.

| Step | Item |
|------|------|
| ☐ | Python **3.11+** venv at `Clinical-Note-Generator\.venv` (or `venv` / `cenv`) with app dependencies installed. |
| ☐ | **Node** on `PATH` for PCHost (path in `office_stack_processes` if not default). |
| ☐ | **[`service_endpoints.json`](../service_endpoints.json)** — ports, `office_stack_processes`, `windows_services`, AI instances match this host. |
| ☐ | **NSSM** service(s): Application / Arguments / App directory correct (§2 or §3). |
| ☐ | **Secrets** on service: `JWT_SECRET`, `JWT_REFRESH_SECRET` (§2a). |
| ☐ | **Admin gates**: `ADMIN_PROCESS_CONTROL_ENABLED`, `ADMIN_SERVICE_CONTROL_ENABLED`, `ADMIN_MUTATIONS_LOCALHOST_ONLY` (§5–§6). |
| ☐ | **PCHost** TLS/certificates if using HTTPS (see `INSTALLATION_GUIDE.md`). |
| ☐ | **Firewall**: only expose what you intend (often PCHost; avoid exposing raw FastAPI port to untrusted networks). |
| ☐ | **Backup** job for SQLite + config (§12). |

---

## 9. Service layout & ports (single source of truth)

**Authoritative file:** repo root [`service_endpoints.json`](../service_endpoints.json) (`fastapi_port`, `office_stack_processes.*.ports`, `services_urls`, `llama_instances.*.base_url`, etc.).

Typical **defaults** (your JSON file may differ):

| Role | Typical port(s) | Config keys |
|------|-----------------|-------------|
| PCHost (main proxy) | `3000` HTTP, `3443` HTTPS | `office_stack_processes.notes_proxy.ports` |
| FastAPI API | `7860` | `fastapi_port`, `office_stack_processes.fastapi.ports` |
| RAG API | `8007` | `office_stack_processes.rag` |
| LLM (example) | `8081`, `8090`, … | `llama_instances.*.base_url` |
| ASR / Whisper (example) | `8095`, `9000`, … | `whisper_instances` / `services_urls` |

After editing JSON, **restart** affected services. URLs and `LLM_*` env sync on FastAPI startup are described in [`Clinical-Note-Generator/docs/ENV_VARIABLES.md`](../Clinical-Note-Generator/docs/ENV_VARIABLES.md).

---

## 10. Bootstrap admin (first operator account)

Implementation: [`Clinical-Note-Generator/server/core/bootstrap_admin.py`](../Clinical-Note-Generator/server/core/bootstrap_admin.py). Runs at FastAPI **startup**.

1. Set **`ADMIN_BOOTSTRAP_EMAIL`** and **`ADMIN_BOOTSTRAP_PASSWORD`** on the FastAPI process (§2a).
2. Start FastAPI once. Log line: bootstrap admin **created** for that email (if the user did not already exist).
3. Sign in at **`admin.html`** with that email/password (`/api/auth/login`).
4. If **`ADMIN_BOOTSTRAP_*`** remains set and the user already exists, startup **does not** overwrite the password — bootstrap is a **create-if-missing** path only.

**Troubleshooting:** If login fails after bootstrap, confirm `JWT_*` secrets are set, DB path is writable, and the user row has `is_admin=1`, `is_approved=1` (SQLite: `Clinical-Note-Generator/data/user_data.sqlite`).

---

## 11. Admin password rotation

Pick **one** approach per rotation event.

### A. Via clinical / admin UI (preferred when available)

If your deployment exposes **Change password** for logged-in users, use it for the admin account while signed in as that admin.

### B. New password via bootstrap (only if user is missing or you accept DB hygiene)

`ensure_bootstrap_admin` **does not** update existing users. Rotation via bootstrap alone requires either a **new** bootstrap email or manual DB update (below).

### C. Direct database (operator)

1. Stop FastAPI (or use DB browser with care — risk of corruption if app writes during edit).
2. Generate a hash from the app venv (from **`Clinical-Note-Generator`**):

   ```bat
   .venv\Scripts\python.exe -c "from server.core.security import hash_password; print(hash_password('YOUR_NEW_PASSWORD'))"
   ```

3. Open `Clinical-Note-Generator/data/user_data.sqlite` with a trusted SQLite tool.
4. Locate the **`user`** row for the admin email; set **`hashed_password`** to the printed value.
5. Restart FastAPI; log in with the new password.

### D. Clear bootstrap secrets from NSSM after go-live

If policy forbids leaving **`ADMIN_BOOTSTRAP_PASSWORD`** on disk: after the admin account exists and you have another recovery path (break-glass DB backup, second admin), remove those two variables from **AppEnvironmentExtra** and restart the service.

---

## 12. Backups & recovery

**Minimum scope:**

| Asset | Path (default) | Notes |
|-------|------------------|--------|
| User DB | `Clinical-Note-Generator/data/user_data.sqlite` | Users, sessions, encounters, queue metadata. |
| App config | `Clinical-Note-Generator/config/config.json` | Model prefs, preprocessing, etc. |
| Endpoints SOT | [`service_endpoints.json`](../service_endpoints.json) (repo root) | Ports, stack, AI instances. |
| Optional | `Clinical-Note-Generator/data/queue_files/` | Queue payload files if you rely on restore of pending jobs. |
| Optional | `RAG/chroma_store/` | Regeneratable; large — see installation guide. |

**Restore:** Stop services → copy files back → start services → verify `/api/version`, login, one note/QA smoke test.

Longer narrative: [`INSTALLATION_GUIDE.md` — Backup & Recovery](../INSTALLATION_GUIDE.md#backup--recovery).

---

## 13. Failure recovery & restart order

1. **Windows Services.msc** — confirm the office stack (or FastAPI) service is **Running**.
2. If the stack uses **`office_stack_supervisor`**, children start in **`office_stack_order`** in `service_endpoints.json` (e.g. proxies before API). Logs: `Clinical-Note-Generator/server/logs/`, AI logs under `server/logs/ai/` if configured.
3. **TCP probes:** admin **Refresh all health** or check each port from §9.
4. **AI / LLM** not listening: confirm `llama_instances` / GPU / antivirus not blocking binaries; restart instance from admin only if `ADMIN_PROCESS_CONTROL_ENABLED=1` (dev) or restart NSSM service (prod).

---

## 14. Related docs

| Doc | Use |
|-----|-----|
| [`planning-archive/MASTER_PLAN_DREAMCISION.md`](./planning-archive/MASTER_PLAN_DREAMCISION.md) | Product phases (archived snapshot) |
| [`planning-archive/ROADMAP_AUTHORITY.md`](./planning-archive/ROADMAP_AUTHORITY.md) | Which design doc wins (archived) |
| [`INSTALLATION_GUIDE.md`](../INSTALLATION_GUIDE.md) | Full install / troubleshooting |
| [`ENV_VARIABLES.md`](../Clinical-Note-Generator/docs/ENV_VARIABLES.md) | All FastAPI env vars |
| [`WHISPER_LAUNCH_ARGUMENTS.md`](../Clinical-Note-Generator/docs/WHISPER_LAUNCH_ARGUMENTS.md) | Whisper CLI JSON arrays |
