#!/usr/bin/env python3
"""
RLM Consult Note Generator - Uses your existing llama.cpp server at localhost:8081
Processes large medical records (>32K tokens) using recursive chunking with 8-12K context windows
"""

import requests
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RLMConsultNoteGenerator:
    """
    Recursive Language Model for generating consult notes from large medical records.
    Uses your existing llama.cpp server at localhost:8081
    """
    
    def __init__(self, 
                 specialist: str = "Internal Medicine",
                 api_base: str = "http://localhost:8081/v1",
                 context_window: int = 8192,  # Stay in your comfort zone
                 chunk_overlap: int = 500):
        
        self.specialist = specialist
        self.api_base = api_base.rstrip('/')
        self.context_window = context_window
        self.chunk_overlap = chunk_overlap
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Test connection to your server
        try:
            response = requests.get(f"{self.api_base.replace('/v1', '')}/health")
            if response.status_code == 200:
                logger.info(f"✅ Connected to llama.cpp server at {api_base}")
            else:
                logger.warning(f"⚠️ Server health check returned {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Cannot connect to server at {api_base}. Is it running?")
            logger.error(f"Error: {e}")
            raise
        
        # Base system prompt (your existing prompt)
        self.system_prompt = f"""You are a clinical documentation specialist for {self.specialist} in Nova Scotia, Canada. Current date is {self.current_date}.

Input sections are delimited by tags: <CURRENT_ENCOUNTER>, <PRIOR_VISITS>, and <LABS_IMAGING_OTHER>. Treat content according to the tag description and do not merge timelines. One or more of these sections might be empty, especially if the note type does not require the distinction.

Hard constraints: Output plain text only. Use ASCII characters only. Use normal spaces only (no non-breaking spaces). Do not use Unicode punctuation, smart quotes, arrows, superscripts, subscripts, em dashes or special symbols. Do not use bracket characters [] anywhere in the note.

Conflicts section rule: The Conflicts section must appear if and only if at least one genuine uncertainty, contradiction, missing critical item, inconsistent impression, inconsistent assessment , inconsistent plan or unverifiable detail exists. If no such issues exist, do not create a Conflicts section and do not write anything stating that there are no conflicts.

Source hierarchy and mixed data handling: You may receive mixed-quality data from multiple sources including transcription, referral letters, prior notes, laboratory data, and imaging reports. Treat the transcription as the current encounter on {self.current_date}. For non-transcribed data, prefer the most recent clearly dated source. Do not merge information from different dates into a single undated statement.

Grounding rule: Do not add new clinical facts, diagnoses, exam findings, investigations, treatments, medication changes, or plan items unless they are explicitly present in the provided source material or transcription. Do not guess or fabricate missing information.

Invalid or garbled data safeguard: If any data element (date, value, unit, medication name, dose, route, frequency, or sentence fragment) is incomplete, malformed, internally inconsistent, or cannot be reliably interpreted, do not repair or infer it. Either preserve it as documented or omit it if unsafe, and report the issue once in Conflicts.

Transcription quality handling: Expect spelling errors, misheard terms, missing punctuation, and fragmented sentences in the transcription. Do not invent missing words, meanings, or intent. If intent is unclear, treat it as uncertain and report it in Conflicts.

Uncertainty placement: Do not add uncertainty markers, verification tags, explanations, or commentary inline in any section. All uncertainty, corrections, and conflicts must appear only in the Conflicts section.

Patient Identification rules: Write exactly one sentence in professional clinical style. You may infer the appropriate honorific (Mr. or Mrs.) based on documented sex and name. Do not infer sex from name alone.

Patient Identification format (use if sex is explicitly documented): Mr. First Last, [age] year-old man, presented today for assessment regarding [reason]. If sex is explicitly documented as female: Mrs. First Last, [age] year-old woman, presented today for assessment regarding [reason].

Extract Physical Exam only from <CURRENT_ENCOUNTER>

If sex is not explicitly documented: First Last, [age] year-old, presented today for assessment regarding [reason].

Age rule (strict): If full DOB (YYYY-MM-DD) is explicitly provided and internally consistent, calculate age as of {self.current_date} using exact month/day comparison. Do not approximate. Do not use ages stated in prior-dated notes unless explicitly tied to the current encounter. If DOB is partial, conflicting, malformed, or unavailable, do not calculate age and omit it.

If age is omitted because it cannot be safely calculated, include DOB in the Patient Identification sentence only if DOB is explicitly provided, formatted as: DOB YYYY-MM-DD.

Do not include MRN, HCN, PHN, address, or any other identifiers.

History of Present Illness: Narrative paragraphs only. Do not use bullets or numbering. Do not write a checklist. Separate unrelated presentations into separate paragraphs if needed. Include dates or relative timing only if explicitly stated in the sources.

Conflict resolution: If sources disagree, do not resolve silently. In the Conflicts section, state what is uncertain or conflicting, cite each source with dates when available, and state what requires verification, addition or omission. Do not repeat the same issue more than once and do not reference internal model behavior.

You may include descriptive clinical reasoning, explanation, and narrative elements that reflect what was discussed, considered, explained, or decided during the encounter, as long as they are explicitly documented or clearly conveyed in the provided source material or transcription. This includes explaining why certain possibilities were considered or deferred, describing discussion with the patient, and outlining conditional future considerations that were explicitly mentioned. Such narrative must remain factual, non-speculative, and must not introduce new diagnoses, evidence, or management decisions beyond what is documented.

Each section may contain only its allowed content type. Do not include content that belongs to another section. If information is relevant but belongs to a different section, place it only in that section and do not repeat it elsewhere.
"""
        
        # Section-specific prompts for chunk processing
        self.section_prompts = {
            "hpi": """Extract ONLY the History of Present Illness from this chunk.
Focus on: chronology, symptoms, and events relevant to the current visit.
Return only the HPI content, no section headers or other information.""",
            
            "pmh": """Extract ONLY the Past Medical History from this chunk.
List each condition separately. Include dates if available.
Return only the PMH content, no section headers.""",
            
            "meds": """Extract ONLY the Medication list from this chunk.
Format each as: Generic Name Dose Unit Route Frequency
Return only the medication list, one per line.""",
            
            "allergies": """Extract ONLY Allergies from this chunk.
Return only the allergies, one per line or 'None documented'.""",
            
            "labs": """Extract ONLY Laboratory results from this chunk.
Include dates, values, and units. Focus on results relevant to current visit.
Return only the lab data, no interpretation.""",
            
            "imaging": """Extract ONLY Imaging results from this chunk.
Include dates, modality, and key findings.
Return only the imaging data, no interpretation.""",
        }
    
    def call_llm(self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.1) -> str:
        """
        Call your existing llama.cpp server at localhost:8081
        Uses OpenAI-compatible endpoint [citation:3][citation:6]
        """
        payload = {
            "model": "gpt-3.5-turbo",  # Model name is ignored by llama.cpp [citation:3]
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }
        
        try:
            response = requests.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                timeout=120  # 2 minute timeout for long generations
            )
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            logger.error("Request timed out")
            raise
        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            raise
    
    def chunk_document(self, text: str, chunk_size: Optional[int] = None) -> List[str]:
        """
        Split document into overlapping chunks at natural boundaries (section tags).
        """
        if chunk_size is None:
            chunk_size = self.context_window - 2000  # Reserve space for prompts
        
        if not text or len(text) <= chunk_size:
            return [text] if text else []
        
        # Try to split by section tags first
        sections = re.split(r'(<[^>]+>)', text)
        chunks = []
        current_chunk = ""
        
        for section in sections:
            # If adding this section would exceed chunk size, save current chunk and start new one
            if len(current_chunk) + len(section) > chunk_size and current_chunk:
                # Add overlap from previous chunk
                if chunks and self.chunk_overlap > 0:
                    overlap = current_chunk[-self.chunk_overlap:] if len(current_chunk) > self.chunk_overlap else current_chunk
                    current_chunk = overlap + section
                else:
                    chunks.append(current_chunk)
                    current_chunk = section
            else:
                current_chunk += section
        
        if current_chunk:
            chunks.append(current_chunk)
        
        logger.info(f"Split document into {len(chunks)} chunks")
        return chunks
    
    def process_chunk(self, chunk: str, section_focus: str = None) -> str:
        """
        Process a single chunk with optional section focus.
        """
        if not chunk or not chunk.strip():
            return ""
            
        if section_focus and section_focus in self.section_prompts:
            user_prompt = f"{self.section_prompts[section_focus]}\n\nChunk content:\n{chunk}"
        else:
            # Generic summarization prompt
            user_prompt = f"""Summarize the key medical information from this chunk.
Focus on facts relevant to a consult note: history, medications, allergies, labs, imaging.
Maintain all dates and specific values.

Chunk content:
{chunk}"""
        
        return self.call_llm(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1024
        )
    
    def recursive_process(self, 
                         current_encounter: str,
                         prior_visits: str,
                         labs_imaging: str,
                         depth: int = 0) -> Dict[str, str]:
        """
        Recursively process the medical record, building up sections.
        """
        logger.info(f"Processing at depth {depth}")
        
        # Combine all inputs with tags
        full_input = f"""
<CURRENT_ENCOUNTER>
{current_encounter}
</CURRENT_ENCOUNTER>

<PRIOR_VISITS>
{prior_visits}
</PRIOR_VISITS>

<LABS_IMAGING_OTHER>
{labs_imaging}
</LABS_IMAGING_OTHER>
"""
        
        # Check if we need chunking (rough character count, but token count would be ~chars/4)
        estimated_tokens = len(full_input) // 4
        if estimated_tokens > self.context_window - 2000:
            logger.info(f"Input too large (~{estimated_tokens} tokens), chunking...")
            
            # Process each section separately if they're large
            section_summaries = {}
            
            # Process current encounter
            if len(current_encounter) > self.context_window * 2:
                logger.info("Chunking current encounter...")
                chunks = self.chunk_document(current_encounter)
                section_summaries["current"] = []
                for i, chunk in enumerate(chunks):
                    logger.info(f"Processing current encounter chunk {i+1}/{len(chunks)}")
                    summary = self.process_chunk(chunk, "hpi")
                    section_summaries["current"].append(summary)
            else:
                section_summaries["current"] = [current_encounter] if current_encounter else []
            
            # Process prior visits
            if len(prior_visits) > self.context_window * 2:
                logger.info("Chunking prior visits...")
                chunks = self.chunk_document(prior_visits)
                section_summaries["prior"] = []
                for i, chunk in enumerate(chunks):
                    logger.info(f"Processing prior visits chunk {i+1}/{len(chunks)}")
                    summary = self.process_chunk(chunk, "pmh")
                    section_summaries["prior"].append(summary)
            else:
                section_summaries["prior"] = [prior_visits] if prior_visits else []
            
            # Process labs/imaging
            if len(labs_imaging) > self.context_window * 2:
                logger.info("Chunking labs/imaging...")
                chunks = self.chunk_document(labs_imaging)
                section_summaries["labs"] = []
                for i, chunk in enumerate(chunks):
                    logger.info(f"Processing labs/imaging chunk {i+1}/{len(chunks)}")
                    summary = self.process_chunk(chunk, "labs")
                    section_summaries["labs"].append(summary)
            else:
                section_summaries["labs"] = [labs_imaging] if labs_imaging else []
            
            # Combine summaries
            combined_encounter = "\n".join(section_summaries["current"]) if section_summaries["current"] else ""
            combined_prior = "\n".join(section_summaries["prior"]) if section_summaries["prior"] else ""
            combined_labs = "\n".join(section_summaries["labs"]) if section_summaries["labs"] else ""
            
            # Recursive call with condensed content
            return self.recursive_process(
                combined_encounter,
                combined_prior,
                combined_labs,
                depth + 1
            )
        else:
            # Base case: small enough to generate final note
            logger.info(f"Generating final consult note (estimated {estimated_tokens} tokens)...")
            final_note = self.generate_final_note(
                current_encounter,
                prior_visits,
                labs_imaging
            )
            return {"final_note": final_note, "depth": depth}
    
    def generate_final_note(self, 
                           current_encounter: str,
                           prior_visits: str,
                           labs_imaging: str) -> str:
        """
        Generate the final consult note from (possibly summarized) inputs.
        """
        consult_prompt = f"""Write a consult note for the current encounter, focused on answering the referral question only to the extent supported by the provided source material and transcription.

Focus on the active problem or problems relevant to the referral and to today's safety, risk stratification, or management.

Include chronic conditions only if they directly affect today's assessment, medication safety, risk, or disposition. Do not copy forward long chronic problem lists from prior notes.

Use only the following section titles and order: Patient ID, History of Present Illness, Past Medical History, Medications, Allergies, Social History, Physical Exam, Investigations, Impression, Plan. A Conflicts section may be added only under the conditions described below.

Patient Identification: Include patient name, age, and sex exactly as documented. Do not guess missing identifiers. You can include the reason for referral but avoid including new diagnoses that was determined during the visit.

History of Present Illness: Describe the current issue prompting the consult. You may begin with a brief, factual orienting summary of relevant recent events or prior encounters if explicitly documented and helpful for context. Focus on details that directly inform the referral question. Avoid including exam findings or investigations in this section as both has its own section. Avoid including physical exam findings, vital signs, investigation results, medication lists, or management decisions, except when explicitly documented as part of the narrative history.

Past Medical History: Include documented conditions only. Lists using bullets, dashes, or numbers are allowed.

Medications: When applicable, include exactly one Medications section representing the pre-encounter medication list only. Do not include medications started, stopped, or changed during the current encounter. Do not create a second, post-visit, or updated medication list.

Medications normalization: Internally normalize obvious and unambiguous medication misspellings, dosing, route, or brand generic equivalents that would be clearly understood by a clinician. Do not invent medications, formulations, routes, or indications. If a medication name could plausibly refer to more than one drug, formulation, or route, normalize based on context and report the uncertainty once in the Conflicts section. Report any medication normalization in the Conflicts section.

Medication handling in Plan: Do not comment on continuing, holding, or maintaining home medications unless an explicit medication instruction or change is documented. Any medication changes during the encounter must appear only in the Plan section and must not alter the Medications section.

Medications formatting: Generic names only. Each medication on its own line. Format exactly as: Generic Name Dose Unit Route Frequency. Use standard ASCII units and spacing. Newly started medications should be mentioned only in the Plan section. Medications changed during the encounter should appear unchanged in the Medications section and be described again after the change in the Plan section.

Medications conflicts: If there are conflicting medication details (name, dose, route, frequency) across sources, resolve them to the best of your abilities and report the conflict once in the Conflicts section. Do not report the conflict in the medication section.

Allergies: List exactly as documented. If not documented, write: Allergies: not documented, and report this in the Conflicts section.

Social History: Include only items explicitly documented. Do not add counseling, advice, or inferred behaviors.

Physical Exam: Include the findings explicitly for the current encounter. If the exam was explicitly deferred, write: Physical exam: Deferred. If the exam is not mentioned at all, write: Physical exam: Not documented, and report this in the Conflicts section.

Investigations: Include tests that is related to the cuurent visit, or materially affect current assessment or management. Do not list exhaustive historical results that do not add clinical value. When multiple similar results exist, summarize them concisely while preserving clinically meaningful trends, extremes, and the most recent relevant values. Include dates when available. Do not interpret results and do not label results as normal or abnormal. You may group results using prose or lists but do not create new section titles.

Impression: List each active problem relevant to the referral as a separate item. Use professional medical language appropriate to the strength of the evidence. Do not escalate certainty beyond what the source supports. Do not add new diagnoses. You may explain the rationale behind the impression if explicitly documented or clearly implied.

Plan: List each plan item separately. Include only actions, decisions, or recommendations explicitly stated in the source or transcription. Do not add generic counseling, preventive advice, or best-practice items unless explicitly documented. You may explain the rationale behind the proposed plan if documented.

Omit any section that is not supported by the source material, except where the system prompt explicitly requires fallback wording.

Conflicts section usage: Add a Conflicts section only when there are missing, ambiguous, or internally inconsistent data, OR when there are clinically important documented findings, problems, or considerations present in the source material that were not addressed in the Impression or Plan. The Conflicts section may highlight these issues but must not introduce new diagnoses, interpretations, or management recommendations, and must not resolve the conflicts.

Do not invent diagnoses, conclusions, or management decisions in order to answer the referral question.

Answer in plain text only. Do not use tables, Markdown, or any tabular formatting.

<CURRENT_ENCOUNTER>
{current_encounter}
</CURRENT_ENCOUNTER>

<PRIOR_VISITS>
{prior_visits}
</PRIOR_VISITS>

<LABS_IMAGING_OTHER>
{labs_imaging}
</LABS_IMAGING_OTHER>
"""
        
        return self.call_llm(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": consult_prompt}
            ],
            max_tokens=2048
        )
    
    def generate_consult_note(self,
                            current_encounter_text: str,
                            prior_visits_text: str = "",
                            labs_imaging_text: str = "") -> str:
        """
        Main entry point: Generate consult note from text strings.
        You can pass empty strings for sections that don't exist.
        """
        logger.info(f"Current encounter: {len(current_encounter_text)} chars")
        logger.info(f"Prior visits: {len(prior_visits_text)} chars")
        logger.info(f"Labs/imaging: {len(labs_imaging_text)} chars")
        
        # Process recursively
        result = self.recursive_process(
            current_encounter_text,
            prior_visits_text,
            labs_imaging_text
        )
        
        return result["final_note"]


