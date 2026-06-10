"""Single shared-password gate for the whole site.

The squad's match data isn't secret, but we don't want it fully public
either. A single password (``HOTS_ACCESS_PASSWORD``) is enough: it's
checked via HTTP Basic auth, which every browser handles with a native
prompt — zero frontend work.

When ``HOTS_ACCESS_PASSWORD`` is unset (local development), the gate is
disabled entirely. The health check is always exempt so platform
probes (Hugging Face / Render) don't need the password.
"""

from __future__ import annotations

import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_EXEMPT_PATHS = frozenset({"/api/health"})
_REALM = "HotS Helper"


class PasswordGateMiddleware(BaseHTTPMiddleware):
    """Reject requests lacking the correct HTTP Basic password."""

    def __init__(self, app, password: str) -> None:
        super().__init__(app)
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        if not self._check(request.headers.get("Authorization")):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": f'Basic realm="{_REALM}"'},
            )
        return await call_next(request)

    def _check(self, header: str | None) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        import base64

        try:
            raw = base64.b64decode(header[len("Basic ") :]).decode("utf-8")
        except Exception:
            return False
        # Username is ignored; only the password must match. Constant-time
        # compare so the gate can't be brute-forced by timing.
        _user, _, supplied = raw.partition(":")
        return secrets.compare_digest(supplied, self._password)


def install_password_gate(app) -> None:
    """Attach the gate iff ``HOTS_ACCESS_PASSWORD`` is set."""
    password = os.environ.get("HOTS_ACCESS_PASSWORD", "")
    if password:
        app.add_middleware(PasswordGateMiddleware, password=password)
