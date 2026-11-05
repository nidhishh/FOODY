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
SWIGGY_DINEOUT_MCP_URL = "https://mcp.swiggy.com/dineout"
SWIGGY_INSTAMART_MCP_URL = "https://mcp.swiggy.com/im"

DEFAULT_ADDRESS_ID = os.getenv(
    "DEFAULT_ADDRESS_ID",
    "d96vons1d96q25a1vd6g__ASDDdwRXLVoelNROTOQ0xA",
)
DEFAULT_ADDRESS_LABEL = os.getenv(
    "DEFAULT_ADDRESS_LABEL",
    "Office — Nariman Point, Mumbai",
)

# Known coordinate mappings for saved addresses and common Mumbai localities
LOCALITY_COORDINATES: Dict[str, tuple[float, float]] = {
    "nariman point": (18.9256, 72.8242),
    "maker chamber": (18.9256, 72.8242),
    "fort": (18.9322, 72.8335),
    "colaba": (18.9067, 72.8147),
    "churchgate": (18.9322, 72.8264),
    "marine lines": (18.9438, 72.8234),
    "worli": (18.9986, 72.8174),
    "tata towers": (18.9986, 72.8174),
    "prabhadevi": (19.0166, 72.8295),
    "lower parel": (18.9953, 72.8244),
    "kamala mills": (18.9953, 72.8244),
    "dadar": (19.0178, 72.8478),
    "bandra": (19.0596, 72.8295),
    "bandra west": (19.0596, 72.8295),
    "bkc": (19.0657, 72.8687),
    "bandra kurla complex": (19.0657, 72.8687),
    "juhu": (19.1075, 72.8263),
    "andheri": (19.1136, 72.8697),
    "powai": (19.1176, 72.9060),
    "mumbai": (18.9256, 72.8242),
}


def resolve_coordinates(area_or_locality: Optional[str] = None) -> tuple[float, float]:
    """
    Resolves (latitude, longitude) for Dineout searches.
    1. Checks if area_or_locality matches a known locality.
    2. Else checks active delivery address label keywords.
    3. Defaults to South Mumbai (Nariman Point: 18.9256, 72.8242).
    """
    if area_or_locality and area_or_locality.strip():
        clean = area_or_locality.strip().lower()
        for loc_name, coords in LOCALITY_COORDINATES.items():
            if loc_name in clean:
                return coords

    # Check active address label
    label_low = STATE.active_address_label.lower()
    for loc_name, coords in LOCALITY_COORDINATES.items():
        if loc_name in label_low:
            return coords

    return (18.9256, 72.8242)

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

