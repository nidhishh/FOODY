"""
Safe UPI Checkout Gate & Order Tracking tools for the FOODY Google ADK agent.
Enforces human-in-the-loop confirmation before initiating any order placement.
"""

from __future__ import annotations

from typing import Any, Dict

from ..config import STATE
from ..swiggy_client import SWIGGY
from .cart_tools import view_current_cart


def stage_checkout_summary() -> Dict[str, Any]:
    """
    SAFE READ-ONLY PREVIEW: Fetches the current cart bill and available payment options
    to present a final pre-checkout confirmation card to the user.
    Always call this first when the user asks to checkout or pay.
    """
    cart = view_current_cart()
    if cart.get("is_empty"):
        return cart

    pay_opts = SWIGGY.call_tool(
        "get_payment_options",
        {"addressId": STATE.active_address_id},
    )

    return {
        "ok": True,
        "status": "AWAITING_USER_CONFIRMATION",
        "cart_summary": cart,
        "payment_options_info": pay_opts.get("data") or pay_opts.get("text"),
        "next_step": (
            "Ask the user to explicitly confirm the item(s), delivery address, and 'To Pay' amount "
            "(or tap the 'Confirm & Get UPI Link' button in Telegram) before calling initiate_upi_checkout."
        ),
    }


def unlock_checkout_gate() -> Dict[str, Any]:
    """
    Unlocks the safety gate ONLY after the user has explicitly replied 'yes' / 'confirm'
    to a staged checkout summary (or tapped the Telegram confirmation button).
    """
    STATE.checkout_unlocked = True
    return {
        "ok": True,
        "checkout_unlocked": True,
        "message": "Checkout safety gate unlocked for the current cart.",
    }


def initiate_upi_checkout(note_to_restaurant: str = "") -> Dict[str, Any]:
    """
    Initiates a Swiggy Food UPI checkout and generates a UPI payment link / QR payload.
    SAFETY GATE: This tool will refuse to run unless `unlock_checkout_gate` has been called
    after explicit user approval of the staged bill.

    Args:
        note_to_restaurant: Optional cooking instruction (e.g. 'less spicy', 'no onions').
    """
    if not STATE.checkout_unlocked:
        return {
            "ok": False,
            "error": (
                "SAFETY_GATE_LOCKED: You must first show `stage_checkout_summary` to the user "
                "and get their explicit confirmation before unlocking and initiating UPI checkout."
            ),
        }

    # First ensure get_payment_options was called on this addressId as required by Swiggy MCP
    SWIGGY.call_tool(
        "get_payment_options",
        {"addressId": STATE.active_address_id},
    )

    args: Dict[str, Any] = {
        "addressId": STATE.active_address_id,
        "paymentMethod": "UPI",
        "generateUPIQR": True,
    }
    if note_to_restaurant.strip():
        args["noteToRestaurant"] = note_to_restaurant.strip()

    res = SWIGGY.call_tool("place_food_order", args)
    # Re-lock gate after invocation so it cannot be triggered twice accidentally
    STATE.checkout_unlocked = False

    return {
        "ok": res.get("ok", False),
        "text": res.get("text"),
        "data": res.get("data"),
        "error": res.get("error"),
    }


def check_payment_and_confirm_order(
    order_id: str = "",
    transaction_id: str = "",
) -> Dict[str, Any]:
    """
    Checks the status of an in-flight UPI payment and, once paid, confirms the order
    so it transitions to PLACED and appears in the user's Swiggy mobile app.

    Args:
        order_id: The Swiggy orderId returned by initiate_upi_checkout (if any).
        transaction_id: The payment transactionId returned by initiate_upi_checkout (if any).
    """
    status_args: Dict[str, Any] = {}
    if order_id:
        status_args["orderId"] = str(order_id)
    if transaction_id:
        status_args["transactionId"] = str(transaction_id)

    status_res = SWIGGY.call_tool("check_payment_status", status_args)

    confirm_res: Dict[str, Any] = {}
    if order_id:
        confirm_res = SWIGGY.call_tool("confirm_order", {"orderId": str(order_id)})

    return {
        "ok": True,
        "payment_status": status_res.get("data") or status_res.get("text") or status_res.get("error"),
        "confirm_order": confirm_res.get("data") or confirm_res.get("text") or confirm_res.get("error"),
    }


def track_active_or_recent_orders() -> Dict[str, Any]:
    """
    Fetches the user's recent/active Swiggy Food orders and live delivery tracking status.
    """
    orders_res = SWIGGY.call_tool(
        "get_food_orders",
        {"addressId": STATE.active_address_id},
    )
    return {
        "ok": orders_res.get("ok", False),
        "orders": orders_res.get("data") or orders_res.get("text") or orders_res.get("error"),
    }

