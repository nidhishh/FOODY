# 🍔 FOODY — Google ADK + Swiggy MCP Multi-Agent Concierge

**FOODY** is an autonomous AI food concierge built with **Google ADK (`google-adk`)**, **Gemini**, **Swiggy's Model Context Protocol (MCP)**, and **Telegram**.

---

## 💡 Why I Built FOODY

> **"Honestly? Because I was too lazy to open Swiggy, scroll through 50 ads, compare 10 different restaurants, and manually test 5 coupon codes every single afternoon just to get lunch."**

The best software is born out of pure, unadulterated laziness:
1. **Decision Fatigue is Real**: Spending 15 minutes debating between burgers, thalis, and biryanis while scrolling past sponsored carousel banners is an everyday productivity killer.
2. **The Coupon Friction**: I want the best food for the lowest price without having to manually copy-paste `TRYNEW`, `WELCOME50`, or calculate cart minimums.
3. **The Gourmet Deal Hack**: If a ₹450 artisanal lunch bowl or wood-fired pizza can be dropped to **₹220–₹250 with coupons**, I want an agent to spot that discount and stage it for me in 2 taps.
4. **Chat > Apps**: I live on Telegram. Sending a quick text like *"Get me the cheapest chicken lunch near me"* and tapping a single button to pay via UPI is 10x faster than navigating an app.

---

## 🧠 System Architecture

FOODY operates as a specialized multi-agent hierarchy powered by **Google ADK**:

```
                  ┌─────────────────────────────────┐
                  │    foody_coordinator (Root)     │
                  │   (Google ADK + Gemini Flash)   │
                  └───────────────┬─────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ deal_hunter_agent│    │cart_coupon_agent │    │checkout_gate_agent│
│ Parallel Dish &  │    │Cart Staging &    │    │Human-in-the-Loop │
│ Gourmet Scanner  │    │Auto Coupon Trials│    │Safe UPI Checkout │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

1. **🔍 Smart Deal Hunter**: Scans across menus in parallel (`thali`, `biryani`, `combo`, `bogo`, `80% off`), strips out ad pollution, and finds high-rated dishes with maximum discount delta.
2. **🛒 Cart & Coupon Optimizer**: Stages the items on your active delivery address, discovers applicable Swiggy promos, auto-tests candidate coupon codes, and outputs a transparent itemized bill.
3. **🔒 Safe UPI Checkout Gate**: Enforces a non-negotiable human confirmation gate. It never places an order autonomously—instead, it presents a final staged bill and generates a 1-tap UPI deep link (GPay, PhonePe, Paytm) only after your explicit approval.

---

## 🚀 Quickstart

### 1. Install Dependencies
From the `FOODY` directory:
```powershell
pip install -r requirements.txt
```

### 2. Verify Swiggy Auth & Initialize `.env`
Your Swiggy OAuth token is automatically loaded from `~/.swiggy/auth.json` (and auto-refreshed before expiry via Swiggy's token endpoint).
Run the self-test script:
```powershell
python setup_env.py
```

### 3. Add Your Keys in `.env`
Open [`.env`](.env) and set:
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
Then open Telegram on your phone and message your bot (`@BhukkadnidBOT`):
- `/start` — Main dashboard with 1-tap buttons
- `/deals` — Scans Swiggy for the cheapest high-rated lunches and renders `[➕ Add]` buttons
- `/cart` — Live itemized bill + coupon optimizer + checkout gate
- `/address` — Switch between saved delivery locations with 1 tap
- Or just chat naturally: *"Find me the cheapest chicken lunch near me"*
