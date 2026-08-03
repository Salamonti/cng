# server/app.py
# app.py

import os
import time
import logging # type: ignore  # noqa: F401
from server.metrics import Metrics
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.responses import JSONResponse
from pathlib import Path
import json

logger = logging.getLogger(__name__)


from server.core.service_endpoints import apply_service_endpoints  # noqa: E402
from server.core.cors_config import cors_middleware_kwargs  # noqa: E402

apply_service_endpoints()

# Create FastAPI app (no root_path). We'll mount routers under "/api" explicitly.
app = FastAPI()

# CORS: default host-scoped regex (ieissa.* / eissa.ca / localhost); override via env — see cors_config.py
_cors = cors_middleware_kwargs()
app.add_middleware(CORSMiddleware, **_cors)


def _admin_mutations_localhost_only() -> bool:
    v = os.environ.get("ADMIN_MUTATIONS_LOCALHOST_ONLY", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


@app.middleware("http")
async def admin_write_localhost_guard(request, call_next):
    """Optional, currently-disabled extra layer: block mutating /api/admin
    calls from non-loopback (set ADMIN_MUTATIONS_LOCALHOST_ONLY=1).

    P1-3 asked whether this is structurally a no-op given the topology
    (every request to FastAPI arrives from PCHost's own loopback
    connection). Traced the actual trust chain rather than guessing:

    - PCHost's proxy config sets xfwd: true (see PCHost/server.js), which
      makes http-proxy-middleware attach X-Forwarded-For with the real
      remote client's IP on every request it relays to FastAPI.
    - uvicorn is started with --proxy-headers --forwarded-allow-ips
      127.0.0.1,::1 (see dreamcision-fastapi.service). Since PCHost's
      connection to FastAPI genuinely originates from 127.0.0.1 (both run
      on this host), uvicorn trusts and applies that X-Forwarded-For,
      so request.client.host below reflects the real remote IP, not
      PCHost's loopback address -- this guard is NOT structurally broken
      given the current deployment.

    That correctness is fragile, though: it depends on PCHost's xfwd
    setting and uvicorn's --proxy-headers/--forwarded-allow-ips flags
    never changing independently of each other, and it does nothing for
    anyone who reaches FastAPI by a path that skips PCHost. It is not the
    real access control regardless -- every mutating /api/admin/* route
    already requires a valid admin JWT via get_current_admin, enforced as
    a router-level dependency (see routes/admin.py's APIRouter(...,
    dependencies=[Depends(get_current_admin)])) independent of this
    middleware entirely. This middleware is optional defense-in-depth on
    top of that, not a substitute for it, and it is off by default.
    """
    if not _admin_mutations_localhost_only():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/admin"):
        return await call_next(request)
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return await call_next(request)
    host = ""
    try:
        if request.client:
            host = (request.client.host or "").strip()
    except Exception:
        host = ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={
            "detail": "Admin mutation blocked: ADMIN_MUTATIONS_LOCALHOST_ONLY is set; only loopback clients may POST/PUT/PATCH/DELETE under /api/admin.",
        },
    )


# Metrics and HTTP logging middleware

logs_dir = os.path.join(os.path.dirname(__file__), "logs")
_metrics = Metrics(logs_dir)

# expose global metrics singleton
import server.metrics as _metrics_module  # type: ignore  # noqa: E402
_metrics_module.metrics = _metrics


@app.middleware("http")
async def http_logger(request, call_next):
    t0 = time.perf_counter()
    in_len = 0
    try:
        if request.headers.get("content-length"):
            in_len = int(request.headers.get("content-length"))
    except Exception:
        in_len = 0
    # increment active concurrency
    try:
        _metrics.inc_active()
    except Exception:
        pass
    try:
        response = await call_next(request)
        out_len = 0
        try:
            if response.headers.get("content-length"):
                out_len = int(response.headers.get("content-length"))
        except Exception:
            out_len = 0
        ms = (time.perf_counter() - t0) * 1000
        _metrics.record_http(request.method, request.url.path, getattr(response, 'status_code', 0), ms, in_len, out_len)
        return response
    except Exception:
        ms = (time.perf_counter() - t0) * 1000
        _metrics.record_http(request.method, request.url.path, 500, ms, in_len, 0)
        raise
    finally:
        try:
            _metrics.dec_active()
        except Exception:
            pass

