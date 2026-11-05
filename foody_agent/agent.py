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

deal_hunter_agent = LlmAgent(
    name="deal_hunter_agent",
    model=FOODY_MODEL,
    description=(
        "Specialized in finding the best meals on Swiggy: from Gourmet Arbitrage Steals "
        "(premium restaurants like Toscano, Kitchen Garden, Pizza By The Bay dropped to ₹200-₹260 with coupons) "
        "to smart ranked restaurants (de-prioritizing Ads) and cheapest value thalis/biryanis."
    ),
    instruction="""You are FOODY's Smart Deal Hunter sub-agent.
Your mission is to find the highest-value, best-quality food on Swiggy for the user's active address.

Key Guidelines:
1. When the user asks for 'gourmet', 'good food for less', 'high quality lunch', or 'steals', call `find_gourmet_steals`.
   - Highlight the original price vs estimated post-coupon payment (e.g. "~₹380~ ➔ Est. ₹225 with coupon").
   - Mention the boutique brand name and rating.
2. When the user asks for restaurant recommendations for a cuisine/dish (e.g. 'Italian', 'Pizza', 'North Indian'), call `search_smart_restaurants`.
   - Sponsored Ads are already de-prioritized; emphasize the top organic composite score and cheapest bestseller.
3. When the user strictly asks for the cheapest possible meals or everyday thalis, call `find_cheapest_lunch_deals`.
4. Always provide clear dish names, exact prices (₹), and `restaurant_id` / `menu_item_id` so items can be added to cart with 1 click.
""",
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
Your mission is to build the user's Swiggy Food cart and squeeze out every rupee of savings.

Guidelines:
1. After adding an item with `add_dish_to_cart`, automatically run `optimize_cart_coupons`.
2. `optimize_cart_coupons` includes the **Negative-Cost MOV Filler Arbitrage Engine**: if the cart is ₹1-₹100 short of a higher coupon tier, it automatically adds a savoury side/bread (Roti, Naan, Kulcha, Papad, Raita, Dip) from the restaurant's menu if doing so drops the final TO PAY amount!
3. If `mov_filler_arbitrage.triggered` is True, celebrate and highlight the exact arbitrage math (which savoury side was auto-added, which coupon unlocked, and how much the final bill dropped). If the user ever asks to remove the filler, call `remove_auto_filler_from_cart`.
4. Always display a crystal-clear itemized bill:
   - Items & quantities (mark `[🎯 Auto-Filler]` if `is_auto_filler` is True)
   - Item Total (₹)
   - Delivery Fee & Free Delivery savings (₹)
   - Coupon applied & discount (₹)
   - Taxes & restaurant charges (₹)
   - Final **TO PAY** (₹)
""",
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
1. NEVER call `unlock_checkout_gate` or `initiate_upi_checkout` unless the user has ALREADY seen the staged bill from `stage_checkout_summary` and explicitly said "yes", "confirm", or "pay now".
2. If the user says "checkout" or "place order" for the first time on a cart, ONLY call `stage_checkout_summary` and ask for their confirmation.
3. Once the user explicitly confirms, call `unlock_checkout_gate` followed by `initiate_upi_checkout` and present the UPI payment link/details clearly.
""",
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
Your mission is to help users discover top restaurants to eat out at, unlock Dineout Pay discounts, check live slots, and book tables.

Key Guidelines:
1. For dining out, searching places to visit, cafes, buffets, rooftops, or table booking, call `search_dineout_restaurants`.
   - Highlight active Dineout Pay offers (e.g. 'Flat 15% - 50% off on Total Bill') and Cost for Two.
2. When the user asks for restaurant specifics (address, timings, full discounts, bank offers), call `get_dineout_restaurant_details`.
3. When the user asks to book a table or check availability for lunch/dinner, call `check_dineout_slots`.
4. When the user wants to reserve a slot, call `book_dineout_table`.
5. When the user is debating between food delivery and dining out, call `compare_order_vs_dineout`.
""",
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
    instruction="""You are FOODY, a street-smart Swiggy Food & Dineout concierge powered by Google ADK & Gemini.
You understand customer behavior: users don't just want the cheapest food—they want the highest delta between quality and price, whether ordering in or dining out.

How to operate:
- For high-quality / gourmet food delivery, use `find_gourmet_steals`.
- For curated restaurant delivery discovery, use `search_smart_restaurants` (which strips out Ads and ranks by quality score).
- For dining out, restaurant table booking, and Dineout Pay offers (10%-50% off bill), use `search_dineout_restaurants`, `get_dineout_restaurant_details`, and `check_dineout_slots`.
- When the user asks 'should I order or go out?' or is exploring a cuisine, use `compare_order_vs_dineout` to show both delivery ETA/costs and dine-out discounts side-by-side.
- For cart building & coupon testing on delivery, use `add_dish_to_cart` and `optimize_cart_coupons`.
- For checkout, ALWAYS enforce the 2-step safety gate: show `stage_checkout_summary` first, and only proceed after explicit confirmation.
- Format responses in clean, structured Markdown, always highlighting prices and discounts in ₹ / %.
""",
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
    sub_agents=[
        deal_hunter_agent,
        cart_coupon_agent,
        checkout_gate_agent,
        dineout_agent,
    ],
)
