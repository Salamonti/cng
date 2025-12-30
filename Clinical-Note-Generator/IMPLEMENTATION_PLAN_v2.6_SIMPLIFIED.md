# CNG6 Refactor Plan v2.6 - SIMPLIFIED (No Two-Pass)

**SIMPLIFIED VERSION** - Focuses on core improvements without two-pass routing

---

## Overview & Goals

**Removing:**
- Admin router mount (service control moved to NSSM-only)
- Llama/OCR auto-management from FastAPI startup/shutdown
- Hardcoded secrets from config.json
- Old admin service control UI

**Adding:**
- Environment-based secrets management (.env)
- Clean admin page (users-only management)
- Enhanced Q&A interface (choose simple or Open WebUI)

**NOT Adding:**
- ~~Two-pass routing~~ (cancelled)
- ~~Extraction/rendering services~~ (cancelled)

**End Result:**
- Clean startup/shutdown (no process management)
- All external services managed via NSSM only
- Secrets in .env, not committed to git
- Better admin and Q&A user experience

---

## Phase 0: Remove Server Auto-Management

### 0.1 Remove Admin Router Mount

**File:** `Clinical-Note-Generator/server/app.py`

**Line 91:** DELETE this line:
```python
from server.routes.admin import router as admin_router  # noqa: E402
```

**Line 114:** DELETE this line:
```python
app.include_router(admin_router)
```

**Verification:**
```bash
cd Clinical-Note-Generator
grep -n "admin_router" server/app.py
# Should return ZERO results
```

---

### 0.2 Remove Llama/OCR Warm-Start Logic (KEEP ASR)

**File:** `Clinical-Note-Generator/server/app.py`

**Lines 176-189: KEEP (ASR pre-warm is safe and helpful)**

**Lines 191-219: DELETE** (llama and OCR warm-start):
```python
# DELETE from "# Optionally warm-start llama-server..." to end of try/except block
```

**After deletion, your startup_event should look like:**
```python
@app.on_event("startup")
async def startup_event():
    logger.info("Server starting up...")
    try:
        init_db()
        logger.info("Auth/workspace database initialized")
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)

    # Pre-warm ASR model to avoid first-request stall (30–120s on cold load)
    try:
        import asyncio as _asyncio  # late import
        from routes.asr import asr_engine  # type: ignore
        # Only pre-warm on CPU to avoid potential GPU init issues on Windows
        dev = getattr(asr_engine, "device", "cpu")
        ensure_model = getattr(asr_engine, "_ensure_model", None)
        if callable(ensure_model):
            _asyncio.create_task(_asyncio.to_thread(ensure_model))
            logger.info("Scheduled ASR model pre-warm in background (device=%s)", dev)
        else:
            logger.info("ASR engine has no _ensure_model callable; skipping warm-up")
    except Exception as _e:
        logger.warning(f"ASR pre-warm skipped: {_e}")

    logger.info("Startup complete. External services (llama, OCR) managed via NSSM.")
```

---

### 0.3 Simplify Shutdown Event

**File:** `Clinical-Note-Generator/server/app.py`

**Lines 221-240: REPLACE** with:
```python
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutdown initiated")
    try:
        from server.routes.ocr import ocr_client
        if hasattr(ocr_client, "close"):
            ocr_client.close()
    except Exception:
        pass
    logger.info("Shutdown complete")
```

---

### 0.4 Verification

**Test startup:**
```bash
cd Clinical-Note-Generator
python -m uvicorn server.app:app --reload
```

**Expected log output:**
- ✅ "Auth/workspace database initialized"
- ✅ "Scheduled ASR model pre-warm in background"
- ✅ "Startup complete. External services managed via NSSM."
- ❌ NO "Scheduled llama-server warm start"
- ❌ NO "Scheduled OCR server restart"

**Test that admin router is gone:**
```bash
curl http://localhost:7860/api/admin/logs/tail
# Should return 404 or "Not Found"
```

