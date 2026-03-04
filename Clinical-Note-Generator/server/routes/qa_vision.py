"""
Vision QA route for medical image questions.
Streaming endpoint with no OCR fallback.
"""
import asyncio
import os
from typing import AsyncIterator, Dict, Any
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from server.services.vision_qa_client import VisionQAEngine
from server.services.qa_deid import deidentify_text
from server.core.security import decode_access_token

router = APIRouter(prefix="/qa", tags=["qa-vision"])
security = HTTPBearer(auto_error=False)

class VisionQAResponse(BaseModel):
    """Non‑streaming response (for error cases or future non‑streaming endpoint)."""
    answer: str
    confidence: float = 0.0
    model_used: str = ""

async def stream_vision_answer(
    image_bytes: bytes,
    mime_type: str,
    question: str,
) -> AsyncIterator[str]:
    """Generator that yields tokens from vision model."""
    # Initialize vision engine
    vision_url = os.environ.get("VISION_QA_URL") or os.environ.get("OCR_URL_PRIMARY") or "http://127.0.0.1:8081"
    engine = VisionQAEngine(url=vision_url)
    
    try:
        async for chunk in engine.stream_vision_answer(
            image_bytes=image_bytes,
            mime_type=mime_type,
            question=question,
        ):
            yield chunk
    except Exception as e:
        # Convert to plain error message (no OCR fallback per requirement)
        yield f"\n\n[ERROR: Vision analysis failed: {str(e)}]"
        # Don't attempt OCR fallback

@router.post("/vision")
async def qa_vision(
    question: str = Form(..., min_length=3, max_length=1000),
    image: UploadFile = File(...),
    creds: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Stream a medical answer about an uploaded image.
    No OCR fallback; if vision fails, an error is streamed.
    """
    # Authentication (same pattern as qa_chat)
    token = creds.credentials if creds else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        payload = decode_access_token(token)
        user_id = str(payload.get("sub") or "")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    
    # Validate image type
    allowed_types = {
        "image/jpeg", "image/jpg", "image/png", 
        "image/webp", "image/bmp", "image/gif"
    }
    if image.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported image type: {image.content_type}. Allowed: {', '.join(sorted(allowed_types))}"
        )
    
    # Read image (size limit ~10MB)
    MAX_SIZE = 10 * 1024 * 1024
    image_data = await image.read()
    if len(image_data) > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large ({len(image_data)} bytes). Maximum is {MAX_SIZE} bytes."
        )
    
    # Optional: basic image dimension check (min 50x50)
    # Could add PIL check here, but keep simple for now
    
    # Streaming response
    async def generate() -> AsyncIterator[str]:
        # Buffer to optionally de‑identify after streaming
        buffer = []
        async for chunk in stream_vision_answer(
            image_data,
            image.content_type or "image/jpeg",
            question,
        ):
            buffer.append(chunk)
            yield chunk
        
        # After stream ends, we could post‑process if needed
        # For now, rely on model prompt to avoid PHI
        
    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
            "X-Vision-Model": "ministral-14b",  # informational
        }
    )