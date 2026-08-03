# CNG Regression Checklist (Phase 0)

Run this checklist after deployment or before/after core changes.

## Preconditions
- Backend running: `python -m server.app`
- Frontend running: `node server.js`
- Valid test user available (approved account).

## 1) Auth smoke
1. Open web app and sign in with approved account.
2. Confirm sign-in succeeds and protected controls become enabled.
3. Refresh browser; confirm session remains valid or refresh flow works.
4. Sign out; confirm protected actions are disabled again.

Expected:
- No auth errors in UI.
- `/api/auth/me` returns current user when signed in.

## 2) Note generation smoke
1. Enter short sample text in transcription/current encounter area.
2. Select note type (for example `consult`).
3. Click generate.
4. Confirm note streams and final output appears.
5. Submit feedback (thumbs up/down) once.

Expected:
- No 5xx error.
- Generated note appears and is non-empty.
- Feedback call succeeds.

## 3) OCR smoke
1. Upload a small image or PDF.
2. Run OCR.
3. Confirm extracted text appears.

Expected:
- `/api/ocr` returns success JSON.
- OCR text is non-empty (for readable input).

## 4) QA smoke
1. Open Q&A panel/page.
2. Ask a short medical question.
3. Confirm response returns with sources list.

Expected:
- `/api/qa/chat` succeeds.
- Answer text is returned.
- No auth failure while signed in.

## 5) Queue smoke
1. Add a file to queue (OCR or transcribe type).
2. Verify queue item appears in list.
3. Retry/process once (if service available).
4. Delete queue item.

Expected:
- Create/list/delete endpoints work.
- Item disappears after delete.

## 6) Deployment/version stamp smoke
1. Open `/api/version` directly.
2. Confirm JSON includes:
   - `commit_hash`
   - `build_timestamp_utc`
   - `versions`
3. Inspect response headers; confirm `Cache-Control: no-store`.
4. Open main UI and confirm footer shows runtime version/build info.
5. Hard refresh page (Ctrl/Cmd+Shift+R) and confirm version label still resolves.

Expected:
- Version endpoint always fresh (no stale cache).
- Footer version visible and readable.

## 7) Service worker sanity
1. Open DevTools Application tab and confirm service worker is active.
2. Reload app and ensure no blank page/regression.
3. Verify API calls still go network (not cached).

Expected:
- Service worker remains functional.
- No regression in auth/API behavior after reload.
