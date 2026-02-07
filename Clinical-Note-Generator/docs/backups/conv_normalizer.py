# C:\Clinical-Note-Generator\server\services\conv_normalizer.py
import os
import json
import time
import requests
from typing import Optional


class ConvNormalizerClient:
    """Calls a local llama.cpp server to normalize diarized WhisperX text
    into Doctor:, Patient:, and Other: turns.
    """

    def __init__(self, base_url: Optional[str] = None, timeout_sec: Optional[float] = None):
        # Load config for defaults (non-fatal if missing)
        cfg = {}
        try:
            with open(r"C:\\Clinical-Note-Generator\\config\\config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        # Allow dedicated normalizer endpoint; fall back to env override
        env_url = os.environ.get("CONV_NORMALIZER_URL") or os.environ.get("LLM_BASE_URL")
        cfg_url = cfg.get("conv_normalizer_url") if isinstance(cfg, dict) else None
        resolved_url = base_url or env_url or cfg_url
        self.base_url = resolved_url.rstrip("/") if resolved_url else None

        try:
            self.timeout = float(timeout_sec if timeout_sec is not None else os.environ.get("LLM_TIMEOUT", "120"))
        except Exception:
            self.timeout = 120.0

        self._headers = {"Content-Type": "application/json"}
        # Add Authorization if provided (prefer env over config)
        api_key = os.environ.get("LLM_API_KEY") or (cfg.get("api_key") if isinstance(cfg, dict) else None)
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

        # Optional minimal debug (no content logged)
        self._debug = os.environ.get("NORMALIZER_DEBUG") == "1"

    def _prompt(self, diarized_text: str) -> str:
        # One continuous line; do not trim or alter the diarized_text
        return (
            "Normalize, clean and format the following clinical conversation. Label turns (Doctor:, Patient:, Other:). Correct ASR errors and standardize medical terminology and dosages, without adding any data. Output only the resulting dialogue, no extra text, No fabricated data. Diarized transcript:"
            f"[{diarized_text}]"
            " Now return only the normalized conversation."
        )

    def normalize(self, diarized_text: str) -> str:
        if not self.base_url:
            # No normalizer configured; return original text unchanged.
            return diarized_text

        body = {
            "model": os.environ.get("LLM_MODEL_ID", "auto"),
            "messages": [
                {"role": "user", "content": self._prompt(diarized_text)},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
        }
        # Prefer OpenAI-style chat; then /v1/completions; then llama.cpp /completion
        url_chat = f"{self.base_url}/v1/chat/completions"
        url_comp = f"{self.base_url}/v1/completions"
        url_llama = f"{self.base_url}/completion"

        t0 = time.time()
        try:
            r = requests.post(url_chat, headers=self._headers, data=json.dumps(body), timeout=self.timeout)
            if r.status_code == 404:
                raise requests.HTTPError("404 chat", response=r)
            r.raise_for_status()
            data = r.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            out = (content or "").strip()
            if self._debug:
                print(f"[ConvNorm] POST {url_chat} status={r.status_code} ms={int((time.time()-t0)*1000)} len={len(out)}")
            if out:
                return out
        except Exception:
            pass

        try:
            comp = {
                "model": body.get("model", "auto"),
                "prompt": self._prompt(diarized_text),
                "temperature": 0.1,
                "max_tokens": 1500,
            }
            r2 = requests.post(url_comp, headers=self._headers, data=json.dumps(comp), timeout=self.timeout)
            r2.raise_for_status()
            data2 = r2.json()
            out2 = (data2.get("choices") or [{}])[0].get("text", "") or data2.get("content", "")
            out2 = (out2 or "").strip()
            if self._debug:
                print(f"[ConvNorm] POST {url_comp} status={r2.status_code} ms={int((time.time()-t0)*1000)} len={len(out2)}")
            if out2:
                return out2
        except Exception:
            pass

        try:
            llama = {
                "prompt": self._prompt(diarized_text),
                "temperature": 0.1,
                "max_tokens": 1500,
            }
            r3 = requests.post(url_llama, headers=self._headers, data=json.dumps(llama), timeout=self.timeout)
            r3.raise_for_status()
            data3 = r3.json()
            out3 = data3.get("content") or (data3.get("choices") or [{}])[0].get("text", "")
            out3 = (out3 or "").strip()
            if self._debug:
                print(f"[ConvNorm] POST {url_llama} status={r3.status_code} ms={int((time.time()-t0)*1000)} len={len(out3)}")
            return out3
        except Exception as e:
            if self._debug:
                print(f"[ConvNorm] ERROR after {int((time.time()-t0)*1000)} ms: {e}")
            raise RuntimeError(f"normalizer error: {e}")
