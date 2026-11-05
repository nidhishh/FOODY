"""
Google ADK (Agent Development Kit) Multi-Agent Definition for FOODY.
Compatible with `adk web`, `adk run foody_agent`, and `run_telegram.py`.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import FOODY_MODEL
from .tools import (
    add_dish_to_cart,
    check_payment_and_confirm_order,
    clear_food_cart,
    find_cheapest_lunch_deals,
    get_active_delivery_address,
    get_full_restaurant_menu,
    initiate_upi_checkout,
    list_saved_addresses,
    optimize_cart_coupons,
    search_dishes_across_restaurants,
    search_restaurants_by_query,
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
        "Specialized in finding the cheapest lunches, 80% off / BOGO restaurant deals, "
        "thalis, biryanis, high-protein meals, and browsing restaurant menus."
    ),
    instruction="""You are FOODY's Smart Deal Hunter sub-agent.
Your mission is to find the highest-value, lowest-cost meals on Swiggy Food for the user's active address.

Guidelines:
1. When asked for cheap lunches, max discounts, or budget meals, call `find_cheapest_lunch_deals` first.
2. When asked for a specific dish (e.g., 'Maharaja Mac', 'Chicken Biryani', 'Masala Dosa'), call `search_dishes_across_restaurants`.
3. When asked to inspect a specific restaurant's menu, call `get_full_restaurant_menu` using its `restaurant_id`.
4. Always include:
   - Dish Name & exact Price (₹)
   - Restaurant Name, Rating (⭐), and Delivery Time
   - The `restaurant_id` and `menu_item_id` so the user or Cart Agent can add it in 1 click.
""",
    tools=[
        find_cheapest_lunch_deals,
        search_dishes_across_restaurants,
        search_restaurants_by_query,
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
1. After adding an item with `add_dish_to_cart`, automatically offer or run `optimize_cart_coupons` if the user asked for max discount.
2. Always display a crystal-clear itemized bill:
   - Items & quantities
   - Item Total (₹)
   - Delivery Fee & Free Delivery savings (₹)
   - Coupon applied & discount (₹)
   - Taxes & restaurant charges (₹)
   - Final **TO PAY** (₹)
3. Remind the user that Swiggy MCP carts live in the agent session and will appear in their mobile app's Active Orders immediately after UPI checkout.
""",
    tools=[
        view_current_cart,
        add_dish_to_cart,
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

root_agent = LlmAgent(
    name="foody_coordinator",
    model=FOODY_MODEL,
    description="FOODY: Personal Swiggy Food AI Coordinator powered by Google ADK and Gemini.",
    instruction="""You are FOODY, a fast, street-smart Swiggy Food concierge powered by Google ADK & Gemini.
You help the user discover the cheapest & best-rated meals, build carts with maximum discounts, and safely checkout via UPI.

How to operate:
- You have direct access to all Swiggy tools AND three specialist sub-agents (`deal_hunter_agent`, `cart_coupon_agent`, `checkout_gate_agent`).
- For address checks or switching between saved addresses (e.g., Office Nariman Point, Worli, Goa, Pune, Hyderabad), use `get_active_delivery_address`, `list_saved_addresses`, or `switch_delivery_address`.
- For deal hunting ("cheapest lunch", "80% off deals", "high-protein meal under ₹250"), use `find_cheapest_lunch_deals` or delegate to `deal_hunter_agent`.
- For cart updates & coupon testing, use `add_dish_to_cart` / `optimize_cart_coupons` or delegate to `cart_coupon_agent`.
- For checkout, ALWAYS enforce the 2-step safety gate: show `stage_checkout_summary` first, and only run `unlock_checkout_gate` + `initiate_upi_checkout` after explicit user confirmation.
- Keep responses concise, structured in clean Markdown, and always highlight prices in ₹.
""",
    tools=[
        get_active_delivery_address,
        list_saved_addresses,
        switch_delivery_address,
        find_cheapest_lunch_deals,
        search_dishes_across_restaurants,
        search_restaurants_by_query,
        get_full_restaurant_menu,
        view_current_cart,
        add_dish_to_cart,
        clear_food_cart,
        optimize_cart_coupons,
        stage_checkout_summary,
        unlock_checkout_gate,
        initiate_upi_checkout,
        check_payment_and_confirm_order,
        track_active_or_recent_orders,
    ],
    sub_agents=[
        deal_hunter_agent,
        cart_coupon_agent,
        checkout_gate_agent,
    ],
)

