# Medical Q&A — manual smoke checklist

Use after backend or `PCHost/web/qa.html` / `index.html` changes. Sign in via the main app so `sessionStorage` has a bearer token.

## Text QA (`qa.html` standalone or iframe from index)

1. Open **Medical Q&A**, send a short clinical question (e.g. “What are first-line options for uncomplicated hypertension?”).
2. Confirm **streaming** answer completes; **Evidence** line shows RAG/Web counts when configured.
3. Tap **New topic** — session changes; next question should **not** reuse prior thread unless that is intended.
4. Optional: open browser devtools → Network — **`/api/qa/chat_stream`** returns body ending with **`__QA_META__`** JSON.

## Vision QA (image + same `session_id` as text)

1. Attach an image, ask a question, wait for the full streamed reply.
2. **Thumbnail** should remain (or re-attach); ask a **follow-up** that references the first answer (same topic `session_id`).
3. Confirm the model’s reply reflects **prior** text + vision context when testing **mixed** sessions (vision and text share server state when using the same `session_id`).

## Embedded QA (index side panel)

1. From the main workspace, open **Medical Q&A** (sidebar / panel).
2. Confirm the iframe loads **`qa.html`** and auth works without a second login.

## Markdown rendering (quick)

1. If the answer includes a **pipe table**, **numbered list**, **fenced code block** (```), or **markdown links** `[label](https://…)`, confirm they render (tables wrap; lists number; code uses monospace).

## Service worker

After changing **`service_worker.js`**, bump the cache constant and hard-refresh (or clear site data) once so the PWA does not serve stale **`index.html`** or JS.
