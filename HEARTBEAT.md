# HEARTBEAT.md

# Lightweight heartbeat tasks (safe, low-cost)

- If a long-running user task is active, send a brief progress update at least every 15 minutes.
- If blocked for any reason (tool/runtime/auth/limits), notify immediately with:
  1) blocker,
  2) whether user action is needed,
  3) next step.
- Do background checks even when nothing urgent; send a brief status summary instead of HEARTBEAT_OK.
- Minimum gap between non-urgent updates: **2 hours**.
- Quiet hours: **10:00 PM to 7:00 AM Atlantic Time**.
  - During quiet hours, batch low-priority items.
  - If user action is needed, send an action prompt immediately.
- Output style: short bullet points.
- Track workspace file changes (new/removed/moved key files) and include a compact file-organization status line in summaries.
