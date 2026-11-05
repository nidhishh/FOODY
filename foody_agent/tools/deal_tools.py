"""
Smart Deal Hunter & Gourmet Arbitrage Engine for FOODY.
Optimized for:
1. Gourmet Arbitrage: Finds high-end/artisanal restaurant meals with coupons dropping them to ₹200-₹260.
2. Smart Composite Restaurant Ranking: De-prioritizes sponsored Ads and ranks by Rating, Delivery Speed, and Value.
3. Dual-Faceted Search: Merges top restaurants with cheapest dishes.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ..config import STATE
from ..swiggy_client import SWIGGY

ITEM_LINE_REGEX = re.compile(
    r"^\d+\.\s+(.*?)\s+—\s+₹(\d+(?:\.\d+)?)\s+\|\s+(Veg|Non-veg)\s+\|\s+([\d.]+)★\s+\|\s+(.*?)\s+\(restaurantId:\s*(\d+)\)\s+\(ID:\s*(\d+)\)",
    re.IGNORECASE,
)

PREMIUM_BRAND_KEYWORDS = [
    "toscano",
    "kitchen garden",
    "smoke house deli",
    "pizza by the bay",
    "cafe basilico",
    "malido",
    "boojee cafe",
    "trattoria",
    "mag st",
    "social",
    "cafe mangii",
    "leopold",
    "cafe churchill",
    "subko",
    "blue tokai",
    "bokka coffee",
    "third wave coffee",
    "flax",
    "kuai kitchen",
]


def _parse_search_menu_items(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Robust parser that extracts menu items from structuredContent OR regex-parses text lines."""
    items: List[Dict[str, Any]] = []

    # 1. Try structured data first
    data = res.get("data")
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        for it in data["items"]:
            items.append(
                {
                    "menu_item_id": str(it.get("menu_item_id", "")),
                    "dish_name": it.get("name"),
                    "price_inr": float(it.get("price") or 0),
                    "is_veg": bool(it.get("isVeg", False)),
                    "dish_rating": str(it.get("rating") or "4.0"),
                    "restaurant_id": str(it.get("restaurant_id", "")),
                    "restaurant_name": it.get("restaurant_name", ""),
                    "restaurant_rating": float(it.get("restaurant_rating") or 0.0),
                    "delivery_time": it.get("deliveryTimeRange") or f"{it.get('deliveryTimeMinutes', '')} mins",
                    "distance_km": float(it.get("distanceKm") or 0.0),
                }
            )
        if items:
            return items

    # 2. Parse text lines (e.g. "1. Alfredo Pasta — ₹399 | Veg | 4.1★ | 1441 Pizzeria (restaurantId: 1360438) (ID: 198579135)")
    raw_text = res.get("text") or ""
    for line in raw_text.splitlines():
        line = line.strip()
        m = ITEM_LINE_REGEX.match(line)
        if m:
            dish_name, price_str, veg_str, rating_str, rest_name, rest_id, item_id = m.groups()
            items.append(
                {
                    "menu_item_id": str(item_id),
                    "dish_name": dish_name.strip(),
                    "price_inr": float(price_str),
                    "is_veg": veg_str.lower() == "veg",
                    "dish_rating": rating_str,
                    "restaurant_id": str(rest_id),
                    "restaurant_name": rest_name.strip(),
                    "restaurant_rating": float(rating_str) if rating_str else 4.0,
                    "delivery_time": "30-40 mins",
                    "distance_km": 2.0,
                }
            )
    return items


