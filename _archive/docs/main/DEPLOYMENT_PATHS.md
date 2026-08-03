# Deployment paths (repo vs live)

Some workstations keep **two path layouts** in play:

| Layout | Role |
|--------|------|
| **Git repo** (e.g. `C:\project-root`) | Source of truth for development; contains `PCHost\`, `Clinical-Note-Generator\`, `RAG\`. |
| **Live symlinks** (e.g. `C:\PCHost` → `...\project-root\PCHost`) | What NSSM / shortcuts / firewall rules often point at. |

**Rule of thumb:** edit and test in the **repo**, then deploy by updating the same tree the symlinks reference (or re-point symlinks after clone). `config.json` keys such as `web_dir` may still use `C:\PCHost\web`; keep that path valid on the machine or override with the repo’s `PCHost\web` path.

This does not remove the need for a single canonical install on disk; it only documents why “I changed the repo but the service reads another folder” happens.
