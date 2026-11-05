"""
Telegram Bot Runner for the FOODY Google ADK Agent.
Combines conversational Google ADK (`root_agent` + Gemini) with 1-tap inline buttons
for Gourmet Steals, smart search, address switching, deal hunting, and safe UPI checkout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from foody_agent.agent import root_agent
from foody_agent.config import ROOT_DIR, STATE
from foody_agent.tools import (
    add_dish_to_cart,
    book_dineout_table,
    check_dineout_slots,
    clear_food_cart,
    compare_order_vs_dineout,
    find_cheapest_lunch_deals,
    find_gourmet_steals,
    get_dineout_restaurant_details,
    initiate_upi_checkout,
    list_saved_addresses,
    optimize_cart_coupons,
    remove_auto_filler_from_cart,
    search_dineout_restaurants,
    search_smart_restaurants,
    stage_checkout_summary,
    switch_delivery_address,
    unlock_checkout_gate,
    view_current_cart,
)

load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("foody.telegram")

APP_NAME = "foody_telegram_adk"
session_service = InMemorySessionService()
adk_runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# Cache recent deals for 1-tap 'Add to Cart' buttons
LAST_DEALS_CACHE: Dict[int, List[Dict[str, Any]]] = {}


def _is_authorized(user_id: int) -> bool:
    allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
    if not allowed_raw:
        return True
    allowed = {int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()}
    return user_id in allowed


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💎 Gourmet Steals (₹200-₹260)", callback_data="menu:gourmet"),
            ],
            [
                InlineKeyboardButton("🔥 Value Lunch Deals", callback_data="menu:deals"),
                InlineKeyboardButton("🍽️ Dineout & Tables", callback_data="menu:dineout"),
            ],
            [
                InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart"),
                InlineKeyboardButton("💳 Checkout Gate", callback_data="menu:checkout"),
            ],
            [
                InlineKeyboardButton("📍 Switch Address", callback_data="menu:address"),
            ],
        ]
    )


def _format_cart_text(cart: Dict[str, Any]) -> str:
    if not cart.get("ok"):
        return f"⚠️ Error fetching cart: {cart.get('error', 'Unknown error')}"
    if cart.get("is_empty"):
        return (
            f"🛒 *Your Swiggy Cart is Empty*\n"
            f"📍 *Delivering to:* {STATE.active_address_label}\n\n"
            f"Tap *💎 Gourmet Steals* or *🔥 Value Lunch Deals* to add something delicious!"
        )

    lines = [
        f"🛒 *Live Swiggy Cart* (`#{cart.get('cart_id', '')}`)",
        f"🏪 *Restaurant:* {cart.get('restaurant_name') or 'Selected Outlet'}",
        f"📍 *Delivering to:* {cart.get('active_address')}",
        "",
        "*Items:*",
    ]
    for idx, item in enumerate(cart.get("items", []), start=1):
        filler_tag = " _(🎯 Auto-Filler)_" if item.get("is_auto_filler") else ""
        lines.append(
            f"  {idx}. *{item.get('name')}* × {item.get('quantity')} — ₹{item.get('final_price_inr')}{filler_tag}"
        )

    arb = cart.get("filler_arbitrage")
    if isinstance(arb, dict) and arb.get("triggered"):
        lines.append("")
        lines.append(
            f"🎯 *MOV Arbitrage Active:*\n"
            f"Added *{arb.get('filler_name')}* (₹{arb.get('filler_price_inr')}) to bridge ₹{arb.get('deficit_bridged_inr')} gap & unlock `{arb.get('unlocked_coupon_title')}` (-₹{arb.get('unlocked_discount_inr')})!\n"
            f"📉 *Bill dropped:* ~₹{arb.get('baseline_to_pay_inr')}~ ➔ *₹{arb.get('new_to_pay_inr')}* (Saved ₹{arb.get('extra_net_savings_inr')} net + free side!)"
        )

    bill = cart.get("bill_breakdown", {})
    lines.append("")
    lines.append("*Bill Breakdown:*")
    lines.append(f"• Item Total: ₹{bill.get('item_total_inr')}")
    if bill.get("free_delivery_applied") or bill.get("delivery_charge_inr") == 0:
        saved = bill.get("delivery_fee_saved_inr", 0)
        saved_str = f" (Saved ₹{saved}!)" if saved else ""
        lines.append(f"• Delivery Fee: *FREE*{saved_str}")
    else:
        lines.append(f"• Delivery Fee: ₹{bill.get('delivery_charge_inr')}")

    if bill.get("coupon_discount_inr"):
        lines.append(
            f"• Coupon (`{bill.get('coupon_applied')}`): -₹{bill.get('coupon_discount_inr')}"
        )
    lines.append(f"• Taxes & Packing: ₹{bill.get('taxes_and_charges_inr')}")
    lines.append(f"💰 *TO PAY: ₹{bill.get('to_pay_inr')}*")
    return "\n".join(lines)


def _cart_keyboard(cart: Dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("💳 Stage UPI Checkout", callback_data="cart:stage_checkout"),
            InlineKeyboardButton("🗑️ Clear Cart", callback_data="cart:clear"),
        ],
    ]
    arb = (cart or STATE.last_cart_snapshot or {}).get("filler_arbitrage")
    if isinstance(arb, dict) and arb.get("triggered"):
        rows.append(
            [
                InlineKeyboardButton(
                    f"➖ Remove Auto-Filler ({arb.get('filler_name', 'Side')[:18]})",
                    callback_data="cart:remove_filler",
                )
            ]
        )
    rows.append([InlineKeyboardButton("💎 Browse Gourmet Steals", callback_data="menu:gourmet")])
    return InlineKeyboardMarkup(rows)


async def _run_adk_agent(user_id: int, user_text: str) -> str:
    """Runs the Google ADK root_agent for a natural-language user prompt."""
    session_id = f"tg_session_{user_id}"
    user_str = str(user_id)

    existing = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_str,
        session_id=session_id,
    )
    if existing is None:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_str,
            session_id=session_id,
        )

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_text)],
    )

    final_chunks: List[str] = []
    async for event in adk_runner.run_async(
        user_id=user_str,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    final_chunks.append(part.text)

    reply = "\n".join(final_chunks).strip()
    return reply or "✅ Done! Use `/cart` to inspect your updated Swiggy cart."


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    welcome = (
        "🍔 *Yo! Welcome to FOODY (Google ADK + Swiggy MCP)*\n\n"
        f"📍 *Active Address:* `{STATE.active_address_label}`\n\n"
        "Smart Food Concierge built for high-value orders & dining:\n"
        "• *💎 Gourmet Steals:* High-end food dropped to ₹200-₹260 via coupons.\n"
        "• *🍽️ Dineout & Tables:* Top restaurants with 10%-50% Dineout Pay bill discounts & instant table booking.\n"
        "• *⚡ Order vs. Dine Out:* Compare food delivery vs eating out side-by-side!\n\n"
        "Tap a button below or tell me what you're craving:"
    )
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
    )


async def gourmet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    msg = await update.message.reply_text("💎 Hunting Gourmet Steals across South Mumbai boutique spots...")
    res = await asyncio.to_thread(find_gourmet_steals, "gourmet", 260, False)
    steals = res.get("gourmet_steals", [])
    LAST_DEALS_CACHE[update.effective_user.id] = steals

    if not steals:
        await msg.edit_text("No gourmet steals under ₹260 found right now. Tap /deals for value lunch deals!")
        return

    lines = [
        f"💎 *Gourmet Arbitrage Steals (Est. Landed ≤ ₹260)*",
        f"📍 _{STATE.active_address_label}_\n",
    ]
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for idx, s in enumerate(steals[:6], start=1):
        veg_icon = "🟢" if s.get("is_veg") else "🔴"
        lines.append(
            f"*{idx}. {veg_icon} {s['dish_name']}*\n"
            f"   🏪 {s['restaurant_name']} (⭐ {s['restaurant_rating']} · {s['delivery_time']})\n"
            f"   💰 ~₹{s['original_price_inr']}~ ➔ *Est. ₹{s['estimated_landed_inr']}* ({s['savings_tag']})"
        )
        row.append(
            InlineKeyboardButton(
                f"➕ Add #{idx} (₹{s['estimated_landed_inr']})",
                callback_data=f"adddeal:{idx - 1}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def deals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    msg = await update.message.reply_text("🔍 Scanning Swiggy for the cheapest high-rated lunches...")
    res = await asyncio.to_thread(find_cheapest_lunch_deals, "lunch", 280, False, 3.8)
    meals = res.get("top_cheapest_meals", [])
    LAST_DEALS_CACHE[update.effective_user.id] = meals

    if not meals:
        await msg.edit_text("No meals found under ₹280 right now. Try asking me for a specific dish!")
        return

    lines = [
        f"🔥 *Cheapest Full Lunches Near You*",
        f"📍 _{STATE.active_address_label}_\n",
    ]
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for idx, m in enumerate(meals[:6], start=1):
        veg_icon = "🟢" if m.get("is_veg") else "🔴"
        lines.append(
            f"*{idx}. {veg_icon} {m['dish_name']}* — *₹{m['price_inr']}*\n"
            f"   🏪 {m['restaurant_name']} (⭐ {m['restaurant_rating']} · {m['delivery_time']})"
        )
        row.append(
            InlineKeyboardButton(
                f"➕ Add #{idx} (₹{m['price_inr']})",
                callback_data=f"adddeal:{idx - 1}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def dineout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    msg = await update.message.reply_text("🍽️ Searching top Dineout restaurants & bill discounts near you...")
    res = await asyncio.to_thread(search_dineout_restaurants, "restaurant", "", 6)
    rests = res.get("restaurants", [])
    if not rests:
        await msg.edit_text("No Dineout restaurants found in this area right now. Try specifying a locality like 'Dineout in Bandra'!")
        return

    lines = [
        f"🍽️ *Top Dineout Restaurants & Offers*",
        f"📍 _{res.get('resolved_area', STATE.active_address_label)}_\n",
    ]
    buttons: List[List[InlineKeyboardButton]] = []
    for idx, r in enumerate(rests[:5], start=1):
        offer_str = f"\n   🏷️ *{r['active_offer']}*" if r.get("active_offer") else ""
        lines.append(
            f"*{idx}. {r['name']}* (⭐ {r['rating']} · {r['locality']})\n"
            f"   🍴 {r['cuisine']} · {r['cost_for_two']}{offer_str}"
        )
        buttons.append([
            InlineKeyboardButton(f"📅 Slots: {r['name'][:16]}", callback_data=f"doslot:{r['restaurant_id']}"),
            InlineKeyboardButton("ℹ️ Offers", callback_data=f"dodet:{r['restaurant_id']}"),
        ])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu:back")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    cart = await asyncio.to_thread(view_current_cart)
    await update.message.reply_text(
        _format_cart_text(cart),
        parse_mode="Markdown",
        reply_markup=_cart_keyboard(cart),
    )


async def address_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    listing = await asyncio.to_thread(list_saved_addresses)
    addrs = listing.get("addresses", [])[:8]
    buttons = []
    for a in addrs:
        active_mark = "✅ " if a.get("is_active") else ""
        label = f"{active_mark}{a.get('tag')}: {str(a.get('addressLine', ''))[:38]}..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"setaddr:{a.get('index')}")])

    await update.message.reply_text(
        f"📍 *Current Address:* `{STATE.active_address_label}`\n\nTap a saved address below to switch:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    await query.answer()
    data = query.data or ""
    uid = update.effective_user.id

    if data == "menu:gourmet":
        await query.edit_message_text("💎 Hunting Gourmet Steals across South Mumbai boutique spots...")
        res = await asyncio.to_thread(find_gourmet_steals, "gourmet", 260, False)
        steals = res.get("gourmet_steals", [])
        LAST_DEALS_CACHE[uid] = steals

        if not steals:
            await query.edit_message_text("No gourmet steals under ₹260 found right now. Tap /deals for lunch deals!")
            return

        lines = [f"💎 *Gourmet Arbitrage Steals (Est. Landed ≤ ₹260)*\n📍 _{STATE.active_address_label}_\n"]
        buttons: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for idx, s in enumerate(steals[:6], start=1):
            veg_icon = "🟢" if s.get("is_veg") else "🔴"
            lines.append(
                f"*{idx}. {veg_icon} {s['dish_name']}*\n"
                f"   🏪 {s['restaurant_name']} (⭐ {s['restaurant_rating']} · {s['delivery_time']})\n"
                f"   💰 ~₹{s['original_price_inr']}~ ➔ *Est. ₹{s['estimated_landed_inr']}* ({s['savings_tag']})"
            )
            row.append(
                InlineKeyboardButton(
                    f"➕ Add #{idx} (₹{s['estimated_landed_inr']})",
                    callback_data=f"adddeal:{idx - 1}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "menu:deals":
        await query.edit_message_text("🔍 Scanning Swiggy for the cheapest high-rated lunches...")
        res = await asyncio.to_thread(find_cheapest_lunch_deals, "lunch", 280, False, 3.8)
        meals = res.get("top_cheapest_meals", [])
        LAST_DEALS_CACHE[uid] = meals
        lines = [f"🔥 *Cheapest Full Lunches Near You*\n📍 _{STATE.active_address_label}_\n"]
        buttons: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for idx, m in enumerate(meals[:6], start=1):
            veg_icon = "🟢" if m.get("is_veg") else "🔴"
            lines.append(
                f"*{idx}. {veg_icon} {m['dish_name']}* — *₹{m['price_inr']}*\n"
                f"   🏪 {m['restaurant_name']} (⭐ {m['restaurant_rating']} · {m['delivery_time']})"
            )
            row.append(
                InlineKeyboardButton(
                    f"➕ Add #{idx} (₹{m['price_inr']})",
                    callback_data=f"adddeal:{idx - 1}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart")])
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("adddeal:"):
        idx = int(data.split(":")[1])
        meals = LAST_DEALS_CACHE.get(uid, [])
        if idx >= len(meals):
            await query.edit_message_text("Deal cache expired. Tap /deals or /gourmet to refresh!")
            return
        chosen = meals[idx]
        await query.edit_message_text(
            f"🛒 Adding *{chosen['dish_name']}* from *{chosen['restaurant_name']}* to your cart & optimizing coupons...",
            parse_mode="Markdown",
        )
        await asyncio.to_thread(
            add_dish_to_cart,
            chosen["restaurant_id"],
            chosen["menu_item_id"],
            1,
            chosen["restaurant_name"],
        )
        await asyncio.to_thread(optimize_cart_coupons, chosen["restaurant_id"])
        cart = await asyncio.to_thread(view_current_cart)
        await query.edit_message_text(
            _format_cart_text(cart),
            parse_mode="Markdown",
            reply_markup=_cart_keyboard(cart),
        )

    elif data == "menu:cart":
        cart = await asyncio.to_thread(view_current_cart)
        await query.edit_message_text(
            _format_cart_text(cart),
            parse_mode="Markdown",
            reply_markup=_cart_keyboard(cart),
        )

    elif data == "cart:remove_filler":
        await query.edit_message_text("➖ Removing auto-added filler item & re-optimizing solo coupon...")
        res = await asyncio.to_thread(remove_auto_filler_from_cart)
        cart = res.get("updated_cart", res) if isinstance(res, dict) else await asyncio.to_thread(view_current_cart)
        await query.edit_message_text(
            _format_cart_text(cart),
            parse_mode="Markdown",
            reply_markup=_cart_keyboard(cart),
        )

    elif data == "cart:clear":
        await asyncio.to_thread(clear_food_cart)
        await query.edit_message_text(
            "🗑️ *Your Swiggy cart has been cleared.*",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(),
        )

    elif data in ("menu:checkout", "cart:stage_checkout"):
        staged = await asyncio.to_thread(stage_checkout_summary)
        cart = staged.get("cart_summary", staged)
        if cart.get("is_empty"):
            await query.edit_message_text(
                "🛒 Your cart is empty! Add a dish first before checking out.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        to_pay = cart.get("bill_breakdown", {}).get("to_pay_inr", "?")
        confirm_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"✅ Confirm & Pay ₹{to_pay} via UPI",
                        callback_data="checkout:confirm_upi",
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:cart")],
            ]
        )
        await query.edit_message_text(
            _format_cart_text(cart)
            + "\n\n🔒 *SAFETY GATE:* Tap below to generate your Swiggy UPI payment request:",
            parse_mode="Markdown",
            reply_markup=confirm_kb,
        )

    elif data == "checkout:confirm_upi":
        await query.edit_message_text("⚡ Unlocking safety gate & generating live Swiggy UPI link...")
        await asyncio.to_thread(unlock_checkout_gate)
        res = await asyncio.to_thread(initiate_upi_checkout)
        text_out = res.get("text") or str(res.get("data") or res.get("error"))
        await query.edit_message_text(
            f"💳 *UPI Checkout Initiated:*\n\n{text_out[:3500]}",
            reply_markup=_main_menu_keyboard(),
        )

    elif data == "menu:address":
        listing = await asyncio.to_thread(list_saved_addresses)
        addrs = listing.get("addresses", [])[:8]
        buttons = []
        for a in addrs:
            active_mark = "✅ " if a.get("is_active") else ""
            label = f"{active_mark}{a.get('tag')}: {str(a.get('addressLine', ''))[:38]}..."
            buttons.append([InlineKeyboardButton(label, callback_data=f"setaddr:{a.get('index')}")])
        await query.edit_message_text(
            f"📍 *Current Address:* `{STATE.active_address_label}`\n\nSelect an address:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("setaddr:"):
        idx = int(data.split(":")[1])
        listing = await asyncio.to_thread(list_saved_addresses)
        for a in listing.get("addresses", []):
            if a.get("index") == idx:
                await asyncio.to_thread(switch_delivery_address, str(a.get("id")))
                break
        await query.edit_message_text(
            f"✅ *Active delivery address updated to:*\n`{STATE.active_address_label}`",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(),
        )

    elif data == "menu:dineout":
        await query.edit_message_text("🍽️ Searching top Dineout restaurants & bill discounts near you...")
        res = await asyncio.to_thread(search_dineout_restaurants, "restaurant", "", 6)
        rests = res.get("restaurants", [])
        if not rests:
            await query.edit_message_text("No Dineout restaurants found. Try asking me for a specific cuisine or area!")
            return
        lines = [
            f"🍽️ *Top Dineout Restaurants & Offers*\n📍 _{res.get('resolved_area', STATE.active_address_label)}_\n"
        ]
        buttons: List[List[InlineKeyboardButton]] = []
        for idx, r in enumerate(rests[:5], start=1):
            offer_str = f"\n   🏷️ *{r['active_offer']}*" if r.get("active_offer") else ""
            lines.append(
                f"*{idx}. {r['name']}* (⭐ {r['rating']} · {r['locality']})\n"
                f"   🍴 {r['cuisine']} · {r['cost_for_two']}{offer_str}"
            )
            buttons.append([
                InlineKeyboardButton(f"📅 Slots: {r['name'][:16]}", callback_data=f"doslot:{r['restaurant_id']}"),
                InlineKeyboardButton("ℹ️ Offers", callback_data=f"dodet:{r['restaurant_id']}"),
            ])
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu:back")])
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("doslot:"):
        rid = data.split(":")[1]
        await query.edit_message_text("📅 Checking live table slots on Swiggy Dineout...")
        slots_res = await asyncio.to_thread(check_dineout_slots, rid)
        summaries = slots_res.get("available_date_summaries", [])
        deals = slots_res.get("booking_deals", [])
        text_lines = [
            f"📅 *Available Table Slots (Next 7 Days)*",
            f"🏪 Restaurant ID: `{rid}`\n",
        ]
        if summaries:
            text_lines.append("*Slot Schedules:*")
            for s in summaries[:4]:
                text_lines.append(f"• {s}")
        if deals:
            text_lines.append("\n*Promotional Deals:*")
            for d in deals[:3]:
                text_lines.append(f"• {d}")
        if not summaries and not deals:
            text_lines.append("No open online slots found for this week. Walk-ins may still be available.")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ℹ️ View Restaurant Offers", callback_data=f"dodet:{rid}")],
            [InlineKeyboardButton("🍽️ Back to Dineout", callback_data="menu:dineout")],
        ])
        await query.edit_message_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data.startswith("dodet:"):
        rid = data.split(":")[1]
        await query.edit_message_text("ℹ️ Fetching full restaurant details & bill discounts...")
        det = await asyncio.to_thread(get_dineout_restaurant_details, rid)
        text_lines = [
            f"🏪 *{det.get('name', 'Restaurant Details')}*",
            f"🍴 *Cuisines:* {det.get('cuisines', 'N/A')}",
            f"⭐ *Rating:* {det.get('rating', 'N/A')}",
            f"💰 *Cost for Two:* {det.get('cost_for_two', 'N/A')}",
            f"📍 *Address:* {det.get('address', 'N/A')}",
            f"⏰ *Timings:* {det.get('timings', 'N/A')}",
            "",
            f"🏷️ *Dineout Pay Offers:*",
            f"   *{det.get('dineout_pay_offers', 'None')}*",
        ]
        if det.get("bank_offers"):
            text_lines.extend(["", "💳 *Bank Discounts & Cashback:*", f"   {det.get('bank_offers')[:300]}"])

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Check Live Slots", callback_data=f"doslot:{rid}")],
            [InlineKeyboardButton("🍽️ Back to Dineout", callback_data="menu:dineout")],
        ])
        await query.edit_message_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data == "menu:back":
        await query.edit_message_text(
            "🍔 *FOODY Main Menu:* Tap an option below or chat with me:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(),
        )


async def handle_natural_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return
    if not _is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized user.")
        return

    user_text = update.message.text.strip()
    thinking_msg = await update.message.reply_text("🧠 _FOODY (Google ADK) is thinking..._", parse_mode="Markdown")

    try:
        reply = await _run_adk_agent(update.effective_user.id, user_text)
        try:
            await thinking_msg.edit_text(
                reply[:4000],
                parse_mode="Markdown",
                reply_markup=_main_menu_keyboard(),
            )
        except Exception:
            await thinking_msg.edit_text(
                reply[:4000],
                reply_markup=_main_menu_keyboard(),
            )
    except Exception as exc:
        logger.exception("Error running ADK agent")
        await thinking_msg.edit_text(f"⚠️ Error: {exc}")


def main() -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    if not api_key or api_key == "your_gemini_api_key_here":
        print("⚠️ Missing GOOGLE_API_KEY / GEMINI_API_KEY in .env!")
        return

    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print("⚠️ Missing TELEGRAM_BOT_TOKEN in .env!")
        return

    print(f"🚀 Starting FOODY Telegram Bot (Model: {os.getenv('FOODY_MODEL', 'gemini-3.5-flash-lite')})...")
    print(f"📍 Default Delivery Address: {STATE.active_address_label}")

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("gourmet", gourmet_cmd))
    app.add_handler(CommandHandler("deals", deals_cmd))
    app.add_handler(CommandHandler("dineout", dineout_cmd))
    app.add_handler(CommandHandler("cart", cart_cmd))
    app.add_handler(CommandHandler("address", address_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
