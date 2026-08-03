// C:\PCHost\web\auth_workspace.js
(function () {
  const STORAGE_KEYS = {
    ACCESS: 'auth_access_token',
    API_BASE: 'auth_api_base',
    DRAFT: 'auth_workspace_draft',
  };

  const AuthWorkspace = {
    apiBase: '/api',
    accessToken: sessionStorage.getItem(STORAGE_KEYS.ACCESS) || null,
    user: null,
    workspaceVersion: 0,
    profilePromise: null,
    workspacePromise: null,
    saveTimer: null,
    initialized: false,
    syncTimer: null,
    syncIntervalMs: 7000,
    lastLocalEditAt: 0,
    lastSyncedStateFingerprint: '',
    saveInFlight: false,
    lastSaveFailed: false,
    suppressAutoSaveUntil: 0,
    idleTimer: null,
    idleWarningTimer: null,
    idleTimeoutMs: 60 * 60 * 1000,
    idleTrackingEnabled: false,
    boundIdleReset: null,
    idleWarningVisible: false,
    tokenExpiryMs: null,
    tokenRefreshTimer: null,
    refreshPromise: null,
    refreshLeadMs: 60 * 1000,
    recordingKeepAliveTimer: null,
    deferredSaveTimer: null,
    _dirtyFields: null,
    dirtyFieldKeys: null,
    _pollLastValues: null,
    syncSaveBackoffMs: 0,

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.cacheElements();
      this.restoreApiBaseFromStorage();
      this.bindEvents();
      this.updateApiBaseInput();
      this.updateStatus('Checking session...');

      if (this.accessToken) {
        this.updateTokenMetadata(this.accessToken);
        this.fetchProfile()
          .then((approved) => {
            if (approved) {
              this.loadWorkspace().catch(() => {
                this.updateStatus('Failed to load workspace', 'error');
              });
            } else {
              this.workspaceVersion = 0;
              this.resetEditableStateSync();
              this.updateWorkspaceMeta();
            }
          })
          .catch(() => {
            this.clearSession();
            this.clearUiState();
            this.showAuthForms();
          });
      } else {
        this.tryColdSessionRefresh()
          .then((ok) => {
            if (!ok) {
              this.clearUiState();
              this.showAuthForms();
              return;
            }
            this.updateTokenMetadata(this.accessToken);
            return this.fetchProfile()
              .then((approved) => {
                if (approved) {
                  return this.loadWorkspace().catch(() => {
                    this.updateStatus('Failed to load workspace', 'error');
                  });
                }
                this.workspaceVersion = 0;
                this.resetEditableStateSync();
                this.updateWorkspaceMeta();
              });
          })
          .catch(() => {
            this.clearUiState();
            this.showAuthForms();
          });
      }
    },

    isLoopbackHost(hostname) {
      const h = String(hostname || '').toLowerCase();
      return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1';
    },

    isAntiStompEnabled() {
      return window.SYNC_ANTI_STOMP !== false;
    },

    markDirtyFields(keys) {
      if (!this._dirtyFields) this._dirtyFields = new Set();
      (keys || []).forEach((k) => this._dirtyFields.add(k));
    },

    clearDirtyFields() {
      if (this._dirtyFields) this._dirtyFields.clear();
    },

    isSaveSuppressed() {
      return Date.now() < (this.suppressAutoSaveUntil || 0);
    },

    scheduleDeferredSave() {
      if (this.deferredSaveTimer) return;
      const wait = Math.max(50, (this.suppressAutoSaveUntil || 0) - Date.now() + 50);
      this.deferredSaveTimer = setTimeout(() => {
        this.deferredSaveTimer = null;
        if (!this.isWorkspaceReady()) return;
        this.lastLocalEditAt = Date.now();
        clearTimeout(this.saveTimer);
        this.setSyncPill('warn', 'Syncing…');
        this.saveTimer = setTimeout(() => this.saveWorkspace(), 1000);
      }, wait);
    },

    async tryColdSessionRefresh() {
      if (window.SYNC_AUTH_FETCH === false) return false;
      try {
        const resp = await fetch('/api/auth/refresh', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        });
        if (!resp.ok) return false;
        const data = await resp.json();
        if (!data.access_token) return false;
        this.setAccessToken(data.access_token);
        if (typeof window.writeSettings === 'function') {
          window.writeSettings({ apiKey: data.access_token });
        }
        this.emitAuthChanged(true);
        return true;
      } catch (e) {
        return false;
      }
    },

    flushUnloadSave() {
      if (!this.isWorkspaceReady()) return;
      // Synchronous localStorage write first — survives tab close even when the
      // keepalive network request below is dropped during page teardown.
      this.persistLocalDraft();
      const payload = JSON.stringify({
        state: this.collectWorkspaceState(),
        version: this.workspaceVersion || 1,
      });
      const url = (!this.apiBase || this.apiBase === '/api')
        ? '/api/workspace/'
        : `${String(this.apiBase).replace(/\/+$/, '')}/workspace/`;
      const useBeaconFix = window.SYNC_BEACON_FIX !== false;
      if (useBeaconFix && this.accessToken && payload.length <= 64000) {
        try {
          fetch(url, {
            method: 'PUT',
            headers: {
              Authorization: `Bearer ${this.accessToken}`,
              'Content-Type': 'application/json',
            },
            body: payload,
            keepalive: true,
            credentials: 'include',
          });
          return;
        } catch (e) {
          console.warn('[Auth] keepalive workspace save failed:', e);
        }
      }
      if (!useBeaconFix) {
        try {
          navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
        } catch (e) {}
      }
    },

    restoreApiBaseFromStorage() {
      try {
        const saved = localStorage.getItem(STORAGE_KEYS.API_BASE);
        if (!saved) return;
        const t = saved.trim().replace(/\/+$/, '');
        if (!t || t === '/api') return;
        if (!/^https?:\/\//i.test(t)) return;
        let host = '';
        try {
          host = new URL(t).hostname;
        } catch (e) {
          return;
        }
        const localPage = this.isLoopbackHost(window.location.hostname);
        if (this.isLoopbackHost(host) && !localPage) {
          localStorage.removeItem(STORAGE_KEYS.API_BASE);
          this.apiBase = '/api';
          if (this.apiBaseInput) this.apiBaseInput.value = '/api';
          try {
            if (typeof showToast === 'function') {
              showToast('Connection', 'Using default /api (cleared stored loopback address).', 'info');
            }
          } catch (_) {}
          return;
        }
        this.apiBase = t;
        if (this.apiBaseInput) this.apiBaseInput.value = t;
      } catch (e) {}
    },

    cacheElements() {
      this.card = document.getElementById('authCard');
      this.statusEl = document.getElementById('authStatus');
      this.formsWrapper = document.getElementById('authForms');
      this.actionsWrapper = document.getElementById('authActions');
      this.logoutBtn = document.getElementById('logoutBtn');
      this.clearBtn = document.getElementById('clearWorkspaceBtn');
      this.loginForm = document.getElementById('loginForm');
      this.registerForm = document.getElementById('registerForm');
      this.apiBaseInput = document.getElementById('authApiBase');
      this.workspaceMeta = document.getElementById('workspaceMeta');
      this.syncPill = document.getElementById('syncPill');
      this.syncPillText = document.getElementById('syncPillText');
      this.diagWorkspaceVersion = document.getElementById('diagWorkspaceVersion');
      this.diagSyncStatus = document.getElementById('diagSyncStatus');
      this.loginError = document.getElementById('loginError');
      this.registerInfo = document.getElementById('registerInfo');
      this.approvalNotice = document.getElementById('approvalNotice');
      this.toggleLink = document.getElementById('toggleRegisterLink');
    },

    bindEvents() {
      if (this.loginForm) {
        this.loginForm.addEventListener('submit', (e) => {
          e.preventDefault();
          this.login();
        });
      }
      if (this.registerForm) {
        this.registerForm.addEventListener('submit', (e) => {
          e.preventDefault();
          this.register();
        });
      }
      if (this.logoutBtn) {
        this.logoutBtn.addEventListener('click', () => this.logout());
      }
      if (this.clearBtn) {
        this.clearBtn.addEventListener('click', () => this.clearWorkspace());
      }
      if (this.toggleLink) {
        this.toggleLink.addEventListener('click', (e) => {
          e.preventDefault();
          this.toggleRegisterForm();
        });
      }

      // Forgot password link
      const forgotLink = document.getElementById('forgotPasswordLink');
      if (forgotLink) {
        forgotLink.addEventListener('click', (e) => {
          e.preventDefault();
          this.showForgotPasswordModal();
        });
      }

      // Forgot password modal buttons
      const forgotSubmit = document.getElementById('forgotPasswordSubmit');
      const forgotCancel = document.getElementById('forgotPasswordCancel');
      if (forgotSubmit) {
        forgotSubmit.addEventListener('click', () => this.requestPasswordReset());
      }
      if (forgotCancel) {
        forgotCancel.addEventListener('click', () => this.hideForgotPasswordModal());
      }

      const adminOptsBtn = document.getElementById('toggleAdminLoginOptions');
      const adminOptsWrap = document.getElementById('adminLoginOptionsWrap');
      if (adminOptsBtn && adminOptsWrap) {
        adminOptsBtn.addEventListener('click', () => {
          const nowClosed = adminOptsWrap.classList.toggle('hidden');
          adminOptsBtn.setAttribute('aria-expanded', nowClosed ? 'false' : 'true');
        });
      }
      if (this.apiBaseInput) {
        this.apiBaseInput.addEventListener('change', () => {
          const value = this.apiBaseInput.value.trim() || '/api';
          this.apiBase = value === '/api' ? '/api' : value.replace(/\/+$/, '');
          localStorage.setItem(STORAGE_KEYS.API_BASE, this.apiBase);
        });
      }

      // Monitor these fields for ANY changes (typing, paste, programmatic updates)
      // GUARD: Only monitor fields on main page to prevent unnecessary processing
      if (window.WORKSPACE_PAGE_TYPE === 'main') {
        const fields = [
          'generatedNote',
          'transcriptionData',
          'transcriptionDisplay',
          // V7 API: New 3-field system
          'oldVisitsData',
          'mixedOtherData',
          'userSpeciality',
          // Encounter-specific (persisted per encounter)
          'encounterInstructionsInput',
          // Legacy: Keep chartData for backward compatibility
          'chartData',
        ];

        // Store last known values
        const lastValues = {};

        const FIELD_DIRTY_KEYS = {
          generatedNote: ['draft', 'generatedNote'],
          transcriptionDisplay: ['transcription'],
          transcriptionData: ['currentEncounter'],
          oldVisitsData: ['oldVisits', 'chart'],
          mixedOtherData: ['mixedOther'],
          userSpeciality: ['userSpeciality'],
          encounterInstructionsInput: ['encounterInstructions'],
          chartData: ['chart'],
        };

        fields.forEach((id) => {
          const el = document.getElementById(id);
          if (!el) return;

          // Store initial value
          lastValues[id] = el.value;

          // Listen to user input (typing, paste, etc.) - saves immediately
          el.addEventListener('input', () => {
            this.markDirtyFields(FIELD_DIRTY_KEYS[id] || []);
            this.queueSave();
          });
        });

        // Poll for programmatic changes every 500ms
        setInterval(() => {
          if (!this.isWorkspaceReady()) return;

          let changed = false;
          fields.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;

            if (el.value !== lastValues[id]) {
              if (!this.isSaveSuppressed()) {
                lastValues[id] = el.value;
                this.markDirtyFields(FIELD_DIRTY_KEYS[id] || []);
                changed = true;
              }
            }
          });

          if (changed) {
            this.queueSave();
          }
        }, 500);
      }

      if (window.saveCustomPrompt) {
        const original = window.saveCustomPrompt;
        window.saveCustomPrompt = (...args) => {
          const result = original.apply(this, args);
          this.queueSave();
          return result;
        };
      }
      if (window.saveSettings) {
        const originalSaveSettings = window.saveSettings;
        window.saveSettings = (...args) => {
          const result = originalSaveSettings.apply(this, args);
          this.queueSave();
          return result;
        };
      }

      // Save workspace before page unload (navigation, close tab, refresh)
      window.addEventListener('beforeunload', () => {
        if (window.WORKSPACE_PAGE_TYPE !== 'main') return;
        clearTimeout(this.saveTimer);
        this.flushUnloadSave();
      });

      window.addEventListener('pagehide', () => {
        if (window.WORKSPACE_PAGE_TYPE !== 'main') return;
        clearTimeout(this.saveTimer);
        this.flushUnloadSave();
      });

      // Save when tab becomes hidden (user switches tabs or minimizes browser)
      document.addEventListener('visibilitychange', () => {
        // GUARD: Only save workspace on main page
        if (window.WORKSPACE_PAGE_TYPE !== 'main') return;

        if (document.hidden && this.isWorkspaceReady()) {
          clearTimeout(this.saveTimer);
          this.saveWorkspace();
        } else if (!document.hidden && this.isWorkspaceReady()) {
          // Never force-pull over unsaved local edits (bad connection / cross-tab race).
          if (!this.hasUnsavedLocalEdits()) {
            this.pullWorkspaceIfNewer(false).catch(() => {});
          } else {
            this.queueSave();
          }
        }
      });
    },

    updateStatus(text, type = 'info') {
      if (!this.statusEl) return;
      this.statusEl.textContent = text;
      this.statusEl.className = `auth-status ${type}`;
    },

    showAuthForms() {
      if (this.formsWrapper) this.formsWrapper.classList.remove('hidden');
      if (this.actionsWrapper) this.actionsWrapper.classList.add('hidden');
      if (this.clearBtn) this.clearBtn.disabled = false;
      this.hideApprovalNotice();
      this.updateStatus('Please sign in to sync your workspace', 'warning');
      if (this.loginForm) this.loginForm.classList.remove('hidden');
      if (this.registerForm) this.registerForm.classList.add('hidden');
      if (this.toggleLink) this.toggleLink.textContent = 'or Register Now';
      if (this.card) this.card.classList.remove('authenticated');
      document.body.classList.remove('auth-ready');
      const apiSection = document.getElementById('apiSection');
      if (apiSection) apiSection.classList.remove('hidden');
      this.disableIdleTracking();

      // Preserve UI state on sign-out; workspace data should persist until explicitly cleared.
    },

    showAuthActions(profile) {
      if (this.formsWrapper) this.formsWrapper.classList.add('hidden');
      if (this.actionsWrapper) this.actionsWrapper.classList.remove('hidden');
      if (this.card) this.card.classList.add('authenticated');
      document.body.classList.add('auth-ready');
      const name = profile?.email || 'User';
      this.updateStatus(`Signed in as ${name}`, 'success');

      // Clear / close encounter: Encounters panel + "Close encounter" (P6).
      if (this.clearBtn) {
        this.clearBtn.style.display = 'none';
      }

      const apiSection = document.getElementById('apiSection');
      if (apiSection) apiSection.classList.add('hidden');
      const apiKeyInput = document.getElementById('apiKey');
      if (apiKeyInput) apiKeyInput.value = 'Authenticated via workspace';
      this.enableIdleTracking();
    },

    showPendingApproval(profile) {
      if (this.formsWrapper) this.formsWrapper.classList.add('hidden');
      if (this.actionsWrapper) this.actionsWrapper.classList.remove('hidden');
      if (this.clearBtn) this.clearBtn.disabled = true;
      const name = profile?.email || 'User';
      this.updateStatus(`Awaiting admin approval for ${name}`, 'warning');
      this.showApprovalNotice('Your registration is awaiting admin approval. We will notify you once it is ready.');
      if (this.workspaceMeta) {
        this.workspaceMeta.textContent = 'Workspace unavailable until approval';
      }
      this.workspaceVersion = 0;
      this.resetEditableStateSync();
      this.updateWorkspaceMeta();
      const apiSection = document.getElementById('apiSection');
      if (apiSection) apiSection.classList.add('hidden');
    },

    showApprovalNotice(message) {
      if (!this.approvalNotice) return;
      this.approvalNotice.textContent = message;
      this.approvalNotice.className = 'register-info warning';
      this.approvalNotice.style.display = 'block';
    },

    hideApprovalNotice() {
      if (!this.approvalNotice) return;
      this.approvalNotice.textContent = '';
      this.approvalNotice.style.display = 'none';
      this.approvalNotice.className = 'register-info';
    },

    updateWorkspaceMeta() {
      const v = this.workspaceVersion ? `v${this.workspaceVersion}` : '--';
      if (this.workspaceMeta) {
        // keep hidden in main UI; retained for backwards compat
        this.workspaceMeta.textContent = this.workspaceVersion ? `Workspace version ${this.workspaceVersion}` : 'Workspace: not synced';
      }
      if (this.diagWorkspaceVersion) {
        this.diagWorkspaceVersion.textContent = v;
      }
    },

    setSyncPill(level, text) {
      if (this.syncPill) {
        this.syncPill.classList.remove('ok', 'warn', 'bad');
        if (level) this.syncPill.classList.add(level);
      }
      if (this.syncPillText) {
        this.syncPillText.textContent = text || 'Sync: --';
      }
      if (this.diagSyncStatus) {
        this.diagSyncStatus.textContent = text || '--';
      }
    },

    toggleRegisterForm() {
      if (!this.loginForm || !this.registerForm || !this.toggleLink) return;
      const showingRegister = !this.registerForm.classList.contains('hidden');
      if (showingRegister) {
        this.registerForm.classList.add('hidden');
        this.loginForm.classList.remove('hidden');
        this.toggleLink.textContent = 'or Register Now';
        this.showRegisterInfo('', 'info');
      } else {
        this.loginForm.classList.add('hidden');
        this.registerForm.classList.remove('hidden');
        this.toggleLink.textContent = 'or Sign In';
        this.showLoginError('');
      }
    },

    showForgotPasswordModal() {
      const modal = document.getElementById('forgotPasswordModal');
      const emailInput = document.getElementById('forgotPasswordEmail');
      const errorDiv = document.getElementById('forgotPasswordError');
      if (modal) {
        modal.style.display = 'flex';
        modal.style.alignItems = 'center';
        modal.style.justifyContent = 'center';
        modal.style.position = 'fixed';
        modal.style.top = '0';
        modal.style.left = '0';
        modal.style.width = '100%';
        modal.style.height = '100%';
        modal.style.background = 'rgba(0,0,0,0.5)';
        modal.style.zIndex = '10000';
        if (emailInput) {
          emailInput.value = '';
          emailInput.focus();
        }
        if (errorDiv) {
          errorDiv.style.display = 'none';
        }
      }
    },

    hideForgotPasswordModal() {
      const modal = document.getElementById('forgotPasswordModal');
      if (modal) {
        modal.style.display = 'none';
      }
    },

    async requestPasswordReset() {
      const emailInput = document.getElementById('forgotPasswordEmail');
      const errorDiv = document.getElementById('forgotPasswordError');
      const submitBtn = document.getElementById('forgotPasswordSubmit');
      if (!emailInput) return;

      const email = emailInput.value.trim();
      if (!email) {
        if (errorDiv) {
          errorDiv.textContent = 'Please enter your email address.';
          errorDiv.style.display = 'block';
        }
        return;
      }

      // Disable button while processing
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Sending...';
      }

      try {
        const response = await fetch(`${this.apiBase}/auth/forgot-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email })
        });

        const data = await response.json();

        if (response.ok) {
          // Always show success message (even if user doesn't exist, for security)
          if (errorDiv) {
            errorDiv.textContent = data.message || 'If an account exists with that email, a reset link has been sent.';
            errorDiv.style.display = 'block';
            errorDiv.style.color = '#16a34a';
          }
          this.hideForgotPasswordModal();
        } else {
          if (errorDiv) {
            errorDiv.textContent = data.detail || 'Something went wrong. Please try again.';
            errorDiv.style.display = 'block';
            errorDiv.style.color = '#dc2626';
          }
        }
      } catch (err) {
        if (errorDiv) {
          errorDiv.textContent = 'Network error. Please check your connection and try again.';
          errorDiv.style.display = 'block';
          errorDiv.style.color = '#dc2626';
        }
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Send Reset Link';
        }
      }
    },

    setAccessToken(token) {
      this.accessToken = token;
      if (token) {
        sessionStorage.setItem(STORAGE_KEYS.ACCESS, token);
        // Ensure app.settings object exists and update apiKey immediately
        if (!window.app) window.app = {};
        if (!window.app.settings) window.app.settings = {};
        window.app.settings.apiKey = token;
        this.updateTokenMetadata(token);
      } else {
        sessionStorage.removeItem(STORAGE_KEYS.ACCESS);
        if (window.app && window.app.settings) {
          window.app.settings.apiKey = '';
        }
        this.tokenExpiryMs = null;
        this.clearTokenRefreshTimer();
      }
    },

    clearSession() {
      this.setAccessToken(null);
      this.stopWorkspaceSyncLoop();
      this.tokenExpiryMs = null;
      this.clearTokenRefreshTimer();
      this.refreshPromise = null;
      this.workspaceVersion = 0;
      this.resetEditableStateSync();
      this.clearLocalDraft();
      this.user = null;
      this.setSyncPill('bad', 'Signed out');
      this.profilePromise = null;
      this.workspacePromise = null;
      this.updateWorkspaceMeta();
      this.emitAuthChanged(false);
    },

    async login() {
      if (!this.loginForm) return;
      const emailInput = this.loginForm.querySelector('input[name="email"]');
      const passwordInput = this.loginForm.querySelector('input[name="password"]');
      const email = emailInput ? emailInput.value.trim() : '';
      const password = passwordInput ? passwordInput.value.trim() : '';
      if (!email || !password) {
        this.showLoginError('Email and password required');
        if (passwordInput) passwordInput.value = '';
        return;
      }
      if (passwordInput) passwordInput.value = '';
      this.showLoginError('');
      try {
        const resp = await this.request('/api/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        }, true);
        if (!resp.ok) {
          const raw = await resp.text();
          const msg =
            typeof formatApiErrorMessage === 'function'
              ? formatApiErrorMessage(raw, { httpStatus: resp.status })
              : raw || 'Login failed';
          throw new Error(msg);
        }
        const data = await resp.json();
        const adminRedirect = document.getElementById('adminLoginRedirect');
        if (adminRedirect && adminRedirect.checked) {
          try {
            sessionStorage.setItem('admin_workspace_token', data.access_token);
          } catch (e) {}
          // No token in the URL (it leaks into server access logs, proxy
          // logs, and browser history) -- admin.html's own bootstrap script
          // picks the token up from sessionStorage and fetches with it in
          // an Authorization header instead.
          window.location.href = 'admin.html';
          return;
        }
        this.setAccessToken(data.access_token);
        this.emitAuthChanged(true);
        
        // Explicitly initialize app.settings to ensure getAuthToken() works immediately
        if (typeof window.writeSettings === 'function') {
          window.writeSettings({ apiKey: data.access_token });
        }
        
        const approved = await this.fetchProfile();
        if (approved) {
          await this.loadWorkspace();
        } else {
          this.workspaceVersion = 0;
          this.resetEditableStateSync();
          this.updateWorkspaceMeta();
        }
      } catch (err) {
        this.showLoginError(err.message || 'Login failed');
      }
    },

    async register() {
      if (!this.registerForm) return;
      const emailInput = this.registerForm.querySelector('input[name="reg_email"]');
      const passwordInput = this.registerForm.querySelector('input[name="reg_password"]');
      const email = emailInput ? emailInput.value.trim() : '';
      const password = passwordInput ? passwordInput.value.trim() : '';
      if (!email || !password) {
        this.showRegisterInfo('Email and password required', 'error');
        if (passwordInput) passwordInput.value = '';
        return;
      }
      if (passwordInput) passwordInput.value = '';
      this.showRegisterInfo('Submitting registration...');
      try {
        const resp = await this.request('/api/auth/register', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        }, true);
        if (!resp.ok) {
          const raw = await resp.text();
          const msg =
            typeof formatApiErrorMessage === 'function'
              ? formatApiErrorMessage(raw, { httpStatus: resp.status })
              : raw || 'Registration failed';
          throw new Error(msg);
        }
        this.showRegisterInfo('Registration submitted. Await admin approval.', 'success');
      } catch (err) {
        this.showRegisterInfo(err.message || 'Registration failed', 'error');
      }
    },

    showLoginError(message) {
      if (!this.loginError) return;
      this.loginError.textContent = message;
      this.loginError.style.display = message ? 'block' : 'none';
    },

    showRegisterInfo(message, type = 'info') {
      if (!this.registerInfo) return;
      this.registerInfo.textContent = message;
      this.registerInfo.className = `register-info ${type}`;
      this.registerInfo.style.display = message ? 'block' : 'none';
    },

    async fetchProfile() {
      if (this.profilePromise) {
        return this.profilePromise;
      }
      this.profilePromise = (async () => {
        const resp = await this.request('/api/auth/me');
        if (!resp.ok) {
          throw new Error('Session expired');
        }
        const profile = await resp.json();
        this.user = profile;
        if (!profile.is_approved) {
          this.showPendingApproval(profile);
          return false;
        }
        this.hideApprovalNotice();
        this.showAuthActions(profile);
        return true;
      })();
      try {
        return await this.profilePromise;
      } finally {
        this.profilePromise = null;
      }
    },

    isClinicalBusy() {
      if (this.isAudioRecordingActive()) return true;
      if (window.app && window.app.isStreaming) return true;
      if (window.__cngOcrBusy) return true;
      const dp = document.getElementById('documentProgress');
      if (dp && !dp.classList.contains('hidden')) return true;
      const ap = document.getElementById('audioProgress');
      if (ap && !ap.classList.contains('hidden')) return true;
      return false;
    },

    isAudioRecordingActive() {
      try {
        const ph = window.app?.audioPipelinePhase;
        if (ph === 'recording' || ph === 'stopping' || ph === 'transcribing' || ph === 'generating_note') {
          return true;
        }
        if (window.app?.audioHandler && typeof window.app.audioHandler.isRecording === 'boolean') {
          return window.app.audioHandler.isRecording;
        }
        if (window.universalAudio) {
          if (window.universalAudio.isRecording) return true;
          if (window.universalAudio.isListening) return true;
          if (window.universalAudio.shouldKeepListening) return true;
          if (window.universalAudio.shouldKeepRecording) return true;
          if (window.universalAudio.dictationRecorder && window.universalAudio.dictationRecorder.state === 'recording') return true;
          if (window.universalAudio.mediaRecorder && window.universalAudio.mediaRecorder.state === 'recording') return true;
        }
        if (typeof window.isRecording === 'boolean') return window.isRecording;
        if (typeof window.isRecordingAudio === 'boolean') return window.isRecordingAudio;
      } catch (e) {
        console.warn('[Auth] Unable to read recording state:', e);
      }
      return false;
    },

    isWorkspaceReady() {
      return Boolean(this.user && this.user.is_approved);
    },

    async loadWorkspace() {
      if (!this.isWorkspaceReady()) return false;
      if (this.workspacePromise) {
        return this.workspacePromise;
      }
      this.workspacePromise = (async () => {
        const resp = await this.request('/api/workspace/');
        if (!resp.ok) {
          throw new Error('Failed to load workspace');
        }
        const data = await resp.json();
        this.workspaceVersion = data.version;
        this.applyWorkspaceState(data.state || {}, { force: true });
        // Recover any unsaved edits that an abrupt close/crash left in localStorage
        // for this same user+encounter (pushes them back to the server if newer).
        try { this.recoverLocalDraftIfAny(); } catch (e) {}
        this.updateWorkspaceMeta();
        this.setSyncPill('ok', 'Synced');
        this.startWorkspaceSyncLoop();
        try {
          window.dispatchEvent(new CustomEvent('encounters-refresh'));
        } catch (e) {}
        return true;
      })();
      try {
        return await this.workspacePromise;
      } finally {
        this.workspacePromise = null;
      }
    },

    startWorkspaceSyncLoop() {
      if (this.syncTimer) return;
      this.syncTimer = setInterval(() => {
        this.pullWorkspaceIfNewer().catch((err) => {
          this.setSyncPill('warn', 'Sync issue');
          console.warn('[WorkspaceSync] pull failed:', err?.message || err);
        });
      }, this.syncIntervalMs);
    },

    stopWorkspaceSyncLoop() {
      if (this.syncTimer) {
        clearInterval(this.syncTimer);
        this.syncTimer = null;
      }
    },

    computeEditableStateFingerprint() {
      const state = this.collectWorkspaceState();
      const extras = state.extras || {};
      return JSON.stringify({
        draft: state.draft || '',
        transcription: extras.transcription || '',
        currentEncounter: extras.currentEncounter || '',
        oldVisits: extras.oldVisits || '',
        mixedOther: extras.mixedOther || '',
        userSpeciality: extras.userSpeciality || '',
        encounterInstructions: extras.encounterInstructions || '',
        chart: extras.chart || '',
      });
    },

    markEditableStateSynced() {
      this.lastSyncedStateFingerprint = this.computeEditableStateFingerprint();
      this.lastSaveFailed = false;
    },

    resetEditableStateSync() {
      this.lastSyncedStateFingerprint = '';
      this.lastSaveFailed = false;
      this.saveInFlight = false;
      this.clearDirtyFields();
    },

    hasUnsavedLocalEdits() {
      if (this.saveTimer || this.saveInFlight) return true;
      if (this.lastSaveFailed) return true;
      if (!this.lastSyncedStateFingerprint) return false;
      // Compare current editable state fingerprint against the last successfully synced one.
      // lastLocalEditAt is used separately (line 1078) as a 3s recency guard to prevent
      // overwriting in-flight edits during sync — it is NOT part of this dirty check.
      return this.computeEditableStateFingerprint() !== this.lastSyncedStateFingerprint;
    },

    // --- Local draft durability (G5a hardening) -------------------------------
    // Mirrors unsaved editable state to localStorage so edits survive an abrupt
    // tab close (when the keepalive network save may be dropped) or a crash, and
    // can be recovered by a reopened tab or another tab on the same origin.
    _localDraftEnabled() {
      return window.SYNC_LOCAL_DRAFT !== false;
    },

    _draftIdentity() {
      const uid = (this.user && (this.user.id || this.user.sub))
        ? String(this.user.id || this.user.sub) : '';
      let enc = '';
      try {
        enc = (window.app && window.app.activeEncounterId)
          ? String(window.app.activeEncounterId) : '';
      } catch (e) {}
      return { uid, enc };
    },

    persistLocalDraft() {
      if (!this._localDraftEnabled() || !this.isWorkspaceReady()) return;
      try {
        const { uid, enc } = this._draftIdentity();
        if (!uid) return;
        const record = {
          uid,
          enc,
          version: this.workspaceVersion || 0,
          savedAt: Date.now(),
          fingerprint: this.computeEditableStateFingerprint(),
          state: this.collectWorkspaceState(),
        };
        localStorage.setItem(STORAGE_KEYS.DRAFT, JSON.stringify(record));
      } catch (e) {}
    },

    clearLocalDraft() {
      try { localStorage.removeItem(STORAGE_KEYS.DRAFT); } catch (e) {}
    },

    // Restore a same-user/same-encounter draft that holds edits newer than the
    // freshly-loaded server state, then push it through the normal (anti-stomp)
    // save path. Called once on initial workspace load only.
    recoverLocalDraftIfAny() {
      if (!this._localDraftEnabled() || !this.isWorkspaceReady()) return false;
      let record = null;
      try {
        const raw = localStorage.getItem(STORAGE_KEYS.DRAFT);
        if (!raw) return false;
        record = JSON.parse(raw);
      } catch (e) { this.clearLocalDraft(); return false; }
      if (!record || typeof record !== 'object') { this.clearLocalDraft(); return false; }

      const { uid, enc } = this._draftIdentity();
      // Only recover for the same user and the same active encounter.
      if (!uid || String(record.uid || '') !== uid ||
          String(record.enc || '') !== String(enc || '')) {
        return false;
      }
      // Stale guard: drop drafts older than 24h.
      if (!record.savedAt || (Date.now() - Number(record.savedAt)) > 24 * 3600 * 1000) {
        this.clearLocalDraft();
        return false;
      }
      // Non-clobber guard: only recover when the server is still at the version the
      // draft was based on. If another device advanced the server since, do NOT
      // auto-apply "local wins" over that newer state — drop the draft instead.
      if (Number(record.version || 0) !== Number(this.workspaceVersion || 0)) {
        this.clearLocalDraft();
        return false;
      }
      // Only recover genuine unsaved edits relative to the just-applied server state.
      const serverFingerprint = this.computeEditableStateFingerprint();
      if (!record.fingerprint || record.fingerprint === serverFingerprint) {
        this.clearLocalDraft();
        return false;
      }
      try {
        // force: the live state we are overriding is the server snapshot we just
        // loaded (no unsaved edits yet), so this never clobbers in-progress typing.
        const applied = this.applyWorkspaceState(record.state || {}, { force: true });
        if (applied === false) return false;
      } catch (e) { return false; }
      // Push recovered edits to the server (clears the draft on confirmed save).
      this.lastLocalEditAt = Date.now();
      this.saveWorkspace().catch((err) => {
        console.warn('[WorkspaceSync] draft recovery save failed:', err?.message || err);
      });
      try {
        if (typeof showToast === 'function') {
          showToast('Recovered unsaved edits', 'Restored note edits that were not yet synced.', 'info');
        }
      } catch (e) {}
      return true;
    },

    mergeLocalEditableIntoServerState(serverState) {
      const local = this.collectWorkspaceState();
      const merged = { ...(serverState || {}) };
      const serverExtras = serverState?.extras || {};
      const localExtras = local.extras || {};
      merged.extras = { ...serverExtras };
      const dirty = this._dirtyFields;
      const isDirty = (key) => !dirty || dirty.size === 0 || dirty.has(key);

      if (isDirty('draft') || isDirty('generatedNote')) {
        merged.draft = local.draft;
        merged.extras.generatedNote = localExtras.generatedNote;
      }
      for (const key of ['oldVisits', 'mixedOther', 'userSpeciality', 'encounterInstructions', 'chart']) {
        if (isDirty(key)) {
          merged.extras[key] = localExtras[key];
        }
      }
      const localTx = String(localExtras.transcription || '').trim();
      const serverTx = String(serverExtras.transcription || '').trim();
      if (isDirty('transcription')) {
        merged.extras.transcription = localExtras.transcription;
        if (isDirty('currentEncounter')) {
          merged.extras.currentEncounter = localExtras.currentEncounter;
        }
      } else if (serverTx && (!localTx || serverTx.length >= localTx.length)) {
        merged.extras.transcription = serverExtras.transcription;
        merged.extras.currentEncounter = serverExtras.currentEncounter;
        if (serverExtras.lastAsrJobId) {
          merged.extras.lastAsrJobId = serverExtras.lastAsrJobId;
        }
      } else if (localTx) {
        merged.extras.transcription = localExtras.transcription;
      }
      if (isDirty('currentEncounter')) {
        merged.extras.currentEncounter = localExtras.currentEncounter;
      }
      if (window.app && window.app.uiState) {
        merged.extras.ui = { ...(merged.extras.ui || {}), ...(window.app.uiState || {}) };
      }
      return merged;
    },

    async pullWorkspaceIfNewer(force = false) {
      if (!this.isWorkspaceReady()) return;
      let serverVersion = 0;
      const verResp = await this.request('/api/workspace/version');
      if (verResp.ok) {
        const verData = await verResp.json();
        serverVersion = Number(verData.version || 0);
      } else {
        const resp = await this.request('/api/workspace/');
        if (!resp.ok) return;
        const data = await resp.json();
        serverVersion = Number(data.version || 0);
      }
      const localVersion = Number(this.workspaceVersion || 0);
      if (!serverVersion || serverVersion <= localVersion) return;

      if (this.hasUnsavedLocalEdits()) {
        this.setSyncPill('warn', 'Not synced');
        if (!this.saveInFlight && !this.saveTimer) {
          this.saveWorkspace().catch((err) => {
            console.warn('[WorkspaceSync] retry save failed:', err?.message || err);
          });
        }
        return;
      }

      const recentlyEdited = Date.now() - Number(this.lastLocalEditAt || 0) < 3000;
      if (!force && recentlyEdited) return;

      const resp = await this.request('/api/workspace/');
      if (!resp.ok) return;
      const data = await resp.json();
      this.workspaceVersion = Number(data.version || serverVersion);
      this.applyWorkspaceState(data.state || {}, { force: false });
      this.updateWorkspaceMeta();
    },

    applyWorkspaceState(state, options = {}) {
      if (this.isAntiStompEnabled() && !options.force && this.hasUnsavedLocalEdits()) {
        console.warn('[WorkspaceSync] skipped applyWorkspaceState — unsaved local edits');
        return false;
      }
      if (this.saveTimer) {
        clearTimeout(this.saveTimer);
        this.saveTimer = null;
      }
      this.suppressAutoSaveUntil = Date.now() + 2500;
      const noteEl = document.getElementById('generatedNote');
      const extras = state.extras || {};
      // P5/P6: active encounter pointer (used for per-encounter queue UI)
      let encounterChanged = false;
      try {
        if (window.app && typeof extras.activeEncounterId === 'string') {
          const prev = window.app.activeEncounterId ? String(window.app.activeEncounterId) : '';
          const next = String(extras.activeEncounterId || '');
          if (prev !== next) {
            encounterChanged = true;
          }
          window.app.activeEncounterId = extras.activeEncounterId;
        }
      } catch (e) {}
      if (noteEl) {
        // Always derive from server: missing keys mean empty (per-encounter state must not leave stale DOM).
        let noteVal = '';
        if (typeof state.draft === 'string') {
          noteVal = state.draft;
        } else if (typeof extras.generatedNote === 'string') {
          noteVal = extras.generatedNote;
        }
        if (typeof window.applyConflictsSplitFromFullNote === 'function') {
          window.applyConflictsSplitFromFullNote(noteVal);
        } else {
          noteEl.value = noteVal;
        }
      }
      const transEl = document.getElementById('transcriptionData');
      const transDisplayEl = document.getElementById('transcriptionDisplay');
      if (transEl) {
        const transcription = typeof extras.transcription === 'string' ? extras.transcription : '';
        const currentEncounter = typeof extras.currentEncounter === 'string' ? extras.currentEncounter : '';
        // Apply server state exactly (including empty string) for deterministic cross-device sync.
        transEl.value = currentEncounter;
        if (transDisplayEl) {
          transDisplayEl.value = transcription;
        }
      }

      // V7 API: Restore old visits field
      const oldVisitsEl = document.getElementById('oldVisitsData');
      if (oldVisitsEl) {
        if (typeof extras.oldVisits === 'string') {
          oldVisitsEl.value = extras.oldVisits;
        } else if (typeof extras.chart === 'string') {
          oldVisitsEl.value = extras.chart;
        } else {
          oldVisitsEl.value = '';
        }
      }

      // V7 API: Restore mixed other field
      const mixedOtherEl = document.getElementById('mixedOtherData');
      const specialityEl = document.getElementById('userSpeciality');
      if (mixedOtherEl) {
        mixedOtherEl.value = typeof extras.mixedOther === 'string' ? extras.mixedOther : '';
      }
      if (specialityEl) {
        specialityEl.value = typeof extras.userSpeciality === 'string' ? extras.userSpeciality : '';
      }
      // Legacy: Also update chartData if it exists (for backward compatibility)
      const chartEl = document.getElementById('chartData');
      if (chartEl) {
        chartEl.value = typeof extras.chart === 'string' ? extras.chart : '';
      }

      // Clear RAG consult comment UI elements
      const consultCommentEl = document.getElementById('consultComment');
      if (consultCommentEl) consultCommentEl.value = '';
      const consultRefsEl = document.getElementById('consultRefs');
      if (consultRefsEl) consultRefsEl.textContent = '';
      const consultCard = document.getElementById('consultCommentCard');
      if (consultCard) consultCard.classList.add('hidden');
      const retryConsultBtn = document.getElementById('retryConsultComment');
      if (retryConsultBtn) retryConsultBtn.classList.add('hidden');
      const ragHintCard = document.getElementById('ragHint');
      if (ragHintCard) ragHintCard.classList.add('hidden');

      // Restore UI persistence for generation tools/buttons
      const ui = extras.ui || {};
      if (typeof window.applyUiStateFromWorkspace === 'function') {
        try { window.applyUiStateFromWorkspace(ui); } catch {}
      } else {
        {
          const lg = ui.lastGenerationId;
          window.lastGenerationId = (lg === undefined || lg === null || lg === '') ? null : lg;
        }
        if (ui.ragHasGenerated !== undefined || ui.ragContent) {
          window.ragState = {
            status: ui.ragHasGenerated ? 'ready' : 'idle',
            content: ui.ragContent || null,
            generationId: ui.lastGenerationId || window.lastGenerationId || null,
            hasGenerated: !!ui.ragHasGenerated,
            lastUpdated: ui.ragLastUpdated || null
          };
        }
        if (ui.orderHasGenerated !== undefined || ui.orderItems) {
          window.orderRequestsState = {
            status: ui.orderHasGenerated ? 'ready' : 'idle',
            items: Array.isArray(ui.orderItems) ? ui.orderItems : [],
            generationId: ui.lastGenerationId || window.lastGenerationId || null,
            hasGenerated: !!ui.orderHasGenerated,
            lastUpdated: ui.orderLastUpdated || null
          };
        }
        if (ui.showOrderRequestsButton && typeof window.setOrderRequestsButtonVisible === 'function') {
          window.setOrderRequestsButtonVisible(true);
        }
        if (ui.showEvidenceButton && typeof window.setEvidenceButtonVisible === 'function') {
          window.setEvidenceButtonVisible(true);
        }
      }

      // Clear uncertain items card
      const uncertainCard = document.getElementById('uncertainItemsCard');
      if (uncertainCard) uncertainCard.classList.add('hidden');

      // Encounter instructions are persisted per encounter in workspace extras.
      // Always apply server state exactly: missing/invalid means empty (prevents cross-encounter leakage).
      try {
        const ta = document.getElementById('encounterInstructionsInput');
        if (ta) ta.value = typeof extras.encounterInstructions === 'string' ? extras.encounterInstructions : '';
      } catch (_) {}
      if (extras.appSettings && window.app && window.app.settings) {
        window.app.settings = { ...window.app.settings, ...extras.appSettings };
      }
      if (typeof window.updateGeneratedNoteEmptyState === 'function') {
        window.updateGeneratedNoteEmptyState();
      }
      // Update character counters after restoration
      if (typeof window.updateCharacterCounter === 'function') {
        window.updateCharacterCounter();
      }
      // Notify listeners that the active encounter pointer changed so per-encounter
      // UI (e.g. Re-transcribe button, queue display) can refresh without polling.
      if (encounterChanged) {
        try {
          window.dispatchEvent(new CustomEvent('encounters-refresh'));
        } catch (_) {}
      }
      this.markEditableStateSynced();
      this.clearDirtyFields();
      return true;
    },

    collectWorkspaceState() {
      const noteEl = document.getElementById('generatedNote');
      const transEl = document.getElementById('transcriptionData');
      const transDisplayEl = document.getElementById('transcriptionDisplay');
      const oldVisitsEl = document.getElementById('oldVisitsData');
      const mixedOtherEl = document.getElementById('mixedOtherData');
      const specialityEl = document.getElementById('userSpeciality');
      const encounterInstructionsEl = document.getElementById('encounterInstructionsInput');
      // Backward compatibility: also check old chartData element
      const chartEl = document.getElementById('chartData');
      const mergedNote =
        typeof window.getMergedGeneratedNoteText === 'function' && noteEl
          ? window.getMergedGeneratedNoteText()
          : noteEl
            ? noteEl.value
            : '';
      return {
        settings: {
          theme: 'light',
          language: 'en',
        },
        documents: [],
        draft: mergedNote,
        extras: {
          transcription: transDisplayEl ? transDisplayEl.value : '',
          currentEncounter: transEl ? transEl.value : '',
          // V7 API: 3-field system
          oldVisits: oldVisitsEl ? oldVisitsEl.value : '',
          mixedOther: mixedOtherEl ? mixedOtherEl.value : '',
          userSpeciality: specialityEl ? specialityEl.value : '',
          encounterInstructions: encounterInstructionsEl ? encounterInstructionsEl.value : '',
          generatedNote: mergedNote,
          customPrompts: {},
          appSettings: window.app?.settings || {},
          ui: window.app?.uiState || {},
          // Backward compatibility: keep chart field for migration
          chart: oldVisitsEl ? oldVisitsEl.value : (chartEl ? chartEl.value : ''),
        },
      };
    },

    queueSave() {
      // GUARD: Only save workspace on main page
      if (window.WORKSPACE_PAGE_TYPE !== 'main') return;
      if (!this.isWorkspaceReady()) return;
      if (this.isSaveSuppressed()) {
        this.scheduleDeferredSave();
        return;
      }
      this.lastLocalEditAt = Date.now();
      // Mirror the edit to localStorage immediately so a crash/close before the
      // 1s debounced save still leaves a recoverable draft.
      this.persistLocalDraft();
      clearTimeout(this.saveTimer);
      this.setSyncPill('warn', 'Syncing…');
      this.saveTimer = setTimeout(() => this.saveWorkspace(), 1000);
      this.resetIdleTimer();
    },

    async saveWorkspace() {
      if (!this.isWorkspaceReady()) return;
      this.saveInFlight = true;
      const maxRetries = 3;
      const delays = [1000, 2000, 4000]; // exponential backoff: 1s, 2s, 4s
      let attempt = 0;
      try {
        while (attempt <= maxRetries) {
          const payload = {
            state: this.collectWorkspaceState(),
            version: this.workspaceVersion || 1,
          };
          let resp;
          try {
            resp = await this.request('/api/workspace/', {
              method: 'PUT',
              body: JSON.stringify(payload),
            });
          } catch (netErr) {
            // Network error (fetch threw) — retry with backoff
            if (attempt < maxRetries) {
              console.warn(`[WorkspaceSync] saveWorkspace network error (attempt ${attempt + 1}/${maxRetries + 1}), retrying in ${delays[attempt]}ms`, netErr);
              await new Promise(r => setTimeout(r, delays[attempt]));
              attempt++;
              continue;
            }
            this.lastSaveFailed = true;
            this.setSyncPill('bad', 'Not synced');
            console.warn('[WorkspaceSync] saveWorkspace failed after retries (network)', netErr);
            return;
          }
          if (resp.status === 409) {
            const data = await resp.json();
            if (data.detail?.version && data.detail?.state) {
              // Local DOM wins: merge editable fields into latest server snapshot, then retry once.
              this.workspaceVersion = data.detail.version;
              const nextState = this.mergeLocalEditableIntoServerState(data.detail.state || {});
              const retryPayload = {
                state: nextState,
                version: this.workspaceVersion,
              };
              const retryResp = await this.request('/api/workspace/', {
                method: 'PUT',
                body: JSON.stringify(retryPayload),
              });
              if (!retryResp.ok) {
                this.lastSaveFailed = true;
                this.setSyncPill('bad', 'Not synced');
                console.warn('Workspace conflict retry failed', await retryResp.text());
              } else {
                const result = await retryResp.json();
                this.workspaceVersion = result.version;
                this.markEditableStateSynced();
                this.clearDirtyFields();
                this.clearLocalDraft();
                this.setSyncPill('ok', 'Synced');
              }
              this.updateWorkspaceMeta();
            }
            return;
          }
          if (!resp.ok) {
            // Non-409 HTTP error — retry with backoff
            if (attempt < maxRetries) {
              console.warn(`[WorkspaceSync] saveWorkspace HTTP ${resp.status} (attempt ${attempt + 1}/${maxRetries + 1}), retrying in ${delays[attempt]}ms`);
              await new Promise(r => setTimeout(r, delays[attempt]));
              attempt++;
              continue;
            }
            this.lastSaveFailed = true;
            this.setSyncPill('bad', 'Not synced');
            console.warn('Workspace save failed after retries', await resp.text());
            return;
          }
          // Success
          const result = await resp.json();
          this.workspaceVersion = result.version;
          this.markEditableStateSynced();
          this.clearDirtyFields();
          this.clearLocalDraft();
          this.updateWorkspaceMeta();
          this.setSyncPill('ok', 'Synced');
          return;
        }
      } finally {
        this.saveInFlight = false;
      }
    },

    async closeCurrentEncounter() {
      if (!this.isWorkspaceReady()) return;

      clearTimeout(this.saveTimer);
      await this.saveWorkspace();

      const confirmed = confirm(
        'Clear this encounter?\n\n' +
        'This clears the draft note, transcriptions, and chart fields for the active encounter ' +
        '(this thread). Your last sync is on the server.\n\n' +
        'Continue?'
      );
      if (!confirmed) return;

      // Close encounter should clear local UI but must NOT delete the entire server queue.
      // (Per-encounter queue actions live in P7.)
      await clearAll(true, false);

      const resp = await this.request('/api/workspace/clear', { method: 'POST' });
      if (!resp.ok) {
        alert('Failed to clear encounter on server');
        return;
      }
      const data = await resp.json();
      this.workspaceVersion = data.version;
      this.applyWorkspaceState(data.state || {}, { force: true });
      this.updateWorkspaceMeta();
      if (typeof window.focusCurrentSection === 'function') {
        try { window.focusCurrentSection(); } catch {}
      }
      try {
        window.dispatchEvent(new CustomEvent('encounters-refresh'));
      } catch (e) {}
    },

    /** @deprecated Use closeCurrentEncounter — kept for older bindings */
    async clearWorkspace() {
      return this.closeCurrentEncounter();
    },

    async logout(skipConfirm = false) {
      // Save workspace before logout
      if (this.isWorkspaceReady()) {
        clearTimeout(this.saveTimer);
        await this.saveWorkspace();
      }

      if (!skipConfirm) {
        const confirmed = window.confirm('Sign out and clear the current UI?');
        if (!confirmed) {
          return;
        }
      }

      // SECURITY: ensure no queued/failed requests (which may contain patient files)
      // are visible while signed out. We clear both the in-memory queue and
      // persisted queue storage.
      try {
        if (window.app) {
          window.app.requestQueue = [];
        }
      } catch (e) {}
      try { localStorage.removeItem('clinicalNoteQueue'); } catch (e) {}
      try {
        if (typeof window.updateQueueDisplay === 'function') {
          window.updateQueueDisplay();
        }
      } catch (e) {}

      this.clearUiState();
      try {
        await this.request('/api/auth/logout', {
          method: 'POST',
          body: JSON.stringify({}),
        }, true);
      } catch (err) {
        console.warn('[Auth] Logout request failed:', err);
      }
      this.clearSession();
      this.showAuthForms();
      this.hideApprovalNotice();
      if (window.app && window.app.settings) {
        window.app.settings.apiKey = '';
      }
      const statusMessage = skipConfirm ? 'Signed out due to inactivity' : 'Signed out successfully';
      this.updateStatus(statusMessage, 'info');
    },

    async tryRefresh() {
      if (this.refreshPromise) {
        return this.refreshPromise;
      }
      this.refreshPromise = (async () => {
        try {
          const resp = await this.request('/api/auth/refresh', {
            method: 'POST',
            body: JSON.stringify({}),
          }, true);
          if (!resp.ok) {
            // 422 means no valid refresh token - expected on first load
            if (resp.status === 422) {
              return false;
            }
            return false;
          }
          const data = await resp.json();
          if (data.access_token) {
            this.setAccessToken(data.access_token);
            this.emitAuthChanged(true);
            return true;
          }
        } catch (err) {
          console.warn('[Auth] Refresh failed:', err);
          return false;
        }
        return false;
      })();
      try {
        const result = await this.refreshPromise;
        return result;
      } finally {
        this.refreshPromise = null;
      }
    },

    async handleUnauthorized(message = 'Session expired. Please sign in again.') {
      this.clearSession();
      this.showAuthForms();
      this.updateStatus(message, 'warning');
    },

    async request(path, options = {}, skipAuth = false) {
      if (!skipAuth) {
        const ok = await this.ensureFreshToken();
        if (!ok) {
          await this.handleUnauthorized();
          throw new Error('Authentication required');
        }
      }
      let url = path;
      if (!path.startsWith('http')) {
        const base = (!this.apiBase || this.apiBase === '/api') ? '' : this.apiBase;
        if (base) {
          const trimmed = base.endsWith('/') ? base.slice(0, -1) : base;
          url = `${trimmed}${path}`;
        }
      }
      const headers = options.headers ? { ...options.headers } : {};
      let body = options.body;
      const isForm = body instanceof FormData;
      if (!isForm && body && typeof body === 'object' && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
        body = JSON.stringify(body);
      }
      if (!skipAuth && this.accessToken) {
        headers['Authorization'] = `Bearer ${this.accessToken}`;
        if (window.app && window.app.settings) {
          window.app.settings.apiKey = this.accessToken;
        }
      }
      if (!isForm && typeof body === 'string') {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
      }
      const makeRequest = () => fetch(url, {
        method: options.method || 'GET',
        credentials: 'include',
        body: isForm ? options.body : body,
        headers,
      });
      let resp = await makeRequest();
      if (resp.status === 401 && !skipAuth) {
        const refreshed = await this.tryRefresh();
        if (refreshed && this.accessToken) {
          headers['Authorization'] = `Bearer ${this.accessToken}`;
          resp = await makeRequest();
          if (resp.status !== 401) {
            return resp;
          }
        }
        await this.handleUnauthorized();
      }
      return resp;
    },

    enableIdleTracking() {
      if (this.idleTrackingEnabled) {
        this.resetIdleTimer();
        return;
      }
      this.boundIdleReset = () => this.resetIdleTimer();
      ['click', 'keydown', 'paste', 'touchstart', 'mousemove', 'scroll'].forEach(evt => {
        document.addEventListener(evt, this.boundIdleReset, true);
      });
      this.idleTrackingEnabled = true;
      this.resetIdleTimer();
      this.recordingKeepAliveTimer = setInterval(() => {
        if (this.isClinicalBusy()) {
          this.resetIdleTimer();
          if (window.ASR_PIPELINE_REFRESH !== false) {
            this.ensureFreshToken().catch(() => {});
          }
        } else if (this.isAudioRecordingActive()) {
          this.resetIdleTimer();
        }
      }, 15000);
    },

    disableIdleTracking() {
      if (!this.idleTrackingEnabled) return;
      ['click', 'keydown', 'paste', 'touchstart', 'mousemove', 'scroll'].forEach(evt => {
        document.removeEventListener(evt, this.boundIdleReset, true);
      });
      this.boundIdleReset = null;
      this.idleTrackingEnabled = false;
      this.clearIdleTimers();
      if (this.recordingKeepAliveTimer) {
        clearInterval(this.recordingKeepAliveTimer);
        this.recordingKeepAliveTimer = null;
      }
    },

    resetIdleTimer() {
      if (!this.user || !this.user.is_approved) return;
      this.clearIdleTimers();
      const warningDelay = this.idleTimeoutMs - 30000;
      if (warningDelay > 0) {
        this.idleWarningTimer = setTimeout(() => this.showIdleWarning(), warningDelay);
      }
      this.idleTimer = setTimeout(() => this.handleIdleTimeout(), this.idleTimeoutMs);
    },

    async handleIdleTimeout() {
      if (!this.user) return;
      if (this.isAudioRecordingActive()) {
        this.resetIdleTimer?.();
        return;
      }
      this.disableIdleTracking();
      if (typeof showToast === 'function') {
        showToast('Session Ended', 'Signed out due to inactivity', 'warning');
      }
      await this.logout(true);
    },

    clearIdleTimers() {
      if (this.idleWarningTimer) {
        clearTimeout(this.idleWarningTimer);
        this.idleWarningTimer = null;
      }
      if (this.idleTimer) {
        clearTimeout(this.idleTimer);
        this.idleTimer = null;
      }
      this.hideIdleWarning();
    },

    showIdleWarning() {
      if (this.idleWarningVisible) return;
      if (this.isAudioRecordingActive()) {
        this.resetIdleTimer?.();
        return;
      }
      this.idleWarningVisible = true;
      if (typeof showToast === 'function') {
        showToast('Inactivity Warning', 'You will be signed out in 30 seconds. Move your mouse or type to stay signed in.', 'warning');
      }
    },

    hideIdleWarning() {
      this.idleWarningVisible = false;
    },

    clearUiState() {
      // Preserve UI data on sign-out; only reset auth UI chrome.
      const registerForm = document.getElementById('registerForm');
      if (registerForm && !registerForm.classList.contains('hidden')) {
        registerForm.classList.add('hidden');
      }
      const toggleLink = document.getElementById('toggleRegisterLink');
      if (toggleLink) {
        toggleLink.textContent = 'or Register Now';
      }
      if (this.card) {
        this.card.classList.remove('authenticated');
      }
      console.log('[Auth] UI state preserved on sign-out');
    },

    updateApiBaseInput() {
      if (this.apiBaseInput) {
        this.apiBaseInput.value = this.apiBase;
      }
      localStorage.setItem(STORAGE_KEYS.API_BASE, this.apiBase);
    },

    decodeToken(token) {
      if (!token) return null;
      try {
        const parts = token.split('.');
        if (parts.length < 2) return null;
        const payload = parts[1]
          .replace(/-/g, '+')
          .replace(/_/g, '/');
        const padLength = (4 - (payload.length % 4)) % 4;
        const padded = payload + '='.repeat(padLength);
        const decoded = atob(padded);
        return JSON.parse(decoded);
      } catch (err) {
        console.warn('[Auth] Failed to decode token payload:', err);
        return null;
      }
    },

    updateTokenMetadata(token) {
      const decoded = this.decodeToken(token);
      if (decoded?.sub) {
        if (!this.user) {
          this.user = { id: decoded.sub };
        } else if (!this.user.id) {
          this.user.id = decoded.sub;
        }
      }
      if (decoded?.exp) {
        this.tokenExpiryMs = decoded.exp * 1000;
      } else {
        this.tokenExpiryMs = null;
      }
      this.scheduleTokenRefresh();
    },

    scheduleTokenRefresh() {
      this.clearTokenRefreshTimer();
      if (!this.tokenExpiryMs) return;
      const refreshAt = this.tokenExpiryMs - this.refreshLeadMs;
      const now = Date.now();
      if (refreshAt <= now) {
        this.tokenRefreshTimer = setTimeout(() => this.tryRefresh(), 0);
        return;
      }
      this.tokenRefreshTimer = setTimeout(() => this.tryRefresh(), refreshAt - now);
    },

    clearTokenRefreshTimer() {
      if (this.tokenRefreshTimer) {
        clearTimeout(this.tokenRefreshTimer);
        this.tokenRefreshTimer = null;
      }
    },

    emitAuthChanged(signedIn) {
      try {
        window.dispatchEvent(new CustomEvent('workspace-auth-changed', {
          detail: { signedIn, token: signedIn ? this.accessToken : '' },
        }));
      } catch (err) {
        console.warn('[Auth] Failed to dispatch auth change event:', err);
      }
    },

    async ensureFreshToken() {
      if (!this.accessToken) return false;
      if (!this.tokenExpiryMs) return true;
      const timeRemaining = this.tokenExpiryMs - Date.now();
      if (timeRemaining <= this.refreshLeadMs) {
        return await this.tryRefresh();
      }
      return true;
    },

    async authFetch(path, options = {}, skipAuth = false) {
      if (window.SYNC_AUTH_FETCH !== false) {
        await this.ensureFreshToken();
      }
      return this.request(path, options, skipAuth);
    },
  };

  function initAuthWorkspace() {
    if (document.getElementById('authCard')) {
      AuthWorkspace.init();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(initAuthWorkspace, 0));
  } else {
    setTimeout(initAuthWorkspace, 0);
  }

  window.AuthWorkspace = AuthWorkspace;
  window.authFetch = (path, options, skipAuth) => AuthWorkspace.authFetch(path, options, skipAuth);
  if (typeof window.SYNC_BEACON_FIX === 'undefined') window.SYNC_BEACON_FIX = true;
  if (typeof window.SYNC_AUTH_FETCH === 'undefined') window.SYNC_AUTH_FETCH = true;
  if (typeof window.SYNC_LOCAL_DRAFT === 'undefined') window.SYNC_LOCAL_DRAFT = true;
  if (typeof window.SYNC_ANTI_STOMP === 'undefined') window.SYNC_ANTI_STOMP = true;
  if (typeof window.ASR_PIPELINE_REFRESH === 'undefined') window.ASR_PIPELINE_REFRESH = true;
})();
