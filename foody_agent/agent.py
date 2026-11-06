"""
Google ADK (Agent Development Kit) Multi-Agent Definition for FOODY.
Compatible with `adk web`, `adk run foody_agent`, and `run_telegram.py`.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import FOODY_MODEL
from .tools import (
    add_dish_to_cart,
    book_dineout_table,
    check_dineout_slots,
    check_payment_and_confirm_order,
    clear_food_cart,
    compare_order_vs_dineout,
    find_cheapest_lunch_deals,
    find_gourmet_steals,
    get_active_delivery_address,
    get_dineout_restaurant_details,
    get_full_restaurant_menu,
    initiate_upi_checkout,
    list_saved_addresses,
    optimize_cart_coupons,
    remove_auto_filler_from_cart,
    search_dineout_restaurants,
    search_dishes_across_restaurants,
    search_smart_restaurants,
    stage_checkout_summary,
    switch_delivery_address,
    track_active_or_recent_orders,
    unlock_checkout_gate,
    view_current_cart,
)

BREVITY_INSTRUCTION = """
CRITICAL COMMUNICATION RULES (STRICT BREVITY):
1. ZERO FLUFF: Never output conversational preambles ("Sure!", "Here are some options:"), filler, or sign-offs ("Let me know..."). Jump straight to the data.
2. HARD LIMIT: Output at most 2 to 3 bullet points per message. Never write paragraphs.
3. FORMAT:
   • Food delivery: `• 🟢/🔴 Dish — ₹Price (~₹Orig~) · Restaurant (⭐Rating, ETA)`
   • Dineout: `• Restaurant (Locality) — ⭐Rating · ₹Cost for 2 (🏷️ Offer)`
   • Cart/Checkout: `🛒 Restaurant | Items: [X, Y] | Net To Pay: ₹XXX. Confirm order?`
"""

deal_hunter_agent = LlmAgent(
    name="deal_hunter_agent",
    model=FOODY_MODEL,
    description=(
        "Specialized in finding the best meals on Swiggy: from Gourmet Arbitrage Steals "
        "(premium restaurants like Toscano, Kitchen Garden, Pizza By The Bay dropped to ₹200-₹260 with coupons) "
        "to smart ranked restaurants (de-prioritizing Ads) and cheapest value thalis/biryanis."
    ),
    instruction="""You are FOODY's Smart Deal Hunter sub-agent.
Your mission is to find high-value food on Swiggy. Be ultra-concise.

Guidelines:
1. When asked for gourmet/steals, call `find_gourmet_steals`. Output top 2-3 items max.
2. When asked for cuisine/restaurant recommendations, call `search_smart_restaurants`. Output top 2-3 restaurants max.
3. When strictly asked for the cheapest meals, call `find_cheapest_lunch_deals`. Output top 2-3 items max.
4. Always provide dish names, prices (₹), and IDs directly without conversational filler.
""" + BREVITY_INSTRUCTION,
    tools=[
        find_gourmet_steals,
        search_smart_restaurants,
        find_cheapest_lunch_deals,
        search_dishes_across_restaurants,
        get_full_restaurant_menu,
        get_active_delivery_address,
    ],
)

cart_coupon_agent = LlmAgent(
    name="cart_coupon_agent",
    model=FOODY_MODEL,
    description=(
        "Specialized in adding items to the Swiggy Food cart, viewing the itemized bill, "
        "clearing the cart, and testing/optimizing coupons for maximum discount."
    ),
    instruction="""You are FOODY's Cart Builder & Coupon Optimizer sub-agent.
Build the cart and optimize coupons. Be ultra-concise.

Guidelines:
1. After adding an item with `add_dish_to_cart`, automatically run `optimize_cart_coupons`.
2. If `mov_filler_arbitrage.triggered` is True, report the net drop in 1 short line:
   `🎯 Auto-added [Filler] (₹X) to bridge coupon gap. Bill dropped: ~₹Old~ ➔ ₹New.`
3. Itemized bill format (max 3 lines):
   `🛒 [Restaurant]`
   `• Items: [Name] × [Qty] — ₹[Price]`
   `💰 Net To Pay: ₹[Total] (Coupon: -₹[Disc] | Del: FREE/₹[Fee])`
