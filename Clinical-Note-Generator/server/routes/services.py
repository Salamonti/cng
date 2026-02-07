from fastapi import APIRouter, Depends
import os

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
    fastapi_port = int(os.environ.get("FASTAPI_PORT", 7860))
    llama_port = 8081
    try:
        llama_url = os.environ.get("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
        parsed_llama = _p.urlparse(llama_url if "://" in llama_url else f"http://{llama_url}")
        llama_port = parsed_llama.port or 8081
    except Exception:
        llama_port = 8081
    ocr_port = 8090
    try:
        ocr_url = os.environ.get("OCR_URL_PRIMARY", "http://127.0.0.1:8090")
        parsed = _p.urlparse(ocr_url if "://" in ocr_url else f"http://{ocr_url}")
        ocr_port = parsed.port or 8090
    except Exception:
        ocr_port = 8090

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
