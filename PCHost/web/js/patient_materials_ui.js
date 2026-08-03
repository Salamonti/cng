/**
 * Patient Materials UI
 * 
 * Modal dialog with 6 category cards for patient-facing educational materials.
 * Each category generates on-demand when clicked.
 */

(function() {
    'use strict';

    // State management
    window.patientMaterialsState = window.patientMaterialsState || {
        status: 'idle',
        materials: {},
        patientData: {},
        generationId: null,
        hasGenerated: false,
        lastUpdated: null
    };

    // Material type → display name mapping
    const MATERIAL_TYPES = {
        'diagnosis': 'New Diagnosis Information',
        'medications': 'New Medication Information',
        'issues_plan': 'Current Visit Summary',
        'diet': 'Diet Plan',
        'exercise': 'Exercise Plan',
        'full_report': 'Comprehensive Health Report'
    };

    // Current active category
    let currentCategory = null;
    let currentGenId = null;

    // Initialize
    function init() {
        // Category card clicks
        document.querySelectorAll('.pm-category-card').forEach(function(card) {
            card.addEventListener('click', function() {
                const category = this.dataset.category;
                generateSingleMaterial(category);
            });
        });

        // Close button
        const closeBtn = document.getElementById('pmCloseBtn');
        if (closeBtn) {
            closeBtn.addEventListener('click', window.closePatientMaterialsModal);
        }

        // Back button
        const backBtn = document.getElementById('pmBackBtn');
        if (backBtn) {
            backBtn.addEventListener('click', showCategoryGrid);
        }

        // Print button
        const printBtn = document.getElementById('pmPrintBtn');
        if (printBtn) {
            printBtn.addEventListener('click', printCurrentMaterial);
        }
    }

    // Show category grid
    function showCategoryGrid() {
        const grid = document.getElementById('pmCategoryGrid');
        const panel = document.getElementById('pmContentPanel');
        
        if (grid) grid.classList.remove('hidden');
        if (panel) panel.classList.add('hidden');
        
        currentCategory = null;
    }

    // Show content panel
    function showContentPanel() {
        const grid = document.getElementById('pmCategoryGrid');
        const panel = document.getElementById('pmContentPanel');
        
        if (grid) grid.classList.add('hidden');
        if (panel) panel.classList.remove('hidden');
    }

    // Open modal
    window.openPatientMaterialsModal = function(genId) {
        if (!genId) {
            safeToast('Error', 'Generate a note first.', 'error');
            return;
        }

        currentGenId = genId;
        const modal = document.getElementById('patientMaterialsModal');
        if (!modal) return;

        modal.classList.remove('hidden');
        modal.classList.add('show');  // app shows modals via .modal.show { display:flex }
        modal.setAttribute('aria-hidden', 'false');

        // Always show the category selection first - never auto-generate
        showCategoryGrid();
    };

    // Close modal
    window.closePatientMaterialsModal = function() {
        const modal = document.getElementById('patientMaterialsModal');
        if (modal) {
            modal.classList.remove('show');
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
        }
    };

    // Generate a single material
    async function generateSingleMaterial(category) {
        if (!currentGenId) return;

        const noteTextarea = document.getElementById('generatedNote');
        if (!noteTextarea || !noteTextarea.value.trim()) {
            safeToast('Error', 'No note to generate materials from.', 'error');
            return;
        }

        currentCategory = category;
        const noteText = noteTextarea.value;

        // For diet/exercise, collect patient data first if not already provided
        if ((category === 'diet' || category === 'exercise') && !hasPatientData()) {
            showPatientDataForm(category);
            return;
        }

        window.patientMaterialsState.status = 'loading';

        // Show loading in the content panel
        showLoadingForCategory(category);

        try {
            // Get patient data from forms (for diet/exercise)
            const patientData = gatherPatientData();

            // Generate single material
            const response = await apiFetch('/patient-materials/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    gen_id: currentGenId,
                    material_type: category,
                    note_text: noteText,
                    patient_data: patientData
                })
            });

            if (!response.ok) {
                throw new Error('Failed to generate material: ' + response.status);
            }

            const data = await response.json();

            // If the backend reports an error, render it as a styled error box
            // (HTTP 200 is always returned; the error lives in data.error).
            if (data.error) {
                contentBody.innerHTML = buildErrorPanel(data.error, MATERIAL_TYPES[category]);
                showContentPanel();
                return;
            }

            // Update state
            if (!window.patientMaterialsState.materials) {
                window.patientMaterialsState.materials = {};
            }
            window.patientMaterialsState.materials[category] = data;
            window.patientMaterialsState.patientData = patientData;
            window.patientMaterialsState.generationId = currentGenId;
            window.patientMaterialsState.hasGenerated = true;
            window.patientMaterialsState.lastUpdated = Date.now();
            window.patientMaterialsState.status = 'ready';

            // Render this material
            renderSingleMaterial(category, data);

            // Save state
            savePatientMaterialsState();

        } catch (error) {
            console.error('Patient material generation failed:', error);
            hideLoading();
            safeToast('Error', 'Failed to generate ' + MATERIAL_TYPES[category] + ': ' + error.message, 'error');
        }
    }

    // Check if we already have patient data for diet/exercise
    function hasPatientData() {
        const data = window.patientMaterialsState.patientData || {};
        return data.weight_kg && data.height_cm && data.goal;
    }

    // Show patient data form for diet/exercise
    function showPatientDataForm(category) {
        showContentPanel();
        currentCategory = category;
        
        const contentBody = document.getElementById('pmContentBody');
        if (!contentBody) return;

        const title = category === 'diet' ? 'Diet Plan' : 'Exercise Plan';
        const saved = loadSavedPmInputs();
        const units = category === 'diet' ? 'kg' : 'kg (used for BMI and activity calculation)';

        contentBody.innerHTML = `
            <div class="pm-patient-data">
                <h3>Patient Data for ${title}</h3>
                <div class="form-group">
                    <label for="pmWeightKg">Weight (${units}):</label>
                    <input type="number" id="pmWeightKg" step="0.1" placeholder="e.g., 85" value="${saved.weight_kg != null ? saved.weight_kg : ''}">
                </div>
                <div class="form-group">
                    <label for="pmHeightCm">Height (cm):</label>
                    <input type="number" id="pmHeightCm" step="0.1" placeholder="e.g., 175" value="${saved.height_cm != null ? saved.height_cm : ''}">
                </div>
                <div class="pm-bmi-display" id="pmBmiDisplay"></div>
                <div class="form-group">
                    <label>Goal:</label>
                    <div class="radio-group">
                        <label class="radio-option"><input type="radio" name="pmGoal" value="maintain" ${saved.goal === 'maintain' ? 'checked' : ''}> Maintain weight</label>
                        <label class="radio-option"><input type="radio" name="pmGoal" value="increase" ${saved.goal === 'increase' ? 'checked' : ''}> Increase weight</label>
                        <label class="radio-option"><input type="radio" name="pmGoal" value="decrease" ${saved.goal === 'decrease' ? 'checked' : ''}> Decrease weight</label>
                    </div>
                </div>
                <button type="button" class="btn btn-primary" id="pmSubmitPatientData">Generate ${title}</button>
            </div>
        `;

        // Add submit handler
        const submitBtn = document.getElementById('pmSubmitPatientData');
        if (submitBtn) {
            submitBtn.addEventListener('click', submitPatientDataForm);
        }

        // BMI calculation
        const weightInput = document.getElementById('pmWeightKg');
        const heightInput = document.getElementById('pmHeightCm');
        if (weightInput && heightInput) {
            weightInput.addEventListener('input', calculateBMI);
            heightInput.addEventListener('input', calculateBMI);
        }
        calculateBMI();
    }

    // Submit patient data form
    function submitPatientDataForm() {
        const patientData = gatherPatientData();
        
        if (!patientData.weight_kg || !patientData.height_cm || !patientData.goal) {
            safeToast('Missing Information', 'Please enter weight, height, and goal.', 'warning');
            return;
        }

        window.patientMaterialsState.patientData = patientData;
        saveSavedPmInputs(patientData);
        savePatientMaterialsState();

        // Now generate the material
        generateSingleMaterial(currentCategory);
    }

    // Calculate BMI
    function calculateBMI() {
        const weightInput = document.getElementById('pmWeightKg');
        const heightInput = document.getElementById('pmHeightCm');
        const bmiDisplay = document.getElementById('pmBmiDisplay');

        if (weightInput && heightInput && bmiDisplay) {
            const weight = parseFloat(weightInput.value);
            const height = parseFloat(heightInput.value);

            if (weight > 0 && height > 0) {
                const heightM = height / 100;
                const bmi = weight / (heightM * heightM);
                bmiDisplay.textContent = 'BMI: ' + bmi.toFixed(1);
            } else {
                bmiDisplay.textContent = '';
            }
        }
    }

    // Show loading for a category
    function showLoadingForCategory(category) {
        showContentPanel();
        
        const contentBody = document.getElementById('pmContentBody');
        if (contentBody) {
            contentBody.innerHTML = `
                <div class="pm-loading">
                    <div class="pm-loading-spinner"></div>
                    <div class="pm-loading-text">Generating ${MATERIAL_TYPES[category]}...</div>
                </div>
            `;
        }
    }

    // Hide loading
    function hideLoading() {
        const contentBody = document.getElementById('pmContentBody');
        if (contentBody) {
            contentBody.innerHTML = '';
        }
    }

    // Render a single material
    function renderSingleMaterial(category, data) {
        const contentBody = document.getElementById('pmContentBody');
        const disclaimer = document.getElementById('pmDisclaimer');
        
        if (contentBody && data.content) {
            // Render markdown
            contentBody.innerHTML = renderMarkdown(data.content);
        }

        if (disclaimer && data.disclaimer) {
            disclaimer.innerHTML = renderMarkdown(data.disclaimer);
        }
    }

    // Gather patient data from forms
    // Persist diet/exercise inputs across sessions so they are not re-entered each time.
    const PM_INPUTS_KEY = 'pm_patient_inputs';
    function loadSavedPmInputs() {
        try { return JSON.parse(localStorage.getItem(PM_INPUTS_KEY) || '{}') || {}; }
        catch (e) { return {}; }
    }
    function saveSavedPmInputs(data) {
        try {
            const keep = {};
            ['weight_kg','height_cm','goal','activity_level','allergies','restrictions','joint_issues'].forEach(function (k) {
                if (data && data[k] != null && data[k] !== '') keep[k] = data[k];
            });
            localStorage.setItem(PM_INPUTS_KEY, JSON.stringify(keep));
        } catch (e) {}
    }

    function gatherPatientData() {
        const data = {};

        // Weight
        const weightInput = document.getElementById('pmWeightKg');
        if (weightInput && weightInput.value) {
            data.weight_kg = parseFloat(weightInput.value);
        }

        // Height
        const heightInput = document.getElementById('pmHeightCm');
        if (heightInput && heightInput.value) {
            data.height_cm = parseFloat(heightInput.value);
        }

        // Goal
        const goalRadio = document.querySelector('input[name="pmGoal"]:checked');
        if (goalRadio) {
            data.goal = goalRadio.value;
        }

        // Allergies
        const allergiesInput = document.getElementById('pmAllergies');
        if (allergiesInput && allergiesInput.value) {
            data.allergies = allergiesInput.value;
        }

        // Activity level
        const activitySelect = document.getElementById('pmActivityLevel');
        if (activitySelect && activitySelect.value) {
            data.activity_level = activitySelect.value;
        }

        // Dietary restrictions
        const restrictionsInput = document.getElementById('pmDietaryRestrictions');
        if (restrictionsInput && restrictionsInput.value) {
            data.restrictions = restrictionsInput.value;
        }

        // Joint issues
        const jointIssuesInput = document.getElementById('pmJointIssues');
        if (jointIssuesInput && jointIssuesInput.value) {
            data.joint_issues = jointIssuesInput.value;
        }

        return data;
    }

    // Print current material
    function printCurrentMaterial() {
        const contentBody = document.getElementById('pmContentBody');
        const disclaimer = document.getElementById('pmDisclaimer');
        
        if (!contentBody) return;

        // Create a new window with just the content
        const printWindow = window.open('', '_blank');
        if (printWindow) {
            printWindow.document.write('<!DOCTYPE html><html><head><title>Patient Material - DreamCision</title>');
            printWindow.document.write('<style>');
            printWindow.document.write('body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5; margin: 20px; }');
            printWindow.document.write('h1 { font-size: 16pt; text-align: center; margin-bottom: 10px; }');
            printWindow.document.write('h2 { font-size: 14pt; margin-top: 20px; }');
            printWindow.document.write('h3 { font-size: 12pt; }');
            printWindow.document.write('.disclaimer { font-size: 8pt; color: #666; margin-top: 30px; padding: 10px; border-top: 1px solid #ccc; }');
            printWindow.document.write('</style></head><body>');
            printWindow.document.write('<h1>DreamCision</h1>');
            printWindow.document.write('<p style="text-align: center; color: #666;">Patient Material</p>');
            printWindow.document.write(contentBody.innerHTML);
            printWindow.document.write('<div class="disclaimer">');
            printWindow.document.write(disclaimer ? disclaimer.innerHTML : '');
            printWindow.document.write('</div>');
            printWindow.document.write('</body></html>');
            printWindow.document.close();
            printWindow.print();
        }
    }

    // Save state
    function savePatientMaterialsState() {
        if (window.AuthWorkspace && window.AuthWorkspace.queueSave) {
            window.AuthWorkspace.queueSave();
        }
    }

    // Utility: safe toast
    function safeToast(title, message, type) {
        if (typeof showToast === 'function') {
            showToast(title, message, type);
        } else {
            console.error('[Patient Materials]', title, message);
        }
    }

    // Utility: build an error panel for LLM generation failures
    function buildErrorPanel(errorMsg, materialName) {
        return '<div class="pm-error">\n' +
            '<div class="pm-error-title">Failed to generate ' + escapeHtml(materialName || '') + '</div>\n' +
            '<div class="pm-error-message">' + escapeHtml(errorMsg || '') + '</div>\n' +
            '<div class="pm-error-hint">The AI language model may be temporarily unavailable. Please try again.</div>\n' +
            '</div>';
    }

    // Utility: escape HTML entities
    function escapeHtml(s) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(s));
        return div.innerHTML;
    }

    // Utility: render markdown
    function renderMarkdown(text) {
        if (!text) return '';
        // Use existing markdown renderer if available
        if (window.CNGMarkdown && window.CNGMarkdown.renderMarkdownSimple) {
            return window.CNGMarkdown.renderMarkdownSimple(text);
        }
        // Fallback: escape HTML first, then apply basic markdown
        var safe = escapeHtml(text)
            .replace(/^### (.+)$/gm, '<h3>$1</h3>')
            .replace(/^## (.+)$/gm, '<h2>$1</h2>')
            .replace(/^# (.+)$/gm, '<h1>$1</h1>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/^- (.+)$/gm, '<li>$1</li>')
            .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
            .replace(/\n/g, '<br>');
        return safe;
    }

    // Initialize on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
