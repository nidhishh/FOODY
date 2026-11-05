"""
Configuration, persistent state, and Swiggy OAuth token manager for FOODY.
Reads OAuth 2.1 tokens directly from ~/.swiggy/auth.json and automatically refreshes
them via Swiggy's token endpoint when near expiration.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

# Load .env from FOODY root or foody_agent directory
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

# Ensure GOOGLE_API_KEY is populated if GEMINI_API_KEY was provided
if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

FOODY_MODEL = os.getenv("FOODY_MODEL", "gemini-2.5-flash")
SWIGGY_FOOD_MCP_URL = "https://mcp.swiggy.com/food"

DEFAULT_ADDRESS_ID = os.getenv(
    "DEFAULT_ADDRESS_ID",
    "d96vons1d96q25a1vd6g__ASDDdwRXLVoelNROTOQ0xA",
)
DEFAULT_ADDRESS_LABEL = os.getenv(
    "DEFAULT_ADDRESS_LABEL",
    "Office — Nariman Point, Mumbai",
)

STATE_FILE = ROOT_DIR / "state_store.json"


def get_swiggy_auth_path() -> Path:
    custom = os.getenv("SWIGGY_AUTH_PATH", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".swiggy" / "auth.json"


class FoodyState:
    """Simple local state store for active delivery address and staged checkout gate."""

    def __init__(self) -> None:
        self.active_address_id: str = DEFAULT_ADDRESS_ID
        self.active_address_label: str = DEFAULT_ADDRESS_LABEL
        self.checkout_unlocked: bool = False
        self.last_cart_snapshot: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.active_address_id = data.get("active_address_id", self.active_address_id)
                self.active_address_label = data.get("active_address_label", self.active_address_label)
            except Exception:
                pass

    def save(self) -> None:
        try:
            STATE_FILE.write_text(
                json.dumps(
                    {
                        "active_address_id": self.active_address_id,
                        "active_address_label": self.active_address_label,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def set_address(self, address_id: str, label: str) -> None:
        self.active_address_id = address_id
        self.active_address_label = label
        self.save()


STATE = FoodyState()


def get_valid_bearer_token(server: str = "food") -> str:
    """
    Returns a valid OAuth 2.1 Bearer accessToken from ~/.swiggy/auth.json.
    If the token is within 10 minutes of expiring, refreshes it automatically.
    """
    auth_path = get_swiggy_auth_path()
    if not auth_path.exists():
        raise RuntimeError(
            f"Swiggy auth file not found at {auth_path}. "
            "Run `npx swiggy-cli auth init` once in your terminal to sign in."
        )

    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    server_auth: Optional[Dict[str, Any]] = raw.get("servers", {}).get(server)
    if not server_auth or not server_auth.get("accessToken"):
        raise RuntimeError(
            f"No accessToken found for server '{server}' in {auth_path}. "
            "Run `npx swiggy-cli auth init` to authenticate."
        )

    expires_at_ms = server_auth.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)

    # Refresh if within 10 minutes of expiry
    if expires_at_ms and (expires_at_ms - now_ms < 600_000) and server_auth.get("refreshToken"):
        token_endpoint = server_auth.get("tokenEndpoint", "https://mcp.swiggy.com/auth/token")
        client_id = server_auth.get("clientId", "")
        try:
            payload: Dict[str, str] = {
                "grant_type": "refresh_token",
                "refresh_token": server_auth["refreshToken"],
            }
            if client_id:
                payload["client_id"] = client_id

            resp = httpx.post(
                token_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
            if resp.status_code == 200:
                new_tok = resp.json()
                if "access_token" in new_tok:
                    server_auth["accessToken"] = new_tok["access_token"]
                    if "refresh_token" in new_tok:
                        server_auth["refreshToken"] = new_tok["refresh_token"]
                    expires_in = int(new_tok.get("expires_in", 432000))
                    server_auth["obtainedAt"] = now_ms
                    server_auth["expiresAt"] = now_ms + (expires_in * 1000)
                    auth_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception:
            # If refresh fails but token hasn't strictly expired yet, continue using existing token
            pass

    return str(server_auth["accessToken"])

