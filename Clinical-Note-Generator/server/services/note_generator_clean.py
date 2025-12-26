# C:\Clinical-Note-Generator\server\services\note_generator_clean.py
import asyncio
import aiohttp
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator, Dict, Optional, List, Tuple


LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "note_generator.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("note_generator")
handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

class SimpleNoteGenerator:
    """Minimal llama-server client with a single streaming code path."""

    def __init__(self) -> None:
        self.config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        self.config = self._load_config()
        self.server_port = int(self.config.get("llama_server_port", 8081))
        self.server_host = str(self.config.get("llama_server_host", "127.0.0.1"))
        self.server_url = f"http://{self.server_host}:{self.server_port}"
        self.model_path = str(self.config.get("llm_model", ""))
        self.use_chat_api = self._cfg_bool("llama_use_chat_api", True)
        self.chat_model_name = self._resolve_chat_model_name()

    def _load_config(self) -> Dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load config.json: %s", exc)
        return {}

    def reload_config(self) -> None:
        self.config = self._load_config()
        self.server_port = int(self.config.get("llama_server_port", 8081))
        self.server_host = str(self.config.get("llama_server_host", "127.0.0.1"))
        self.server_url = f"http://{self.server_host}:{self.server_port}"
        self.model_path = str(self.config.get("llm_model", ""))
        self.use_chat_api = self._cfg_bool("llama_use_chat_api", True)
        self.chat_model_name = self._resolve_chat_model_name()

    def _cfg_bool(self, key: str, default: bool) -> bool:
        val = self.config.get(key, default)
        if isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return default
        return bool(val)

    def _resolve_chat_model_name(self) -> str:
        override = self.config.get("llama_chat_api_model")
        if isinstance(override, str) and override.strip():
            return override.strip()
        if self.model_path:
            stem = Path(self.model_path).stem
            if stem:
                return stem
            return Path(self.model_path).name
        return "local-model"

    async def _reset_context(self) -> None:
        """Request llama-server to drop cached KV state after each call."""
        timeout = aiohttp.ClientTimeout(total=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.server_url}/command",
                    json={"cmd": "reset"},
                    headers={"Content-Type": "application/json"},
                ):
                    pass
        except Exception:
            logger.debug("Context reset failed", exc_info=True)

    async def stream_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        """Yield streamed chunks directly from llama-server."""
        payload, endpoint, _ = self._build_payload(
            prompt, temperature, max_tokens, stream=True, stop=stop
        )
        logger.info("[SMPL] streaming payload (%s): %s", endpoint, payload)

        timeout = aiohttp.ClientTimeout(total=300)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.server_url}{endpoint}",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(f"llama-server error {response.status}: {error_text[:200]}")

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
                                logger.warning("Malformed SSE chunk: %s", data_str[:120])
                                continue

                            content = self._extract_stream_content(data)
                            if content:
                                yield content
                            else:
                                logger.info("[SMPL] empty stream chunk: %s", data)
        finally:
            await self._reset_context()

    async def collect_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
    ) -> str:
        """Return the entire completion as a single string."""
        payload, endpoint, used_chat = self._build_payload(
            prompt, temperature, max_tokens, stream=False, stop=stop
        )
        logger.info("[SMPL] collect payload (%s): %s", endpoint, payload)

        try:
            response_data = await self._collect_json_response(payload, endpoint)
            content = self._extract_stream_content(response_data)

            if content:
                return content

            if used_chat:
                logger.info("[SMPL] chat completion empty - retrying legacy /completion")
                fallback_payload, fallback_endpoint, _ = self._build_payload(
                    prompt,
                    temperature,
                    max_tokens,
                    stream=False,
                    stop=stop,
                    force_chat=False,
                )
                logger.info("[SMPL] fallback payload (%s): %s", fallback_endpoint, fallback_payload)
                fallback_data = await self._collect_json_response(fallback_payload, fallback_endpoint)
                fallback_content = self._extract_stream_content(fallback_data)
                if not fallback_content:
                    logger.info("[SMPL] fallback empty response: %s", fallback_data)
                return fallback_content or ""

            logger.info("[SMPL] collect empty response: %s", response_data)
            return ""
        finally:
            await self._reset_context()

    async def _collect_json_response(self, payload: Dict, endpoint: str) -> Dict:
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.server_url}{endpoint}",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.info("[SMPL] collect error %s: %s", response.status, error_text[:200])
                    raise RuntimeError(f"llama-server error {response.status}: {error_text[:200]}")

                raw_text = await response.text()
                try:
                    return json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    logger.info("[SMPL] collect JSON error: %s", exc)
                    logger.debug("Response snippet: %s", raw_text[:200])
                    raise

    def _build_payload(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        stop: Optional[List[str]],
        force_chat: Optional[bool] = None,
    ) -> Tuple[Dict, str, bool]:
        use_chat = self.use_chat_api if force_chat is None else force_chat
        if use_chat:
            return (
                self._build_chat_payload(prompt, temperature, max_tokens, stream, stop),
                "/v1/chat/completions",
                True,
            )
        return (
            self._build_completion_payload(prompt, temperature, max_tokens, stream, stop),
            "/completion",
            False,
        )

    def _sampler_params(self, temperature: float, max_tokens: int, stream: bool) -> Dict[str, object]:
        def _cfg_float(name: str, default: float) -> float:
            try:
                return float(self.config.get(name, default))
            except Exception:
                return default

        def _cfg_int(name: str, default: int) -> int:
            try:
                return int(self.config.get(name, default))
            except Exception:
                return default

        repeat_penalty = _cfg_float("default_repeat_penalty", 1.18)
        repeat_last_n = max(64, _cfg_int("default_repeat_last_n", 1024))
        top_p = min(max(_cfg_float("default_top_p", 0.92), 0.01), 1.0)
        top_k = max(1, _cfg_int("default_top_k", 40))
        min_p = min(max(_cfg_float("default_min_p", 0.06), 0.0), top_p)
        seed = max(_cfg_int("default_seed", -1), -1)

        return {
            "temperature": temperature,
            "n_predict": max_tokens,
            "max_tokens": max_tokens,
            "stream": stream,
            "repeat_penalty": repeat_penalty,
            "repeat_last_n": repeat_last_n,
            "seed": seed,
            "top_p": top_p,
            "top_k": top_k,
            "min_p": min_p,
        }

    def _build_completion_payload(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        stop: Optional[List[str]],
    ) -> Dict:
        payload: Dict[str, object] = {
            **self._sampler_params(temperature, max_tokens, stream),
            "prompt": prompt,
            "n_keep": 256,
            "cache_prompt": False,
        }

        if stop is not None:
            payload["stop"] = stop
            if stop:
                payload["trim_stop"] = True

        if self.config.get("llama_server_enable_jinja", True):
            payload["template"] = "jinja"

        return payload

    def _build_chat_payload(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        stop: Optional[List[str]],
    ) -> Dict:
        payload: Dict[str, object] = {
            **self._sampler_params(temperature, max_tokens, stream),
            "model": self.chat_model_name,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }

        if stop is not None:
            payload["stop"] = stop

        return payload

    @staticmethod
    def _extract_stream_content(data: Dict) -> Optional[str]:
        if "content" in data:
            return data.get("content")
        if "text" in data:
            return data.get("text")
        if "choices" in data and isinstance(data["choices"], list):
            choice = data["choices"][0] if data["choices"] else None
            if isinstance(choice, dict):
                delta = choice.get("delta")
                if isinstance(delta, dict) and delta.get("content"):
                    return delta.get("content")
                message = choice.get("message")
                if isinstance(message, dict) and message.get("content"):
                    return message.get("content")
                if "text" in choice:
                    return choice.get("text")
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content")
        return None


_simple_client: Optional[SimpleNoteGenerator] = None


def get_simple_note_generator() -> SimpleNoteGenerator:
    global _simple_client
    if _simple_client is None:
        _simple_client = SimpleNoteGenerator()
    return _simple_client
