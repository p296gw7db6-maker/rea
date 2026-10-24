"""Spotify OAuth 2.0 Authorization Code flow with PKCE (stdlib only).

PKCE means no client secret is stored on disk. The access token is short-lived
and refreshed automatically; both are cached under the config home directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import Config, token_path

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"


def _make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _post_form(data: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        _TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = (params.get("code") or [None])[0]  # type: ignore[attr-defined]
        self.server.auth_state = (params.get("state") or [None])[0]  # type: ignore[attr-defined]
        self.server.auth_error = (params.get("error") or [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif'>"
            b"<h2>spotsync: authorized.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, *args):  # silence default stderr logging
        pass


def _redirect_port(redirect_uri: str) -> int:
    parsed = urllib.parse.urlparse(redirect_uri)
    return parsed.port or 8888


def _save_token(token: dict) -> None:
    token = dict(token)
    token.setdefault("obtained_at", time.time())
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_token() -> dict | None:
    path = token_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def authorize(cfg: Config, open_browser: bool = True) -> dict:
    """Run the interactive PKCE flow and cache the resulting token."""
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": cfg.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": cfg.scopes,
        }
    )
    auth_url = f"{_AUTH_URL}?{query}"

    httpd = HTTPServer(("127.0.0.1", _redirect_port(cfg.redirect_uri)), _CallbackHandler)
    httpd.auth_code = None  # type: ignore[attr-defined]
    httpd.auth_state = None  # type: ignore[attr-defined]
    httpd.auth_error = None  # type: ignore[attr-defined]
    httpd.timeout = 180

    print("Open this URL in your browser to authorize (read-only):\n")
    print(auth_url + "\n")
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    # Serve single requests until the callback arrives (ignores favicon, etc.).
    deadline = time.time() + 300
    while httpd.auth_code is None and httpd.auth_error is None:  # type: ignore[attr-defined]
        if time.time() > deadline:
            raise TimeoutError("Timed out waiting for Spotify authorization callback.")
        httpd.handle_request()

    if httpd.auth_error:  # type: ignore[attr-defined]
        raise RuntimeError(f"Authorization failed: {httpd.auth_error}")  # type: ignore[attr-defined]
    if httpd.auth_state != state:  # type: ignore[attr-defined]
        raise RuntimeError("State mismatch in OAuth callback (possible CSRF). Aborting.")

    token = _post_form(
        {
            "grant_type": "authorization_code",
            "code": httpd.auth_code,  # type: ignore[attr-defined]
            "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id,
            "code_verifier": verifier,
        }
    )
    _save_token(token)
    return token


def _refresh(cfg: Config, token: dict) -> dict:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token cached; run `spotsync auth` again.")
    new_token = _post_form(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cfg.client_id,
        }
    )
    # Spotify may omit a new refresh_token; keep the old one.
    new_token.setdefault("refresh_token", refresh_token)
    _save_token(new_token)
    return new_token


def get_access_token(cfg: Config) -> str:
    """Return a valid bearer token, refreshing or prompting re-auth as needed."""
    token = _load_token()
    if token is None:
        token = authorize(cfg)

    obtained = token.get("obtained_at", 0)
    expires_in = token.get("expires_in", 3600)
    if time.time() >= obtained + expires_in - 60:
        token = _refresh(cfg, token)

    access = token.get("access_token")
    if not access:
        raise RuntimeError("No access token available; run `spotsync auth`.")
    return access
