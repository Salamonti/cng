# Future plan backlog

Use this list when starting a **new** roadmap so scope stays clear and separate from the archived DreamCision master plan.

1. **Chunking Whisper for pseudo-streaming** — overlap / windowed ASR for lower perceived latency (optional quality experiment; see archived master plan §P12 / Whisper notes).

2. **Splitting `notes.py` and `workspace_app.js`** — further decomposition into smaller modules (`notes.py` route surface + helpers; more `workspace_app.js` slices or ESM + bundler). Partial work already exists (e.g. `feedback` route, `workspace_ui_state.js`, `workspace_file_camera.js`).

3. **Improving and enhancing RAG** — retrieval quality, corpus expansion, filters/metadata, consult-comment vs QA parity, operator tooling.

4. **EMR integration** — connect to the electronic medical record for **auto-retrieval of patient context** and **auto-insertion** of generated notes, orders, and referrals (interfaces, auth, mapping, and compliance are product decisions).

---

**Pass status:** The roadmap in this archive reflects work **complete through the agreed milestone** (including P10b slice, debt batch, and planning consolidation). Congratulations on shipping.