# Include API routes and wire auth dependencies
from server.routes.ocr import router as ocr_router  # noqa: E402
from server.routes.asr import router as asr_router  # noqa: E402
from server.routes.notes import router as notes_router  # noqa: E402
from server.routes.feedback import router as feedback_router  # noqa: E402
from server.routes.rag_updates import router as rag_router  # noqa: E402
from server.routes.perf import router as perf_router  # noqa: E402
from server.routes.admin import router as admin_router  # noqa: E402
from server.routes.auth_users import router as auth_router  # noqa: E402
from server.routes.workspace import router as workspace_router  # noqa: E402
from server.routes.admin_users import router as admin_users_router  # noqa: E402
from server.routes.qa_chat import router as qa_chat_router  # noqa: E402
from server.routes.qa_vision import router as qa_vision_router  # noqa: E402
from server.routes.queue import router as queue_router  # noqa: E402
from server.routes.asr_segments import router as asr_segments_router  # noqa: E402
from server.routes.asr_retranscribe import router as asr_retranscribe_router  # noqa: E402
from server.routes.asr_modes import router as asr_modes_router  # noqa: E402
from server.routes.version import router as version_router  # noqa: E402
from server.routes.profile import router as profile_router  # noqa: E402
from server.routes.encounters import router as encounters_router  # noqa: E402
from server.routes.patient_materials import router as patient_materials_router  # noqa: E402
from server.core.dependencies import require_api_bearer  # noqa: E402
from server.core.db import init_db  # noqa: E402
from server.core.bootstrap_admin import ensure_bootstrap_admin  # noqa: E402


"""
Mount all APIs under "/api" so direct access works and behind proxy too.
Make health open (no auth). Admin endpoints remain protected by admin token.
Also include backward-compatible routes at root (no /api) to avoid 404s from old pages.
"""
app.include_router(ocr_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(asr_router, prefix="/api")
app.include_router(notes_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(feedback_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(rag_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(qa_chat_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(qa_vision_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(perf_router, prefix="/api")  # /api/health open
app.include_router(version_router, prefix="/api")  # /api/version open
app.include_router(auth_router)
app.include_router(workspace_router)
app.include_router(admin_users_router)
app.include_router(admin_router)
app.include_router(queue_router)
app.include_router(asr_segments_router, dependencies=[Depends(require_api_bearer)])
app.include_router(asr_retranscribe_router, dependencies=[Depends(require_api_bearer)])
app.include_router(asr_modes_router, dependencies=[Depends(require_api_bearer)])
app.include_router(profile_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(encounters_router, dependencies=[Depends(require_api_bearer)])
app.include_router(patient_materials_router, prefix="/api", dependencies=[Depends(require_api_bearer)])

"""
Serve static files for the web UI.
- Primary: path from config.json key 'web_dir' if present
- Fallback: C:/PCHost/web
- Fallback: repo ./web
"""

def _load_cfg() -> dict:
    try:
        # server/app.py -> repo_root/config/config.json
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


cfg = _load_cfg()
web_dir_cfg = cfg.get("web_dir")
web_dir: Path
if isinstance(web_dir_cfg, str) and web_dir_cfg.strip():
    web_dir = Path(web_dir_cfg.strip())
else:
    # Use PCHost/web (where actual web files are)
    web_dir = Path("C:/PCHost/web")

# Fallback to local web directory if previous target doesn't exist
if not web_dir.exists():
    current_dir = Path(__file__).parent
    web_dir = (current_dir.parent / "web").resolve()

if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    print(f"Serving web UI from: {web_dir}")
else:
    print(
        "Warning: Web directory not found. Tried config 'web_dir', C:/PCHost/web, and ./web.\n"
        "Pages under /static will 404 until web files are available."
    )

# Root redirect to admin page
@app.get("/")
async def root():
    return RedirectResponse(url="/static/admin.html")

# Password reset page
@app.get("/reset-password/{token}")
async def reset_password_page(token: str):
    reset_html = web_dir / "reset-password.html"
    if reset_html.exists():
        return HTMLResponse(reset_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>Reset page not found</h2><p>Please contact support.</p>", status_code=404)

# Add startup and shutdown handlers for process cleanup
@app.on_event("startup")
async def startup_event():
    logger.info("Server starting up...")
    try:
        init_db()
        logger.info("Auth/workspace database initialized")
        ensure_bootstrap_admin()
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
    try:
        from server.core.llm_routing import summarize_llm_routing_for_log

        _cfg_startup = _load_cfg()
        logger.info("LLM routing (per-feature, host:port only):")
        for line in summarize_llm_routing_for_log(_cfg_startup):
            logger.info(line)
    except Exception as exc:
        logger.warning("LLM routing summary failed: %s", exc)
    # Note: We do NOT auto-start llama/OCR servers here anymore
    # This allows admin.html to have full control over when servers start/stop
    # and prevents conflicting processes. Use admin.html to manually start servers.
    logger.info("Use admin.html to manually start llama/OCR servers when needed")
    # External services are managed outside this app (no auto-start here).

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Server shutting down")

# Run server
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("FASTAPI_PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