---

## Phase 1: Admin Users-Only Page

### 1.1 Backend Routes (Already Correct)

**No changes needed** - `admin_users_router` is already mounted correctly:
- File: `server/app.py` line 112
- Endpoints: `/api/admin/users` (list, approve, reject, delete)

---

### 1.2 Frontend - Replace Admin Page

**File:** `PCHost/web/admin.html`

**BACKUP FIRST:**
```bash
cd PCHost/web
cp admin.html admin.html.backup.$(date +%Y%m%d_%H%M%S)
```

**REPLACE ENTIRE FILE** with users-only version:

**File:** `PCHost/web/admin.html` (REPLACE ALL)

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CNG6 - User Management</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      padding: 20px;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
      background: white;
      border-radius: 12px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.1);
      overflow: hidden;
    }
    .header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 30px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header h1 { font-size: 28px; font-weight: 600; }
    .header .user-info { font-size: 14px; opacity: 0.9; }
    .nav {
      background: #f8f9fa;
      padding: 15px 30px;
      border-bottom: 1px solid #dee2e6;
    }
    .nav a {
      color: #667eea;
      text-decoration: none;
      margin-right: 20px;
      font-weight: 500;
    }
    .nav a:hover { text-decoration: underline; }
    .content { padding: 30px; }
    .section-title {
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 20px;
      color: #333;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }
    th, td {
      padding: 12px;
      text-align: left;
      border-bottom: 1px solid #e9ecef;
    }
    th {
      background: #f8f9fa;
      font-weight: 600;
      color: #495057;
    }
    .badge {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 600;
    }
    .badge-success { background: #d4edda; color: #155724; }
    .badge-warning { background: #fff3cd; color: #856404; }
    .badge-danger { background: #f8d7da; color: #721c24; }
    .btn {
      padding: 6px 12px;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      margin-right: 5px;
    }
    .btn-success { background: #28a745; color: white; }
    .btn-danger { background: #dc3545; color: white; }
    .btn-secondary { background: #6c757d; color: white; }
    .btn:hover { opacity: 0.9; }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .alert {
      padding: 12px 16px;
      border-radius: 6px;
      margin-bottom: 20px;
    }
    .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    .alert-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    .empty-state {
      text-align: center;
      padding: 60px 20px;
      color: #6c757d;
    }
    .logout-btn {
      background: rgba(255,255,255,0.2);
      color: white;
      padding: 8px 16px;
      border: 1px solid rgba(255,255,255,0.3);
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
    }
    .logout-btn:hover { background: rgba(255,255,255,0.3); }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <h1>CNG6 User Management</h1>
        <div class="user-info" id="currentUserEmail"></div>
      </div>
      <button class="logout-btn" onclick="logout()">Logout</button>
    </div>

    <div class="nav">
      <a href="/static/index.html">← Back to Note Generator</a>
    </div>

    <div class="content">
      <div id="alertContainer"></div>

      <div class="section-title">Registered Users</div>
      <table id="usersTable">
        <thead>
          <tr>
            <th>Email</th>
            <th>Status</th>
            <th>Role</th>
            <th>Created</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="usersTableBody">
          <tr>
            <td colspan="5" class="empty-state">Loading users...</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    const API_BASE = '/api';
    let currentUser = null;

    function getAccessToken() {
      return localStorage.getItem('access_token') || '';
    }

    function showAlert(message, type = 'error') {
      const container = document.getElementById('alertContainer');
      const alert = document.createElement('div');
      alert.className = `alert alert-${type}`;
      alert.textContent = message;
      container.innerHTML = '';
      container.appendChild(alert);
      setTimeout(() => alert.remove(), 5000);
    }

    async function fetchCurrentUser() {
      try {
        const resp = await fetch(`${API_BASE}/auth/me`, {
          headers: { 'Authorization': 'Bearer ' + getAccessToken() }
        });
        if (!resp.ok) throw new Error('Not authenticated');
        currentUser = await resp.json();
        document.getElementById('currentUserEmail').textContent = currentUser.email;

        if (!currentUser.is_admin) {
          showAlert('Admin access required', 'error');
          setTimeout(() => window.location.href = '/static/index.html', 2000);
        }
      } catch (e) {
        showAlert('Please log in', 'error');
        setTimeout(() => window.location.href = '/static/index.html', 2000);
      }
    }

    async function loadUsers() {
      try {
        const resp = await fetch(`${API_BASE}/admin/users`, {
          headers: { 'Authorization': 'Bearer ' + getAccessToken() }
        });
        if (!resp.ok) throw new Error('Failed to load users');

        const users = await resp.json();
        renderUsers(users);
      } catch (e) {
        showAlert('Error loading users: ' + e.message, 'error');
      }
    }

    function renderUsers(users) {
      const tbody = document.getElementById('usersTableBody');

      if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No users found</td></tr>';
        return;
      }

      tbody.innerHTML = users.map(user => `
        <tr>
          <td>${user.email}</td>
          <td>
            <span class="badge badge-${user.is_approved ? 'success' : 'warning'}">
              ${user.is_approved ? 'Approved' : 'Pending'}
            </span>
          </td>
          <td>
            <span class="badge badge-${user.is_admin ? 'danger' : 'secondary'}">
              ${user.is_admin ? 'Admin' : 'User'}
            </span>
          </td>
          <td>${new Date(user.created_at).toLocaleDateString()}</td>
          <td>
            ${!user.is_approved ? `
              <button class="btn btn-success" onclick="approveUser('${user.id}')">
                Approve
              </button>
            ` : `
              <button class="btn btn-secondary" onclick="rejectUser('${user.id}')">
                Revoke
              </button>
            `}
            ${!user.is_admin ? `
              <button class="btn btn-danger" onclick="deleteUser('${user.id}')">
                Delete
              </button>
            ` : ''}
          </td>
        </tr>
      `).join('');
    }

    async function approveUser(userId) {
      if (!confirm('Approve this user?')) return;
      try {
        const resp = await fetch(`${API_BASE}/admin/users/${userId}/approve`, {
          method: 'PATCH',
          headers: { 'Authorization': 'Bearer ' + getAccessToken() }
        });
        if (!resp.ok) throw new Error('Approval failed');
        showAlert('User approved', 'success');
        loadUsers();
      } catch (e) {
        showAlert('Error: ' + e.message, 'error');
      }
    }

    async function rejectUser(userId) {
      if (!confirm('Revoke approval for this user?')) return;
      try {
        const resp = await fetch(`${API_BASE}/admin/users/${userId}/reject`, {
          method: 'PATCH',
          headers: { 'Authorization': 'Bearer ' + getAccessToken() }
        });
        if (!resp.ok) throw new Error('Rejection failed');
        showAlert('User approval revoked', 'success');
        loadUsers();
      } catch (e) {
        showAlert('Error: ' + e.message, 'error');
      }
    }

    async function deleteUser(userId) {
      if (!confirm('DELETE this user permanently? This cannot be undone.')) return;
      try {
        const resp = await fetch(`${API_BASE}/admin/users/${userId}`, {
          method: 'DELETE',
          headers: { 'Authorization': 'Bearer ' + getAccessToken() }
        });
        if (!resp.ok) throw new Error('Deletion failed');
        showAlert('User deleted', 'success');
        loadUsers();
      } catch (e) {
        showAlert('Error: ' + e.message, 'error');
      }
    }

    async function logout() {
      try {
        await fetch(`${API_BASE}/auth/logout`, {
          method: 'POST',
          headers: {
            'Authorization': 'Bearer ' + getAccessToken(),
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({})
        });
      } catch (e) {
        console.error('Logout error:', e);
      }
      localStorage.removeItem('access_token');
      window.location.href = '/static/index.html';
    }

    // Initialize
    fetchCurrentUser();
    loadUsers();
  </script>
</body>
</html>
```

---

## Phase 2: Secrets Management

### 2.1 Create .env File

**File:** `Clinical-Note-Generator/.env` (CREATE NEW)

```env
# JWT Authentication Secrets (REQUIRED - replace with strong random values)
JWT_SECRET_KEY=CHANGE-ME-min-32-characters-long-random-string-here
JWT_REFRESH_SECRET_KEY=CHANGE-ME-different-secret-at-least-32-chars

# Admin API Key
ADMIN_API_KEY=notegenadmin-change-this-in-production

# Hugging Face Token (for ASR model downloads)
HF_TOKEN=your-hugging-face-token-here

# Database URL (optional - defaults to sqlite in data/)
# DATABASE_URL=sqlite:///C:/Clinical-Note-Generator/data/user_data.sqlite
```

**Generate secure secrets (PowerShell):**
```powershell
# Run this to generate random secrets:
-join ((65..90) + (97..122) + (48..57) | Get-Random -Count 40 | % {[char]$_})
-join ((65..90) + (97..122) + (48..57) | Get-Random -Count 40 | % {[char]$_})
```

---

### 2.2 Update .gitignore

**File:** `Clinical-Note-Generator/.gitignore`

**ADD** these lines (if not already present):
```gitignore
.env
.env.local
.env.*.local
*.db
*.sqlite
*.sqlite3
__pycache__/
*.py[cod]
*.log
server/logs/
server/temp-audio/
```

---

### 2.3 Load .env Early in Application

**File:** `Clinical-Note-Generator/server/app.py`

**Lines 1-2: INSERT AT VERY TOP** (before all other imports):
```python
from dotenv import load_dotenv
load_dotenv()
```

**After change, top of file should be:**
```python
from dotenv import load_dotenv
load_dotenv()

# C:\Clinical-Note-Generator\server\app.py
# app.py

import os
import sys
...
```

---

### 2.4 Update Config Loader

**File:** `Clinical-Note-Generator/server/core/config.py`

**GOOD NEWS:** This file ALREADY supports environment variables correctly!

**Only change needed:** Rename env var names to match .env

**Lines 44-46: CHANGE** variable names:
```python
# OLD:
jwt_secret = os.environ.get("JWT_SECRET") or cfg.get("jwt_secret")
jwt_refresh = os.environ.get("JWT_REFRESH_SECRET") or cfg.get("jwt_refresh_secret")

# NEW:
jwt_secret = os.environ.get("JWT_SECRET_KEY") or cfg.get("jwt_secret")
jwt_refresh = os.environ.get("JWT_REFRESH_SECRET_KEY") or cfg.get("jwt_refresh_secret")
```

---

### 2.5 Update HF Token Usage

**File:** `Clinical-Note-Generator/server/services/asr_whisperx.py`

**FIND** the line where `hf_token` is loaded (search for `hf_token`):

**REPLACE** with:
```python
import os

# Load HF token from environment first, then config
hf_token = os.getenv("HF_TOKEN", "").strip()
if not hf_token:
    # Fallback to config.json if env var not set
    try:
        from pathlib import Path
        import json
        config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        if config_path.exists():
            with open(config_path, "r") as f:
                cfg = json.load(f)
                hf_token = cfg.get("models", {}).get("hf_token", "").strip()
    except Exception:
        pass
```

---

### 2.6 Install python-dotenv

**File:** `Clinical-Note-Generator/requirements_server.txt`

**ADD** this line:
```
python-dotenv>=1.0.0
```

**Install:**
```bash
cd Clinical-Note-Generator
pip install python-dotenv
```

---

### 2.7 Verification

**Test .env loading:**
```bash
cd Clinical-Note-Generator
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('JWT_SECRET_KEY:', 'LOADED' if os.getenv('JWT_SECRET_KEY') else 'NOT FOUND')"
```

**Test config.py:**
```bash
python -c "from server.core.config import get_settings; s = get_settings(); print('Settings loaded:', s.jwt_secret[:10] + '...')"
```

---

## Phase 3A: Enhanced Q&A (Simple Version - RECOMMENDED)

**✅ SIMPLER APPROACH:** Clean chat-style interface, no external dependencies

**Advantages:**
- Works immediately
- No Caddy, DNS, or Open WebUI needed
- Same-origin, no CORS
- Built-in RAG integration

### 3A.1 Enhanced qa.html

**File:** `PCHost/web/qa.html`

**BACKUP FIRST:**
```bash
cd PCHost/web
cp qa.html qa.html.backup.$(date +%Y%m%d_%H%M%S)
```

**REPLACE ENTIRE FILE:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CNG6 - Medical Q&A</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      padding: 20px;
    }
    .container {
      max-width: 900px;
      margin: 0 auto;
      background: white;
      border-radius: 12px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.1);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      height: calc(100vh - 40px);
    }
    .header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 20px 30px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header h1 { font-size: 24px; font-weight: 600; }
    .header a {
      color: white;
      text-decoration: none;
      font-size: 14px;
      opacity: 0.9;
    }
    .header a:hover { opacity: 1; text-decoration: underline; }
    .chat-container {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      background: #f8f9fa;
    }
    .message {
      margin-bottom: 20px;
      display: flex;
      gap: 12px;
    }
    .message.user {
      justify-content: flex-end;
    }
    .message-content {
      max-width: 70%;
      padding: 12px 16px;
      border-radius: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .message.user .message-content {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      border-bottom-right-radius: 4px;
    }
    .message.assistant .message-content {
      background: white;
      color: #333;
      border: 1px solid #dee2e6;
      border-bottom-left-radius: 4px;
    }
    .message.system .message-content {
      background: #fff3cd;
      color: #856404;
      border: 1px solid #ffeaa7;
      max-width: 100%;
      text-align: center;
      font-size: 14px;
    }
    .input-area {
      padding: 20px;
      background: white;
      border-top: 1px solid #dee2e6;
    }
    .input-container {
      display: flex;
      gap: 10px;
    }
    #questionInput {
      flex: 1;
      padding: 12px 16px;
      border: 1px solid #ced4da;
      border-radius: 8px;
      font-size: 15px;
      font-family: inherit;
    }
    #questionInput:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }
    #sendBtn {
      padding: 12px 24px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    #sendBtn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); }
    #sendBtn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }
    .spinner {
      display: inline-block;
      width: 16px;
      height: 16px;
      border: 2px solid rgba(102, 126, 234, 0.3);
      border-top-color: #667eea;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .references {
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid #e9ecef;
      font-size: 13px;
    }
    .references-title {
      font-weight: 600;
      color: #6c757d;
      margin-bottom: 8px;
    }
    .reference-item {
      padding: 6px 0;
      color: #495057;
    }
    .reference-link {
      color: #667eea;
      text-decoration: none;
    }
    .reference-link:hover { text-decoration: underline; }
    .empty-state {
      text-align: center;
      padding: 60px 20px;
      color: #6c757d;
    }
    .empty-state-icon {
      font-size: 48px;
      margin-bottom: 16px;
      opacity: 0.5;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>💬 Medical Q&A</h1>
      <a href="/static/index.html">← Back to Note Generator</a>
    </div>

    <div class="chat-container" id="chatContainer">
      <div class="empty-state">
        <div class="empty-state-icon">🩺</div>
        <div>Ask a medical question to get evidence-based guidance</div>
      </div>
    </div>

    <div class="input-area">
      <div class="input-container">
        <input
          type="text"
          id="questionInput"
          placeholder="Ask a clinical question..."
          autocomplete="off"
        />
        <button id="sendBtn">Send</button>
      </div>
    </div>
  </div>

  <script>
    const API_BASE = '/api';
    const chatContainer = document.getElementById('chatContainer');
    const questionInput = document.getElementById('questionInput');
    const sendBtn = document.getElementById('sendBtn');

    function getAccessToken() {
      return localStorage.getItem('access_token') || '';
    }

    function addMessage(type, content, references = []) {
      // Remove empty state if present
      const emptyState = chatContainer.querySelector('.empty-state');
      if (emptyState) emptyState.remove();

      const messageDiv = document.createElement('div');
      messageDiv.className = `message ${type}`;

      const contentDiv = document.createElement('div');
      contentDiv.className = 'message-content';
      contentDiv.textContent = content;

      if (references && references.length > 0) {
        const refsDiv = document.createElement('div');
        refsDiv.className = 'references';
        refsDiv.innerHTML = `
          <div class="references-title">📚 References:</div>
          ${references.map((ref, idx) => `
            <div class="reference-item">
              ${idx + 1}. ${ref.title || 'Reference'}
              ${ref.source ? `(${ref.source})` : ''}
              ${ref.year ? ` ${ref.year}` : ''}
              ${ref.link ? `<a href="${ref.link}" class="reference-link" target="_blank">↗</a>` : ''}
            </div>
          `).join('')}
        `;
        contentDiv.appendChild(refsDiv);
      }

      messageDiv.appendChild(contentDiv);
      chatContainer.appendChild(messageDiv);
      chatContainer.scrollTop = chatContainer.scrollHeight;

      return contentDiv;
    }

    async function sendQuestion() {
      const question = questionInput.value.trim();
      if (!question) return;

      // Add user message
      addMessage('user', question);
      questionInput.value = '';
      sendBtn.disabled = true;

      // Add loading message
      const loadingContent = addMessage('assistant', 'Searching evidence and generating answer...');
      loadingContent.innerHTML = '<div class="spinner"></div> Searching evidence and generating answer...';

      try {
        const formData = new FormData();
        formData.append('chart_data', '');
        formData.append('transcription', question);
        formData.append('note_type', 'qa');

        const response = await fetch(`${API_BASE}/generate_stream`, {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + getAccessToken() },
          body: formData
        });

        if (!response.ok) throw new Error('Generation failed');

        const generationId = response.headers.get('X-Generation-Id');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let answer = '';

        // Remove loading message
        loadingContent.parentElement.remove();

        // Create answer message
        const answerContent = addMessage('assistant', '');

        // Stream answer
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (line.includes('__STREAM_END__')) break;
            if (line.trim()) {
              answer += line;
              answerContent.textContent = answer;
            }
          }
        }

        // Fetch references
        if (generationId) {
          try {
            const metaResp = await fetch(`${API_BASE}/generation/${generationId}/meta`, {
              headers: { 'Authorization': 'Bearer ' + getAccessToken() }
            });
            if (metaResp.ok) {
              const meta = await metaResp.json();
              const refs = meta.refs || [];

              if (refs.length > 0) {
                const refsDiv = document.createElement('div');
                refsDiv.className = 'references';
                refsDiv.innerHTML = `
                  <div class="references-title">📚 References:</div>
                  ${refs.slice(0, 5).map((ref, idx) => `
                    <div class="reference-item">
                      ${idx + 1}. ${ref.title || 'Reference'}
                      ${ref.source ? `(${ref.source})` : ''}
                      ${ref.year ? ` ${ref.year}` : ''}
                      ${ref.link ? `<a href="${ref.link}" class="reference-link" target="_blank">↗</a>` : ''}
                    </div>
                  `).join('')}
                `;
                answerContent.appendChild(refsDiv);
              }
            }
          } catch (e) {
            console.error('Failed to load references:', e);
          }
        }

      } catch (error) {
        loadingContent.parentElement.remove();
        addMessage('system', `Error: ${error.message}`);
      } finally {
        sendBtn.disabled = false;
        questionInput.focus();
      }
    }

    sendBtn.addEventListener('click', sendQuestion);
    questionInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendQuestion();
      }
    });

    // Focus input on load
    questionInput.focus();
  </script>
</body>
</html>
```

### 3A.2 Add Q&A Balloon to index.html

**File:** `PCHost/web/index.html`

**FIND** the closing `</body>` tag (near end of file).

**BEFORE `</body>`, INSERT:**

```html
<!-- Medical Q&A Balloon -->
<style>
  #qaSimpleBalloon {
    position: fixed;
    right: 20px;
    bottom: 20px;
    width: 60px;
    height: 60px;
    border-radius: 50%;
    border: none;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 9998;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 28px;
    font-weight: bold;
    transition: all 0.3s;
    text-decoration: none;
  }

  #qaSimpleBalloon:hover {
    transform: scale(1.1);
    box-shadow: 0 6px 20px rgba(0,0,0,0.25);
  }
</style>

<a href="/static/qa.html" id="qaSimpleBalloon" title="Medical Q&A">?</a>
```

---

## Phase 3B: Open WebUI Integration (Advanced - OPTIONAL)

**⚠️ COMPLEXITY WARNING:** Requires Caddy, DNS, Open WebUI service

See full implementation in v2.5 plan Phase 5A if needed. For most users, **Phase 3A is recommended**.

---

## Phase 4: NSSM Services Configuration

All external services managed via NSSM (Windows Service Manager).

### 4.1 Service List

**Required Services:**
1. **CNG6-FastAPI** - Backend API (port 7860)
2. **CNG6-LLM** - LLM inference (port 8080 or configured)
3. **CNG6-OCR** - OCR service (port 8090)

**Optional Services:**
4. **CaddyProxy** - Reverse proxy (only if using Phase 3B)
5. **OpenWebUI** - Open WebUI (only if using Phase 3B)

### 4.2 Example NSSM Commands

**FastAPI Service:**
```powershell
# PowerShell as Administrator
nssm install CNG6-FastAPI "C:\Clinical-Note-Generator\.venv\Scripts\python.exe"
nssm set CNG6-FastAPI AppDirectory "C:\Clinical-Note-Generator"
nssm set CNG6-FastAPI AppParameters "-m uvicorn server.app:app --host 0.0.0.0 --port 7860"
nssm start CNG6-FastAPI
```

**Verify service:**
```powershell
nssm status CNG6-FastAPI
# Should show: SERVICE_RUNNING
```

**View logs:**
```powershell
Get-EventLog -LogName Application -Source CNG6-FastAPI -Newest 20
```

---

## Phase 5: Verification & Testing

### 5.1 Backend Startup Test

```bash
cd Clinical-Note-Generator
python -m uvicorn server.app:app --reload
```

**Expected log output:**
- ✅ "Auth/workspace database initialized"
- ✅ "Scheduled ASR model pre-warm in background"
- ✅ "Startup complete. External services managed via NSSM."
- ❌ NO "Scheduled llama-server warm start"
- ❌ NO "Scheduled OCR server restart"

### 5.2 Admin Router Removed

```bash
curl http://localhost:7860/api/admin/logs/tail
# Expected: 404 Not Found
```

### 5.3 Admin Users Page

1. Navigate to `http://localhost:7860/static/admin.html`
2. Should see clean user management interface
3. No server control buttons
4. Can approve/reject/delete users

### 5.4 Secrets Loading

```bash
# Verify .env is loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('JWT_SECRET_KEY loaded:', bool(os.getenv('JWT_SECRET_KEY')))"
# Expected: True

# Verify config uses env vars
python -c "from server.core.config import get_settings; s = get_settings(); print('Config loaded from env')"
# Should not error
```

### 5.5 Q&A Interface (Phase 3A)

1. Navigate to `/static/qa.html`
2. Ask: "What is the first-line treatment for hypertension?"
3. Expected:
   - Streaming answer appears in chat
   - References displayed below answer
   - Clean chat interface
   - No errors in console

### 5.6 Q&A Balloon

1. Navigate to `/static/index.html`
2. Click purple "?" button in bottom-right
3. Should navigate to qa.html
4. Button should be visible and styled correctly

---

## Implementation Checklist

### Phase 0: Cleanup Auto-Management
- [ ] Remove admin_router imports (app.py lines 91, 114)
- [ ] Remove llama warm-start (app.py lines 191-200)
- [ ] Remove OCR warm-start (app.py lines 202-219)
- [ ] Simplify shutdown_event (app.py lines 221-240)
- [ ] Test startup - verify no auto-management logs
- [ ] Test /api/admin/logs/tail returns 404

### Phase 1: Admin Users-Only
- [ ] Backup current admin.html
- [ ] Replace admin.html with users-only version
- [ ] Navigate to /static/admin.html
- [ ] Verify user management works
- [ ] Test approve/reject/delete functions

### Phase 2: Secrets Management
- [ ] Create .env file with secure secrets
- [ ] Generate random JWT secrets (use PowerShell command)
- [ ] Update .gitignore
- [ ] Add dotenv load to app.py (lines 1-2)
- [ ] Update config.py env var names (lines 44-46)
- [ ] Update asr_whisperx.py for HF_TOKEN
- [ ] Add python-dotenv to requirements_server.txt
- [ ] Install: `pip install python-dotenv`
- [ ] Test: Verify JWT_SECRET_KEY loads from .env
- [ ] Test: Start FastAPI, check for config errors

### Phase 3A: Enhanced Q&A (Simple)
- [ ] Backup current qa.html
- [ ] Replace qa.html with enhanced version
- [ ] Add balloon button to index.html
- [ ] Navigate to /static/qa.html
- [ ] Test: Ask a medical question
- [ ] Verify: Streaming answer works
- [ ] Verify: References display correctly
- [ ] Test: Click balloon button from index.html

### Phase 3B: Open WebUI (Optional - Skip if using 3A)
- [ ] Only proceed if you have Caddy/DNS/Open WebUI ready
- [ ] See v2.5 plan Phase 5A for full steps

### Phase 4: NSSM Services
- [ ] Configure CNG6-FastAPI service
- [ ] Configure CNG6-LLM service
- [ ] Configure CNG6-OCR service
- [ ] Start all services
- [ ] Verify services are running: `nssm status <service>`

### Phase 5: Final Testing
- [ ] Run all verification tests (5.1-5.6)
- [ ] Check logs for errors
- [ ] Test note generation still works
- [ ] Test Q&A functionality
- [ ] Test admin user management
- [ ] Verify no hardcoded secrets in logs

---

## Rollback Plan

If issues occur, rollback in reverse order:

### Phase 2 Rollback (Secrets):
```bash
git checkout HEAD -- Clinical-Note-Generator/server/app.py
git checkout HEAD -- Clinical-Note-Generator/server/core/config.py
# Keep .env file (it's in .gitignore)
```

### Phase 1 Rollback (Admin Page):
```bash
cd PCHost/web
cp admin.html.backup.* admin.html
```

### Phase 0 Rollback (Cleanup):
```bash
git checkout HEAD -- Clinical-Note-Generator/server/app.py
```

---

## Summary

**Total Implementation Time:** ~1.5 hours

**Phases:**
- Phase 0: Remove auto-management (30 min)
- Phase 1: Admin users-only (10 min)
- Phase 2: Secrets in .env (15 min)
- Phase 3A: Enhanced Q&A (15 min) **OR** Phase 3B: Open WebUI (2+ hours)
- Phase 4: NSSM setup (15 min)
- Phase 5: Testing (15 min)

**Recommended Path:** Phases 0, 1, 2, 3A, 4, 5

**Result:**
- Clean startup/shutdown
- Better admin interface
- Secure secrets management
- Enhanced Q&A experience
- All services managed externally via NSSM

---

**Ready to implement?** Start with Phase 0 and proceed sequentially.
