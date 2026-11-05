"""
Cart & Coupon Optimizer tools for the FOODY Google ADK agent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..config import STATE
from ..swiggy_client import SWIGGY


def _extract_cart_summary(raw_res: Dict[str, Any]) -> Dict[str, Any]:
    if not raw_res.get("ok"):
        return raw_res

    structured = raw_res.get("data", {})
    inner = structured.get("data", {}) if isinstance(structured, dict) else {}
    if not inner or not inner.get("items"):
        return {
            "ok": True,
            "is_empty": True,
            "message": "Your Swiggy Food cart is currently empty.",
            "active_address": STATE.active_address_label,
        }

    items = inner.get("items", [])
    pricing = inner.get("pricing", {})
    offers = inner.get("offers", {})
    rest = inner.get("restaurant", {})

    formatted_items = [
        {
            "menu_item_id": str(i.get("menu_item_id", "")),
            "name": i.get("name"),
            "quantity": i.get("quantity", 1),
            "subtotal_inr": i.get("subtotal"),
            "final_price_inr": i.get("final_price"),
        }
        for i in items
    ]

    summary = {
        "ok": True,
        "is_empty": False,
        "cart_id": str(inner.get("cart_id", "")),
        "restaurant_name": rest.get("name"),
        "delivery_subtitle": rest.get("deliverySubtitle"),
        "active_address": STATE.active_address_label,
        "items": formatted_items,
        "bill_breakdown": {
            "item_total_inr": pricing.get("item_total"),
            "delivery_charge_inr": pricing.get("delivery_charge"),
            "delivery_fee_saved_inr": pricing.get("delivery_charge_strikeoff", 0),
            "taxes_and_charges_inr": pricing.get("taxes_and_charges"),
            "coupon_applied": offers.get("coupon_applied"),
            "coupon_discount_inr": offers.get("coupon_discount", 0),
            "free_delivery_applied": offers.get("free_delivery_applied", False),
            "to_pay_inr": pricing.get("to_pay"),
        },
    }
    STATE.last_cart_snapshot = summary
    return summary


def view_current_cart() -> Dict[str, Any]:
    """
    Fetches the user's current Swiggy Food cart and returns the itemized list,
    active discounts/coupons, free delivery savings, taxes, and final amount to pay.
    """
    res = SWIGGY.call_tool(
        "get_food_cart",
        {"addressId": STATE.active_address_id},
    )
    return _extract_cart_summary(res)


def add_dish_to_cart(
    restaurant_id: str,
    menu_item_id: str,
    quantity: int = 1,
    restaurant_name: str = "",
) -> Dict[str, Any]:
    """
    Adds or updates a menu item in the user's Swiggy Food cart for the active delivery address.
    Note: Swiggy allows one restaurant per cart; adding from a new restaurant replaces previous items.

    Args:
        restaurant_id: Swiggy restaurant ID from search results.
        menu_item_id: Swiggy menu_item_id from dish/menu search results.
        quantity: Quantity of the item to set in cart (default 1).
        restaurant_name: Optional human-readable restaurant name.
    """
    # Lock checkout gate whenever cart changes so user must confirm the new total
    STATE.checkout_unlocked = False

    args: Dict[str, Any] = {
        "addressId": STATE.active_address_id,
        "restaurantId": str(restaurant_id),
        "cartItems": [
            {
                "menu_item_id": str(menu_item_id),
                "quantity": int(quantity),
            }
        ],
    }
    if restaurant_name:
        args["restaurantName"] = restaurant_name

    res = SWIGGY.call_tool("update_food_cart", args)
    return _extract_cart_summary(res)


def clear_food_cart() -> Dict[str, Any]:
    """
    Empties/flushes all items from the current Swiggy Food cart.
    """
    STATE.checkout_unlocked = False
    STATE.last_cart_snapshot = {}
    res = SWIGGY.call_tool(
        "flush_food_cart",
        {"addressId": STATE.active_address_id},
    )
    return {
        "ok": res.get("ok", True),
        "message": "Swiggy Food cart has been cleared.",
        "details": res.get("text") or res.get("data"),
    }


def optimize_cart_coupons(
    restaurant_id: str,
    custom_coupon_code: str = "",
) -> Dict[str, Any]:
    """
    Fetches available coupons for the given restaurant on the current cart, tests
    applicable coupon codes (or a specific custom_coupon_code if provided), and returns
    the updated cart bill with maximum savings applied.

    Args:
        restaurant_id: The Swiggy restaurant ID currently in the cart.
        custom_coupon_code: Optional specific promo code to try applying (e.g. 'SWIGGYIT', 'TRYNEW').
    """
    before_cart = view_current_cart()
    if before_cart.get("is_empty"):
        return before_cart

    coupons_res = SWIGGY.call_tool(
        "fetch_food_coupons",
        {
            "restaurantId": str(restaurant_id),
            "addressId": STATE.active_address_id,
        },
    )

    discovered_codes: List[str] = []
    if coupons_res.get("ok") and isinstance(coupons_res.get("data"), dict):
        sections = coupons_res["data"].get("coupon_sections", [])
        for sec in sections:
            for c in sec.get("coupons", []) if isinstance(sec, dict) else []:
                code = c.get("coupon_code") or c.get("code")
                if code:
                    discovered_codes.append(str(code))

    codes_to_try: List[str] = []
    if custom_coupon_code.strip():
        codes_to_try.append(custom_coupon_code.strip().upper())
    for c in discovered_codes:
        if c not in codes_to_try:
            codes_to_try.append(c)

    applied_results: List[Dict[str, Any]] = []
    for code in codes_to_try[:5]:
        apply_res = SWIGGY.call_tool(
            "apply_food_coupon",
            {
                "couponCode": code,
                "addressId": STATE.active_address_id,
            },
        )
        applied_results.append(
            {
                "code": code,
                "ok": apply_res.get("ok"),
                "response": apply_res.get("error") or apply_res.get("text"),
            }
        )

    after_cart = view_current_cart()
    return {
        "ok": True,
        "discovered_coupons": discovered_codes,
        "tested_codes": applied_results,
        "updated_cart": after_cart,
    }

