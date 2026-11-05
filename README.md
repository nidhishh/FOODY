# 🍔 FOODY — Google ADK + Swiggy MCP Multi-Agent Concierge

**FOODY** is an autonomous AI food concierge built with **Google ADK (`google-adk`)**, **Gemini**, **Swiggy's Model Context Protocol (MCP)**, and **Telegram**.

---

## 💡 Why I Built FOODY (The Product Thesis)

Every afternoon in high-density office hubs like South Mumbai (Nariman Point / Fort), ordering lunch on food delivery apps has become an exhausting, high-friction chore:
1. **Ad-Polluted Feeds & Decision Fatigue**: Opening Swiggy or Zomato bombards you with sponsored ads, banners, and infinite scrolling, obscuring authentic high-rated local kitchens.
2. **The "Gourmet Arbitrage" Opportunity**: As urban consumers, we don't want cheap, low-quality food; **we want the highest delta between perceived quality and actual price paid**. We would rather eat a ₹450 artisanal sourdough sandwich, wood-fired Neapolitan pizza, or a gourmet bistro bowl dropped to **₹220–₹250 after smart coupon stacking** than settle for an unbranded oily meal.
3. **The Hidden Coupon Maze**: Manually copying promo codes, testing cart minimum thresholds, and calculating dynamic delivery fees wastes 10–15 minutes every single day.
4. **Agentic Commerce in the Real World**: With the emergence of the Model Context Protocol (MCP), AI shouldn't just summarize text—it should transact on our behalf. I built FOODY to turn everyday food ordering into a street-smart, 2-tap conversational experience directly from Telegram.

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