""" + BREVITY_INSTRUCTION,
    tools=[
        view_current_cart,
        add_dish_to_cart,
        remove_auto_filler_from_cart,
        clear_food_cart,
        optimize_cart_coupons,
        get_active_delivery_address,
    ],
)

checkout_gate_agent = LlmAgent(
    name="checkout_gate_agent",
    model=FOODY_MODEL,
    description=(
        "Specialized in staging checkout summaries, enforcing the human-in-the-loop "
        "safety gate, generating UPI payment links/QR codes, and tracking orders."
    ),
    instruction="""You are FOODY's Safe UPI Checkout Gate sub-agent.
CRITICAL SAFETY RULE:
1. NEVER call `unlock_checkout_gate` or `initiate_upi_checkout` unless the user has ALREADY confirmed.
2. When the user says checkout, call `stage_checkout_summary` and output ONLY 1 line:
   `🛒 Staged order for [Restaurant] | Net To Pay: ₹[Amount]. Reply 'confirm' or 'pay' to generate UPI link.`
3. Once confirmed, unlock gate and generate UPI link directly.
""" + BREVITY_INSTRUCTION,
    tools=[
        stage_checkout_summary,
        unlock_checkout_gate,
        initiate_upi_checkout,
        check_payment_and_confirm_order,
        track_active_or_recent_orders,
        view_current_cart,
    ],
)

dineout_agent = LlmAgent(
    name="dineout_agent",
    model=FOODY_MODEL,
    description=(
        "Specialized in dining out, restaurant table discovery, table slot availability, "
        "Dineout Pay bill discounts (10%-50% off), reservations, and comparing eating in vs dining out."
    ),
    instruction="""You are FOODY's Swiggy Dineout & Table Concierge sub-agent.
Help users discover dining out spots, Dineout Pay bill discounts, and book tables. Be ultra-concise.

Guidelines:
1. For dining out queries, call `search_dineout_restaurants`. Show top 2-3 spots max.
2. For restaurant specifics, call `get_dineout_restaurant_details`. Output address, bill discount, and bank offer in 2 lines.
3. For slots, call `check_dineout_slots`. State today/tomorrow meal slot availability in 1-2 lines.
4. When comparing eating in vs out, call `compare_order_vs_dineout`. Output 2 bullets (Delivery vs Dine-In) and 1-line verdict.
""" + BREVITY_INSTRUCTION,
    tools=[
        search_dineout_restaurants,
        get_dineout_restaurant_details,
        check_dineout_slots,
        book_dineout_table,
        compare_order_vs_dineout,
        get_active_delivery_address,
    ],
)

root_agent = LlmAgent(
    name="foody_coordinator",
    model=FOODY_MODEL,
    description="FOODY: Street-smart Swiggy Food & Dineout AI Coordinator powered by Google ADK and Gemini.",
    instruction="""You are FOODY, a street-smart Swiggy Food & Dineout concierge.
You are strictly to-the-point, fast, and punchy.

Core Rules:
- ZERO preambles or filler ("Sure!", "Here you go:"). Jump straight to data.
- Max 2 to 3 bullet points per reply.
- Use `find_gourmet_steals` for gourmet deals (₹200-₹260).
- Use `search_smart_restaurants` for organic quality ranking.
- Use `search_dineout_restaurants` for dining out and Dineout Pay offers (10%-50% off).
- Use `compare_order_vs_dineout` when user asks whether to order or eat out.
- For cart & checkout, enforce the 2-step safety gate with a 1-line confirmation.
""" + BREVITY_INSTRUCTION,
    tools=[
        get_active_delivery_address,
        list_saved_addresses,
        switch_delivery_address,
        find_gourmet_steals,
        search_smart_restaurants,
        find_cheapest_lunch_deals,
        search_dishes_across_restaurants,
        get_full_restaurant_menu,
        view_current_cart,
        add_dish_to_cart,
        remove_auto_filler_from_cart,
        clear_food_cart,
        optimize_cart_coupons,
        stage_checkout_summary,
        unlock_checkout_gate,
        initiate_upi_checkout,
        check_payment_and_confirm_order,
        track_active_or_recent_orders,
        search_dineout_restaurants,
        get_dineout_restaurant_details,
        check_dineout_slots,
        book_dineout_table,
        compare_order_vs_dineout,
    ],
)
