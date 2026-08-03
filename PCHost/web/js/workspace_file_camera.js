/* P10b — file upload, camera (getUserMedia), drag/drop (depends on workspace_app.js globals). */
(function () {
    'use strict';

    /** Block video uploads (some pickers ignore accept=). */
    function isVideoLikeFile(file) {
        if (!file) return false;
        const t = (file.type || '').toLowerCase();
        if (t.startsWith('video/')) return true;
        const n = (file.name || '').toLowerCase();
        return /\.(mp4|mpe?g|mov|webm|mkv|m4v|avi|wmv|ogv|3gp|mts|m2ts|flv)(\b|$)/i.test(n);
    }

    async function acquireCameraStream() {
        if (typeof navigator === 'undefined' || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            return null;
        }
        const attempts = [
            { video: { facingMode: { ideal: 'environment' } }, audio: false },
            { video: { facingMode: 'environment' }, audio: false },
            { video: true, audio: false },
        ];
        for (const constraints of attempts) {
            try {
                return await navigator.mediaDevices.getUserMedia(constraints);
            } catch (e) {
                /* try next */
            }
        }
        return null;
    }

    function openCameraFileFallback() {
        const input = document.getElementById('cameraFileFallback') || document.getElementById('filePicker');
        if (!input) return;
        input.removeAttribute('capture');
        input.value = '';
        input.click();
    }

    async function useFileCamera() {
        const stream = await acquireCameraStream();
        if (stream) {
            const modal = document.getElementById('cameraModal');
            const video = document.getElementById('cameraVideo');
            if (!modal || !video) {
                try {
                    stream.getTracks().forEach((t) => t.stop());
                } catch (e) {}
                openCameraFileFallback();
                return;
            }
            video.setAttribute('playsinline', '');
            video.playsInline = true;
            try {
                video.muted = true;
            } catch (e) {}
            video.srcObject = stream;
            modal.classList.add('show');
            try {
                await video.play();
            } catch (e) {}
            return;
        }
        openCameraFileFallback();
    }

    async function handleSelectedFiles(input) {
        const files = Array.from(input.files || []);
        if (!files.length) return;

        if (!window.currentDropTargetField) {
            window.currentDropTargetField = window.lastInputFieldId || 'oldVisitsData';
        }

        const audioFiles = [];
        const documentFiles = [];

        files.forEach(file => {
            if (isVideoLikeFile(file)) {
                showToast('Unsupported File', 'Video uploads are not supported. Use a photo or PDF.', 'warning');
                return;
            }
            if (file.type.startsWith('audio/')) {
                audioFiles.push(file);
            } else if (file.type.startsWith('image/') || file.type === 'application/pdf') {
                documentFiles.push(file);
            } else {
                showToast('Unsupported File', `File type not supported: ${file.name}`, 'error');
            }
        });

        if (audioFiles.length) audioFiles.forEach(file => handleAudioFile({ files: [file] }));
        try {
            if (documentFiles.length) await processDocuments(documentFiles);
        } finally {
            input.value = "";
        }
    }

    function handleAudioFile(input) {
        const files = input.files;
        if (!files || files.length === 0) return;

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const appRef = window.app;
            if (appRef && appRef.connection.status === 'connected') {
                if (typeof window.transcribeUploadedAudioFile === 'function') {
                    window.transcribeUploadedAudioFile(file);
                } else {
                    showToast('Unavailable', 'Audio import is not ready yet.', 'warning');
                }
            } else {
                showToast('Offline', 'Please connect to server to transcribe audio', 'warning');
            }
        }
    }

    function openCamera() {
        void useFileCamera();
    }

    function closeCamera() {
        const modal = document.getElementById('cameraModal');
        const video = document.getElementById('cameraVideo');

        if (video && video.srcObject) {
            video.srcObject.getTracks().forEach((track) => track.stop());
            video.srcObject = null;
        }

        if (modal) modal.classList.remove('show');
    }

    function capturePhoto() {
        const video = document.getElementById('cameraVideo');
        const canvas = document.getElementById('cameraCanvas');
        const ctx = canvas.getContext('2d');

        if (!video || !video.videoWidth || !video.videoHeight) {
            showToast('Camera', 'Camera is not ready yet. Wait a moment or close and try again.', 'warning');
            return;
        }

        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0);

        canvas.toBlob(blob => {
            const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
            processDocuments([file]);
            closeCamera();
        }, 'image/jpeg', 0.8);
    }

    function handleDrop(e, targetField) {
        e.preventDefault();
        e.stopPropagation();
        const dt = e.dataTransfer;

        const targetFieldId = targetField || window.lastInputFieldId || 'oldVisitsData';
        window.currentDropTargetField = targetFieldId;

        const textData = dt.getData('text/plain');
        if (textData && textData.trim()) {
            const targetEl = document.getElementById(targetFieldId);
            if (targetEl) {
                const currentValue = targetEl.value || '';
                const separator = currentValue.trim() ? '\n\n' : '';
                setFieldValue(targetFieldId, currentValue + separator + textData);
                saveToStorage();
                showToast('Pasted', `Text added to ${getFieldDisplayName(targetFieldId)}`, 'success');
            }
        }

        const files = dt.files;
        if (files && files.length) {
            handleSelectedFiles({ files });
        }

        if (e.currentTarget && e.currentTarget.classList) {
            e.currentTarget.classList.remove('dragover');
        }
    }

    function getFieldDisplayName(fieldId) {
        const names = {
            'transcriptionData': "Today's Dictation",
            'oldVisitsData': 'Prior Visits',
            'mixedOtherData': 'Labs/Imaging/Consults',
            'chartData': 'Chart Data'
        };
        return names[fieldId] || fieldId;
    }

    function initDragAndDrop() {
        if (!window.lastInputFieldId) {
            window.lastInputFieldId = 'oldVisitsData';
        }
        const dropZone = document.getElementById('documentDrop');
        const chartTextarea = document.getElementById('chartData');
        const oldVisitsTextarea = document.getElementById('oldVisitsData');
        const mixedOtherTextarea = document.getElementById('mixedOtherData');
        const transcriptionTextarea = document.getElementById('transcriptionData');
        const filePicker = document.getElementById('filePicker');

        function preventDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        if (dropZone) {
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, preventDefaults, false);
            });

            ['dragenter', 'dragover'].forEach(eventName => {
                dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
            });

            dropZone.addEventListener('drop', handleDrop, false);
            dropZone.addEventListener('click', () => {
                if (filePicker) filePicker.click();
            });
            dropZone.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    if (filePicker) filePicker.click();
                }
            });
        }

        if (chartTextarea) {
            ['dragenter', 'dragover'].forEach(eventName => {
                chartTextarea.addEventListener(eventName, preventDefaults, false);
            });
            chartTextarea.addEventListener('paste', () => {
                setTimeout(() => setChartDataValue(undefined, { preserveCaret: true }), 0);
            });
            chartTextarea.addEventListener('blur', () => setChartDataValue());
        }

        if (transcriptionTextarea) {
            transcriptionTextarea.addEventListener('focus', () => {
                window.lastInputFieldId = 'transcriptionData';
            });
        }

        if (oldVisitsTextarea) {
            oldVisitsTextarea.addEventListener('paste', () => {
                setTimeout(() => setFieldValue('oldVisitsData', undefined, { preserveCaret: true }), 0);
            });
            oldVisitsTextarea.addEventListener('blur', () => setFieldValue('oldVisitsData'));
            oldVisitsTextarea.addEventListener('focus', () => {
                window.lastInputFieldId = 'oldVisitsData';
            });
        }

        if (mixedOtherTextarea) {
            mixedOtherTextarea.addEventListener('paste', () => {
                setTimeout(() => setFieldValue('mixedOtherData', undefined, { preserveCaret: true }), 0);
            });
            mixedOtherTextarea.addEventListener('blur', () => setFieldValue('mixedOtherData'));
            mixedOtherTextarea.addEventListener('focus', () => {
                window.lastInputFieldId = 'mixedOtherData';
            });
        }

        document.body.addEventListener('dragover', preventDefaults, false);
        document.body.addEventListener('drop', preventDefaults, false);

        document.addEventListener('paste', (e) => {
            const items = e.clipboardData && e.clipboardData.items ? Array.from(e.clipboardData.items) : [];
            const files = [];
            for (const item of items) {
                if (item.kind === 'file') {
                    const file = item.getAsFile();
                    if (file) files.push(file);
                }
            }
            if (files.length) {
                handleSelectedFiles({ files });
                showToast('Pasted', `Added ${files.length} item(s) from clipboard`, 'success');
            }
        });

        setChartDataValue();
        setFieldValue('oldVisitsData');
        setFieldValue('mixedOtherData');
    }

    window.useFileCamera = useFileCamera;
    window.handleSelectedFiles = handleSelectedFiles;
    window.handleAudioFile = handleAudioFile;
    window.openCamera = openCamera;
    window.closeCamera = closeCamera;
    window.capturePhoto = capturePhoto;
    window.handleDrop = handleDrop;
    window.getFieldDisplayName = getFieldDisplayName;
    window.initDragAndDrop = initDragAndDrop;
})();
