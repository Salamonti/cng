# C:\Clinical-Note-Generator\server\app.py
# app.py

import os
import sys
import time
import logging # type: ignore  # noqa: F401
from server.metrics import Metrics
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
import json

logger = logging.getLogger(__name__)


# Ensure local package imports work even though parent folder name has a dash
BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)



# Create FastAPI app (no root_path). We'll mount routers under "/api" explicitly.
app = FastAPI()

# CORS (adjust allow_origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Expose streaming generation id to browsers so UI can poll metadata endpoints
    expose_headers=["X-Generation-Id"],
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
from server.routes.rag_updates import router as rag_router  # noqa: E402
from server.routes.perf import router as perf_router  # noqa: E402
from server.routes.admin import router as admin_router  # noqa: E402
from server.routes.auth_users import router as auth_router  # noqa: E402
from server.routes.workspace import router as workspace_router  # noqa: E402
from server.routes.admin_users import router as admin_users_router  # noqa: E402
from server.routes.qa_chat import router as qa_chat_router  # noqa: E402
#from server.routes.services import router as services_router # noqa: E402
from server.auth import require_api_bearer  # noqa: E402
from server.core.db import init_db  # noqa: E402


"""
Mount all APIs under "/api" so direct access works and behind proxy too.
Make health open (no auth). Admin endpoints remain protected by admin token.
Also include backward-compatible routes at root (no /api) to avoid 404s from old pages.
"""
app.include_router(ocr_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(asr_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(notes_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(rag_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(qa_chat_router, prefix="/api", dependencies=[Depends(require_api_bearer)])
app.include_router(perf_router, prefix="/api")  # /api/health open
app.include_router(auth_router)
app.include_router(workspace_router)
app.include_router(admin_users_router)
#app.include_router(services_router)
app.include_router(admin_router)

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

# Add startup and shutdown handlers for process cleanup
@app.on_event("startup")
async def startup_event():
    logger.info("Server starting up...")
    try:
        init_db()
        logger.info("Auth/workspace database initialized")
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
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
