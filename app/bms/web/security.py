"""Authentication for the internal application.

Deliberately simple, as BMS asked: a local username and password inside the
secured BMS environment, with no Active Directory, LDAP, SSO or MFA. All users
share one operational role.

Passwords are stored as salted PBKDF2-HMAC-SHA256, never in the clear. The
session is a signed cookie carrying only the user id and an expiry -- no case
data is ever placed in the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PBKDF2_ROUNDS = 240_000
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
COOKIE_NAME = "bms_session"


def _secret() -> bytes:
    """Signing key.

    A generated key means sessions do not survive a restart, which is safe but
    inconvenient; set BMS_SECRET_KEY on any real deployment.
    """
    configured = os.environ.get("BMS_SECRET_KEY")
    if configured:
        return configured.encode()
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: bytes | None = None


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), expected)


def _sign(payload: bytes) -> str:
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode().rstrip("=")
        + "."
        + base64.urlsafe_b64encode(signature).decode().rstrip("=")
    )


def _unpad(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session(user_id: str) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS})
    return _sign(payload.encode())


def read_session(token: str | None) -> str | None:
    """Return the user id if the token is valid and unexpired."""
    if not token or "." not in token:
        return None
    payload_part, signature_part = token.rsplit(".", 1)
    try:
        payload = _unpad(payload_part)
        signature = _unpad(signature_part)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    return data.get("uid")
