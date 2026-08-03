# DreamCision — Resend Email Integration Plan

## Overview

Add email verification (OTP) and password recovery to DreamCision using Resend as the transactional email provider. Admin approval remains required after email verification. Admin retains full user management (approve, reject, delete, block).

---

## 1. Database Schema Changes

### New columns on `User` table (`server/models/user.py`):

| Column | Type | Default | Purpose |
|---|---|---|---|
| `email_verified` | `bool` | `False` | OTP confirmed user owns the email |
| `verification_code` | `str \| None` | `None` | 6-digit code (hashed via PBKDF2) |
| `verification_code_expires` | `datetime \| None` | `None` | Expires after 15 minutes |
| `verification_attempts` | `int` | `0` | Fail counter — lock after 5 attempts |
| `reset_token` | `str \| None` | `None` | Single-use password reset token (hashed) |
| `reset_token_expires` | `datetime \| None` | `None` | Expires after 60 minutes |
| `last_verification_sent` | `datetime \| None` | `None` | Rate-limit: 1 email per 60 seconds |
| `last_reset_sent` | `datetime \| None` | `None` | Rate-limit: 1 reset email per 60 seconds |

### New model: `VerificationLog` (audit trail):

| Column | Type | Purpose |
|---|---|---|
| `id` | `UUID` | Primary key |
| `user_id` | `UUID` | FK → User |
| `event_type` | `str` | `verification_sent`, `verification_success`, `verification_failed`, `reset_sent`, `reset_success`, `reset_failed` |
| `ip_address` | `str \| None` | Source IP |
| `created_at` | `datetime` | Timestamp |

---

## 2. New API Endpoints

All under `/api/auth`:

### `POST /send-verification`
- **Who:** Unauthenticated user just registered
- **What:** Sends 6-digit OTP to their email
- **Guardrails:**
  - User must exist and be unverified (`email_verified=False`)
  - Rate-limited: max 1 per 60 seconds
  - If 5 failed attempts: must wait 15 minutes before retrying
- **Response:** `{"ok": true, "message": "Verification code sent"}`

### `POST /verify-email`
- **Who:** Unauthenticated user with email + code
- **Body:** `{ "email": "...", "code": "123456" }`
- **What:** Validates the code, marks `email_verified=True`
- **Guardrails:**
  - Code expires after 15 minutes
  - 5 attempts then lockout for 15 minutes
  - Code invalidated after success (single-use)
- **Response:** `{"ok": true, "email_verified": true, "is_approved": false}`

### `POST /request-password-reset`
- **Who:** Unauthenticated user who forgot password
- **Body:** `{ "email": "..." }`
- **What:** If email exists AND is verified, generates single-use reset token, sends reset email
- **Security:** Always returns `{"ok": true, "message": "If an account exists, a reset link was sent"}` — **never reveals whether the email is registered** (prevents enumeration)
- **Guardrails:**
  - Rate-limited: max 1 per 60 seconds per email
  - Only sent to verified emails
  - Token expires after 60 minutes
- **Response:** Generic success message regardless

### `POST /reset-password`
- **Who:** Unauthenticated user with reset token + new password
- **Body:** `{ "token": "***", "new_password": "..." }`
- **What:** Validates token, sets new password, invalidates token
- **Guardrails:**
  - Token is single-use
  - New password must meet same rules (12+ chars, upper/lower/digit/symbol)
  - Token expires after 60 minutes
  - On success: revokes ALL existing refresh tokens for that user (forces re-login)
- **Response:** `{"ok": true, "message": "Password reset successful. Please sign in."}`

---

## 3. Admin Endpoint Extensions

Under `/api/admin/users`:

### `PATCH /{user_id}/block`
- Sets `is_active = False`
- Revokes all refresh tokens
- User can't log in until unblocked

### `PATCH /{user_id}/unblock`
- Sets `is_active = True`

### `POST /{user_id}/force-reset`
- Triggers a password reset email to the user (admin can help a user who lost access)

---

## 4. Email Templates (Resend HTML)

**From:** `DreamCision <noreply@your-domain.com>`

### Template A — Email Verification
```
Subject: Verify your DreamCision email

Body:
  DreamCision — Email Verification

  Your verification code is:

  8 4 7 2 9 1

  This code expires in 15 minutes.
  If you didn't create an account, ignore this email.
```

