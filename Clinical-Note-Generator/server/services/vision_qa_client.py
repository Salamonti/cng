"""Vision client used to extract grounded evidence from uploaded images."""
import base64
import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import aiohttp

from server.core.clinical_output_guard import (
    IncrementalDegenerationGuard,
    detect_degenerate_output,
)
from server.core.llm_routing import resolve_qa_vision_urls
from server.core.llm_request_policy import (
    apply_dreamcision_generation_policy,
    strip_reasoning_markup,
)
from server.services.note_generator_clean import ExternalServiceError


_EVIDENCE_LIST_FIELDS = (
    "observations",
    "visible_text",
    "measurements",
    "quality_limitations",
    "uncertainties",
)


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[:max_chars]


def normalize_visual_evidence(value: Any, raw_fallback: str = "") -> Dict[str, Any]:
    """Normalize model JSON into a small, predictable evidence contract."""
    source = value if isinstance(value, dict) else {}
    result: Dict[str, Any] = {
        "image_type": _bounded_text(source.get("image_type") or "unknown", 160) or "unknown",
    }
    for field in _EVIDENCE_LIST_FIELDS:
        raw = source.get(field)
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        cleaned = [_bounded_text(item, 1200) for item in items[:32]]
        result[field] = [item for item in cleaned if item]

    fallback = strip_reasoning_markup(raw_fallback)
    if fallback and not result["observations"] and not result["visible_text"]:
        result["observations"] = [_bounded_text(fallback, 4000)]
    return result


def format_visual_evidence(evidence: Dict[str, Any], max_chars: int = 12000) -> str:
    """Render evidence for the text model without adding interpretation."""
    text = json.dumps(normalize_visual_evidence(evidence), ensure_ascii=True, indent=2)
    return text[:max_chars]


