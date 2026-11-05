# 🍔 FOODY — Google ADK + Swiggy MCP Multi-Agent Concierge

**FOODY** is a local-first AI food concierge built with **Google ADK (`google-adk`)**, **Gemini 2.5 Flash**, **Swiggy Food MCP (`https://mcp.swiggy.com/food`)**, and **Telegram**.

It features a 3-agent hierarchy (`deal_hunter_agent`, `cart_coupon_agent`, `checkout_gate_agent`) coordinated by `foody_coordinator`:
1. **🔍 Smart Deal Hunter**: Runs parallel queries across dish menus (`thali`, `biryani`, `combo`, `lunchbox`) and promo restaurants (`80% off`, `BOGO`) to rank meals by true lowest cost.
2. **🛒 Cart & Coupon Optimizer**: Adds items to your Swiggy Food cart, auto-tests applicable coupons, and breaks down the exact bill (`Item Total`, `Delivery Fee Saved`, `Taxes`, `TO PAY`).
3. **🔒 Safe UPI Checkout Gate**: Enforces a strict human-in-the-loop gate—never placing an order until you explicitly approve the staged bill (or tap the confirmation button in Telegram), which then generates your live UPI link.

---

## 🚀 Quickstart

### 1. Install Dependencies
From the `FOODY` directory:
```powershell
pip install -r requirements.txt
```

### 2. Verify Swiggy Auth & Initialize `.env`
Your Swiggy OAuth token is automatically loaded from `C:\Users\Nitish Jindal\.swiggy\auth.json` (and auto-refreshed before expiry).
Run the self-test script:
```powershell
python setup_env.py
```

### 3. Add Your Keys in `.env`
Open [`.env`](file:///c:/Users/Nitish%20Jindal/OneDrive%20-%20JSW/Documents/deckx/FOODY/.env) and set:
- `GOOGLE_API_KEY` / `GEMINI_API_KEY`: Get from [Google AI Studio](https://aistudio.google.com/app/apikey)
- `TELEGRAM_BOT_TOKEN`: Create a bot in 15 seconds by messaging `@BotFather` on Telegram with `/newbot`

---

## 💻 Option A: Run Google ADK Dev UI (`adk web`)
Google ADK includes a built-in local web playground where you can chat with `foody_agent`, inspect live MCP tool calls, and watch sub-agent handoffs:
```powershell
adk web
```
Then open **`http://localhost:8000`** in your browser and select `foody_agent` from the dropdown!

You can also run it directly in your terminal:
```powershell
adk run foody_agent
```

---

## 📱 Option B: Run the Telegram Bot
Start the Telegram bot on your laptop:
```powershell
python run_telegram.py
```
Then message your bot on Telegram from your phone:
- `/start` — Main dashboard with 1-tap buttons
- `/deals` — Scans Swiggy for the cheapest lunches and renders `[➕ Add #1]` buttons
- `/cart` — Live itemized bill + coupon optimizer + checkout gate
- `/address` — Switch between **Office (Nariman Point)**, **Flat (Worli)**, **BITS Goa**, **Pune**, etc.
- Or just chat naturally: *"Find me the cheapest high-protein chicken meal under ₹250 and add it to my cart"*

