# server/services/ocr_llm_client.py
import base64
import os
import re
import time
from typing import Tuple, Dict, Optional, List
import requests
from requests import Session

from server.core.llm_routing import resolve_ocr_llm_urls

OCR_MAX_TOKENS = max(128, int(os.environ.get("OCR_MAX_TOKENS", "1024")))


class OCRLLMEngine:
    """Client for llama-server OCR using the configured multimodal model."""

    def __init__(self, url: str = "", timeout: int = 90, server_url: str | None = None):
        # Accept either url= or server_url= for compatibility
        base = server_url or url
        self.url = base.rstrip("/")
        self.timeout = timeout
        self._session: Session = requests.Session()
        self._warmed: bool = False
        self.model_name = self._load_model_name()
        rp, rf = resolve_ocr_llm_urls()
        self.primary_url = self.url if self.url else (rp or "")
        self.fallback_url = rf
        self._primary_down_until = 0.0
        self._cooldown_sec = 20.0
        self._model_id_cache: Dict[str, Tuple[str, float]] = {}
        self._model_cache_ttl_sec = 300.0

    def _load_model_name(self) -> str:
        """Optional override; empty means discover from /v1/models on the target URL."""
        env_name = os.environ.get("OCR_MODEL_NAME") or os.environ.get("OCR_CHAT_MODEL")
        if env_name:
            name = env_name.strip()
            if name:
                return name
        return ""

    def check_server(self) -> bool:
        """Check if model server is running"""
        try:
            response = requests.get(f"{self.primary_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def _resolve_model_id_for_url(self, base_url: str) -> str:
        """Use the model id served at this base URL (vLLM Gemma on :8081), not legacy OCR model names."""
        now = time.time()
        cached = self._model_id_cache.get(base_url)
        if cached and cached[1] > now:
            return cached[0]

        override = (
            os.environ.get("OCR_MODEL_NAME")
            or os.environ.get("OCR_CHAT_MODEL")
            or self.model_name
        )
        if isinstance(override, str) and override.strip():
            mid = override.strip()
            self._model_id_cache[base_url] = (mid, now + self._model_cache_ttl_sec)
            return mid

        try:
            r = self._session.get(f"{base_url}/v1/models", timeout=5)
            if r.ok:
                js = r.json()
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

    def _warmup(self) -> None:
        if self._warmed:
            return
        try:
            # cheap health check; ignore errors
            self._session.get(f"{self.primary_url}/health", timeout=3)
        except Exception:
            pass
        self._warmed = True

    def _flush_server_context(self, base_url: str) -> None:
        """Ask the OCR server to release cached KV data (best-effort)."""
        try:
            self._session.post(f"{base_url}/command", json={"cmd": "reset"}, timeout=3)
        except Exception:
            pass

    def ocr_image_bytes(
        self,
        image_bytes: bytes,
        mime_type: Optional[str] = None,
        _attempt: int = 0,
    ) -> Tuple[str, float]:
        """Process image using pinned fast path by default; legacy fallback behind OCR_LEGACY_MODE."""

        print(f"[DEBUG] OCR request - Image size: {len(image_bytes)} bytes")
        print(f"[DEBUG] OCR server URL: {self.primary_url}")

        # Convert image to base64
        image_b64 = base64.b64encode(image_bytes).decode('utf-8')
        mime = (mime_type or 'image/png').strip() or 'image/png'
        data_uri = f"data:{mime};base64,{image_b64}"

        self._warmup()

        text = ""
        errors: List[str] = []
        used_url = None
        try:
            for base_url in self._candidate_urls():
                model_id = self._resolve_model_id_for_url(base_url)
                chat_payload = {
                    "model": model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": data_uri}},
                                {
                                    "type": "text",
                                    "text": "Extract all visible text from this image, including handwritten notes, typed text, and table contents. Preserve the original structure and formatting. Output only the transcribed text without any commentary or explanation.",
                                },
                            ]
                        }
                    ],
                    "max_tokens": OCR_MAX_TOKENS,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "repetition_penalty": 1.15,
                    "stream": False,
                    # Force no thinking even if vLLM default is enable_thinking=true
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                try:
                    r = self._session.post(f"{base_url}/v1/chat/completions", json=chat_payload, timeout=self.timeout)
                    if r.status_code != 200:
                        raise RuntimeError(f"OCR model HTTP {r.status_code}: {r.text[:200]}")
                    data = r.json()

                    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
                        choice = data["choices"][0]
                        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
                            mc = choice["message"].get("content")
                            if isinstance(mc, str):
                                text = mc.strip()
                    if text:
                        used_url = base_url
                        break
                except Exception as exc:
                    if base_url == self.primary_url:
                        self._mark_primary_down()
                    errors.append(f"{base_url}: {exc}")
                    continue
        finally:
            if used_url:
                self._flush_server_context(used_url)

        if not text:
            raise ExternalServiceError("ocr", self.primary_url, self.fallback_url, errors or ["OCR model returned no text"])

        if not text:
            raise RuntimeError("OCR model returned no text")

        # Cleanup
        if '<think>' in text and '</think>' in text:
            think_end = text.find('</think>')
            if think_end != -1:
                text = text[think_end + 8:].strip()
        if "</think>" in text:
            text = text.rsplit("</think>", 1)[-1].strip()
        while text.startswith("<image>"):
            text = text[7:].strip()
        text = re.sub(r'<[^>]*>', '', text).strip()

        # Better confidence heuristic based on content quality
        confidence = self._estimate_confidence(text)

        return text, confidence

    @staticmethod
    def _env_url(key: str) -> Optional[str]:
        val = os.environ.get(key)
        if not val:
            return None
        cleaned = val.strip().rstrip("/")
        return cleaned or None

    def _candidate_urls(self) -> List[str]:
        urls: List[str] = []
        now = time.time()
        if self.primary_url and now >= self._primary_down_until:
            urls.append(self.primary_url)
        if self.fallback_url and self.fallback_url not in urls:
            urls.append(self.fallback_url)
        if not urls:
            raise ExternalServiceError(
                "ocr",
                self.primary_url,
                self.fallback_url,
                ["OCR_URL_PRIMARY is not set (and no fallback configured)."],
            )
        return urls

    def _mark_primary_down(self) -> None:
        if self.primary_url:
            self._primary_down_until = time.time() + self._cooldown_sec

    def _estimate_confidence(self, text: str) -> float:
        """Estimate OCR confidence based on output characteristics."""
        if not text:
            return 0.0

        word_count = len(text.split())
        char_count = len(text)

        # Base confidence on length
        if word_count < 3:
            base_conf = 0.50
        elif word_count < 10:
            base_conf = 0.65
        elif word_count < 30:
            base_conf = 0.75
        else:
            base_conf = 0.80

        # Boost if contains medical/structured content
        medical_indicators = sum([
            bool(re.search(r'\b\d+\s*(mg|ml|mcg|units?)\b', text, re.I)),  # Dosages
            bool(re.search(r'\b\d{1,3}/\d{1,3}\b', text)),  # BP/fractions
            bool(re.search(r'\b(patient|diagnosis|treatment|medication)\b', text, re.I)),  # Medical terms
            '|' in text,  # Tables (markdown pipes)
            bool(re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text)),  # Dates
        ])
        base_conf += medical_indicators * 0.03

        # Penalize if contains OCR artifacts
        penalties = sum([
            text.count('???') * 0.05,  # Unknown chars
            text.count('□') * 0.05,   # Missing chars
            (char_count / max(1, word_count) > 15) * 0.10,  # Abnormally long "words"
        ])
        base_conf -= penalties

        return max(0.40, min(0.95, base_conf))


class ExternalServiceError(RuntimeError):
    def __init__(
        self,
        service: str,
        primary_url: Optional[str],
        fallback_url: Optional[str],
        errors: List[str],
    ) -> None:
        msg = f"{service} unavailable; attempted: {primary_url or '<unset>'}"
        if fallback_url:
            msg += f", fallback: {fallback_url}"
        if errors:
            msg += f"; errors: {', '.join(errors)}"
        super().__init__(msg)
        self.service = service
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.errors = errors
