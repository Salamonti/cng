# DreamCision Production Readiness Checklist

*Scan date: June 21, 2026 — Target: 50 doctors*

## Priority 1 — Do Before Launch

- [ ] **Rate limiting** — Add throttling on `/login`, `/register`, `/forgot-password` (slowapi or token bucket). No current protection against brute force or spam.
- [ ] **Data backup** — Set up automated SQLite backup (`sqlite3 .backup` or `rsync`) to NAS (192.168.0.210). No backup strategy exists.
- [ ] **JWT secret verification** — Confirm `jwt_secret` env var is actually set (config.json shows placeholder `"SET_IN_ENV_JWT_SECRET"`). Critical if not.

## Priority 2 — Important

- [ ] **Account lockout** — Track failed login attempts, lock account after N failures. Currently unlimited password attempts.
- [ ] **Monitoring/alerting** — Add uptime monitor (UptimeRobot, etc.) to alert if service crashes.
- [ ] **Resend API key** — Move from `config/config.json` plaintext to environment variable or gitignored `.env` file.

## Priority 3 — Nice to Have

- [ ] **Unauthenticated endpoints** — `/api/performance` and `/api/qa_config` expose internal data without auth. Put behind auth.
- [ ] **Cookie `secure=True`** — Refresh token cookie has `secure=False`. Verify TLS at reverse proxy level, then flip to true.
- [ ] **Request size limits** — Add max payload size for uploads (audio, notes) to prevent memory exhaustion.
- [ ] **In-memory store persistence** — `_generation_meta`, `_patient_materials_store`, `_reset_tokens` are lost on restart. Consider Redis or DB-backed store.
- [ ] **Admin MFA / IP restriction** — Admin console has no additional auth layer beyond password. Low risk (only you have access), but worth considering.

## Already Solid ✅

- JWT access + refresh tokens, token revocation on logout
- PBKDF2-SHA256 password hashing (passlib)
- Password policy: 12+ chars, upper/lower/digit/symbol
- Admin approval gate before any account can log in
- CORS scoped to `ieissa.com`/`eissa.ca`/localhost (not wildcard)
- SQLModel/SQLAlchemy — parameterized queries, no SQL injection
- Email enumeration protection (forgot-password always returns 200)
- Health check endpoint (`/api/health`)
- 60+ test files with good coverage
- systemd service, clean restarts
- Password recovery flow (Resend integration)
- Registration/approval email notifications
