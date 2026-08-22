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

        // Diet/exercise: ALWAYS go through the API first. The backend reads
        // the note and returns either a generated plan (note-based data plus
        // any previously-entered values cover the blocking fields) or a
        // needs_input response that opens the form pre-filled with what the
        // note said, so the clinician only confirms/fills the gaps.

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

            // Interactive param-completion flow: the note was read first and
            // still lacks blocking fields -> open the form pre-filled from
            // what the note said; the clinician confirms/fills only the gaps.
            if (data.status === 'needs_input') {
                showPatientDataForm(category, data.known_data || {}, data.missing_fields || []);
                return;
            }

            // If the backend reports an error, render it as a styled error box
            // (HTTP 200 is always returned; the error lives in data.error).
            if (data.error) {
                // contentBody was referenced here without ever being declared in
                // this scope (every other use does getElementById first), so the
                // error-DISPLAY path threw ReferenceError and masked the real
                // backend error with "contentBody is not defined".
                const errorBody = document.getElementById('pmContentBody');
                if (errorBody) {
                    errorBody.innerHTML = buildErrorPanel(data.error, MATERIAL_TYPES[category]);
                }
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
            // Report explicitly. This error is caught, so it never reaches
            // window.onerror and produced no telemetry at all -- and this is the
            // exact branch that rendered "contentBody is not defined" to
            // clinicians while the backend was healthy. A console line on the
            // clinician's own machine is not somewhere an operator can look.
            if (typeof window.reportClientError === 'function') {
                window.reportClientError(
                    'patient materials (' + category + '): ' + ((error && error.message) || String(error)),
                    error && error.stack,
                    'caught'
                );
            }
            hideLoading();
            // Reading .message off a null/undefined throw would throw again here,
            // inside the handler whose only job is to report the first failure.
            var detail = (error && error.message) || 'The AI service is temporarily unavailable. Please try again.';
            safeToast('Error', 'Failed to generate ' + MATERIAL_TYPES[category] + ': ' + detail, 'error');
        }
    }

    // Show patient data form for diet/exercise.
    // noteExtracted (optional): values read from the note by the backend.
    //   Used to pre-fill fields the clinician didn't enter; shown with a
    //   "from note" marker so they know to verify it.
    // missingFields (optional): the blocking fields still absent.
    function showPatientDataForm(category, noteExtracted, missingFields) {
        showContentPanel();
        currentCategory = category;
        
        const contentBody = document.getElementById('pmContentBody');
        if (!contentBody) return;

        const extract = noteExtracted || {};
        const missing = Array.isArray(missingFields) ? missingFields : [];
        const hasExtract = Object.keys(extract).some(function (k) { return extract[k] != null && extract[k] !== ''; });

        const fromNoteTag = '<span class="pm-from-note">from note</span>';
        const missingMark = function (field) {
            return missing.indexOf(field) !== -1 ? '<span class="pm-missing-mark">required</span>' : '';
        };

        const saved = loadSavedPmInputs();
        // Precedence: previously-saved/entered value > note extraction > empty.
        const prefilled = function (key, fromSaved) {
            if (fromSaved != null && fromSaved !== '') return fromSaved;
            if (extract[key] != null && extract[key] !== '') return extract[key];
            return '';
        };
        const hadNote = function (key) {
            return extract[key] != null && extract[key] !== '';
        };

        const title = category === 'diet' ? 'Diet Plan' : 'Exercise Plan';
        const units = category === 'diet' ? 'kg' : 'kg (used for BMI and activity calculation)';

        const banner = hasExtract ? (
            '<div class="pm-extract-banner">Read from your note: <b>' +
            Object.keys(extract).filter(function (k) { return extract[k] != null && extract[k] !== ''; }).join(', ') +
            '</b>. Verify and complete the highlighted fields below.</div>'
        ) : '';

        const ageTag = hadNote('age') ? fromNoteTag : '';
        const sexTag = hadNote('sex') ? fromNoteTag : '';
        const weightTag = hadNote('weight_kg') ? fromNoteTag : '';
        const heightTag = hadNote('height_cm') ? fromNoteTag : '';
        const goalTag = hadNote('goal') ? fromNoteTag : '';

        const extraFields = category === 'exercise' ? `
                <div class="form-group">
                    <label for="pmActivityLevel">Activity Level (optional):</label>
                    <select id="pmActivityLevel">
                        <option value="" ${!prefilled('activity_level', saved.activity_level) ? 'selected' : ''}>Not specified (assumed lightly active)</option>
                        <option value="sedentary" ${prefilled('activity_level', saved.activity_level) === 'sedentary' ? 'selected' : ''}>Sedentary (little or no exercise)</option>
                        <option value="lightly_active" ${prefilled('activity_level', saved.activity_level) === 'lightly_active' ? 'selected' : ''}>Lightly active (light exercise 1-3 days/week)</option>
                        <option value="moderately_active" ${prefilled('activity_level', saved.activity_level) === 'moderately_active' ? 'selected' : ''}>Moderately active (moderate exercise 3-5 days/week)</option>
                        <option value="very_active" ${prefilled('activity_level', saved.activity_level) === 'very_active' ? 'selected' : ''}>Very active (hard exercise 6-7 days/week)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="pmJointIssues">Joint / mobility issues (optional):</label>
                    <input type="text" id="pmJointIssues" placeholder="e.g., knee arthritis, avoid high-impact" value="${prefilled('joint_issues', saved.joint_issues)}">
                </div>` : `
                <div class="form-group">
                    <label for="pmAllergies">Allergies (optional):</label>
                    <input type="text" id="pmAllergies" placeholder="e.g., peanuts, shellfish" value="${prefilled('allergies', saved.allergies)}">
                </div>
                <div class="form-group">
                    <label for="pmDietaryRestrictions">Dietary restrictions (optional):</label>
                    <input type="text" id="pmDietaryRestrictions" placeholder="e.g., diabetic, low-sodium, vegetarian" value="${prefilled('restrictions', saved.restrictions)}">
                </div>`;

        contentBody.innerHTML = `
            <div class="pm-patient-data">
                <h3>Patient Data for ${title}</h3>
                ${banner}
                <div class="pm-demographics">
                    <div class="form-group">
                        <label for="pmAge">Age (years) ${ageTag}:</label>
                        <input type="number" id="pmAge" step="1" placeholder="e.g., 68" value="${prefilled('age', '')}">
                    </div>
                    <div class="form-group">
                        <label for="pmSex">Sex ${sexTag}:</label>
                        <select id="pmSex">
                            <option value="" ${prefilled('sex', '') === '' ? 'selected' : ''}>Not specified</option>
                            <option value="female" ${prefilled('sex', '') === 'female' ? 'selected' : ''}>Female</option>
                            <option value="male" ${prefilled('sex', '') === 'male' ? 'selected' : ''}>Male</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label for="pmWeightKg">Weight (${units}) ${weightTag} ${missingMark('weight_kg')}:</label>
                    <input type="number" id="pmWeightKg" step="0.1" placeholder="e.g., 85" value="${prefilled('weight_kg', saved.weight_kg)}">
                </div>
                <div class="form-group">
                    <label for="pmHeightCm">Height (cm) ${heightTag} ${missingMark('height_cm')}:</label>
                    <input type="number" id="pmHeightCm" step="0.1" placeholder="e.g., 175" value="${prefilled('height_cm', saved.height_cm)}">
                </div>
                <div class="pm-bmi-display" id="pmBmiDisplay"></div>
                <div class="form-group">
                    <label>Goal ${goalTag} ${missingMark('goal')}:</label>
                    <div class="radio-group">
                        <label class="radio-option"><input type="radio" name="pmGoal" value="maintain" ${prefilled('goal', saved.goal) === 'maintain' ? 'checked' : ''}> Maintain weight</label>
                        <label class="radio-option"><input type="radio" name="pmGoal" value="increase" ${prefilled('goal', saved.goal) === 'increase' ? 'checked' : ''}> Increase weight</label>
                        <label class="radio-option"><input type="radio" name="pmGoal" value="decrease" ${prefilled('goal', saved.goal) === 'decrease' ? 'checked' : ''}> Decrease weight</label>
                    </div>
                </div>
                ${extraFields}
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
        // Activity level is intentionally NOT required: the plan states its
        // assumption when it is absent.

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
        catch (e) {
            // (a) Best-effort load of persisted patient-materials inputs; a
            // corrupt entry (or blocked storage) falls back to empty defaults.
            return {};
        }
    }
    function saveSavedPmInputs(data) {
        try {
            const keep = {};
            ['age','sex','weight_kg','height_cm','goal','activity_level','allergies','restrictions','joint_issues'].forEach(function (k) {
                if (data && data[k] != null && data[k] !== '') keep[k] = data[k];
            });
            localStorage.setItem(PM_INPUTS_KEY, JSON.stringify(keep));
        } catch (e) {
            // (a) Best-effort persistence of patient-materials form inputs; a
            // quota/blocked-storage failure must not break material generation.
        }
    }

    function gatherPatientData() {
        // Start from the last-saved patient data (if any) so that re-clicking
        // Diet/Exercise after a successful generation still sends valid vitals,
        // even though the form inputs were replaced by the rendered material (so
        // they are no longer in the DOM). Overlay any inputs still present so the
        // form path (first click / submitPatientDataForm) works unchanged.
        const data = Object.assign({}, window.patientMaterialsState.patientData || {});

        // Age
        const ageInput = document.getElementById('pmAge');
        if (ageInput && ageInput.value) {
            data.age = parseInt(ageInput.value, 10);
        }

        // Sex
        const sexSelect = document.getElementById('pmSex');
        if (sexSelect && sexSelect.value) {
            data.sex = sexSelect.value;
        }

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
