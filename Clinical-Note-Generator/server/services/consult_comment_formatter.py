"""Consult comment structured output — JSON schema, severity scoring, confidence levels.

Replaces free-text output with structured JSON that the frontend can render
as severity-coded badges, collapsible sections, and action items.
"""
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Output schema (used in prompt and for parsing)
# ---------------------------------------------------------------------------

CONSULT_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "differential_diagnosis",
        "recommended_workup",
        "management_adjustments",
        "safety_alerts",
        "evidence_summary",
    ],
    "properties": {
        "differential_diagnosis": {
            "type": "array",
            "description": "Ranked differential diagnoses with rationale",
            "items": {
                "type": "object",
                "required": ["condition", "rank", "confidence", "rationale"],
                "properties": {
                    "condition": {"type": "string"},
                    "rank": {"type": "integer"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "moderate", "low", "speculative"],
                    },
                    "rationale": {"type": "string"},
                    "evidence_source": {"type": "string"},
                },
            },
        },
        "recommended_workup": {
            "type": "array",
            "description": "Tests and investigations to add now",
            "items": {
                "type": "object",
                "required": ["test", "urgency", "rationale"],
                "properties": {
                    "test": {"type": "string"},
                    "urgency": {
                        "type": "string",
                        "enum": ["stat", "urgent", "routine", "consider"],
                    },
                    "rationale": {"type": "string"},
                    "evidence_source": {"type": "string"},
                },
            },
        },
        "management_adjustments": {
            "type": "array",
            "description": "Therapy changes to consider",
            "items": {
                "type": "object",
                "required": ["change", "severity", "rationale"],
                "properties": {
                    "change": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "moderate", "low"],
                    },
                    "rationale": {"type": "string"},
                    "evidence_source": {"type": "string"},
                },
            },
        },
        "safety_alerts": {
            "type": "array",
            "description": "Red flags, safety issues, medication concerns",
            "items": {
                "type": "object",
                "required": ["alert", "severity", "action_required"],
                "properties": {
                    "alert": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "moderate", "low", "informational"],
                    },
                    "action_required": {"type": "string"},
                    "evidence_source": {"type": "string"},
                },
            },
        },
        "evidence_summary": {
            "type": "object",
            "required": ["sources_consulted", "evidence_quality"],
            "properties": {
                "sources_consulted": {
                    "type": "integer",
                    "description": "Number of distinct evidence sources used",
                },
                "evidence_quality": {
                    "type": "string",
                    "enum": ["strong", "moderate", "limited", "insufficient"],
                },
                "limitations": {"type": "string"},
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

STRUCTURED_CONSULT_PROMPT = """You are a senior clinical consultant writing an evidence-grounded addendum.

CRITICAL RULES:
- Use ONLY the Evidence Context provided below. Do not invent facts.
- Every claim MUST cite an evidence source tag (e.g., [RAG-1], [WEB-2]).
- Mark uncertain items with appropriate confidence levels.
- Output MUST be valid JSON matching the schema below.

JSON OUTPUT SCHEMA:
{
  "differential_diagnosis": [
    {
      "condition": "string",
      "rank": 1,
      "confidence": "high|moderate|low|speculative",
      "rationale": "Brief clinical reasoning grounding this differential",
      "evidence_source": "RAG-1 or WEB-2"
    }
  ],
  "recommended_workup": [
    {
      "test": "string",
      "urgency": "stat|urgent|routine|consider",
      "rationale": "Why this test should be added",
      "evidence_source": "RAG-1 or WEB-2"
    }
  ],
  "management_adjustments": [
    {
      "change": "string",
      "severity": "critical|high|moderate|low",
      "rationale": "Why this change is recommended",
      "evidence_source": "RAG-1 or WEB-2"
    }
  ],
  "safety_alerts": [
    {
      "alert": "string",
      "severity": "critical|high|moderate|low|informational",
      "action_required": "Specific action the clinician should take",
      "evidence_source": "RAG-1 or WEB-2"
    }
  ],
  "evidence_summary": {
    "sources_consulted": 0,
    "evidence_quality": "strong|moderate|limited|insufficient",
    "limitations": "Any caveats about the evidence"
  }
}

Rules for filling the schema:
1. DIFFERENTIAL DIAGNOSIS: Do NOT repeat diagnoses already confirmed in the assessment/impression. Generate alternative possibilities based on the patient's symptoms, clinical exam findings, and investigation results. Look for conditions that could explain the presentation but haven't been considered yet. Rank from most to least likely.
2. RECOMMENDED WORKUP: Identify ALL gaps in the current diagnostic plan. For each symptom, finding, or condition documented in the note, check if the appropriate workup has been done. If tests are missing, recommend them with specific guideline references. Look up current guidelines and best practices for each issue — don't just suggest generic tests. Be specific about what test, why it's needed, and what guideline supports it.
3. MANAGEMENT ADJUSTMENTS: Focus on optimizing the current therapeutic plan. Include: dose optimization (suboptimal per guidelines), therapy escalation/de-escalation based on response, guideline-aligned additions (evidence-based treatments missing from the plan), treatment duration adjustments, and monitoring plan changes. CRITICAL: Check for drug-drug interactions between the patient's current medications and recommend specific changes (adjust dose, switch, stop, add monitoring). This section is about active treatment modifications — not safety alerts (those go in safety_alerts) or missing tests (those go in recommended_workup).
4. Urgency: stat=immediately, urgent=within hours, routine=this visit, consider=if indicated.
5. Severity: critical=life-threatening, high=significant harm risk, moderate=important, low=minor.
6. Evidence quality: strong=RCTs/meta-analyses, moderate=cohort/guidelines, limited=case reports/expert opinion, insufficient=no direct evidence.
7. Include safety alerts ONLY for issues directly related to the patient's CURRENT medications and DOCUMENTED conditions. An empty array is valid if no genuine safety issues exist.
8. Do NOT alert about medications the patient is not taking. Do NOT speculate about hypothetical treatment scenarios.
9. Every item must have an evidence_source tag (e.g., RAG-1, WEB-3).

Original Note Excerpt:
{note_excerpt}

Confirmed / Ruled-Out Statements:
{assertions_text}

Evidence Context:
{evidence_context}

Focus Summary:
{focus_summary}

Now output ONLY valid JSON (no markdown, no extra text):"""


def build_structured_prompt(
    *,
    note_excerpt: str,
    assertions_text: str,
    evidence_context: str,
    focus_summary: str,
) -> str:
    """Build the structured JSON-output prompt for the LLM.

    Uses manual replacement to avoid Python .format() interpreting
    JSON schema curly braces as format field references.
    """
    prompt = STRUCTURED_CONSULT_PROMPT
    prompt = prompt.replace("{note_excerpt}", note_excerpt)
    prompt = prompt.replace("{assertions_text}", assertions_text)
    prompt = prompt.replace("{evidence_context}", evidence_context)
    prompt = prompt.replace("{focus_summary}", focus_summary)
    return prompt


# ---------------------------------------------------------------------------
# JSON parser / validator
# ---------------------------------------------------------------------------

def parse_consult_json(raw_output: str) -> Dict[str, Any]:
    """Attempt to parse LLM output as structured JSON.

    Handles common LLM wrapping patterns (```json, stray backticks, etc.).
    Returns a dict with 'raw' key on parse failure.
    """
    import json
    import re

    text = raw_output.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Legitimately safe: falling through to the substring parse / raw-text
        # fallback rendering ({'raw':..., 'parse_error':True}) is the designed
        # tolerant path for model output.
        pass

    # Try to find JSON object within the text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            # Legitimately safe: same tolerant fallback as above.
            pass

    # Return raw text for fallback rendering
    return {"raw": raw_output, "parse_error": True}


def is_valid_structured(parsed: Dict[str, Any]) -> bool:
    """Check if parsed output has the minimum required fields."""
    required = ["differential_diagnosis", "safety_alerts"]
    for field in required:
        if field not in parsed:
            return False
        if not isinstance(parsed[field], list):
            return False
    return True
