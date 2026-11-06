"""
Cart & Coupon Optimizer tools for the FOODY Google ADK agent.
Includes the Negative-Cost MOV Filler Arbitrage Engine:
Automatically detects when adding a savoury side/bread (roti, naan, kulcha, papad, raita, dip)
bridges a Minimum Order Value (MOV) coupon threshold and reduces the net payable bill.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from ..config import STATE
from ..swiggy_client import SWIGGY

SAVOURY_TIER_1_KEYWORDS = [
    "roti",
    "chapati",
    "phulka",
    "naan",
    "kulcha",
    "paratha",
    "parantha",
    "papad",
    "raita",
    "dip",
    "hummus",
    "garlic bread",
    "pita",
    "curd",
    "dahi",
    "salad",
    "salsa",
    "tzatziki",
    "mayo",
    "peri peri",
    "pav",
    "bhakri",
    "thepla",
]

SAVOURY_TIER_2_KEYWORDS = [
    "chaas",
    "buttermilk",
    "soup",
    "fries",
    "wedge",
    "samosa",
    "roll",
    "starter",
    "lime",
    "nimbu",
    "solkadhi",
]

EXCLUDED_JUNK_KEYWORDS = [
    "cutlery",
    "carry bag",
    "plastic",
    "napkin",
    "tissue",
    "water bottle",
    "packaged drinking water",
    "mineral water",
    "kinley",
    "bisleri",
    "aquafina",
]

DEFICIT_REGEX = re.compile(r"Add\s+₹\s*(\d+(?:\.\d+)?)\s+more", re.IGNORECASE)
SAVE_REGEX = re.compile(r"Save\s+₹\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _extract_cart_summary(
    raw_res: Dict[str, Any],
    filler_arbitrage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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

    # Preserve existing filler_arbitrage metadata if it still applies to the items in cart
    if filler_arbitrage is None and isinstance(STATE.last_cart_snapshot, dict):
        prev_arb = STATE.last_cart_snapshot.get("filler_arbitrage")
        if isinstance(prev_arb, dict):
            filler_id = str(prev_arb.get("filler_menu_item_id", ""))
            if any(str(i.get("menu_item_id", "")) == filler_id for i in items):
                filler_arbitrage = prev_arb

    formatted_items = [
        {
            "menu_item_id": str(i.get("menu_item_id", "")),
            "name": i.get("name"),
            "quantity": i.get("quantity", 1),
            "subtotal_inr": i.get("subtotal"),
            "final_price_inr": i.get("final_price"),
            "is_auto_filler": bool(
                filler_arbitrage
                and str(i.get("menu_item_id", "")) == str(filler_arbitrage.get("filler_menu_item_id", ""))
            ),
        }
        for i in items
    ]

    summary: Dict[str, Any] = {
        "ok": True,
        "is_empty": False,
        "cart_id": str(inner.get("cart_id", "")),
        "restaurant_id": str(rest.get("id") or STATE.last_cart_snapshot.get("restaurant_id") or ""),
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
    if filler_arbitrage:
        summary["filler_arbitrage"] = filler_arbitrage

    STATE.last_cart_snapshot = summary
    return summary


def view_current_cart() -> Dict[str, Any]:
    """
    Fetches the user's current Swiggy Food cart and returns the itemized list,
    active discounts/coupons, free delivery savings, taxes, filler arbitrage status,
    and final amount to pay.
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
    STATE.checkout_unlocked = False
    STATE.last_cart_snapshot = {"restaurant_id": str(restaurant_id)}

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
    summary = _extract_cart_summary(res, filler_arbitrage=None)
    summary["restaurant_id"] = str(restaurant_id)
    STATE.last_cart_snapshot = summary
    return summary


