// C:\PCHost\web\auth_workspace.js
(function () {
  const STORAGE_KEYS = {
    ACCESS: 'auth_access_token',
    API_BASE: 'auth_api_base',
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

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.cacheElements();
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
              this.updateWorkspaceMeta();
            }
          })
          .catch(() => {
            this.clearSession();
            this.clearUiState();
            this.showAuthForms();
          });
      } else {
        this.clearUiState();
        this.showAuthForms();
      }
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
          // V7 API: New 3-field system
          'oldVisitsData',
          'mixedOtherData',
          'userSpeciality',
          // Legacy: Keep chartData for backward compatibility
          'chartData',
        ];

        // Store last known values
        const lastValues = {};

        fields.forEach((id) => {
          const el = document.getElementById(id);
          if (!el) return;

          // Store initial value
          lastValues[id] = el.value;

          // Listen to user input (typing, paste, etc.) - saves immediately
          el.addEventListener('input', () => this.queueSave());
        });

        // Poll for programmatic changes every 500ms
        setInterval(() => {
          if (!this.isWorkspaceReady()) return;

          let changed = false;
          fields.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;

            if (el.value !== lastValues[id]) {
              lastValues[id] = el.value;
              changed = true;
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
        // GUARD: Only save workspace on main page to prevent data loss
        if (window.WORKSPACE_PAGE_TYPE !== 'main') {
          console.log('[Auth] Skipping workspace save on page unload (not on main page)');
          return;
        }

        clearTimeout(this.saveTimer);
        if (this.isWorkspaceReady()) {
          const payload = {
            state: this.collectWorkspaceState(),
            version: this.workspaceVersion || 1,
          };
          // Use sendBeacon for reliability during page unload
          const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
          const url = (!this.apiBase || this.apiBase === '/api') ? '/api/workspace/' : `${this.apiBase}/workspace/`;
          navigator.sendBeacon(url, blob);
        }

        // NOTE: Do not clear UI fields here; it can overwrite persisted workspace data on unload.
      });

      // Save when tab becomes hidden (user switches tabs or minimizes browser)
      document.addEventListener('visibilitychange', () => {
        // GUARD: Only save workspace on main page
        if (window.WORKSPACE_PAGE_TYPE !== 'main') return;

        if (document.hidden && this.isWorkspaceReady()) {
          clearTimeout(this.saveTimer);
          this.saveWorkspace();
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

      // Hide clear workspace button on secondary pages
      if (this.clearBtn) {
        if (window.WORKSPACE_PAGE_TYPE === 'main') {
          this.clearBtn.style.display = '';
          this.clearBtn.disabled = false;
        } else {
          this.clearBtn.style.display = 'none';
        }
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
      if (!this.workspaceMeta) return;
      if (!this.workspaceVersion) {
        this.workspaceMeta.textContent = 'Workspace: not synced';
      } else {
        this.workspaceMeta.textContent = `Workspace version ${this.workspaceVersion}`;
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
      this.tokenExpiryMs = null;
      this.clearTokenRefreshTimer();
      this.refreshPromise = null;
      this.workspaceVersion = 0;
      this.user = null;
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
          const msg = await resp.text();
          throw new Error(msg || 'Login failed');
        }
        const data = await resp.json();
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
          const msg = await resp.text();
          throw new Error(msg || 'Registration failed');
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

    isAudioRecordingActive() {
      try {
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
        this.applyWorkspaceState(data.state || {});
        this.updateWorkspaceMeta();
        return true;
      })();
      try {
        return await this.workspacePromise;
      } finally {
        this.workspacePromise = null;
      }
    },

    applyWorkspaceState(state) {
      const noteEl = document.getElementById('generatedNote');
      const extras = state.extras || {};
      if (noteEl) {
        const draft = typeof state.draft === 'string' ? state.draft : '';
        const fallbackNote = typeof extras.generatedNote === 'string' ? extras.generatedNote : '';
        noteEl.value = draft || fallbackNote || noteEl.value || '';
      }
      const transEl = document.getElementById('transcriptionData');
      const transDisplayEl = document.getElementById('transcriptionDisplay');
      if (transEl) {
        const transcription = typeof extras.transcription === 'string' ? extras.transcription : '';
        const currentEncounter = typeof extras.currentEncounter === 'string' ? extras.currentEncounter : '';
        const notesValue = currentEncounter || transEl.value || '';
        transEl.value = notesValue;
        if (transDisplayEl) {
          transDisplayEl.value = transcription || transDisplayEl.value || '';
        }
      }

      // V7 API: Restore old visits field
      const oldVisitsEl = document.getElementById('oldVisitsData');
      if (oldVisitsEl) {
        // Check for new format first, then fall back to legacy 'chart' field
        if (typeof extras.oldVisits === 'string' && extras.oldVisits) {
          oldVisitsEl.value = extras.oldVisits;
        } else if (typeof extras.chart === 'string' && extras.chart) {
          // Backward compatibility: migrate old 'chart' data to 'oldVisits'
          oldVisitsEl.value = extras.chart;
        } else {
          oldVisitsEl.value = oldVisitsEl.value || '';
        }
      }

      // V7 API: Restore mixed other field
      const mixedOtherEl = document.getElementById('mixedOtherData');
      const specialityEl = document.getElementById('userSpeciality');
      if (mixedOtherEl) {
        const mixedOther = typeof extras.mixedOther === 'string' ? extras.mixedOther : '';
        mixedOtherEl.value = mixedOther || mixedOtherEl.value || '';
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
        if (ui.lastGenerationId) window.lastGenerationId = ui.lastGenerationId;
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

      if (extras.customPrompts && window.app) {
        window.app.customPrompts = extras.customPrompts;
        if (typeof saveCustomPromptsToStorage === 'function') {
          // saveCustomPromptsToStorage(); // DEPRECATED - workspace is source of truth
        }
      }
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
    },

    collectWorkspaceState() {
      const noteEl = document.getElementById('generatedNote');
      const transEl = document.getElementById('transcriptionData');
      const transDisplayEl = document.getElementById('transcriptionDisplay');
      const oldVisitsEl = document.getElementById('oldVisitsData');
      const mixedOtherEl = document.getElementById('mixedOtherData');
      const specialityEl = document.getElementById('userSpeciality');
      // Backward compatibility: also check old chartData element
      const chartEl = document.getElementById('chartData');
      return {
        settings: {
          theme: 'light',
          language: 'en',
        },
        documents: [],
        draft: noteEl ? noteEl.value : '',
        extras: {
          transcription: transDisplayEl ? transDisplayEl.value : '',
          currentEncounter: transEl ? transEl.value : '',
          // V7 API: 3-field system
          oldVisits: oldVisitsEl ? oldVisitsEl.value : '',
          mixedOther: mixedOtherEl ? mixedOtherEl.value : '',
          userSpeciality: specialityEl ? specialityEl.value : '',
          generatedNote: noteEl ? noteEl.value : '',
          customPrompts: window.app?.customPrompts || {},
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
      clearTimeout(this.saveTimer);
      this.saveTimer = setTimeout(() => this.saveWorkspace(), 1000);
      this.resetIdleTimer();
    },

    async saveWorkspace() {
      if (!this.isWorkspaceReady()) return;
      const payload = {
        state: this.collectWorkspaceState(),
        version: this.workspaceVersion || 1,
      };
      const resp = await this.request('/api/workspace/', {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      if (resp.status === 409) {
        const data = await resp.json();
        if (data.detail?.version && data.detail?.state) {
          // Merge UI state into the latest server state and retry once.
          this.workspaceVersion = data.detail.version;
          const nextState = data.detail.state || {};
          if (!nextState.extras) nextState.extras = {};
          if (window.app && window.app.uiState) {
            nextState.extras.ui = { ...(nextState.extras.ui || {}), ...(window.app.uiState || {}) };
          }
          const retryPayload = {
            state: nextState,
            version: this.workspaceVersion,
          };
          const retryResp = await this.request('/api/workspace/', {
            method: 'PUT',
            body: JSON.stringify(retryPayload),
          });
          if (!retryResp.ok) {
            // Fallback: apply server state, then re-apply UI locally
            this.applyWorkspaceState(data.detail.state);
            if (window.app && window.app.uiState && typeof window.applyUiStateFromWorkspace === 'function') {
              try { window.applyUiStateFromWorkspace(window.app.uiState); } catch {}
            }
          } else {
            const result = await retryResp.json();
            this.workspaceVersion = result.version;
          }
          this.updateWorkspaceMeta();
        }
        return;
      }
      if (!resp.ok) {
        console.warn('Workspace save failed', await resp.text());
        return;
      }
      const result = await resp.json();
      this.workspaceVersion = result.version;
      this.updateWorkspaceMeta();
    },

    async clearWorkspace() {
      if (!this.isWorkspaceReady()) return;
      
      // Save current state first (in case user wants to review/undo)
      clearTimeout(this.saveTimer);
      await this.saveWorkspace();
      
      // Strong warning with explicit confirmation
      const confirmed = confirm(
        '⚠️ RESET WORKSPACE TO BASELINE?\n\n' +
        'This will DELETE all your current work:\n' +
        '• Generated notes\n' +
        '• Transcriptions\n' +
        '• Chart data\n\n' +
        'Your current work has been saved to version ' + this.workspaceVersion + '.\n' +
        'You can contact support to recover if needed.\n\n' +
        'Are you SURE you want to reset?'
      );
      
      if (!confirmed) return;

      // Clear all local data (audio, queue, UI fields) first
      await clearAll(true);

      const resp = await this.request('/api/workspace/clear', { method: 'POST' });
      if (!resp.ok) {
        alert('Failed to clear workspace');
        return;
      }
      const data = await resp.json();
      this.workspaceVersion = data.version;
      this.applyWorkspaceState(data.state || {});
      this.updateWorkspaceMeta();
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
        if (this.isAudioRecordingActive()) {
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
})();
