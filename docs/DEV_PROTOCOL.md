# DreamCision Development Protocol

**Version:** 1.0
**Date:** 2026-07-24
**Applies to:** All agents (Hermes, OpenCode, OpenClaw, etc.) working on this repo

---

## Core Principle

**Production is sacred. Never touch it directly.**

All development work happens on the workstation. Production is updated only after verification.

---

## Environment Layout

| Environment | Location | Purpose |
|---|---|---|
| **Development** | C:\project-root (Workstation) | All development, testing, experimentation |
| **Production** | /opt/dreamcision on 192.168.0.108 (Ubuntu Server) | Live system serving real users |

**Production is read-only for development agents.** Do not SSH into the server to make code changes.

---

## Git Branch Strategy

`
main          ← Production branch (matches what's running on server)
develop       ← Active development branch (all new work happens here)
feat/xxx      ← Feature branches (short-lived, merge to develop)
fix/xxx       ← Bug fix branches (short-lived, merge to develop)
`

### Branch Rules

1. **main** = always matches production. Never commit directly to main on workstation.
2. **develop** = integration branch. All features/fixes merge here first.
3. **Feature/fix branches** = created from develop, merged back to develop when done.
4. **Promote to main** = only after testing on workstation, merge develop → main.
5. **Deploy to server** = push main to server, then git pull + restart services.

---

## What Agents May Do

✅ **On workstation (C:\project-root):**
- Create branches, commit, merge
- Read files, explore codebase
- Run tests (pytest, linting)
- Modify code, configs, scripts
- Write documentation
- Review git history

✅ **Read-only on server:**
- SSH to read files (cat, ls, git log)
- Check service status (systemctl status, ss -tlnp)
- Read logs (journalctl, log files)
- Investigate issues

❌ **Never on server:**
- Modify code files directly
- Edit configs directly
- Restart services without Islam's approval
- Install packages or change dependencies
- Run git push from server
- Make any changes that affect running services

---

## Production Services

| Service | Port | systemd Unit |
|---|---|---|
| vLLM (Qwen3.6-27B) | 8000 | dreamcision-vllm.service |
| FastAPI Backend | 7860 | dreamcision-fastapi.service |
| RAG | 8007 | dreamcision-rag.service |
| SearXNG | 8083 | dreamcision-searxng.service |
| PCHost Proxy | 3000/3443 | dreamcision-pchost.service |
| Whisper ×4 | 8095-8098 | dreamcision-whisper@.service |

---

## Safety Checklist

Before any production deployment:
- [ ] Changes tested on develop branch
- [ ] Merged to main on workstation
- [ ] git diff reviewed for unexpected changes
- [ ] Islam approved the deployment
- [ ] Rollback plan identified
- [ ] Health endpoint checked post-deployment

---

## Emergency Procedures

**If production is broken:**
1. Notify Islam immediately
2. Do NOT attempt blind fixes
3. If rollback is obvious (git revert HEAD), ask Islam for approval
4. Preserve logs and error output for debugging

**If you accidentally modified production:**
1. Stop immediately
2. Notify Islam
3. Do not try to hide or auto-fix — let Islam assess
