# C:\Clinical-Note-Generator\server\schemas\auth.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @validator("password")
    def validate_password(cls, value: str) -> str:
        rules = [
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        ]
        if not all(rules):
            raise ValueError("Password must include upper, lower, digit, and symbol.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserProfile(BaseModel):
    id: str
    email: EmailStr
    is_admin: bool
    is_approved: bool
    created_at: datetime