def main():
    """
    Example usage - you can pass strings directly, no files needed.
    """
    # Configuration - adjust these
    SPECIALIST = "Internal Medicine"
    API_BASE = "http://localhost:8081/v1"  # Your llama.cpp server
    
    # Example medical record text - REPLACE WITH YOUR ACTUAL DATA
    current_encounter = """
    [Your current visit transcription here]
    Patient seen for follow-up of hypertension. Reports good compliance with medications.
    BP today 128/78. Will continue current regimen.
    """
    
    prior_visits = """
    [Your prior visit notes here]
    Past Medical History: Hypertension, Type 2 Diabetes
    Medications: Lisinopril 10mg daily, Metformin 500mg BID
    """
    
    labs_imaging = """
    [Your labs and imaging here]
    HbA1c: 7.2% (2025-12-15)
    Lipid panel: LDL 95, HDL 45 (2025-12-15)
    """
    
    # Initialize generator
    generator = RLMConsultNoteGenerator(
        specialist=SPECIALIST,
        api_base=API_BASE,
        context_window=8192  # Stay in your comfort zone
    )
    
    # Generate note
    try:
        consult_note = generator.generate_consult_note(
            current_encounter_text=current_encounter,
            prior_visits_text=prior_visits,
            labs_imaging_text=labs_imaging
        )
        
        # Print result
        print("\n" + "="*50)
        print("GENERATED CONSULT NOTE:")
        print("="*50)
        print(consult_note)
        
        # Optionally save to file
        with open("consult_note_output.txt", "w", encoding="utf-8") as f:
            f.write(consult_note)
        logger.info("Consult note saved to consult_note_output.txt")
        
    except Exception as e:
        logger.error(f"Error generating consult note: {e}")
        raise


if __name__ == "__main__":
    main()