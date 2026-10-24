"""Configuration + credential locations.

Credentials come from the environment (or a config file), never hard-coded.
You register a *free* app in the Spotify developer dashboard to get a client
ID; with PKCE no client secret is needed. All scopes requested are read-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Read-only scopes: list playlists, their tracks, and the saved/"liked" songs.
# No modify/playback scopes are ever requested.
DEFAULT_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

# Spotify now requires a loopback IP (not "localhost") for new app redirects.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


def home_dir() -> Path:
    """Config/state directory. Override with SPOTSYNC_HOME."""
    env = os.environ.get("SPOTSYNC_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base).expanduser() / "spotsync"


def token_path() -> Path:
    return home_dir() / "token.json"


@dataclass
class Config:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: str = DEFAULT_SCOPES

    @classmethod
    def from_env(cls) -> "Config":
        client_id = os.environ.get("SPOTSYNC_CLIENT_ID", "").strip()
        if not client_id:
            raise RuntimeError(
                "SPOTSYNC_CLIENT_ID is not set. Register a free app at "
                "https://developer.spotify.com/dashboard, add the redirect URI "
                f"'{DEFAULT_REDIRECT_URI}', and export SPOTSYNC_CLIENT_ID=<your id>."
            )
        return cls(
            client_id=client_id,
            redirect_uri=os.environ.get("SPOTSYNC_REDIRECT_URI", DEFAULT_REDIRECT_URI),
            scopes=os.environ.get("SPOTSYNC_SCOPES", DEFAULT_SCOPES),
        )
