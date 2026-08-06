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


# ------------------------------------------------------------- login throttling

# Verifying a password costs 240,000 PBKDF2 rounds by design -- expensive for an
# attacker guessing, but equally expensive for the server, and the login form is
# reachable without a session. Unthrottled it is therefore two problems at once:
# an unlimited guessing oracle, and a way for one unauthenticated client to burn
# the host's CPU. Throttling per username and per client address closes both.
FAILURE_LIMIT = 5
FAILURE_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60

# The tracking itself must not become the denial of service it prevents. Each
# distinct username tried creates an entry, and an attacker chooses the
# usernames -- so without a bound, guessing random names grows the process until
# it dies. Entries older than the window are worthless, so they are swept
# periodically, and a hard cap backstops a burst that outruns the sweep.
MAX_TRACKED_KEYS = 10_000
SWEEP_INTERVAL_SECONDS = 60

_failures: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}
_last_sweep = 0.0


def _sweep(now: float) -> None:
    """Drop everything that can no longer affect a decision."""
    global _last_sweep
    if now - _last_sweep < SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now

    for key in [k for k, until in _locked_until.items() if until <= now]:
        _locked_until.pop(key, None)
    for key in list(_failures):
        if key in _locked_until:
            continue
        recent = [t for t in _failures[key] if now - t < FAILURE_WINDOW_SECONDS]
        if recent:
            _failures[key] = recent
        else:
            del _failures[key]


def _enforce_cap() -> None:
    """Hard bound on tracked keys, checked on every failure.

    The periodic sweep cannot be the only bound: it is rate-limited, and an
    attacker sending tens of thousands of distinct usernames inside one interval
    outruns it entirely. This is a cheap length check on the common path, and
    only does work when the cap is actually exceeded. Locked-out keys are the
    ones still doing useful work, so they are kept and the least recently active
    of the rest are dropped first.
    """
    overflow = len(_failures) - MAX_TRACKED_KEYS
    if overflow <= 0:
        return
    droppable = sorted(
        (k for k in _failures if k not in _locked_until),
        key=lambda k: _failures[k][-1],
    )
    for key in droppable[:overflow]:
        del _failures[key]


def _prune(key: str, now: float) -> None:
    recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)


def locked_out(key: str, *, now: float | None = None) -> int:
    """Seconds remaining on a lockout, or 0. Checked before any hashing."""
    now = time.time() if now is None else now
    until = _locked_until.get(key)
    if until is None:
        return 0
    if until <= now:
        _locked_until.pop(key, None)
        _failures.pop(key, None)
        return 0
    return int(until - now)


def record_failure(key: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _sweep(now)
    _prune(key, now)
    _failures.setdefault(key, []).append(now)
    if len(_failures[key]) >= FAILURE_LIMIT:
        _locked_until[key] = now + LOCKOUT_SECONDS
    _enforce_cap()


def record_success(key: str) -> None:
    _failures.pop(key, None)
    _locked_until.pop(key, None)


def reset_throttle() -> None:
    """Test helper: forget all recorded failures."""
    global _last_sweep
    _failures.clear()
    _locked_until.clear()
    _last_sweep = 0.0


# ---------------------------------------------------------------------- CSRF

# The token is derived from the session cookie rather than stored anywhere: it is
# HMAC(secret, session-value), so it is stable for the life of a session, unique
# per session, and unguessable without the signing key. That means no extra
# cookie, no server-side table to expire, and nothing to keep in sync -- and a
# stolen token is useless once its session ends.
#
# SameSite=Lax already blocks the classic cross-site form POST, so this is
# defence in depth: it also covers same-site attacks, a browser that does not
# honour SameSite, and any future deployment where the platform is reachable from
# outside the BMS network.
CSRF_FIELD = "_csrf"


def csrf_token(session_cookie: str | None) -> str:
    """The token belonging to this session. Empty when there is no session."""
    if not session_cookie:
        return ""
    return hmac.new(_secret(), b"csrf:" + session_cookie.encode(), hashlib.sha256).hexdigest()


def csrf_valid(session_cookie: str | None, submitted: str | None) -> bool:
    expected = csrf_token(session_cookie)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)
