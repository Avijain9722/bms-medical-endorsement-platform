"""Shared test fixtures.

The CSRF client below is the important part: it makes the tests submit forms the
way a browser does, rather than the way a script does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.web.security import COOKIE_NAME, CSRF_FIELD, csrf_token  # noqa: E402


class BrowserClient(TestClient):
    """A TestClient that carries the session's CSRF token, as a browser would.

    Every form the platform renders contains the hidden field, so a real
    submission always has it. Without this the tests would have to thread the
    token through by hand at every call site, which is noise -- and worse, the
    temptation would be to exempt the routes instead.

    Requests that deliberately omit or corrupt the token pass `csrf=False` and
    supply their own, so the guard itself is still tested directly.
    """

    def post(self, url, *args, csrf: bool = True, **kwargs):  # type: ignore[override]
        if csrf and url != "/login":
            token = csrf_token(self.cookies.get(COOKIE_NAME))
            if token:
                data = kwargs.get("data")
                if data is None and "files" not in kwargs and "json" not in kwargs:
                    data = {}
                if isinstance(data, dict):
                    data = {**data, CSRF_FIELD: token}
                    kwargs["data"] = data
                elif "files" in kwargs:
                    kwargs["data"] = {**(data or {}), CSRF_FIELD: token}
        return super().post(url, *args, **kwargs)


@pytest.fixture()
def reset_login_throttle():
    """Login throttling is process-wide, so tests must not leak into each other."""
    from bms.web import security

    security.reset_throttle()
    yield
    security.reset_throttle()
