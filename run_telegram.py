"""
Telegram Bot Runner for the FOODY Google ADK Agent.
Combines conversational Google ADK (`root_agent` + Gemini) with 1-tap inline buttons
for address switching, deal hunting, cart building, and safe UPI checkout.
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
    clear_food_cart,
    find_cheapest_lunch_deals,
    initiate_upi_checkout,
    list_saved_addresses,
    optimize_cart_coupons,
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
                InlineKeyboardButton("🔥 Cheapest Lunch Deals", callback_data="menu:deals"),
                InlineKeyboardButton("🛒 View Cart", callback_data="menu:cart"),
            ],
            [
                InlineKeyboardButton("📍 Switch Address", callback_data="menu:address"),
                InlineKeyboardButton("💳 Checkout Gate", callback_data="menu:checkout"),
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
            f"Tap *🔥 Cheapest Lunch Deals* or tell me what you're craving!"
        )

    lines = [
        f"🛒 *Live Swiggy Cart* (`#{cart.get('cart_id', '')}`)",
        f"🏪 *Restaurant:* {cart.get('restaurant_name') or 'Selected Outlet'}",
        f"📍 *Delivering to:* {cart.get('active_address')}",
        "",
        "*Items:*",
    ]
    for idx, item in enumerate(cart.get("items", []), start=1):
        lines.append(
            f"  {idx}. *{item.get('name')}* × {item.get('quantity')} — ₹{item.get('final_price_inr')}"
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


def _cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💳 Stage UPI Checkout", callback_data="cart:stage_checkout"),
                InlineKeyboardButton("🗑️ Clear Cart", callback_data="cart:clear"),
            ],
            [
                InlineKeyboardButton("🔥 Browse More Deals", callback_data="menu:deals"),
            ],
        ]
    )


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
        "You can chat with me naturally:\n"
        "• _\"Find me the cheapest chicken lunch under ₹200\"_\n"
        "• _\"Add a Chicken Maharaja Mac to my cart\"_\n"
        "• _\"Show 80% off and BOGO pizza places near me\"_\n\n"
        "Or use the quick action buttons below:"
    )
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
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


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_authorized(update.effective_user.id):
        return
    cart = await asyncio.to_thread(view_current_cart)
    await update.message.reply_text(
        _format_cart_text(cart),
        parse_mode="Markdown",
        reply_markup=_cart_keyboard(),
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
        # Store index to avoid 64-byte callback_data limit
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

    if data == "menu:deals":
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
            await query.edit_message_text("Deal cache expired. Tap /deals to refresh!")
            return
        chosen = meals[idx]
        await query.edit_message_text(
            f"🛒 Adding *{chosen['dish_name']}* from *{chosen['restaurant_name']}* to your cart & checking coupons...",
            parse_mode="Markdown",
        )
        cart = await asyncio.to_thread(
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
            reply_markup=_cart_keyboard(),
        )

    elif data == "menu:cart":
        cart = await asyncio.to_thread(view_current_cart)
        await query.edit_message_text(
            _format_cart_text(cart),
            parse_mode="Markdown",
            reply_markup=_cart_keyboard(),
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
            + "\n\n🔒 *SAFETY GATE:* Tap below only if you want to generate a live UPI payment request for this order:",
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
            # Fallback if Gemini emitted unescaped Markdown characters
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
        print("⚠️ Missing GOOGLE_API_KEY / GEMINI_API_KEY in .env! Run `python setup_env.py` first.")
        return

    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print(
            "⚠️ Missing TELEGRAM_BOT_TOKEN in .env!\n"
            "• To use the Google ADK Browser UI right now without Telegram, run:\n"
            "    adk web\n"
            "• Or add TELEGRAM_BOT_TOKEN to .env (via `python setup_env.py`) and re-run `python run_telegram.py`."
        )
        return

    print(f"🚀 Starting FOODY Telegram Bot (Model: {os.getenv('FOODY_MODEL', 'gemini-2.5-flash')})...")
    print(f"📍 Default Delivery Address: {STATE.active_address_label}")

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("deals", deals_cmd))
    app.add_handler(CommandHandler("cart", cart_cmd))
    app.add_handler(CommandHandler("address", address_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