class VisionQAEngine:
    """Async client for a shared OpenAI-compatible vision endpoint."""
    
    def __init__(
        self,
        url: str = "",
        timeout: int = 90,
        model_name: str = "",
    ):
        rp, rf = resolve_qa_vision_urls()
        if url and str(url).strip():
            self.primary_url = str(url).strip().rstrip("/")
        else:
            self.primary_url = (rp or "").rstrip("/")
        self.fallback_url = (rf or "").rstrip("/")
        self.timeout = timeout
        self.model_name = model_name.strip() or self._load_model_name()
        self._primary_down_until = 0.0
        self._cooldown_sec = 20.0
        self._model_id_cache: Dict[str, Tuple[str, float]] = {}
        self._model_cache_ttl_sec = 300.0
    
    def _load_model_name(self) -> str:
        """Optional override; empty means discover from /v1/models on the target URL."""
        env_name = os.environ.get("VISION_QA_MODEL") or os.environ.get("OCR_MODEL_NAME")
        if env_name:
            name = env_name.strip()
            if name:
                return name
        return ""
    
    def _candidate_urls(self) -> List[str]:
        """Return list of candidate server URLs (primary first, if not down)."""
        urls = []
        now = time.time()
        if self.primary_url and now >= self._primary_down_until:
            urls.append(self.primary_url)
        if self.fallback_url and self.fallback_url not in urls:
            urls.append(self.fallback_url)
        if not urls:
            raise ExternalServiceError(
                "vision_qa",
                self.primary_url,
                self.fallback_url,
                ["No vision QA base URL (set LLM_QA_VISION_URL / VISION_QA_URL / OCR_URL_PRIMARY)."]
            )
        return urls
    
    def _mark_primary_down(self):
        """Temporarily exclude primary URL."""
        if self.primary_url:
            self._primary_down_until = time.time() + self._cooldown_sec
    
    async def _resolve_model_id_for_url(self, base_url: str) -> str:
        """Use the model id served at this base URL (vLLM Gemma on :8081), not legacy ministral paths."""
        now = time.time()
        cached = self._model_id_cache.get(base_url)
        if cached and cached[1] > now:
            return cached[0]

        override = (
            os.environ.get("VISION_QA_MODEL")
            or os.environ.get("OCR_MODEL_NAME")
            or self.model_name
        )
        if isinstance(override, str) and override.strip():
            mid = override.strip()
            self._model_id_cache[base_url] = (mid, now + self._model_cache_ttl_sec)
            return mid

        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{base_url}/v1/models") as resp:
                    if resp.status == 200:
                        js = await resp.json()
                        data = js.get("data") if isinstance(js, dict) else None
                        if isinstance(data, list) and data:
                            item = data[0] if isinstance(data[0], dict) else {}
                            mid = str(item.get("id") or item.get("name") or "").strip()
                            if mid:
                                self._model_id_cache[base_url] = (mid, now + self._model_cache_ttl_sec)
                                return mid
        except Exception:
            pass

        return self.model_name or "auto"
    
    _MAX_ASSISTANT_CHARS = 8000

    @staticmethod
    def _evidence_instruction(question: str) -> str:
        q = (question or "").strip()
        return (
            "You are the visual evidence extraction stage of a clinical application. "
            "Inspect the image carefully and return only a JSON object. Do not answer the user's question, "
            "diagnose, provide a differential, recommend management, or infer findings that are not visible. "
            "The question is context for deciding which objective details need close inspection.\n\n"
            f"Context question: {q}\n\n"
            "Use exactly these keys:\n"
            '{"image_type":"document|browser|photograph|CXR|CT|other",'
            '"observations":["objective visible finding"],'
            '"visible_text":["verbatim visible text"],'
            '"measurements":["visible measurement with units"],'
            '"quality_limitations":["crop, blur, projection, missing series, or other limitation"],'
            '"uncertainties":["detail that cannot be established from the image"]}\n\n'
            "For documents and browser screenshots, prioritize complete text and control/state extraction. "
            "For medical imaging, describe only visible patterns, location, laterality, and measurements. "
            "Use empty arrays when a category has no evidence. Output JSON only."
        )

    def _build_evidence_payload(
        self,
        image_b64: str,
        mime_type: str,
        question: str,
    ) -> Dict[str, Any]:
        data_uri = f"data:{mime_type};base64,{image_b64}"
        try:
            max_tokens = int(os.environ.get("VISION_EVIDENCE_MAX_TOKENS", "2048"))
        except (TypeError, ValueError):
            max_tokens = 2048
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": self._evidence_instruction(question)},
                    ],
                }
            ],
            "max_tokens": max(512, min(max_tokens, 3072)),
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
        }
        return dict(apply_dreamcision_generation_policy(payload, profile="vision"))

    @staticmethod
    def _message_content(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        return content.strip() if isinstance(content, str) else ""

    @staticmethod
    def _parse_evidence_json(content: str) -> Dict[str, Any]:
        cleaned = strip_reasoning_markup(content)
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
        try:
            parsed = json.loads(cleaned)
            return normalize_visual_evidence(parsed, cleaned)
        except (TypeError, ValueError, json.JSONDecodeError):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return normalize_visual_evidence(json.loads(cleaned[start : end + 1]), cleaned)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return normalize_visual_evidence({}, cleaned)

    async def extract_visual_evidence(
        self,
        image_bytes: bytes,
        mime_type: str,
        question: str,
    ) -> Dict[str, Any]:
        """Return grounded visual evidence; never a final clinical answer."""
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = self._build_evidence_payload(image_b64, mime_type, question)
        errors: List[str] = []

        for base_url in self._candidate_urls():
            payload["model"] = await self._resolve_model_id_for_url(base_url)
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise RuntimeError(f"Vision model HTTP {response.status}: {error_text[:200]}")
                        data = await response.json()

                choices = data.get("choices") if isinstance(data, dict) else None
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                if choice.get("finish_reason") == "length":
                    raise RuntimeError("Visual evidence extraction reached its output token limit")
                content = self._message_content(data)
                if not content:
                    raise RuntimeError("Vision model returned no visual evidence")
                degeneration = detect_degenerate_output(content)
                if degeneration:
                    raise RuntimeError(
                        "Visual evidence output rejected: " + "; ".join(degeneration)
                    )
                await self._reset_context(base_url)
                return self._parse_evidence_json(content)
            except Exception as exc:
                if base_url == self.primary_url:
                    self._mark_primary_down()
                errors.append(f"{base_url}: {exc}")

        raise ExternalServiceError(
            "vision_evidence",
            self.primary_url,
            self.fallback_url,
            errors or ["Vision model returned no visual evidence"],
        )

    def _first_turn_instruction(self, question: str) -> str:
        """Full prompt for the initial image question (single-turn or first message in multi-turn)."""
        q = (question or "").strip()
        return (
            "You are a medical AI assistant. The user provides an image and asks:\n\n"
            "## Current question\n"
            f'"{q}"\n\n'
            "Based on the image, provide a helpful clinical analysis. Cover these themes (use Markdown headings; "
            "for ordered lists use a single list with 1. 2. 3. … in sequence, not repeated 1.):\n"
            "- Relevant visual findings (if any)\n"
            "- Possible interpretations / differential\n"
            "- Safety red flags (if visible)\n"
            "- Recommended next steps (imaging, labs, referral)\n\n"
            "Important disclaimers:\n"
            "- You are NOT a certified radiologist/pathologist\n"
            "- Describe only what you see; avoid over‑interpretation\n"
            "- If image contains text, transcribe only when relevant\n"
            "- If unsure, state uncertainty clearly\n\n"
            "Answer with clinical utility. Be specific to this question."
        )

    def _followup_turn_instruction(self, question: str) -> str:
        """User message only (no image) — same image as in the first turn."""
        q = (question or "").strip()
        return (
            "You are continuing the same medical image Q&A session. The image was already attached in the first message.\n\n"
            "## Follow-up question\n"
            f'"{q}"\n\n'
            "Answer this follow-up directly and specifically. Do not repeat your entire prior answer unless the user "
            "asks for a recap or summary. Build on prior context when helpful. If the question needs new detail from "
            "the image, refer to what you see."
        )

    def _build_vision_payload(
        self,
        image_b64: str,
        mime_type: str,
        question: str,
        stream: bool = True,
        prior_conversation: str = "",
        session_context: str = "",
    ) -> Dict:
        """Build OpenAI‑compatible chat payload with image (single user turn; legacy prior text)."""
        data_uri = f"data:{mime_type};base64,{image_b64}"

        prior = (prior_conversation or "").strip()
        sess = (session_context or "").strip()
        prior_block = (
            "## Prior messages in this session (context only; the image is the same unless stated otherwise)\n"
            + prior
            + "\n\n"
            if prior
            else ""
        )
        prompt = self._first_turn_instruction(question)
        if sess:
            prompt = sess + "\n\n---\n\n" + prompt
        if prior_block:
            prompt = prior_block + prompt

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": 4096,
            "temperature": 0.15,
            "top_p": 0.92,
            "stream": stream,
        }
        return dict(apply_dreamcision_generation_policy(payload, profile="vision"))

    def _build_multi_turn_payload(
        self,
        image_b64: str,
        mime_type: str,
        vision_turns: List[Dict[str, Any]],
        current_question: str,
        stream: bool = True,
        session_context: str = "",
    ) -> Dict:
        """Multi-turn: image only on the first user message; follow-ups are text-only user messages.

        `vision_turns` are completed Q&A pairs for this session (not including `current_question`).
        """
        data_uri = f"data:{mime_type};base64,{image_b64}"
        messages: List[Dict[str, Any]] = []

        if not vision_turns:
            return self._build_vision_payload(
                image_b64,
                mime_type,
                current_question,
                stream=stream,
                prior_conversation="",
                session_context=session_context,
            )

        first_q = (vision_turns[0].get("q") or "").strip()
        first_user_text = self._first_turn_instruction(first_q or current_question)
        sess = (session_context or "").strip()
        if sess:
            first_user_text = sess + "\n\n---\n\n" + first_user_text
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": first_user_text},
                ],
            }
        )

        for i, t in enumerate(vision_turns):
            a = (t.get("a") or "").strip()
            if len(a) > self._MAX_ASSISTANT_CHARS:
                a = a[: self._MAX_ASSISTANT_CHARS] + "\n\n[…truncated…]"
            messages.append({"role": "assistant", "content": a})
            if i + 1 < len(vision_turns):
                nq = (vision_turns[i + 1].get("q") or "").strip()
                messages.append({"role": "user", "content": self._followup_turn_instruction(nq)})

        messages.append({"role": "user", "content": self._followup_turn_instruction(current_question)})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.28,
            "top_p": 0.92,
            "stream": stream,
        }
        return dict(apply_dreamcision_generation_policy(payload, profile="vision"))
    
    @staticmethod
    def _extract_stream_content(data: Dict) -> Optional[str]:
        """Extract content from streaming response chunk."""
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            delta = choice.get("delta")
            if isinstance(delta, dict) and "content" in delta:
                return delta["content"]
            message = choice.get("message")
            if isinstance(message, dict) and "content" in message:
                return message["content"]
        return None
    
    async def stream_vision_answer(
        self,
        image_bytes: bytes,
        mime_type: str,
        question: str,
        prior_conversation: str = "",
        vision_history: Optional[List[Dict[str, Any]]] = None,
        session_context: str = "",
    ) -> AsyncIterator[str]:
        """Stream tokens from vision model.

        When `vision_history` has prior vision turns, builds a real multi-turn chat: image is sent only on the
        first user message; follow-ups are text-only (better follow-up answers, no duplicate first reply).

        `session_context` carries text-only Q&A + rolling summary when the user mixed text and image in one session.
        """
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        vh = [t for t in (vision_history or []) if (t.get("channel") or "text").lower() == "vision"]
        sess = (session_context or "").strip()

        if len(vh) > 0:
            payload = self._build_multi_turn_payload(
                image_b64, mime_type, vh, question, stream=True, session_context=sess
            )
        else:
            payload = self._build_vision_payload(
                image_b64,
                mime_type,
                question,
                stream=True,
                prior_conversation=prior_conversation,
                session_context=sess,
            )

        errors = []
        for base_url in self._candidate_urls():
            model_id = await self._resolve_model_id_for_url(base_url)
            payload["model"] = model_id
            had_output = False
            output_guard = IncrementalDegenerationGuard()
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise RuntimeError(f"Vision model HTTP {response.status}: {error_text[:200]}")
                        
                        async for line_bytes in response.content:
                            if not line_bytes:
                                continue
                            for raw_line in line_bytes.decode("utf-8", errors="ignore").splitlines():
                                if not raw_line.startswith("data: "):
                                    continue
                                data_str = raw_line[6:].strip()
                                if not data_str or data_str == "[DONE]":
                                    continue
                                try:
                                    data = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue
                                
                                content = self._extract_stream_content(data)
                                if content:
                                    output_guard.add(content)
                                    had_output = True
                                    yield content
                        
                        # Successfully consumed entire stream
                        await self._reset_context(base_url)
                        return
                        
            except Exception as exc:
                if base_url == self.primary_url:
                    self._mark_primary_down()
                errors.append(f"{base_url}: {exc}")
                if had_output:
                    raise
                continue
        
        raise ExternalServiceError(
            "vision_qa",
            self.primary_url,
            self.fallback_url,
            errors or ["Vision model returned no output"]
        )
    
    async def _reset_context(self, base_url: str):
        """Reset llama.cpp context (best‑effort)."""
        timeout = aiohttp.ClientTimeout(total=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/command",
                    json={"cmd": "reset"},
                    headers={"Content-Type": "application/json"},
                ):
                    pass
        except Exception:
            pass
