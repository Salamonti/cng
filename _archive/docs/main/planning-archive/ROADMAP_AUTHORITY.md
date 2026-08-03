# Which planning doc is authoritative?

> **Archived** with the rest of `docs/planning-archive/`. Next scope: [`FUTURE_PLAN_BACKLOG.md`](./FUTURE_PLAN_BACKLOG.md).

| Document | Authority |
|----------|-----------|
| **[`MASTER_PLAN_DREAMCISION.md`](./MASTER_PLAN_DREAMCISION.md)** | **Product roadmap:** phases P0–P14, sequencing, what is done vs deferred, exit criteria. Use this to decide **the next milestone**. |
| **[`MULTI_ENCOUNTER_DESIGN.md`](./MULTI_ENCOUNTER_DESIGN.md)** | **Design archive + rationale** for multi-encounter threads, API shape, and migration ideas. **Status** for shipped work is in the master plan (P5–P7 **Done**). Use this for **edge cases and history**, not for current phase order. |
| **[`MODULARIZATION_PLAN.md`](./MODULARIZATION_PLAN.md)** | **Frontend structure only:** P10 ship vs optional **P10b** splits (`workspace_app.js`, etc.). **Not** a substitute for product phases — it does not define encounters, backend, or admin work. |

**Handoff / engineering log:** [`../IMPLEMENTATION_LOG.md`](../IMPLEMENTATION_LOG.md).

**Operator procedures (this machine):** [`../OPERATOR_RUNBOOK_WINDOWS.md`](../OPERATOR_RUNBOOK_WINDOWS.md) (**P14 v1 complete** — NSSM env template, checklist, backups, bootstrap, rotation).
