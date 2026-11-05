"""
Interactive setup & self-test script for FOODY (Google ADK + Swiggy MCP + Telegram).
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from foody_agent.config import ROOT_DIR, STATE, get_swiggy_auth_path, get_valid_bearer_token
from foody_agent.tools import find_cheapest_lunch_deals, view_current_cart


def test_swiggy_connection() -> bool:
    print("============================================================")
    print("🔍 Step 1: Verifying Swiggy MCP OAuth 2.1 Token...")
    auth_path = get_swiggy_auth_path()
    try:
        tok = get_valid_bearer_token("food")
        print(f"  ✅ Found valid Swiggy Bearer token in {auth_path} ({tok[:12]}...)")
    except Exception as exc:
        print(f"  ❌ Auth error: {exc}")
        return False

    print(f"\n📍 Active Delivery Address: {STATE.active_address_label}")
    print("🛒 Checking current Swiggy cart via direct HTTP MCP...")
    cart = view_current_cart()
    if cart.get("ok"):
        if cart.get("is_empty"):
            print("  ✅ Connected! Cart is currently empty.")
        else:
            bill = cart.get("bill_breakdown", {})
            print(
                f"  ✅ Connected! Cart #{cart.get('cart_id')} has {len(cart.get('items', []))} item(s) "
                f"— To Pay: ₹{bill.get('to_pay_inr')}"
            )
    else:
        print(f"  ❌ Cart check failed: {cart}")
        return False

    print("\n🔥 Running Parallel Deal Hunter self-test (max ₹250)...")
    deals = find_cheapest_lunch_deals("lunch", max_budget_inr=250, veg_only=False, min_restaurant_rating=4.0)
    meals = deals.get("top_cheapest_meals", [])
    print(f"  ✅ Found {len(meals)} top-rated meals under ₹250! Top 3:")
    for idx, m in enumerate(meals[:3], start=1):
        print(
            f"     {idx}. {m['dish_name']} — ₹{m['price_inr']} "
            f"@ {m['restaurant_name']} (⭐ {m['restaurant_rating']}, {m['delivery_time']})"
        )
    print("============================================================\n")
    return True


def configure_dotenv() -> None:
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        print(f"ℹ️  {env_file} already exists.")
        return

    print("📝 Creating default .env file in FOODY/...")
    template = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    env_file.write_text(template, encoding="utf-8")
    print(f"✅ Created {env_file}! Paste your GEMINI_API_KEY and TELEGRAM_BOT_TOKEN inside.")


if __name__ == "__main__":
    configure_dotenv()
    test_swiggy_connection()