### Template B — Password Reset
```
Subject: Reset your DreamCision password

Body:
  DreamCision — Password Reset

  Your reset code is:

  3 9 1 0 5 6

  This code expires in 60 minutes.
  If you didn't request this, ignore this email.
```

### Template C — Admin Approval Notification (nice-to-have)
```
Subject: Your DreamCision account has been approved

Body:
  DreamCision — Account Approved

  Your account has been approved by an administrator.
  You can now sign in and use DreamCision.
```

---

## 5. Updated Registration & Login Flow

```
REGISTER:
  User enters email + password → POST /register
  → User created with email_verified=False, is_approved=False
  → Verification OTP sent automatically
  → User sees: "Check your email for a verification code"
  → User enters code → POST /verify-email
  → email_verified=True, still is_approved=False
  → User sees: "Email verified. Awaiting admin approval."
  → Admin sees user in admin panel: "Verified, Pending"
  → Admin clicks Approve → is_approved=True
  → User can now log in

LOGIN:
  User enters email + password → POST /login
  → Checks: is_approved? is_active? email_verified?
  → Any of these false → blocked with appropriate message
  → All true → tokens issued

FORGOT PASSWORD:
  User clicks "Forgot password?" on login form
  → Enters email → POST /request-password-reset
  → If email exists and is verified → reset code sent
  → User enters code + new password → POST /reset-password
  → Password changed, old tokens revoked
  → User signs in with new password
```

---

## 6. Frontend Changes (index.html + auth_workspace.js)

### Login form:
- Add "Forgot password?" link below the sign-in button
- Clicking opens a modal/panel for password reset

### Register form:
- After successful registration: show OTP entry field instead of just "Awaiting approval"
- Two-step message: "1. Verify email → 2. Await admin approval"

### New UI elements:
- Verification code input modal (6 digits, numeric keyboard on mobile)
- Password reset flow: email input → code input → new password + confirm
- Rate-limit messaging: "Code expired. Request a new one." / "Too many attempts. Try again in 15 min."

---

## 7. Configuration

### Environment variables (in `.env` or `config.json`):

```
RESEND_API_KEY=***                    # API key from Resend dashboard
RESEND_FROM=noreply@dreamcision.com   # Verified sending domain
RESEND_FROM_NAME=DreamCision
EMAIL_VERIFICATION_EXPIRE_MIN=15      # OTP expiry
PASSWORD_RESET_EXPIRE_MIN=60          # Reset token expiry
MAX_VERIFICATION_ATTEMPTS=5           # Fail lockout threshold
VERIFICATION_LOCKOUT_MIN=15           # Lockout duration
EMAIL_RATE_LIMIT_SEC=60               # Min seconds between sends
```

### Resend setup steps:
1. Create account at resend.com
2. Verify sending domain (DreamCision domain)
3. Add DNS records (DKIM, SPF) — Resend provides these
4. API key stored in env, never committed to code

---

## 8. Security Details

- **Codes are hashed in DB** (same `hash_password` / PBKDF2) — even if DB is leaked, codes aren't readable
- **Single-use tokens** — invalidated after use or expiry
- **No email enumeration** — reset endpoint returns same message whether email exists or not
- **Rate limiting** — prevents brute force on OTP codes
- **Token revocation on password reset** — all active sessions killed
- **Verification required before login** — even if admin approves, unverified email blocks login

---

## 9. Resend Cost Estimate

At DreamCision volumes right now:
- ~5-20 verification emails/month
- ~2-5 password resets/month
- **$0/month** — well within the free tier of 3,000 emails/month

Even at 100 users/month, you'd use ~120 emails. Free tier covers ~250 users/month.

---

## 10. Implementation Order

1. Add DB columns (User model migration)
2. Config + Resend SDK install (`pip install resend`)
3. Email service module (send verification, send reset)
4. API endpoints (send-verification, verify-email, request-reset, reset-password)
5. Admin endpoints (block, unblock, force-reset)
6. Frontend: forgot password link + modals
7. Frontend: verification code flow after registration
8. Frontend: admin panel block/unblock buttons
9. Testing (unit + integration)
10. Admin approval notification email (nice-to-have, phase 2)