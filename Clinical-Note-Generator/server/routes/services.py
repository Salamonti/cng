from fastapi import APIRouter, Depends

from server.core.dependencies import get_current_admin
from server.models.user import User

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("/status")
@router.get("/status/")
def get_services_status(_: User = Depends(get_current_admin)):
    """
    Get status of all services.
    Requires admin authentication.
    
    Note: This is a placeholder implementation.
    Actual service monitoring would require additional logic to check process status.
    """
    import urllib.parse as _p
    try:
        from server.core.config import get_settings
        cfg = get_settings()
    except Exception:
        cfg = None

    fastapi_port = int(os.environ.get("FASTAPI_PORT", getattr(cfg, "fastapi_port", 7860) if cfg else 7860))
    llama_port = int(getattr(cfg, "llama_server_port", 8081)) if cfg else 8081
    ocr_port = None
    try:
        ocr_url = str(getattr(cfg, "ocr_server_url", ""))
        parsed = _p.urlparse(ocr_url if "://" in ocr_url else f"http://{ocr_url}")
        ocr_port = parsed.port or 80
    except Exception:
        ocr_port = None

    return {
        "services": {
            "fastapi": {
                "name": "FastAPI Server",
                "status": "running",
                "port": fastapi_port,
                "process": {"pid": None},
                "nssm": {"status": None}
            },
            "llama": {
                "name": "LLaMA Server", 
                "status": "unknown",
                "port": llama_port,
                "process": {"pid": None},
                "nssm": {"status": None}
            },
            "ocr": {
                "name": "OCR Service",
                "status": "unknown",
                "port": ocr_port,
                "process": {"pid": None},
                "nssm": {"status": None}
            }
        }
    }
