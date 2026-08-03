# server/services/note_generator_clean.py
import aiohttp
import json
import logging
import os
import time
from pathlib import Path
from typing import AsyncIterator, Dict, Optional, List, Tuple

from server.core.clinical_output_guard import (
    ClinicalOutputRejected,
    IncrementalDegenerationGuard,
    detect_degenerate_output,
)
from server.core.llm_routing import resolve_llm_urls
from server.core.llm_request_policy import (
    apply_dreamcision_generation_policy,
    prompt_to_chat_messages,
)

LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "note_generator.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("note_generator")
handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


def _safe_payload_summary(payload: Dict) -> Dict[str, object]:
    """Return a PHI-safe payload summary for logs."""
    summary = {
        "stream": bool(payload.get("stream")),
        "max_tokens": payload.get("max_tokens", payload.get("n_predict")),
        "temperature": payload.get("temperature"),
        "stop_count": len(payload.get("stop") or []),
    }
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        summary["prompt_chars"] = len(prompt)
    messages = payload.get("messages")
    if isinstance(messages, list):
        summary["messages"] = len(messages)
        summary["message_chars"] = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    return summary


class SimpleNoteGenerator:
    """Minimal llama-server client with a single streaming code path."""

    _default_request_timeout_sec = 60.0

    def __init__(
        self,
        role: str = "note_gen",
        *,
        explicit_urls: Optional[Tuple[Optional[str], Optional[str]]] = None,
    ) -> None:
        self.config_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        self.config = self._load_config()
        self.model_path = str(self.config.get("llm_model", ""))
        self.use_chat_api = self._cfg_bool("llama_use_chat_api", True)
        self.chat_model_name = self._resolve_chat_model_name()
        self._model_id_cache: Dict[str, Tuple[str, float]] = {}
        self._model_cache_ttl_sec = 300.0
        if explicit_urls is not None:
            self.role = "__explicit__"
            self.primary_url, self.fallback_url = explicit_urls
        else:
            self.role = role
            self.primary_url, self.fallback_url = resolve_llm_urls(role)
        self._primary_down_until = 0.0
        self._cooldown_sec = 20.0

    def _load_config(self) -> Dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load config.json: %s", exc)
        return {}

    def reload_config(self) -> None:
        self.config = self._load_config()
        self.model_path = str(self.config.get("llm_model", ""))
        self.use_chat_api = self._cfg_bool("llama_use_chat_api", True)
        self.chat_model_name = self._resolve_chat_model_name()
        self._model_id_cache.clear()
        if getattr(self, "role", "") == "__explicit__":
            return
        if self.role in ("note_gen", "qa_text"):
            self.primary_url, self.fallback_url = resolve_llm_urls(self.role)
        else:
            self.primary_url, self.fallback_url = resolve_llm_urls("note_gen")

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

    async def _resolve_model_id_for_url(self, base_url: str) -> str:
        """Use the model id served at this base URL (vLLM / OpenAI-compatible), not config.json paths."""
        now = time.time()
        cached = self._model_id_cache.get(base_url)
        if cached and cached[1] > now:
            return cached[0]

        override = (
            os.environ.get("LLM_CHAT_MODEL")
            or os.environ.get("NOTEGEN_CHAT_MODEL")
            or self.config.get("llama_chat_api_model")
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
                                logger.info("[SMPL] model for %s -> %s", base_url, mid)
                                return mid
        except Exception as exc:
            logger.debug("Model discovery failed for %s: %s", base_url, exc)

        return self.chat_model_name

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
        if self.primary_url and (
            now >= self._primary_down_until or not self.fallback_url
        ):
            urls.append(self.primary_url)
        if self.fallback_url and self.fallback_url not in urls:
            urls.append(self.fallback_url)
        if not urls:
            raise ExternalServiceError(
                self.role if self.role != "__explicit__" else "note_gen",
                self.primary_url,
                self.fallback_url,
                ["No LLM base URL resolved (set LLM_* or NOTEGEN_URL_* env vars)."],
            )
        return urls

    def _request_timeout_seconds(self, timeout_sec: Optional[float]) -> float:
        if timeout_sec is not None:
            raw_timeout = timeout_sec
        else:
            raw_timeout = os.environ.get(
                "LLM_REQUEST_TIMEOUT_SEC",
                self.config.get(
                    "llm_request_timeout_sec", self._default_request_timeout_sec
                ),
            )
        try:
            parsed = float(raw_timeout)
        except (TypeError, ValueError):
            return self._default_request_timeout_sec
        return parsed if parsed > 0 else self._default_request_timeout_sec

    def _mark_primary_down(self) -> None:
        if self.primary_url:
            self._primary_down_until = time.time() + self._cooldown_sec

    async def _reset_context(self, base_url: str) -> None:
        """Request llama-server to drop cached KV state after each call."""
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
            logger.debug("Context reset failed", exc_info=True)

    async def stream_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
        timeout_sec: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """Yield streamed chunks directly from llama-server."""
        errors: List[str] = []
        for base_url in self._candidate_urls():
            model_id = await self._resolve_model_id_for_url(base_url)
            payload, endpoint, _ = self._build_payload(
                prompt, temperature, max_tokens, stream=True, stop=stop, model_name=model_id
            )
            logger.info("[SMPL] streaming request (%s, model=%s): %s", endpoint, model_id, _safe_payload_summary(payload))

            had_output = False
            output_guard = IncrementalDegenerationGuard()
            timeout = aiohttp.ClientTimeout(
                total=self._request_timeout_seconds(timeout_sec)
            )
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{base_url}{endpoint}",
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

                                content, finish_reason = self._extract_stream_content(data)
                                if finish_reason == "length":
                                    raise ClinicalOutputRejected(
                                        "output truncated by max_tokens cap (finish_reason=length)"
                                    )
                                if content:
                                    output_guard.add(content)
                                    had_output = True
                                    yield content
                                else:
                                    logger.debug("[SMPL] empty stream chunk")
                await self._reset_context(base_url)
                return
            except Exception as exc:
                if base_url == self.primary_url:
                    self._mark_primary_down()
                errors.append(f"{base_url}: {exc}")
                if had_output:
                    raise
                continue

        raise ExternalServiceError("note_gen", self.primary_url, self.fallback_url, errors)

    async def collect_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: Optional[List[str]] = None,
        timeout_sec: Optional[float] = None,
    ) -> str:
        """Return the entire completion as a single string."""
        import time as _time
        t_start = _time.time()
        used_url = None
        try:
            errors_probe: List[str] = []
            for base_url in self._candidate_urls():
                model_id = await self._resolve_model_id_for_url(base_url)
                payload, endpoint, used_chat = self._build_payload(
                    prompt, temperature, max_tokens, stream=False, stop=stop, model_name=model_id
                )
                logger.info("[SMPL] collect request (%s, model=%s): %s", endpoint, model_id, _safe_payload_summary(payload))
                t_req = _time.time()
                try:
                    response_data, used_url = await self._collect_json_response(payload, endpoint, base_urls=[base_url], timeout_sec=timeout_sec)
                except ExternalServiceError as exc:
                    logger.info("[SMPL] collect FAILED on %s (%.2fs): %s", base_url, _time.time() - t_req, exc.errors)
                    errors_probe.extend(exc.errors)
                    continue

                logger.info("[SMPL] collect HTTP DONE on %s (%.2fs)", used_url, _time.time() - t_req)
                content, finish_reason = self._extract_stream_content(response_data)

                if content:
                    if finish_reason == "length":
                        raise ClinicalOutputRejected(
                            "output truncated by max_tokens cap (finish_reason=length)"
                        )
                    degeneration = detect_degenerate_output(content)
                    if degeneration:
                        raise ClinicalOutputRejected("; ".join(degeneration))
                    logger.info("[SMPL] collect_completion TOTAL (%.2fs, url=%s, output_len=%d)",
                               _time.time() - t_start, used_url, len(content))
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
                        model_name=model_id,
                    )
                    logger.info("[SMPL] fallback request (%s): %s", fallback_endpoint, _safe_payload_summary(fallback_payload))
                    t_fb = _time.time()
                    try:
                        fallback_data, used_url = await self._collect_json_response(
                            fallback_payload, fallback_endpoint, base_urls=[base_url], timeout_sec=timeout_sec
                        )
                    except ExternalServiceError as exc:
                        logger.info("[SMPL] legacy fallback FAILED on %s (%.2fs): %s", base_url, _time.time() - t_fb, exc.errors)
                        errors_probe.extend(exc.errors)
                        continue
                    logger.info("[SMPL] fallback HTTP DONE (%.2fs)", _time.time() - t_fb)
                    fallback_content, fallback_finish_reason = self._extract_stream_content(fallback_data)
                    if fallback_content:
                        if fallback_finish_reason == "length":
                            raise ClinicalOutputRejected(
                                "output truncated by max_tokens cap (finish_reason=length)"
                            )
                        degeneration = detect_degenerate_output(fallback_content)
                        if degeneration:
                            raise ClinicalOutputRejected("; ".join(degeneration))
                        logger.info("[SMPL] collect_completion TOTAL (%.2fs, url=%s, output_len=%d)",
                                   _time.time() - t_start, used_url, len(fallback_content))
                        return fallback_content
                    logger.info("[SMPL] fallback empty response on %s: %s — trying next candidate URL", base_url, fallback_data)
                    errors_probe.append(f"{base_url}: empty content from both chat and legacy /completion")
                    continue

                logger.info("[SMPL] collect returned empty content on %s — trying next candidate URL", base_url)
                errors_probe.append(f"{base_url}: empty content")
                continue

            raise ExternalServiceError("note_gen", self.primary_url, self.fallback_url, errors_probe)
        finally:
            if used_url:
                t_reset = _time.time()
                await self._reset_context(used_url)
                logger.info("[SMPL] _reset_context DONE (%.2fs)", _time.time() - t_reset)

    async def _collect_json_response(
        self, payload: Dict, endpoint: str, base_urls: Optional[List[str]] = None, timeout_sec: Optional[float] = None
    ) -> Tuple[Dict, str]:
        import time as _time
        errors: List[str] = []
        for base_url in base_urls or self._candidate_urls():
            timeout = aiohttp.ClientTimeout(
                total=self._request_timeout_seconds(timeout_sec)
            )
            t_http = _time.time()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{base_url}{endpoint}",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        http_time = _time.time() - t_http
                        if response.status != 200:
                            error_text = await response.text()
                            logger.info("[SMPL] HTTP %s on %s (%.2fs): %s", response.status, base_url, http_time, error_text[:200])
                            raise RuntimeError(f"llama-server error {response.status}: {error_text[:200]}")

                        raw_text = await response.text()
                        total_time = _time.time() - t_http
                        logger.info("[SMPL] HTTP 200 on %s (%.2fs, body_len=%d)", base_url, total_time, len(raw_text))
                        try:
                            return json.loads(raw_text), base_url
                        except json.JSONDecodeError as exc:
                            logger.info("[SMPL] collect JSON error: %s", exc)
                            logger.debug("Response snippet: %s", raw_text[:200])
                            raise
            except Exception as exc:
                if base_url == self.primary_url:
                    self._mark_primary_down()
                errors.append(f"{base_url}: {exc}")
                continue
        raise ExternalServiceError("note_gen", self.primary_url, self.fallback_url, errors)

    def _build_payload(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        stop: Optional[List[str]],
        force_chat: Optional[bool] = None,
        model_name: Optional[str] = None,
    ) -> Tuple[Dict, str, bool]:
        use_chat = self.use_chat_api if force_chat is None else force_chat
        if use_chat:
            return (
                self._build_chat_payload(prompt, temperature, max_tokens, stream, stop, model_name),
                "/v1/chat/completions",
                True,
            )
        return (
            self._build_completion_payload(prompt, temperature, max_tokens, stream, stop),
            "/completion",
            False,
        )

    def _sampler_params(self, temperature: float, max_tokens: int, stream: bool) -> Dict[str, object]:
        def _cfg_float(name: str, default: float = 0.0) -> float:
            try:
                return float(self.config.get(name, default))
            except Exception:
                return default

        def _cfg_int(name: str, default: int = 0) -> int:
            try:
                return int(self.config.get(name, default))
            except Exception:
                return default

        repeat_penalty = 1.0
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
        model_name: Optional[str] = None,
    ) -> Dict:
        sampler = self._sampler_params(temperature, max_tokens, stream)
        # Keep the chat payload OpenAI/vLLM-compatible. Completion-only llama.cpp
        # aliases are retained only by _build_completion_payload.
        sampler.pop("min_p", None)
        sampler.pop("n_predict", None)
        sampler.pop("repeat_last_n", None)
        repeat_penalty = sampler.pop("repeat_penalty", None)
        if repeat_penalty is not None:
            sampler["repetition_penalty"] = repeat_penalty
        payload: Dict[str, object] = {
            **sampler,
            "model": model_name or self.chat_model_name,
            "messages": prompt_to_chat_messages(prompt),
        }

        if stop is not None:
            payload["stop"] = stop

        profile = "qa" if self.role == "qa_text" else "clinical_note"
        return dict(apply_dreamcision_generation_policy(payload, profile=profile))

    @staticmethod
    def _extract_stream_content(data: Dict) -> Tuple[Optional[str], Optional[str]]:
        """Return (content, finish_reason) for a chunk or a full response.

        finish_reason == "length" is the API's authoritative signal that
        generation stopped because max_tokens was hit rather than because the
        model finished naturally — the same truncation signal already checked
        in asr_refine.py. Without it, a note that hits the cap looks complete.
        """

        def _pick_msg(msg: Dict) -> Optional[str]:
            if not isinstance(msg, dict):
                return None
            c = msg.get("content")
            r = msg.get("reasoning_content")  # noqa: F841 — extraction pattern
            if isinstance(c, str) and c.strip():
                return c
            # Do not stream reasoning/thinking into clinical notes or chart output.
            return c if isinstance(c, str) and c else None

        if "content" in data and isinstance(data.get("content"), str) and (data.get("content") or "").strip():
            # Native /completion response (non-chat fallback path). No
            # standardized finish_reason field is verified for this server's
            # native endpoint, so truncation isn't detected here — the
            # default chat-completions path above is what's actually used
            # in production (llama_use_chat_api defaults True).
            return data.get("content"), None

        if "text" in data and data.get("text"):
            return data.get("text"), None

        if "choices" in data and isinstance(data["choices"], list):
            choice = data["choices"][0] if data["choices"] else None
            if isinstance(choice, dict):
                finish_reason = choice.get("finish_reason")
                finish_reason = str(finish_reason) if finish_reason is not None else None
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    picked = _pick_msg(delta)
                    if picked is not None:
                        return picked, finish_reason
                message = choice.get("message")
                picked = _pick_msg(message) if isinstance(message, dict) else None
                if picked is not None:
                    return picked, finish_reason
                if "text" in choice:
                    return choice.get("text"), finish_reason
                return None, finish_reason
        if "message" in data and isinstance(data["message"], dict):
            return _pick_msg(data["message"]), None
        return None, None


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


_clients: Dict[str, SimpleNoteGenerator] = {}


def get_simple_note_generator(role: str = "note_gen") -> SimpleNoteGenerator:
    """Return a cached client for the routing role (note_gen or qa_text)."""
    global _clients
    if role not in _clients:
        _clients[role] = SimpleNoteGenerator(role=role)
    return _clients[role]


def invalidate_llm_client_cache() -> None:
    global _clients
    _clients.clear()