def _parse_search_restaurants(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Robust parser for search_restaurants."""
    data = res.get("data")
    if isinstance(data, dict) and "restaurants" in data and isinstance(data["restaurants"], list):
        return data["restaurants"]

    raw_text = res.get("text") or ""
    try:
        clean = raw_text.split("⚠️")[0].strip()
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "restaurants" in parsed:
            return parsed["restaurants"]
    except Exception:
        pass
    return []


def search_smart_restaurants(
    query: str = "lunch",
    max_delivery_mins: int = 50,
) -> Dict[str, Any]:
    """
    Smart Restaurant Search:
    1. Fetches restaurants matching query.
    2. De-prioritizes sponsored Ads.
    3. Calculates a Composite Quality Score (Rating 40%, Speed 25%, Value 35%).
    4. Automatically peeks into top restaurants to tag their #1 lowest-cost bestseller item.

    Args:
        query: Cuisine, dish, or restaurant keyword (e.g. 'Italian', 'Pizza', 'North Indian', 'Healthy').
        max_delivery_mins: Maximum acceptable delivery SLA in minutes.
    """
    res = SWIGGY.call_tool("search_restaurants", {"addressId": STATE.active_address_id, "query": query})
    raw_rests = _parse_search_restaurants(res)

    scored_rests: List[Dict[str, Any]] = []
    for r in raw_rests:
        name = str(r.get("name", ""))
        is_ad = "(ad)" in name.lower() or bool(r.get("isAd", False))
        clean_name = re.sub(r"\s*\(Ad\)", "", name, flags=re.IGNORECASE).strip()

        rating = float(r.get("avgRating") or 3.8)
        sla_mins = int(r.get("deliveryTimeMinutes") or 40)
        dist_km = float(r.get("distanceKm") or 2.5)

        if sla_mins > max_delivery_mins:
            continue

        # Extract numeric cost for two
        c2_str = str(r.get("costForTwo", "₹400 for two"))
        c2_match = re.search(r"₹?(\d+)", c2_str)
        cost_for_two = int(c2_match.group(1)) if c2_match else 400

        # Composite Score Calculation
        rating_score = min(rating / 5.0, 1.0) * 40.0
        speed_score = max(0.0, (60.0 - sla_mins) / 60.0) * 25.0
        # Value factor (lower cost gets higher score, up to 35 pts)
        value_score = max(5.0, (1200 - min(cost_for_two, 1200)) / 1200.0 * 35.0)
        ad_penalty = 12.0 if is_ad else 0.0

        total_score = round(rating_score + speed_score + value_score - ad_penalty, 1)

        scored_rests.append(
            {
                "restaurant_id": str(r.get("id", "")),
                "name": clean_name,
                "is_sponsored_ad": is_ad,
                "composite_score": total_score,
                "rating": rating,
                "total_ratings": r.get("totalRatings", ""),
                "cost_for_two": f"₹{cost_for_two} for two",
                "area": r.get("areaName", ""),
                "distance_km": dist_km,
                "delivery_time": r.get("deliveryTimeRange") or f"{sla_mins} mins",
                "cuisines": r.get("cuisines", [])[:3],
                "status": r.get("availabilityStatus", "OPEN"),
            }
        )

    scored_rests.sort(key=lambda x: -x["composite_score"])

    # De-clone Ghost Kitchens: If multiple brands share the exact same (area, distance_km)
    # dark-kitchen shed, keep the #1 highest-rated brand in the primary list and tag sister clones.
    cluster_map: Dict[str, List[str]] = {}
    decloned_rests: List[Dict[str, Any]] = []
    for r in scored_rests:
        shed_key = f"{str(r.get('area', '')).strip().lower()}_{r.get('distance_km')}"
        if shed_key not in cluster_map:
            cluster_map[shed_key] = [r["name"]]
            r["ghost_kitchen_clones_hidden"] = []
            decloned_rests.append(r)
        else:
            cluster_map[shed_key].append(r["name"])
            for primary in decloned_rests:
                p_key = f"{str(primary.get('area', '')).strip().lower()}_{primary.get('distance_km')}"
                if p_key == shed_key:
                    primary["ghost_kitchen_clones_hidden"].append(r["name"])
                    break

    top_candidates = decloned_rests[:8]

    # Quick peek: Fetch cheapest bestseller for top 3 restaurants in parallel
    def _peek_menu(rid: str) -> Optional[Dict[str, Any]]:
        m_res = SWIGGY.call_tool("get_restaurant_menu", {"addressId": STATE.active_address_id, "restaurantId": rid})
        raw_m = m_res.get("data")
        if isinstance(raw_m, dict) and "categories" in raw_m:
            items_found = []
            for cat in raw_m.get("categories", []):
                for it in cat.get("items", []):
                    items_found.append(
                        {
                            "name": it.get("name"),
                            "price": float(it.get("price") or 9999),
                            "menu_item_id": str(it.get("id", "")),
                            "is_veg": bool(it.get("isVeg", False)),
                        }
                    )
            if items_found:
                items_found.sort(key=lambda x: x["price"])
                return items_found[0]
        return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_idx = {
            pool.submit(_peek_menu, top_candidates[i]["restaurant_id"]): i
            for i in range(min(3, len(top_candidates)))
        }
        for fut, idx in future_to_idx.items():
            try:
                top_candidates[idx]["cheapest_item"] = fut.result()
            except Exception:
                pass

    return {
        "ok": True,
        "query": query,
        "active_address": STATE.active_address_label,
        "ranked_restaurants": top_candidates,
    }


def find_gourmet_steals(
    craving: str = "gourmet",
    max_landed_budget_inr: int = 260,
    veg_only: bool = False,
) -> Dict[str, Any]:
    """
    💎 Gourmet Arbitrage Engine:
    Finds single-serve meals from high-end, artisanal, and boutique restaurants
    (Toscano, Kitchen Garden, Pizza By The Bay, Smoke House Deli, Cafe Basilico, Boojee Cafe, etc.)
    with base prices ₹280-₹450 where Swiggy promo coupons and free delivery drop the
    final payment down into the ₹200-₹260 sweet spot.

    Args:
        craving: Optional specific craving (e.g. 'pasta', 'pizza', 'sandwich', 'salad', 'gourmet').
        max_landed_budget_inr: Target final payment threshold in INR (default 260).
        veg_only: Set True for vegetarian-only items.
    """
    search_queries = ["pasta", "sandwich", "pizza", "bowl", "salad", "burger"]
    clean_c = craving.strip().lower()
    if clean_c and clean_c not in ("gourmet", "all", "any") and clean_c not in search_queries:
        search_queries.insert(0, clean_c)

    def _fetch_menu_query(q: str) -> List[Dict[str, Any]]:
        r = SWIGGY.call_tool("search_menu", {"addressId": STATE.active_address_id, "query": q})
        return _parse_search_menu_items(r)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_fetch_menu_query, q) for q in search_queries[:5]]
        all_dish_lists = [f.result() for f in futures]

    seen_ids = set()
    gourmet_candidates: List[Dict[str, Any]] = []

    for d_list in all_dish_lists:
        for it in d_list:
            mid = it["menu_item_id"]
            if not mid or mid in seen_ids:
                continue

            r_name = it["restaurant_name"].lower()
            is_premium_brand = any(pk in r_name for pk in PREMIUM_BRAND_KEYWORDS)
            rest_rating = it.get("restaurant_rating") or 4.0

            # Consider either known premium brands OR any 4.3+ star boutique restaurant
            if not (is_premium_brand or rest_rating >= 4.3):
                continue

            price = it["price_inr"]
            # Target single-serve items in the ₹220-₹450 bracket where 40-50% off drops it to ≤ 260
            if price > 460 or price < 180:
                continue
            if veg_only and not it.get("is_veg"):
                continue

            # Estimate landed cost after 40% discount + taxes/free delivery
            # In Mumbai Swiggy: typical discount is ~40-50% (capped at ₹80-₹100)
            est_discount = min(price * 0.40, 100.0)
            est_landed = round((price - est_discount) + (price * 0.05), 0)

            if est_landed > max_landed_budget_inr:
                continue

            seen_ids.add(mid)
            gourmet_candidates.append(
                {
                    "menu_item_id": mid,
                    "dish_name": it["dish_name"],
                    "original_price_inr": int(price),
                    "estimated_landed_inr": int(est_landed),
                    "is_veg": it["is_veg"],
                    "dish_rating": it["dish_rating"],
                    "restaurant_id": it["restaurant_id"],
                    "restaurant_name": it["restaurant_name"],
                    "restaurant_rating": rest_rating,
                    "delivery_time": it["delivery_time"],
                    "distance_km": it["distance_km"],
                    "savings_tag": f"~₹{int(price - est_landed)} Saved via Coupons",
                }
            )

    # Sort by highest perceived quality delta
    gourmet_candidates.sort(key=lambda x: (x["estimated_landed_inr"], -float(x["restaurant_rating"])))

    return {
        "ok": True,
        "active_address": STATE.active_address_label,
        "target_landed_budget": f"≤ ₹{max_landed_budget_inr}",
        "gourmet_steals": gourmet_candidates[:12],
    }


def find_cheapest_lunch_deals(
    craving_or_category: str = "lunch",
    max_budget_inr: int = 300,
    veg_only: bool = False,
    min_restaurant_rating: float = 3.9,
) -> Dict[str, Any]:
    """
    Parallel Deal Hunter: Scans thalis, biryanis, mealboxes, and BOGO/80% off promos
    to find the cheapest high-rated everyday lunch meals.
    """
    dish_queries = ["thali", "biryani", "combo", "lunchbox"]
    clean_craving = craving_or_category.strip().lower()
    if clean_craving and clean_craving not in ("lunch", "food", "meal", "any") and clean_craving not in dish_queries:
        dish_queries.insert(0, craving_or_category.strip())

    def _fetch_dish_query(q: str) -> List[Dict[str, Any]]:
        r = SWIGGY.call_tool("search_menu", {"addressId": STATE.active_address_id, "query": q})
        return _parse_search_menu_items(r)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {q: pool.submit(_fetch_dish_query, q) for q in dish_queries}
        dish_results = {q: fut.result() for q, fut in futures.items()}

    seen_item_ids = set()
    candidates: List[Dict[str, Any]] = []

    for q, items in dish_results.items():
        for it in items:
            mid = it["menu_item_id"]
            if not mid or mid in seen_item_ids:
                continue
            price = it["price_inr"]
            is_veg = it["is_veg"]
            rest_rating = it.get("restaurant_rating") or 4.0

            if price > max_budget_inr:
                continue
            if veg_only and not is_veg:
                continue
            if rest_rating and rest_rating < min_restaurant_rating:
                continue

            seen_item_ids.add(mid)
            candidates.append(
                {
                    "menu_item_id": mid,
                    "dish_name": it["dish_name"],
                    "price_inr": int(price),
                    "is_veg": is_veg,
                    "dish_rating": it["dish_rating"],
                    "restaurant_id": it["restaurant_id"],
                    "restaurant_name": it["restaurant_name"],
                    "restaurant_rating": rest_rating,
                    "delivery_time": it["delivery_time"],
                    "distance_km": it["distance_km"],
                    "matched_category": q,
                }
            )

    candidates.sort(key=lambda x: (x["price_inr"], -float(x.get("restaurant_rating") or 0)))

    return {
        "ok": True,
        "active_address": STATE.active_address_label,
        "top_cheapest_meals": candidates[:12],
    }


def get_full_restaurant_menu(
    restaurant_id: str,
    max_item_price_inr: int = 500,
    veg_only: bool = False,
) -> Dict[str, Any]:
    """Fetches the live menu of a specific restaurant and extracts its best items."""
    res = SWIGGY.call_tool("get_restaurant_menu", {"addressId": STATE.active_address_id, "restaurantId": str(restaurant_id)})
    if not res.get("ok"):
        return res

    data = res.get("data", {})
    if not isinstance(data, dict):
        return {"ok": True, "raw": data}

    rest_info = data.get("restaurant", {})
    categories = data.get("categories", [])
    seen_ids = set()
    all_items: List[Dict[str, Any]] = []

    for cat in categories:
        cat_title = str(cat.get("title", "Menu"))
        raw_cat_items = list(cat.get("items", []))
        for sub in cat.get("subcategories", []):
            raw_cat_items.extend(sub.get("items", []))
        for item in raw_cat_items:
            mid = str(item.get("id", ""))
            if not mid or mid in seen_ids:
                continue
            price = float(item.get("price") or 9999)
            is_veg = bool(item.get("isVeg", False))
            if price > max_item_price_inr:
                continue
            if veg_only and not is_veg:
                continue
            seen_ids.add(mid)
            all_items.append(
                {
                    "menu_item_id": mid,
                    "dish_name": item.get("name"),
                    "price_inr": int(price) if price.is_integer() else price,
                    "category": cat_title,
                    "is_veg": is_veg,
                    "is_bestseller": bool(item.get("isBestseller", False)),
                    "rating": item.get("rating"),
                }
            )

    all_items.sort(key=lambda x: x["price_inr"])
    return {
        "ok": True,
        "restaurant": {
            "restaurant_id": str(rest_info.get("id") or restaurant_id),
            "name": rest_info.get("name"),
            "area": rest_info.get("areaName"),
            "rating": rest_info.get("avgRating"),
            "cost_for_two": rest_info.get("costForTwoMessage"),
            "delivery_time": rest_info.get("slaString"),
        },
        "cheapest_items": all_items[:15],
    }


def search_dishes_across_restaurants(
    query: str,
    max_price_inr: int = 500,
    veg_only: bool = False,
) -> Dict[str, Any]:
    """Searches for specific dishes across all nearby restaurants and sorts by lowest price."""
    res = SWIGGY.call_tool("search_menu", {"addressId": STATE.active_address_id, "query": query})
    items = _parse_search_menu_items(res)
    filtered = [
        it for it in items
        if it["price_inr"] <= max_price_inr and (not veg_only or it["is_veg"])
    ]
    filtered.sort(key=lambda x: (x["price_inr"], -float(x.get("restaurant_rating") or 0)))
    return {
        "ok": True,
        "query": query,
        "active_address": STATE.active_address_label,
        "cheapest_dishes": filtered[:15],
    }
