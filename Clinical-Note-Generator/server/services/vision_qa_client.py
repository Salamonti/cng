"""
Vision QA client for medical image questions.
Uses llama‑server's vision‑capable models with streaming.
Pattern matches SimpleNoteGenerator but with vision payloads.
"""
import asyncio
import base64
import json
import os
import time
from typing import AsyncIterator, Dict, List, Optional, Tuple

import aiohttp

from server.services.note_generator_clean import ExternalServiceError


class VisionQAEngine:
    """Async client for medical image Q&A with streaming."""
    
    def __init__(
        self,
        url: str = "",
        timeout: int = 90,
        model_name: str = "",
    ):
        # Primary URL from env or argument
        self.primary_url = (url or os.environ.get("VISION_QA_URL") or "").rstrip("/")
        if not self.primary_url:
            # Fallback to OCR URL (same server)
            self.primary_url = (os.environ.get("OCR_URL_PRIMARY") or "http://127.0.0.1:8081").rstrip("/")
        self.fallback_url = (os.environ.get("VISION_QA_URL_FALLBACK") or "").rstrip("/")
        self.timeout = timeout
        self.model_name = model_name.strip() or self._load_model_name()
        self._primary_down_until = 0.0
        self._cooldown_sec = 20.0
    
    def _load_model_name(self) -> str:
        """Get configured vision model name."""
        env_name = os.environ.get("VISION_QA_MODEL") or os.environ.get("OCR_MODEL_NAME")
        if env_name:
            name = env_name.strip()
            if name:
                return name
        return "ministral-14b"  # default assumption
    
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
                ["VISION_QA_URL not set (and no fallback configured)."]
            )
        return urls
    
    def _mark_primary_down(self):
        """Temporarily exclude primary URL."""
        if self.primary_url:
            self._primary_down_until = time.time() + self._cooldown_sec
    
    async def _discover_vision_models(self) -> List[str]:
        """Query /v1/models and return IDs likely to be vision‑capable."""
        urls = self._candidate_urls()
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for base_url in urls:
                try:
                    async with session.get(f"{base_url}/v1/models") as resp:
                        if resp.status != 200:
                            continue
                        js = await resp.json()
                        out = []
                        keywords = {"llava", "qwen", "vl", "vision", "ocr", "minicpm", "nanonets", "ministral"}
                        model_token = self.model_name.lower().strip()
                        if model_token:
                            keywords.add(model_token)
                        if isinstance(js, dict) and isinstance(js.get("data"), list):
                            for item in js["data"]:
                                if not isinstance(item, dict):
                                    continue
                                mid = str(item.get("id") or item.get("name") or "").strip()
                                low = mid.lower()
                                if any(k in low for k in keywords):
                                    out.append(mid)
                        return out
                except Exception:
                    continue
        return []
    
    async def _resolve_model_id(self) -> str:
        """Pick the actual deployed model id that matches our configured name."""
        configured = (self.model_name or "").strip()
        candidates = await self._discover_vision_models()
        if not candidates:
            return configured or "auto"
        
        if configured:
            target = configured.lower()
            base_target = os.path.basename(configured).lower()
            for cand in candidates:
                low = cand.lower()
                base_low = os.path.basename(cand).lower()
                if target and target in low:
                    return cand
                if base_target and base_target in base_low:
                    return cand
        
        return candidates[0]
    
    def _build_vision_payload(
        self,
        image_b64: str,
        mime_type: str,
        question: str,
        stream: bool = True,
    ) -> Dict:
        """Build OpenAI‑compatible chat payload with image."""
        data_uri = f"data:{mime_type};base64,{image_b64}"
        
        # Medical vision prompt with safety disclaimers
        prompt = (
            "You are a medical AI assistant. The user provides an image and asks:\n"
            f'"{question}"\n\n'
            "Based on the image, provide a helpful clinical analysis. Include:\n"
            "1. Relevant visual findings (if any)\n"
            "2. Possible interpretations/differential\n"
            "3. Safety red flags (if visible)\n"
            "4. Recommended next steps (imaging, labs, referral)\n\n"
            "Important disclaimers:\n"
            "- You are NOT a certified radiologist/pathologist\n"
            "- Describe only what you see; avoid over‑interpretation\n"
            "- If image contains text, transcribe only when relevant\n"
            "- If unsure, state uncertainty clearly\n\n"
            "Answer concisely with clinical utility."
        )
        
        return {
            "model": self.model_name,  # will be replaced with resolved ID
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
            "top_p": 0.9,
            "stream": stream,
        }
    
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
    ) -> AsyncIterator[str]:
        """Stream tokens from vision model."""
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        # Resolve model ID (async)
        model_id = await self._resolve_model_id()
        payload = self._build_vision_payload(image_b64, mime_type, question, stream=True)
        payload["model"] = model_id
        
        errors = []
        for base_url in self._candidate_urls():
            had_output = False
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