# server/services/rag_http_client.py
import aiohttp
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class RAGHttpClient:
    def __init__(self, base_url: str, timeout: int = 30_000):
        # base_url like http://127.0.0.1:8007
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout / 1000)
        # Lazy config (used for caps)
        self._cfg: Optional[Dict[str, Any]] = None

    def _load_cfg(self) -> Dict[str, Any]:
        if self._cfg is not None:
            return self._cfg
        try:
            cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    self._cfg = json.load(f)
                    return self._cfg
        except Exception:
            pass
        self._cfg = {}
        return self._cfg

    def _year_of(self, meta: Dict[str, Any]) -> Optional[int]:
        try:
            y = str(meta.get("timestamp") or meta.get("year") or meta.get("date") or "").strip()
            if len(y) >= 4 and y[:4].isdigit():
                return int(y[:4])
        except Exception:
            return None
        return None

    def _normalize_meta(self, md: Dict[str, Any]) -> Dict[str, Any]:
        # Produce a normalized metadata view while preserving original keys
        out = dict(md or {})
        title = out.get("title") or out.get("guideline_type") or out.get("lab_test") or out.get("paper_title")
        source = out.get("source") or out.get("society") or out.get("publisher")
        link = out.get("link") or out.get("url")
        section = out.get("section") or out.get("heading") or out.get("chapter")
        year = self._year_of(out)
        if title: out["title"] = str(title)
        if source: out["source"] = str(source)
        if link: out["link"] = str(link)
        if section: out["section"] = str(section)
        if year is not None: out["year"] = year
        return out

    def _sentences(self, text: str) -> List[str]:
        # Simple sentence splitter; avoids heavy deps
        if not text:
            return []
        # Split on ., !, ? followed by space/newline and a capital or digit
        parts = re.split(r"(?<=[\.!?])\s+(?=[A-Z0-9])", text.strip())
        # Cleanup stray whitespace
        return [p.strip() for p in parts if p and p.strip()]

    def _snippet(self, text: str, max_words: int = 160, max_sentences: int = 3) -> str:
        sents = self._sentences(text)
        if not sents:
            s = text.strip()
        else:
            s = " ".join(sents[:max_sentences]).strip()
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words]) + " …"
        return s

    def _compose_context(self, items: List[Dict[str, Any]], max_words_total: int) -> str:
        parts: List[str] = []
        total_words = 0
        for idx, r in enumerate(items, start=1):
            text = r.get("text", "") if isinstance(r, dict) else ""
            md = self._normalize_meta(r.get("metadata", {}) if isinstance(r, dict) else {})
            title = md.get("title") or ""
            year = md.get("year")
            src = md.get("source") or ""
            link = md.get("link") or ""
            section = md.get("section") or ""
            header_bits = []
            if title:
                header_bits.append(str(title))
            if year:
                header_bits.append(str(year))
            if src:
                header_bits.append(str(src))
            if section:
                header_bits.append(str(section))
            header = " - ".join(header_bits) if header_bits else f"Snippet {idx}"
            snip = self._snippet(text)
            chunk = f"[{idx}] {header}\n{snip}"
            if link:
                chunk += f"\n{link}"
            # Enforce total cap
            words_here = len(chunk.split())
            if total_words + words_here > max_words_total:
                remain = max(0, max_words_total - total_words)
                if remain <= 0:
                    break
                # trim chunk to remaining words
                chunk = " ".join(chunk.split()[:remain]) + " …"
                parts.append(chunk)
                break
            parts.append(chunk)
            total_words += words_here
        return "\n\n".join(parts)

    async def query(
        self,
        query: str,
        *,
        top_k: int = 8,
        include_keywords: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        specialty: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        """Query RAG service and return (context, results, used_filters).

        - Builds a concatenated context from top-k snippets, capped by rag_max_context_words.
        - Normalizes metadata (title, year, section, source, link) on each result.
        - Gracefully falls back to keyword-heavy query if evidence is weak.
        """

        def _payload(base_top_k: int, kws: Optional[List[str]] = None) -> Dict[str, Any]:
            p: Dict[str, Any] = {"query": query, "top_k": base_top_k}
            if kws:
                p["include_keywords"] = kws
            if date_from:
                p["date_from"] = date_from
            if date_to:
                p["date_to"] = date_to
            if specialty:
                p["specialty"] = specialty
            return p

        async def _post(session: aiohttp.ClientSession, pld: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
            url = f"{self.base_url}/query"
            async with session.post(url, json=pld) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    raise RuntimeError(f"RAG /query failed: HTTP {resp.status}: {txt[:200]}")
                data = await resp.json()
                results: List[Dict[str, Any]] = data.get("results", []) or []
                used: Dict[str, Any] = data.get("used_filters", {}) or {}
                return results, used

        cfg = self._load_cfg()
        cap_words = int(cfg.get("rag_max_context_words", 600))
        # Per-snippet soft cap
        snippet_words = 110

        # First attempt
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            results, used = await _post(session, _payload(top_k, include_keywords))

            # Normalize metadata and compute quick strength signal
            norm_results: List[Dict[str, Any]] = []
            scores: List[float] = []
            for r in results:
                md = self._normalize_meta(r.get("metadata", {}) if isinstance(r, dict) else {})
                sc = float(r.get("score", 0.0)) if isinstance(r, dict) else 0.0
                scores.append(sc)
                norm_r = dict(r)
                norm_r["metadata"] = md
                # Ensure text is a string
                norm_r["text"] = str(r.get("text", ""))
                norm_r["score"] = sc
                norm_results.append(norm_r)

            def _weak(res: List[Dict[str, Any]]) -> bool:
                if not res:
                    return True
                try:
                    scs = [float(x.get("score", 0.0)) for x in res]
                    return (sum(scs) / max(1, len(scs))) < 0.12 or len(" ".join([x.get("text", "") for x in res]).split()) < 40
                except Exception:
                    return False

            # Fallback: broaden recall using keywords if weak
            if _weak(norm_results):
                try:
                    # Build a simple broader keyword set
                    base_kws = (include_keywords or []) + [
                        "guideline", "recommendation", "study", "trial", "evidence",
                        "dosage", "contraindication", "renal", "hepatic", "pregnancy",
                        "follow-up", "APAP", "CPAP"
                    ]
                    # Limit duplicates
                    seen = set()
                    kws = []
                    for k in base_kws:
                        kk = str(k).lower().strip()
                        if kk and kk not in seen:
                            kws.append(k)
                            seen.add(kk)
                    results2, used2 = await _post(session, _payload(min(20, max(10, top_k * 2)), kws))
                    # Use fallback only if it improves coverage
                    if results2:
                        used = {**used, "client_fallback": "keywords"}
                        # Normalize as above
                        norm_results = []
                        for r in results2:
                            md = self._normalize_meta(r.get("metadata", {}) if isinstance(r, dict) else {})
                            sc = float(r.get("score", 0.0)) if isinstance(r, dict) else 0.0
                            norm_r = dict(r)
                            norm_r["metadata"] = md
                            norm_r["text"] = str(r.get("text", ""))
                            norm_r["score"] = sc
                            norm_results.append(norm_r)
                except Exception:
                    pass

            # Compose context from normalized results
            # Respect per-snippet cap implicitly via _snippet and global cap via compose
            try:
                min_score = float(cfg.get("rag_min_score", 0.0))
            except Exception:
                min_score = 0.0
            if min_score > 0:
                filtered = [r for r in norm_results if float(r.get("score", 0.0)) >= min_score]
                if filtered:
                    norm_results = filtered
            context = self._compose_context(norm_results[:top_k], cap_words)
            used["min_score"] = min_score
            return context, norm_results, used