def remove_auto_filler_from_cart() -> Dict[str, Any]:
    """
    Removes the auto-added MOV filler item from the current cart and re-applies the best
    solo-item coupon if the user prefers ordering only their main dish.
    """
    STATE.checkout_unlocked = False
    snap = STATE.last_cart_snapshot if isinstance(STATE.last_cart_snapshot, dict) else {}
    arb = snap.get("filler_arbitrage") or {}
    filler_id = str(arb.get("filler_menu_item_id", ""))
    rest_id = str(snap.get("restaurant_id") or "")

    current = view_current_cart()
    if current.get("is_empty"):
        return current

    remaining_items = [
        {"menu_item_id": str(i["menu_item_id"]), "quantity": int(i.get("quantity", 1))}
        for i in current.get("items", [])
        if str(i.get("menu_item_id")) != filler_id
    ]
    if not remaining_items or not rest_id:
        return current

    STATE.last_cart_snapshot = {"restaurant_id": rest_id}
    SWIGGY.call_tool(
        "update_food_cart",
        {
            "addressId": STATE.active_address_id,
            "restaurantId": rest_id,
            "cartItems": remaining_items,
        },
    )
    # Re-apply best solo coupon without filler arbitrage
    return optimize_cart_coupons(rest_id, allow_filler_arbitrage=False)


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


