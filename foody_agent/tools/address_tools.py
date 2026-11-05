"""
Address management tools for the FOODY Google ADK agent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..config import STATE
from ..swiggy_client import SWIGGY


def get_active_delivery_address() -> Dict[str, Any]:
    """
    Returns the currently active Swiggy delivery address ID and human-readable label.
    Always check this if the user asks where their food will be delivered.
    """
    return {
        "active_address_id": STATE.active_address_id,
        "active_address_label": STATE.active_address_label,
    }


def list_saved_addresses() -> Dict[str, Any]:
    """
    Fetches all saved delivery addresses from the user's Swiggy account.
    Use this when the user wants to see their saved locations or switch addresses.
    """
    res = SWIGGY.call_tool("get_addresses", {})
    if not res.get("ok"):
        return res

    structured = res.get("data", {})
    addresses: List[Dict[str, Any]] = []
    if isinstance(structured, dict):
        raw_list = structured.get("addresses", [])
        for idx, addr in enumerate(raw_list, start=1):
            addresses.append(
                {
                    "index": idx,
                    "id": addr.get("id"),
                    "tag": addr.get("addressTag") or addr.get("addressCategory") or f"Address {idx}",
                    "addressLine": addr.get("addressLine"),
                    "is_active": addr.get("id") == STATE.active_address_id,
                }
            )

    return {
        "ok": True,
        "active_address_id": STATE.active_address_id,
        "active_address_label": STATE.active_address_label,
        "addresses": addresses,
    }


def switch_delivery_address(address_query_or_id: str) -> Dict[str, Any]:
    """
    Switches the active delivery address by matching either an exact addressId
    or a keyword/tag (such as 'Office', 'Nariman Point', 'Worli', 'Flat', 'Goa', 'Pune', 'Hyderabad').

    Args:
        address_query_or_id: The tag, city/area keyword, or exact Swiggy addressId to switch to.
    """
    listing = list_saved_addresses()
    if not listing.get("ok"):
        return listing

    q = address_query_or_id.strip().lower()
    addresses = listing.get("addresses", [])

    # 1. Exact ID match
    for addr in addresses:
        if str(addr.get("id", "")).lower() == q:
            label = f"{addr.get('tag')} — {str(addr.get('addressLine', ''))[:70]}"
            STATE.set_address(addr["id"], label)
            return {
                "ok": True,
                "message": f"Switched active delivery address to: {label}",
                "active_address_id": STATE.active_address_id,
                "active_address_label": STATE.active_address_label,
            }

    # 2. Tag or substring match in addressLine
    for addr in addresses:
        tag = str(addr.get("tag", "")).lower()
        line = str(addr.get("addressLine", "")).lower()
        if q in tag or q in line:
            label = f"{addr.get('tag')} — {str(addr.get('addressLine', ''))[:70]}"
            STATE.set_address(addr["id"], label)
            return {
                "ok": True,
                "message": f"Switched active delivery address to: {label}",
                "active_address_id": STATE.active_address_id,
                "active_address_label": STATE.active_address_label,
            }

    return {
        "ok": False,
        "error": f"No saved address matched '{address_query_or_id}'.",
        "available_addresses": addresses,
    }

