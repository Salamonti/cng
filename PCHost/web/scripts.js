// C:\PCHost\web\scripts.js
// OCR Document Processing JavaScript
class OCRProcessor {
    constructor() {
        this.apiBaseUrl = '/api';
        this.currentFile = null;
        this.isProcessing = false;
        this.queue = [];
        this.retryDelayMs = 5000;
        this.retryTimer = null;
        this.maxRetries = 3;
        this.authTokenKey = 'auth_access_token';
        
        this.initializeEventListeners();
    }

    getAuthToken() {
        if (window?.app?.settings?.apiKey) {
            return window.app.settings.apiKey;
        }
        try {
            const token = sessionStorage.getItem(this.authTokenKey);
            if (token) return token;
        } catch {
        }
        return '';
    }
    
    initializeEventListeners() {
        const uploadArea = document.getElementById('uploadArea');
        const documentInput = document.getElementById('documentInput');
        const processBtn = document.getElementById('processBtn');
        
        if (uploadArea && documentInput) {
            uploadArea.addEventListener('click', () => documentInput.click());
            uploadArea.addEventListener('dragover', this.handleDragOver.bind(this));
            uploadArea.addEventListener('dragleave', this.handleDragLeave.bind(this));
            uploadArea.addEventListener('drop', this.handleDrop.bind(this));
            
            documentInput.addEventListener('change', this.handleFileSelect.bind(this));
        }
        
        if (processBtn) {
            processBtn.addEventListener('click', this.processDocument.bind(this));
        }
    }
    
    handleDragOver(e) {
        e.preventDefault();
        document.getElementById('uploadArea').classList.add('dragover');
    }
    
    handleDragLeave(e) {
        e.preventDefault();
        document.getElementById('uploadArea').classList.remove('dragover');
    }
    