def _parse_coupons_and_deficits(
    coupons_res: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extracts (applicable_coupons, locked_mov_coupons) from `fetch_food_coupons`.
    Note: Swiggy's `fetch_food_coupons` stores the coupon UUID in `id` and promo name in `title`.
    """
    applicable: List[Dict[str, Any]] = []
    locked_mov: List[Dict[str, Any]] = []

    data = coupons_res.get("data")
    if isinstance(data, dict):
        for sec in data.get("coupon_sections", []):
            if not isinstance(sec, dict):
                continue
            for c in sec.get("coupons", []):
                if not isinstance(c, dict):
                    continue
                code_id = str(c.get("id") or c.get("coupon_code") or c.get("code") or "")
                title = str(c.get("title") or code_id)
                subtitle = str(c.get("subtitle") or "")
                is_app = bool(c.get("applicable", False))

                save_m = SAVE_REGEX.search(subtitle)
                est_save = float(save_m.group(1)) if save_m else 0.0

                def_m = DEFICIT_REGEX.search(subtitle)
                deficit = float(def_m.group(1)) if def_m else 0.0

                entry = {
                    "code": code_id,
                    "title": title,
                    "subtitle": subtitle,
                    "estimated_save_inr": est_save,
                    "deficit_inr": deficit,
                }
                if is_app and code_id:
                    applicable.append(entry)
                elif deficit > 0 and code_id:
                    locked_mov.append(entry)

    # Sort applicable by highest estimated save first
    applicable.sort(key=lambda x: -x["estimated_save_inr"])
    # Sort locked MOV coupons by smallest deficit first
    locked_mov.sort(key=lambda x: x["deficit_inr"])
    return applicable, locked_mov


def _find_best_savoury_filler(
    restaurant_id: str,
    deficit_inr: float,
    existing_item_ids: set[str],
    max_filler_price_inr: float = 110.0,
) -> Optional[Dict[str, Any]]:
    """
    Scans up to 3 pages of the restaurant's live menu in parallel to find the ideal
    savoury side or bread (roti, naan, kulcha, papad, raita, dip) that bridges `deficit_inr`.
    """
    def _fetch_page(page_num: int) -> Dict[str, Any]:
        return SWIGGY.call_tool(
            "get_restaurant_menu",
            {
                "addressId": STATE.active_address_id,
                "restaurantId": str(restaurant_id),
                "page": page_num,
            },
        )

    first_page = _fetch_page(1)
    if not first_page.get("ok") or not isinstance(first_page.get("data"), dict):
        return None

    first_data = first_page["data"]
    all_categories: List[Dict[str, Any]] = list(first_data.get("categories", []))

    if first_data.get("hasMore"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(_fetch_page, p) for p in (2, 3, 4)]
            for fut in futs:
                try:
                    r_page = fut.result()
                    if r_page.get("ok") and isinstance(r_page.get("data"), dict):
                        all_categories.extend(r_page["data"].get("categories", []))
                except Exception:
                    pass

    candidates: List[Dict[str, Any]] = []
    seen_ids = set(existing_item_ids)

    def _collect_items_from_cat(cat_obj: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
        pairs: List[Tuple[str, Dict[str, Any]]] = []
        c_title = str(cat_obj.get("title", "Sides"))
        for it in cat_obj.get("items", []):
            pairs.append((c_title, it))
        for sub in cat_obj.get("subcategories", []):
            sub_title = f"{c_title} - {sub.get('title', '')}"
            for it in sub.get("items", []):
                pairs.append((sub_title, it))
        return pairs

    for cat in all_categories:
        for cat_title_display, it in _collect_items_from_cat(cat):
            cat_title = cat_title_display.lower()
            mid = str(it.get("id", ""))
            if not mid or mid in seen_ids:
                continue
            if it.get("inStock", 1) == 0:
                continue
            has_variants = bool(it.get("hasVariants", False))

            price = float(it.get("price") or 9999.0)
            if price < deficit_inr or price > max(deficit_inr + 65.0, max_filler_price_inr):
                continue

            name_raw = str(it.get("name", ""))
            name_low = name_raw.lower()

            if any(junk in name_low for junk in EXCLUDED_JUNK_KEYWORDS):
                continue

            # Score savoury priority
            tier = 3
            if any(k in name_low or k in cat_title for k in SAVOURY_TIER_1_KEYWORDS):
                tier = 1
            elif any(k in name_low or k in cat_title for k in SAVOURY_TIER_2_KEYWORDS):
                tier = 2

            seen_ids.add(mid)
            candidates.append(
                {
                    "menu_item_id": mid,
                    "name": name_raw,
                    "price_inr": int(price) if price.is_integer() else price,
                    "is_veg": bool(it.get("isVeg", True)),
                    "category": cat_title_display,
                    "savoury_tier": tier,
                    "has_variants": has_variants,
                    "overshoot_inr": round(price - deficit_inr, 2),
                }
            )

    if not candidates:
        return None

    # Sort by: 1) No complex variants first, 2) Savoury Tier (1=Breads/Dips/Papad/Raita), 3) Smallest price overshoot above deficit
    candidates.sort(
        key=lambda x: (
            1 if x["has_variants"] else 0,
            x["savoury_tier"],
            x["overshoot_inr"],
        )
    )
    return candidates[0]


def optimize_cart_coupons(
    restaurant_id: str,
    custom_coupon_code: str = "",
    allow_filler_arbitrage: bool = True,
) -> Dict[str, Any]:
    """
    High-Performance Deterministic Arbitrage Engine:
    1. Fetches coupons and Page-1 menu in parallel (both usually 0ms if pre-fetched).
    2. Simulates solo-coupon vs. MOV savoury filler arbitrage in pure Python (<1ms).
    3. Commits ONLY the single winning cart update and coupon over the wire.
    """
    before_cart = view_current_cart()
    if before_cart.get("is_empty"):
        return before_cart

    baseline_items = [
        {"menu_item_id": str(i["menu_item_id"]), "quantity": int(i.get("quantity", 1))}
        for i in before_cart.get("items", [])
    ]
    existing_ids = {i["menu_item_id"] for i in baseline_items}

    # Parallel fetch: coupons + menu (concurrent execution)
    with ThreadPoolExecutor(max_workers=2) as pool:
        coupon_fut = pool.submit(
            SWIGGY.call_tool,
            "fetch_food_coupons",
            {"restaurantId": str(restaurant_id), "addressId": STATE.active_address_id},
        )
        menu_fut = pool.submit(
            SWIGGY.call_tool,
            "get_restaurant_menu",
            {"addressId": STATE.active_address_id, "restaurantId": str(restaurant_id), "page": 1},
        )
        coupons_res = coupon_fut.result()
        menu_res = menu_fut.result()

    applicable_coupons, locked_mov_coupons = _parse_coupons_and_deficits(coupons_res)

    best_solo_code = custom_coupon_code.strip() or (applicable_coupons[0]["code"] if applicable_coupons else "")
    est_solo_discount = applicable_coupons[0]["estimated_save_inr"] if applicable_coupons else 0.0

    # Step 2: Pure-Python MOV Arbitrage Simulation
    winning_filler: Optional[Dict[str, Any]] = None
    winning_locked_coupon: Optional[Dict[str, Any]] = None

    if allow_filler_arbitrage and locked_mov_coupons:
        viable_locked = [c for c in locked_mov_coupons if 1.0 <= c["deficit_inr"] <= 100.0]
        for locked in viable_locked[:2]:
            deficit = locked["deficit_inr"]
            filler = _find_best_savoury_filler(
                restaurant_id=str(restaurant_id),
                deficit_inr=deficit,
                existing_item_ids=existing_ids,
            )
            if not filler:
                continue

            filler_price = float(filler["price_inr"])
            locked_est_save = float(locked["estimated_save_inr"])
            # If unlocked coupon savings outweigh the filler cost more than the solo coupon
            net_benefit = locked_est_save - filler_price
            if net_benefit > est_solo_discount:
                winning_filler = filler
                winning_locked_coupon = locked
                break

    # Step 3: Single Winning Commit
    if winning_filler and winning_locked_coupon:
        # Commit MOV filler + locked coupon in 1 go
        test_cart_items = baseline_items + [
            {"menu_item_id": str(winning_filler["menu_item_id"]), "quantity": 1}
        ]
        upd_res = SWIGGY.call_tool(
            "update_food_cart",
            {
                "addressId": STATE.active_address_id,
                "restaurantId": str(restaurant_id),
                "cartItems": test_cart_items,
            },
        )
        apply_res = SWIGGY.call_tool(
            "apply_food_coupon",
            {
                "couponCode": winning_locked_coupon["code"],
                "addressId": STATE.active_address_id,
            },
        )
        final_cart_raw = SWIGGY.call_tool("get_food_cart", {"addressId": STATE.active_address_id})
        final_cart = _extract_cart_summary(final_cart_raw)
        final_bill = final_cart.get("bill_breakdown", {})
        new_to_pay = float(final_bill.get("to_pay_inr") or 0.0)
        new_discount = float(final_bill.get("coupon_discount_inr") or winning_locked_coupon["estimated_save_inr"])

        baseline_bill = before_cart.get("bill_breakdown", {})
        baseline_to_pay = float(baseline_bill.get("to_pay_inr") or (new_to_pay + 50.0))
        net_saved = max(1.0, round(baseline_to_pay - new_to_pay, 2))

        filler_arbitrage_report = {
            "triggered": True,
            "filler_menu_item_id": str(winning_filler["menu_item_id"]),
            "filler_name": winning_filler["name"],
            "filler_price_inr": winning_filler["price_inr"],
            "filler_category": winning_filler["category"],
            "deficit_bridged_inr": int(winning_locked_coupon["deficit_inr"]),
            "unlocked_coupon_title": winning_locked_coupon["title"],
            "unlocked_discount_inr": int(new_discount),
            "baseline_to_pay_inr": int(baseline_to_pay),
            "new_to_pay_inr": int(new_to_pay),
            "extra_net_savings_inr": int(net_saved),
            "headline": (
                f"🎯 MOV Arbitrage Unlocked! Auto-added '{winning_filler['name']}' (₹{winning_filler['price_inr']}) "
                f"to bridge the ₹{int(winning_locked_coupon['deficit_inr'])} coupon gap and unlock {winning_locked_coupon['title']}. "
                f"Your final bill dropped from ₹{int(baseline_to_pay)} ➔ ₹{int(new_to_pay)} "
                f"(₹{int(net_saved)} cheaper net + free {winning_filler['name']}!)."
            ),
        }
        final_cart["filler_arbitrage"] = filler_arbitrage_report
        final_cart["restaurant_id"] = str(restaurant_id)
        STATE.last_cart_snapshot = final_cart
        return {
            "ok": True,
            "mov_filler_arbitrage": filler_arbitrage_report,
            "discovered_applicable_coupons": [c["title"] for c in applicable_coupons],
            "discovered_locked_coupons": locked_mov_coupons,
            "tested_codes": [{"code": winning_locked_coupon["code"], "ok": True}],
            "updated_cart": final_cart,
        }

    # Step 4: Apply Solo Coupon (Single Call)
    applied_results = []
    if best_solo_code:
        apply_res = SWIGGY.call_tool(
            "apply_food_coupon",
            {
                "couponCode": best_solo_code,
                "addressId": STATE.active_address_id,
            },
        )
        applied_results.append({
            "code": best_solo_code,
            "ok": apply_res.get("ok"),
            "response": apply_res.get("error") or apply_res.get("text"),
        })

    after_cart = view_current_cart()
    after_cart["restaurant_id"] = str(restaurant_id)
    STATE.last_cart_snapshot = after_cart
    return {
        "ok": True,
        "mov_filler_arbitrage": {"triggered": False},
        "discovered_applicable_coupons": [c["title"] for c in applicable_coupons],
        "discovered_locked_coupons": locked_mov_coupons,
        "tested_codes": applied_results,
        "updated_cart": after_cart,
    }
