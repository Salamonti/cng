# server/core/service_endpoints_sync.py
"""Derive env URL keys in service_endpoints.json from llama_instances + feature_routing.

Keeps a single file as source of truth: structured fields drive env.{NOTEGEN_*, LLM_*, OCR_*, RAG_URL, ASR_*}.
Explicit env edits are preserved when llama_instances is absent or empty.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _inst_url(llama_instances: Dict[str, Any], instance_id: str) -> str:
    if not instance_id or not isinstance(instance_id, str):
        return ""
    inst = llama_instances.get(instance_id.strip())
    if not isinstance(inst, dict):
        return ""
    return str(inst.get("base_url") or "").strip()


def _pair(feature_routing: Dict[str, Any], feature: str) -> Tuple[str, str]:
    block = feature_routing.get(feature)
    if not isinstance(block, dict):
        return "", ""
    pr = block.get("primary") or ""
    fb = block.get("fallback") or ""
    if not isinstance(pr, str):
        pr = str(pr) if pr is not None else ""
    if not isinstance(fb, str):
        fb = str(fb) if fb is not None else ""
    return pr.strip(), fb.strip()


def sync_env_from_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mutate data: set data['env'] URL keys from llama_instances + feature_routing + services_urls
    (+ optional whisper_instances for ASR).
    Returns the same dict for chaining.
    """
    fr = data.get("feature_routing")
    if not isinstance(fr, dict):
        fr = {}

    env = data.setdefault("env", {})
    if not isinstance(env, dict):
        data["env"] = {}
        env = data["env"]

    li = data.get("llama_instances")
    if isinstance(li, dict) and li:

        def apply_pair(feature: str, primary_key: str, fallback_key: str) -> None:
            if feature not in fr:
                return
            pr, fb = _pair(fr, feature)
            if not pr and not fb:
                return
            env[primary_key] = _inst_url(li, pr)
            env[fallback_key] = _inst_url(li, fb)

        apply_pair("note_gen", "NOTEGEN_URL_PRIMARY", "NOTEGEN_URL_FALLBACK")
        apply_pair("qa_text", "LLM_QA_TEXT_URL", "LLM_QA_TEXT_URL_FALLBACK")
        apply_pair("qa_vision", "LLM_QA_VISION_URL", "LLM_QA_VISION_URL_FALLBACK")
        apply_pair("ocr", "OCR_URL_PRIMARY", "OCR_URL_FALLBACK")
        apply_pair("rag_comment", "LLM_RAG_COMMENT_URL", "LLM_RAG_COMMENT_URL_FALLBACK")
        apply_pair("order_request", "LLM_ORDER_REQUEST_URL", "LLM_ORDER_REQUEST_URL_FALLBACK")

    su = data.get("services_urls")
    if isinstance(su, dict):
        if str(su.get("rag") or "").strip():
            env["RAG_URL"] = str(su["rag"]).strip()
        if str(su.get("asr_primary") or "").strip():
            env["ASR_URL"] = str(su["asr_primary"]).strip()
        if str(su.get("asr_fallback") or "").strip():
            env["ASR_URL_FALLBACK"] = str(su["asr_fallback"]).strip()

    wi = data.get("whisper_instances")
    if isinstance(wi, dict):
        pr = wi.get("primary")
        fb = wi.get("fallback")
        if isinstance(pr, dict) and str(pr.get("base_url") or "").strip():
            env["ASR_URL"] = str(pr["base_url"]).strip()
        if isinstance(fb, dict) and str(fb.get("base_url") or "").strip():
            env["ASR_URL_FALLBACK"] = str(fb["base_url"]).strip()
        # Feed the newer multi-instance ASR router directly. Dict insertion
        # order keeps primary/fallback first when the standard keys exist,
        # then includes any extra whisper instances (e.g. 8097-8099).
        asr_urls = []
        for inst in wi.values():
            if not isinstance(inst, dict):
                continue
            url = str(inst.get("base_url") or "").strip().rstrip("/")
            if url and url not in asr_urls:
                asr_urls.append(url)
        if asr_urls:
            env["ASR_URLS"] = ",".join(asr_urls)

    fp = data.get("fastapi_port")
    if fp is not None:
        try:
            port = int(fp)
            data.setdefault("pchost", {})
            pch = data["pchost"]
            if isinstance(pch, dict):
                pch["backend_url"] = f"http://127.0.0.1:{port}"
        except (TypeError, ValueError):
            # Legitimately safe to swallow: on a non-integer fastapi_port we just
            # leave backend_url unset (defaults apply); nothing operator-visible
            # regresses.
            pass

    return data


def validate_llama_instances(data: Dict[str, Any]) -> List[str]:
    """Return human-readable warnings (non-fatal)."""
    warnings: List[str] = []
    li = data.get("llama_instances")
    if not isinstance(li, dict):
        return warnings
    fr = data.get("feature_routing")
    if not isinstance(fr, dict):
        return warnings
    for feat, block in fr.items():
        if not isinstance(block, dict):
            continue
        for slot in ("primary", "fallback"):
            iid = block.get(slot)
            if not iid or not str(iid).strip():
                continue
            sid = str(iid).strip()
            if sid not in li:
                warnings.append(f"feature_routing.{feat}.{slot} references unknown llama_instances id '{sid}'")
    return warnings