    handleDrop(e) {
        e.preventDefault();
        document.getElementById('uploadArea').classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            this.selectFile(files[0]);
        }
    }
    
    handleFileSelect(e) {
        if (e.target.files.length > 0) {
            this.selectFile(e.target.files[0]);
        }
    }
    
    selectFile(file) {
        const allowedTypes = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff', 'image/bmp'];
        if (!allowedTypes.includes(file.type)) {
            this.showError('Unsupported file type. Please select a PDF or image file.');
            return;
        }
        
        const maxSize = 50 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showError('File too large. Maximum size is 50MB.');
            return;
        }
        
        this.currentFile = file;
        
        const uploadText = document.querySelector('.upload-text');
        if (uploadText) {
            const fileName = file.name || 'Unknown file';
            uploadText.textContent = `Selected: ${fileName}`;
        }
        
        const processBtn = document.getElementById('processBtn');
        if (processBtn) {
            processBtn.disabled = false;
        }
        
        this.clearPreviousResults();
    }
    
    async processDocument() {
        if (!this.currentFile || this.isProcessing) {
            return;
        }
        
        this.isProcessing = true;
        
        try {
            this.showProgress();
            
            const formData = new FormData();
            formData.append('file', this.currentFile);
            formData.append('mode', document.getElementById('ocrMode').value);
            
            const headers = {};
            const apiKey = this.getAuthToken();
            if (!apiKey) {
                this.showError('Please sign in before running OCR.');
                this.isProcessing = false;
                this.hideProgress();
                return;
            }
            headers['Authorization'] = `Bearer ${apiKey}`;
            
            const response = await fetch(`${this.apiBaseUrl}/ocr`, {
                method: 'POST',
                headers: headers,
                body: formData
            });
            
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Processing failed: ${response.status} - ${errorText}`);
            }
            
            const result = await response.json();
            this.showResults(result);
            
        } catch (error) {
            console.error('OCR processing error:', error);
            // Queue for retry instead of failing in-place
            this.enqueueCurrentFile();
            const fileName = this.currentFile?.name || 'Unknown file';
            this.showError(`Server busy or unavailable. Queued ${fileName} for retry.`);
            this.scheduleQueue();
        } finally {
            this.isProcessing = false;
            this.hideProgress();
        }
    }

    enqueueCurrentFile() {
        if (!this.currentFile) return;
        this.queue.push({ file: this.currentFile, retries: 0 });
    }

    scheduleQueue() {
        if (this.retryTimer || this.queue.length === 0) return;
        this.retryTimer = setTimeout(() => {
            this.retryTimer = null;
            this.processQueue();
        }, this.retryDelayMs);
    }

    async processQueue() {
        if (this.queue.length === 0) return;
        const item = this.queue.shift();
        try {
            const formData = new FormData();
            formData.append('file', item.file);
            const headers = {};
            const apiKey = this.getAuthToken();
            if (!apiKey) {
                throw new Error('Missing auth token');
            }
            headers['Authorization'] = `Bearer ${apiKey}`;
            const response = await fetch(`${this.apiBaseUrl}/ocr`, { method: 'POST', headers, body: formData });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const result = await response.json();
            this.showResults(result);
        } catch (e) {
            // Requeue with backoff if retries remain
            item.retries += 1;
            if (item.retries < this.maxRetries) {
                this.queue.push(item);
            } else {
                this.showError('Failed after multiple retries. Please try again later.');
            }
        } finally {
            // Schedule next if any
            if (this.queue.length > 0) {
                this.retryDelayMs = Math.min(this.retryDelayMs * 2, 60000);
                this.scheduleQueue();
            } else {
                this.retryDelayMs = 5000;
            }
        }
    }
    
    showProgress() {
        const progressContainer = document.getElementById('progressContainer');
        const processBtn = document.getElementById('processBtn');
        
        if (progressContainer) {
            progressContainer.style.display = 'block';
        }
        
        if (processBtn) {
            processBtn.disabled = true;
            processBtn.textContent = '⏳ Processing...';
        }
        
        this.animateProgress();
    }
    
    hideProgress() {
        const progressContainer = document.getElementById('progressContainer');
        const processBtn = document.getElementById('processBtn');
        
        if (progressContainer) {
            progressContainer.style.display = 'none';
        }
        
        if (processBtn) {
            processBtn.disabled = false;
            processBtn.textContent = '🚀 Process Document';
        }
    }
    
    animateProgress() {
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        
        if (!progressFill || !progressText) return;
        
        let progress = 0;
        const messages = [
            'Uploading document...',
            'Analyzing document structure...',
            'Extracting text with AI...',
            'Processing with OCR engine...',
            'Finalizing results...'
        ];
        
        const interval = setInterval(() => {
            if (!this.isProcessing) {
                clearInterval(interval);
                return;
            }
            
            progress += Math.random() * 15 + 5;
            if (progress > 90) progress = 90;
            
            progressFill.style.width = `${progress}%`;
            
            const messageIndex = Math.floor((progress / 100) * messages.length);
            progressText.textContent = messages[Math.min(messageIndex, messages.length - 1)];
            
        }, 1000);
    }
    
    showResults(result) {
        const resultsContainer = document.getElementById('resultsContainer');
        const ocrResults = document.getElementById('ocrResults');
        const resultsInfo = document.getElementById('resultsInfo');
        
        if (resultsContainer) {
            resultsContainer.style.display = 'block';
        }
        
        if (ocrResults) {
            ocrResults.value = result.text || 'No text extracted';
        }
        
        if (resultsInfo) {
            const info = `Engine: ${result.engine_used || 'unknown'} | ` +
                        `Confidence: ${(result.confidence || 0).toFixed(3)} | ` +
                        `Time: ${(result.processing_time || 0).toFixed(1)}s`;
            resultsInfo.textContent = info;
        }
        
        resultsContainer.scrollIntoView({ behavior: 'smooth' });
    }
    
    showError(message) {
        const errorContainer = document.getElementById('errorContainer');
        const errorMessage = document.getElementById('errorMessage');
        
        if (errorContainer) {
            errorContainer.style.display = 'block';
        }
        
        if (errorMessage) {
            errorMessage.textContent = message;
        }
        
        errorContainer.scrollIntoView({ behavior: 'smooth' });
    }
    
    clearPreviousResults() {
        const resultsContainer = document.getElementById('resultsContainer');
        const errorContainer = document.getElementById('errorContainer');
        
        if (resultsContainer) {
            resultsContainer.style.display = 'none';
        }
        
        if (errorContainer) {
            errorContainer.style.display = 'none';
        }
    }
}

// OCR Utility Functions - moved from separate file to avoid duplication
function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[c]));
}

function engineVariant(engineRaw) {
    const e = String(engineRaw || '').toLowerCase();
    if (e.includes('mixed')) return { cls: 'engine-mixed', label: 'TrOCR (mixed)' };
    if (e.includes('printed')) return { cls: 'engine-printed', label: 'TrOCR (printed)' };
    if (e.includes('handwritten') || e.includes('trocr')) return { cls: 'engine-handwritten', label: 'TrOCR (handwritten)' };
    return { cls: 'engine-unknown', label: 'OCR' };
}

function renderOcrBadge(engine, conf) {
    const { cls, label } = engineVariant(engine);
    const pct = typeof conf === 'number' ? ` ${Math.round(conf * 100)}%` : '';
    return `<span class="ocr-badge ${cls}" title="${escapeHtml(label)}">${escapeHtml(label)}${pct}</span>`;
}

function addEngineBadge(filename, engine, conf) {
    const list = document.getElementById('ocrEngineList');
    if (!list) return;
    const div = document.createElement('div');
    div.className = 'ocr-engine-item';
    div.innerHTML = `<span class="ocr-file">${escapeHtml(filename)}</span>${renderOcrBadge(engine, conf)}`;
    list.appendChild(div);
}

// Helper function for toast notifications
function showToast(message) {
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: #2ecc71;
        color: white;
        padding: 1rem 2rem;
        border-radius: 4px;
        z-index: 1000;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    `;
    toast.textContent = message;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 300);
    }, 3000);
}

