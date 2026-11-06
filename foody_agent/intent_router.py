"""
Lightweight Deterministic Intent Router for FOODY.
Executes in < 1ms to intercept high-confidence user requests
(gourmet deals, value lunches, dish under ₹X, dineout, cart, checkout, address)
and bypass Gemini LLM calls entirely for ~80% of routine traffic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .config import STATE
from .telemetry import TELEMETRY
from .tools import (
    clear_food_cart,
    compare_order_vs_dineout,
    find_cheapest_lunch_deals,
    find_gourmet_steals,
    get_active_delivery_address,
    initiate_upi_checkout,
    list_saved_addresses,
    remove_auto_filler_from_cart,
    search_dineout_restaurants,
    search_dishes_across_restaurants,
    search_smart_restaurants,
    stage_checkout_summary,
    switch_delivery_address,
    unlock_checkout_gate,
    view_current_cart,
)


class RoutedResponse:
    __slots__ = ("text", "keyboard", "matched", "intent", "cached_deals")

    def __init__(
        self,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        matched: bool = True,
        intent: str = "",
        cached_deals: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.text = text
        self.keyboard = keyboard
        self.matched = matched
        self.intent = intent
        self.cached_deals = cached_deals or []


# Precompiled regex patterns for < 1ms matching
RE_GOURMET = re.compile(
    r"\b(gourmet|steals?|boutique|artisanal|toscano|kitchen garden)\b",
    re.IGNORECASE,
)
RE_CHEAP_LUNCH = re.compile(
    r"\b(cheap(?:est)?|budget|value|thali|combo|lunchbox)\b",
    re.IGNORECASE,
)
RE_DISH_UNDER = re.compile(
    r"\b([a-z\s]+?)\s+(?:under|below|less\s+than|<=)\s*(?:₹|rs\.?)?\s*(\d+)\b",
    re.IGNORECASE,
)
RE_CART_VIEW = re.compile(
    r"^(?:show|view|check|see|my)?\s*(?:cart|bill|items)\b",
    re.IGNORECASE,
)
RE_CART_CLEAR = re.compile(
    r"^(?:clear|empty|flush|delete)\s*(?:my\s*)?cart\b",
    re.IGNORECASE,
)
RE_REMOVE_FILLER = re.compile(
    r"\b(remove\s+filler|delete\s+filler|no\s+filler|remove\s+auto)\b",
    re.IGNORECASE,
)
RE_CHECKOUT = re.compile(
    r"^(?:checkout|stage\s+checkout|proceed\s+to\s+pay)\b",
    re.IGNORECASE,
)
RE_CONFIRM_PAY = re.compile(
    r"^(?:confirm|confirm\s+order|confirm\s+and\s+pay|pay|pay\s+now|pay\s+upi|yes\s+pay)\b",
    re.IGNORECASE,
)
RE_ADDRESS_VIEW = re.compile(
    r"\b(where\s+(?:is|are\s+you)\s+delivering|active\s+address|current\s+address|my\s+address)\b",
    re.IGNORECASE,
)
RE_ADDRESS_SWITCH = re.compile(
    r"^(?:switch|change|deliver\s+to)\s+(?:address\s+to\s+)?([a-z0-9\s]+)$",
    re.IGNORECASE,
)
RE_DINEOUT_SEARCH = re.compile(
    r"\b(dineout|dine\s+out|table\s+booking|reserve\s+table|table|restaurant(?:s)?\s+in\s+([a-z]+))\b",
    re.IGNORECASE,
)
RE_ORDER_VS_DINEOUT = re.compile(
    r"\b(order\s+vs\s+dine\s*out|order\s+or\s+dine|delivery\s+vs\s+dine|should\s+i\s+order\s+or\s+eat\s+out)\b",
    re.IGNORECASE,
)
RE_PERF = re.compile(
    r"^(?:/perf|perf|telemetry|latency|stats|metrics)\b",
    re.IGNORECASE,
)


def _format_cart_response(cart: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    if not cart.get("ok"):
        return f"⚠️ {cart.get('error', 'Error fetching cart')}", InlineKeyboardMarkup([])
    if cart.get("is_empty"):
        return f"🛒 *Cart is empty.* (📍 `{STATE.active_address_label}`)", InlineKeyboardMarkup([])

    bill = cart.get("bill_breakdown", {})
    rest_name = cart.get("restaurant_name") or "Outlet"
    lines = [f"🛒 *Cart:* {rest_name}"]
    for it in cart.get("items", []):
        filler_tag = " _(🎯 Auto-Filler)_" if it.get("is_auto_filler") else ""
        lines.append(f"• {it.get('name')} × {it.get('quantity')} — ₹{it.get('final_price_inr')}{filler_tag}")

    arb = cart.get("filler_arbitrage")
    if isinstance(arb, dict) and arb.get("triggered"):
        lines.append(f"🎯 *Arbitrage:* Added {arb.get('filler_name')} to save ₹{arb.get('extra_net_savings_inr')} net")

    disc = bill.get("coupon_discount_inr") or 0
    del_str = "Del: FREE" if (bill.get("free_delivery_applied") or bill.get("delivery_charge_inr") == 0) else f"Del: ₹{bill.get('delivery_charge_inr')}"
    save_parts = []
    if disc:
        save_parts.append(f"Saved ₹{disc}")
    save_parts.append(del_str)
    savings_text = f" ({' | '.join(save_parts)})" if save_parts else ""
    lines.append(f"💰 *Net To Pay: ₹{bill.get('to_pay_inr')}*{savings_text}")

    rows = [
        [
            InlineKeyboardButton("💳 Stage UPI Checkout", callback_data="cart:stage_checkout"),
            InlineKeyboardButton("🗑️ Clear Cart", callback_data="cart:clear"),
        ]
    ]
    if isinstance(arb, dict) and arb.get("triggered"):
        rows.append([
            InlineKeyboardButton(
                f"➖ Remove Auto-Filler ({arb.get('filler_name', 'Side')[:18]})",
                callback_data="cart:remove_filler",
            )
        ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def try_route_intent(user_text: str) -> RoutedResponse:
    """
    Attempts to route user text via deterministic rules in < 1ms.
    Returns RoutedResponse with matched=True if handled, or matched=False to fall back to Gemini.
    """
    clean = user_text.strip()
    is_veg = bool(re.search(r"\b(veg|vegetarian|pure\s+veg)\b", clean, re.IGNORECASE))

    # 0. Telemetry / Perf Stats
    if RE_PERF.match(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        return RoutedResponse(
            text=TELEMETRY.format_perf_card(),
            matched=True,
            intent="perf_stats",
        )

    # 1. Cart Clear
    if RE_CART_CLEAR.match(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        clear_food_cart()
        return RoutedResponse(
            text="🗑️ *Your Swiggy cart has been cleared.*",
            matched=True,
            intent="clear_cart",
        )

    # 2. Remove Auto-Filler
    if RE_REMOVE_FILLER.search(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = remove_auto_filler_from_cart()
        cart = res.get("updated_cart", res) if isinstance(res, dict) else view_current_cart()
        text, kb = _format_cart_response(cart)
        return RoutedResponse(text=text, keyboard=kb, matched=True, intent="remove_filler")

    # 3. Cart View
    if RE_CART_VIEW.match(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        cart = view_current_cart()
        text, kb = _format_cart_response(cart)
        return RoutedResponse(text=text, keyboard=kb, matched=True, intent="view_cart")

    # 4. Confirm Payment / Unlock Safety Gate
    if RE_CONFIRM_PAY.match(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        unlock_checkout_gate()
        res = initiate_upi_checkout()
        text_out = res.get("text") or str(res.get("data") or res.get("error"))
        return RoutedResponse(
            text=f"💳 *UPI Checkout Initiated:*\n\n{text_out[:3500]}",
            matched=True,
            intent="confirm_pay",
        )

    # 5. Stage Checkout
    if RE_CHECKOUT.match(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        staged = stage_checkout_summary()
        cart = staged.get("cart_summary", staged)
        if cart.get("is_empty"):
            return RoutedResponse(
                text="🛒 Your cart is empty! Add a dish first before checking out.",
                matched=True,
                intent="stage_checkout_empty",
            )
        to_pay = cart.get("bill_breakdown", {}).get("to_pay_inr", "?")
        text, _ = _format_cart_response(cart)
        confirm_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"✅ Confirm & Pay ₹{to_pay} via UPI", callback_data="checkout:confirm_upi")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:cart")],
            ]
        )
        return RoutedResponse(
            text=text + "\n\n🔒 *SAFETY GATE:* Tap below to generate your Swiggy UPI payment request:",
            keyboard=confirm_kb,
            matched=True,
            intent="stage_checkout",
        )

    # 6. Address View
    if RE_ADDRESS_VIEW.search(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        listing = list_saved_addresses()
        addrs = listing.get("addresses", [])[:8]
        buttons = []
        for a in addrs:
            active_mark = "✅ " if a.get("is_active") else ""
            label = f"{active_mark}{a.get('tag')}: {str(a.get('addressLine', ''))[:38]}..."
            buttons.append([InlineKeyboardButton(label, callback_data=f"setaddr:{a.get('index')}")])
        return RoutedResponse(
            text=f"📍 *Active Delivery Address:* `{STATE.active_address_label}`\n\nTap to switch:",
            keyboard=InlineKeyboardMarkup(buttons),
            matched=True,
            intent="view_address",
        )

    # 7. Address Switch
    m_addr = RE_ADDRESS_SWITCH.match(clean)
    if m_addr:
        target = m_addr.group(1).strip()
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = switch_delivery_address(target)
        if res.get("ok"):
            return RoutedResponse(
                text=f"✅ *Active delivery address updated to:*\n`{STATE.active_address_label}`",
                matched=True,
                intent="switch_address",
            )
        else:
            return RoutedResponse(
                text=f"⚠️ {res.get('error', 'Could not find that address')}",
                matched=True,
                intent="switch_address_fail",
            )

    # 8. Order vs. Dine Out
    if RE_ORDER_VS_DINEOUT.search(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = compare_order_vs_dineout("food", "")
        deliv = res.get("delivery_options", [])
        dine = res.get("dineout_options", [])
        top_del = deliv[0]["name"] if deliv else "Swiggy Food"
        top_din = dine[0]["name"] if dine else "Nearby Dineout spot"
        est_del_price = deliv[0].get("est_solo_delivery_inr", 250) if deliv else 250
        est_din_offer = dine[0].get("active_offer", "Dineout Pay available") if dine else "Dineout Pay available"
        text = (
            f"⚡ *Order vs. Dine Out Comparison:*\n"
            f"• *Delivery:* {top_del} (~₹{est_del_price} landed, 35 mins)\n"
            f"• *Dine Out:* {top_din} ({est_din_offer})\n"
            f"💡 *Verdict:* {res.get('verdict', 'Compare delivery vs dining out based on your mood!')}"
        )
        return RoutedResponse(text=text, matched=True, intent="order_vs_dineout")

    # 9. Gourmet Steals
    if RE_GOURMET.search(clean):
        craving = "gourmet"
        for word in ("pasta", "pizza", "sandwich", "salad", "burger", "bowl", "sushi"):
            if word in clean.lower():
                craving = word
                break

        TELEMETRY.record_router_hit(is_fast_path=True)
        res = find_gourmet_steals(craving=craving, max_landed_budget_inr=260, veg_only=is_veg)
        steals = res.get("gourmet_steals", [])
        if not steals:
            return RoutedResponse(
                text="No gourmet steals under ₹260 found right now. Tap /deals for value lunches!",
                matched=True,
                intent="gourmet_empty",
            )

        lines = [f"💎 *Gourmet Steals (≤ ₹260)*\n"]
        buttons: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for idx, s in enumerate(steals[:4], start=1):
            veg_icon = "🟢" if s.get("is_veg") else "🔴"
            lines.append(
                f"{idx}. {veg_icon} *{s['dish_name']}* — *₹{s['estimated_landed_inr']}* ~₹{s['original_price_inr']}~ · {s['restaurant_name']} (⭐{s['restaurant_rating']})"
            )
            row.append(
                InlineKeyboardButton(
                    f"➕ #{idx} (₹{s['estimated_landed_inr']})",
                    callback_data=f"adddeal:{idx - 1}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])
        return RoutedResponse(
            text="\n".join(lines),
            keyboard=InlineKeyboardMarkup(buttons),
            matched=True,
            intent="gourmet_steals",
            cached_deals=steals,
        )

    # 10. Specific Dish Under ₹X (e.g. "biryani under 250", "pasta below 200")
    m_under = RE_DISH_UNDER.search(clean)
    if m_under:
        dish_q = m_under.group(1).strip()
        budget = int(m_under.group(2).strip())
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = search_dishes_across_restaurants(query=dish_q, max_price_inr=budget, veg_only=is_veg)
        dishes = res.get("cheapest_dishes", [])
        if not dishes:
            return RoutedResponse(
                text=f"No {dish_q} found under ₹{budget} right now.",
                matched=True,
                intent="dish_under_empty",
            )

        lines = [f"🔥 *{dish_q.title()} (≤ ₹{budget})*\n"]
        buttons = []
        row = []
        for idx, d in enumerate(dishes[:4], start=1):
            veg_icon = "🟢" if d.get("is_veg") else "🔴"
            lines.append(
                f"{idx}. {veg_icon} *{d['dish_name']}* — *₹{d['price_inr']}* · {d['restaurant_name']} (⭐{d['restaurant_rating']})"
            )
            row.append(
                InlineKeyboardButton(
                    f"➕ #{idx} (₹{d['price_inr']})",
                    callback_data=f"adddeal:{idx - 1}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])
        return RoutedResponse(
            text="\n".join(lines),
            keyboard=InlineKeyboardMarkup(buttons),
            matched=True,
            intent="dish_under",
            cached_deals=dishes,
        )

    # 11. Cheap Lunch Deals
    if RE_CHEAP_LUNCH.search(clean):
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = find_cheapest_lunch_deals(craving_or_category="lunch", max_budget_inr=280, veg_only=is_veg)
        meals = res.get("top_cheapest_meals", [])
        if not meals:
            return RoutedResponse(
                text="No meals found under ₹280 right now.",
                matched=True,
                intent="deals_empty",
            )
        lines = [f"🔥 *Value Lunches (≤ ₹280)*\n"]
        buttons = []
        row = []
        for idx, m in enumerate(meals[:4], start=1):
            veg_icon = "🟢" if m.get("is_veg") else "🔴"
            lines.append(
                f"{idx}. {veg_icon} *{m['dish_name']}* — *₹{m['price_inr']}* · {m['restaurant_name']} (⭐{m['restaurant_rating']})"
            )
            row.append(
                InlineKeyboardButton(
                    f"➕ #{idx} (₹{m['price_inr']})",
                    callback_data=f"adddeal:{idx - 1}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])
        return RoutedResponse(
            text="\n".join(lines),
            keyboard=InlineKeyboardMarkup(buttons),
            matched=True,
            intent="cheap_deals",
            cached_deals=meals,
        )

    # 12. Dineout Discovery
    m_din = RE_DINEOUT_SEARCH.search(clean)
    if m_din:
        locality = m_din.group(2) if m_din.groups() and len(m_din.groups()) >= 2 and m_din.group(2) else ""
        TELEMETRY.record_router_hit(is_fast_path=True)
        res = search_dineout_restaurants("restaurant", locality, 4.0, 4000)
        rests = res.get("restaurants", [])
        if not rests:
            return RoutedResponse(
                text="No Dineout restaurants found in this area.",
                matched=True,
                intent="dineout_empty",
            )
        lines = [f"🍽️ *Dineout Deals* — {res.get('resolved_area', STATE.active_address_label)}\n"]
        buttons = []
        for idx, r in enumerate(rests[:4], start=1):
            offer = f" ({r['active_offer']})" if r.get("active_offer") else ""
            lines.append(f"{idx}. *{r['name']}* — ⭐{r['rating']} · {r['cost_for_two']}{offer} · {r['locality']}")
            buttons.append([
                InlineKeyboardButton(f"📅 Slots: {r['name'][:14]}", callback_data=f"doslot:{r['restaurant_id']}"),
                InlineKeyboardButton("ℹ️ Offers", callback_data=f"dodet:{r['restaurant_id']}"),
            ])
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu:back")])
        return RoutedResponse(
            text="\n".join(lines),
            keyboard=InlineKeyboardMarkup(buttons),
            matched=True,
            intent="dineout_search",
        )

    # No fast-path match -> fallback to single-pass Gemini coordinator
    TELEMETRY.record_router_hit(is_fast_path=False)
    return RoutedResponse(text="", matched=False, intent="llm_fallback")
