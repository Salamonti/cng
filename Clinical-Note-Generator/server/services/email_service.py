# server/services/email_service.py
"""Email service using Resend API for transactional emails."""
import logging
from typing import Optional

import resend

from server.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = None
_initialized = False


def _init():
    global _settings, _initialized
    if _initialized:
        return
    _settings = get_settings()
    if _settings.resend_api_key:
        resend.api_key = _settings.resend_api_key
    _initialized = True


def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> bool:
    """Send an email via Resend. Returns True if sent successfully."""
    _init()
    if not _settings or not _settings.resend_api_key:
        logger.warning("Resend API key not configured — email not sent")
        return False

    try:
        params = {
            "from": _settings.resend_from_email,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if text:
            params["text"] = text
        result = resend.Emails.send(params)
        logger.info("Email sent to %s: %s", to, result)
        return True
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)
        return False


def send_password_reset_email(
    to: str,
    reset_link: str,
) -> bool:
    """Send a password reset email with a reset link."""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; padding: 20px;">
        <h2 style="color: #333;">Password Reset Request</h2>
        <p>You requested a password reset for your DreamCision account.</p>
        <p style="margin: 20px 0;">
            <a href="{reset_link}" 
               style="background-color: #2563eb; color: white; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; display: inline-block;">
                Reset Password
            </a>
        </p>
        <p style="color: #666; font-size: 14px;">
            This link expires in 15 minutes. If you didn't request this, 
            please ignore this email.
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px;">
            DreamCision AI Scribe<br>
            If the button doesn't work, copy this link:<br>
            <a href="{reset_link}">{reset_link}</a>
        </p>
    </div>
    """
    text = f"Reset your password: {reset_link}\nThis link expires in 15 minutes."
    return send_email(
        to=to,
        subject="DreamCision: Password Reset Request",
        html=html,
        text=text,
    )


def send_admin_registration_notification(
    admin_email: str,
    user_email: str,
) -> bool:
    """Notify admin when a new user registers."""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; padding: 20px;">
        <h2 style="color: #333;">New Registration</h2>
        <p>A new user has registered for DreamCision:</p>
        <div style="background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 16px 0;">
            <p style="margin: 4px 0;"><strong>Email:</strong> {user_email}</p>
        </div>
        <p style="color: #666; font-size: 14px;">
            Please review and approve or reject this user from the admin console.
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px;">DreamCision AI Scribe</p>
    </div>
    """
    text = f"New user registered: {user_email}. Please review in the admin console."
    return send_email(
        to=admin_email,
        subject="DreamCision: New User Registration",
        html=html,
        text=text,
    )


def send_approval_email(
    to: str,
) -> bool:
    """Notify user that their account has been approved."""
    html = """
    <div style="font-family: Arial, sans-serif; max-width: 500px; padding: 20px;">
        <h2 style="color: #16a34a;">Account Approved ✓</h2>
        <p>Your DreamCision account has been approved. You can now log in and use the application.</p>
        <p style="margin: 20px 0;">
            <a href="https://notes.ieissa.com/" 
               style="background-color: #16a34a; color: white; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; display: inline-block;">
                Go to DreamCision
            </a>
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px;">DreamCision AI Scribe</p>
    </div>
    """
    text = "Your DreamCision account has been approved. You can now log in at https://notes.ieissa.com/"
    return send_email(
        to=to,
        subject="DreamCision: Account Approved",
        html=html,
        text=text,
    )