// OCR Action Functions
function copyOcrText() {
    const ocrResults = document.getElementById('ocrResults');
    if (ocrResults && ocrResults.value) {
        navigator.clipboard.writeText(ocrResults.value).then(() => {
            showToast('Text copied to clipboard!');
        }).catch(err => {
            console.error('Failed to copy text:', err);
            showToast('Failed to copy text');
        });
    }
}

function downloadOcrText() {
    const ocrResults = document.getElementById('ocrResults');
    if (ocrResults && ocrResults.value) {
        const blob = new Blob([ocrResults.value], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ocr-result-${new Date().getTime()}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('Text downloaded!');
    }
}

function useInNotes() {
    const ocrResults = document.getElementById('ocrResults');
    if (ocrResults && ocrResults.value) {
        localStorage.setItem('extracted_text', ocrResults.value);
        
        const notesSection = document.getElementById('notes');
        if (notesSection) {
            notesSection.scrollIntoView({ behavior: 'smooth' });
        }
        
        showToast('Text ready for use in notes!');
    }
}

function clearResults() {
    const ocrResults = document.getElementById('ocrResults');
    const resultsContainer = document.getElementById('resultsContainer');
    const uploadText = document.querySelector('.upload-text');
    const processBtn = document.getElementById('processBtn');
    const documentInput = document.getElementById('documentInput');
    
    if (ocrResults) ocrResults.value = '';
    if (resultsContainer) resultsContainer.style.display = 'none';
    if (uploadText) uploadText.textContent = 'Drop document here or click to browse';
    if (processBtn) processBtn.disabled = true;
    if (documentInput) documentInput.value = '';
}

function clearError() {
    const errorContainer = document.getElementById('errorContainer');
    if (errorContainer) {
        errorContainer.style.display = 'none';
    }
}

// Initialize OCR processor and add navigation when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('uploadArea')) {
        window.ocrProcessor = new OCRProcessor();
    }
    
    // Add OCR section to navigation if it doesn't exist
    const navLinks = document.querySelector('.nav-links');
    if (navLinks && !document.querySelector('a[href="#ocr"]')) {
        const ocrLink = document.createElement('li');
        ocrLink.innerHTML = '<a href="#ocr">📄 OCR</a>';
        navLinks.appendChild(ocrLink);
    }
});
