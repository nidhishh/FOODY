"""
Deal Hunter & Menu Discovery tools for the FOODY Google ADK agent.
Includes a parallel scanner that cross-references dish prices and discount restaurants.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ..config import STATE
from ..swiggy_client import SWIGGY


def search_restaurants_by_query(query: str) -> Dict[str, Any]:
    """
    Searches for restaurants delivering to the active address matching a keyword,
    cuisine, or promo tag (e.g. '80% off', '60% off', 'BOGO', 'biryani', 'pizza', 'thali').

    Args:
        query: Search term, cuisine, restaurant name, or promo tag.
    """
    res = SWIGGY.call_tool(
        "search_restaurants",
        {
            "addressId": STATE.active_address_id,
            "query": query,
        },
    )
    if not res.get("ok"):
        return res

    data = res.get("data", {})
    restaurants = data.get("restaurants", []) if isinstance(data, dict) else []
    cleaned = []
    for r in restaurants[:12]:
        cleaned.append(
            {
                "restaurant_id": str(r.get("id", "")),
                "name": r.get("name"),
                "cuisines": r.get("cuisines", []),
                "rating": r.get("avgRating"),
                "total_ratings": r.get("totalRatings"),
                "cost_for_two": r.get("costForTwo"),
                "area": r.get("areaName"),
                "distance_km": r.get("distanceKm"),
                "delivery_time": r.get("deliveryTimeRange") or f"{r.get('deliveryTimeMinutes')} mins",
                "status": r.get("availabilityStatus"),
            }
        )
    return {
        "ok": True,
        "query": query,
        "active_address": STATE.active_address_label,
        "restaurants": cleaned,
    }


def search_dishes_across_restaurants(
    query: str,
    max_price_inr: int = 500,
    veg_only: bool = False,
) -> Dict[str, Any]:
    """
    Searches for specific dishes/menu items across all nearby restaurants and sorts them
    by lowest price first.

    Args:
        query: Dish name or food type (e.g. 'Maharaja Mac', 'thali', 'chicken biryani', 'paneer').
        max_price_inr: Maximum dish price in INR (default 500).
        veg_only: If True, only returns vegetarian dishes.
    """
    res = SWIGGY.call_tool(
        "search_menu",
        {
            "addressId": STATE.active_address_id,
            "query": query,
        },
    )
    if not res.get("ok"):
        return res

    data = res.get("data", {})
    items = data.get("items", []) if isinstance(data, dict) else []

    filtered: List[Dict[str, Any]] = []
    for it in items:
        price = float(it.get("price") or 9999)
        is_veg = bool(it.get("isVeg", False))
        if price > max_price_inr:
            continue
        if veg_only and not is_veg:
            continue
        filtered.append(
            {
                "menu_item_id": str(it.get("menu_item_id", "")),
                "dish_name": it.get("name"),
                "price_inr": int(price) if price.is_integer() else price,
                "is_veg": is_veg,
                "dish_rating": it.get("rating"),
                "is_bestseller": bool(it.get("isBestseller", False)),
                "restaurant_id": str(it.get("restaurant_id", "")),
                "restaurant_name": it.get("restaurant_name"),
                "restaurant_rating": it.get("restaurant_rating"),
                "delivery_time": it.get("deliveryTimeRange"),
                "distance_km": it.get("distanceKm"),
            }
        )

    filtered.sort(key=lambda x: (x["price_inr"], -float(x.get("restaurant_rating") or 0)))

    return {
        "ok": True,
        "query": query,
        "active_address": STATE.active_address_label,
        "count": len(filtered),
        "cheapest_dishes": filtered[:15],
    }


def get_full_restaurant_menu(
    restaurant_id: str,
    max_item_price_inr: int = 400,
    veg_only: bool = False,
) -> Dict[str, Any]:
    """
    Fetches the live menu of a specific restaurant and extracts its cheapest dishes and bestsellers.

    Args:
        restaurant_id: The numeric Swiggy restaurant ID (e.g. '117392', '9869').
        max_item_price_inr: Filter items up to this price in INR (default 400).
        veg_only: If True, only returns vegetarian items.
    """
    res = SWIGGY.call_tool(
        "get_restaurant_menu",
        {
            "addressId": STATE.active_address_id,
            "restaurantId": str(restaurant_id),
        },
    )
    if not res.get("ok"):
        return res

    data = res.get("data", {})
    if not isinstance(data, dict):
        return {"ok": True, "raw": data}

    rest_info = data.get("restaurant", {})
    categories = data.get("categories", [])

    seen_ids = set()
    all_items: List[Dict[str, Any]] = []

    def _collect(items_list: List[Dict[str, Any]], cat_title: str) -> None:
        for item in items_list:
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
                    "description": str(item.get("description") or "")[:100],
                }
            )

    for cat in categories:
        cat_title = str(cat.get("title", "Menu"))
        _collect(cat.get("items", []), cat_title)
        for sub in cat.get("subcategories", []) or []:
            _collect(sub.get("items", []), f"{cat_title} > {sub.get('title', '')}")

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
        "bestsellers": [i for i in all_items if i.get("is_bestseller")][:10],
    }


def find_cheapest_lunch_deals(
    craving_or_category: str = "lunch",
    max_budget_inr: int = 300,
    veg_only: bool = False,
    min_restaurant_rating: float = 3.9,
) -> Dict[str, Any]:
    """
    Parallel Deal Hunter: Scans multiple lunch categories ('thali', 'biryani', 'meal',
    'combo', 'pulao', plus the user's craving) AND checks '80% off' & 'BOGO' promo
    restaurants simultaneously to find the absolute cheapest, highest-value meals near
    the user's active address.

    Args:
        craving_or_category: Specific craving (e.g. 'chicken', 'thali', 'high protein', 'pizza', 'lunch').
        max_budget_inr: Maximum item price in INR (default 300).
        veg_only: Set True to strictly filter for vegetarian meals.
        min_restaurant_rating: Minimum acceptable Swiggy restaurant rating (default 3.9).
    """
    dish_queries = ["thali", "biryani", "combo", "lunchbox"]
    clean_craving = craving_or_category.strip().lower()
    if clean_craving and clean_craving not in ("lunch", "food", "meal", "any") and clean_craving not in dish_queries:
        dish_queries.insert(0, craving_or_category.strip())

    promo_queries = ["80% off", "BOGO"]

    def _fetch_dish_query(q: str) -> List[Dict[str, Any]]:
        r = SWIGGY.call_tool(
            "search_menu",
            {"addressId": STATE.active_address_id, "query": q},
        )
        if not r.get("ok"):
            return []
        d = r.get("data", {})
        return d.get("items", []) if isinstance(d, dict) else []

    def _fetch_promo_rests(q: str) -> List[Dict[str, Any]]:
        r = SWIGGY.call_tool(
            "search_restaurants",
            {"addressId": STATE.active_address_id, "query": q},
        )
        if not r.get("ok"):
            return []
        d = r.get("data", {})
        return d.get("restaurants", []) if isinstance(d, dict) else []

    with ThreadPoolExecutor(max_workers=6) as pool:
        dish_futures = {q: pool.submit(_fetch_dish_query, q) for q in dish_queries}
        promo_futures = {q: pool.submit(_fetch_promo_rests, q) for q in promo_queries}

    promo_rest_ids: Dict[str, str] = {}
    promo_restaurants_summary: List[Dict[str, Any]] = []
    for tag, fut in promo_futures.items():
        for rest in fut.result()[:8]:
            rid = str(rest.get("id", ""))
            if rid and rid not in promo_rest_ids:
                promo_rest_ids[rid] = tag
                promo_restaurants_summary.append(
                    {
                        "restaurant_id": rid,
                        "name": rest.get("name"),
                        "promo_bucket": tag,
                        "rating": rest.get("avgRating"),
                        "cost_for_two": rest.get("costForTwo"),
                        "delivery_time": rest.get("deliveryTimeRange"),
                    }
                )

    seen_item_ids = set()
    candidates: List[Dict[str, Any]] = []

    for q, fut in dish_futures.items():
        for it in fut.result():
            mid = str(it.get("menu_item_id", ""))
            if not mid or mid in seen_item_ids:
                continue
            price = float(it.get("price") or 9999)
            is_veg = bool(it.get("isVeg", False))
            rest_rating = float(it.get("restaurant_rating") or 0.0)

            if price > max_budget_inr:
                continue
            if veg_only and not is_veg:
                continue
            if rest_rating and rest_rating < min_restaurant_rating:
                continue

            seen_item_ids.add(mid)
            rid = str(it.get("restaurant_id", ""))
            candidates.append(
                {
                    "menu_item_id": mid,
                    "dish_name": it.get("name"),
                    "price_inr": int(price) if price.is_integer() else price,
                    "is_veg": is_veg,
                    "dish_rating": it.get("rating"),
                    "is_bestseller": bool(it.get("isBestseller", False)),
                    "restaurant_id": rid,
                    "restaurant_name": it.get("restaurant_name"),
                    "restaurant_rating": rest_rating,
                    "delivery_time": it.get("deliveryTimeRange"),
                    "distance_km": it.get("distanceKm"),
                    "promo_tag": promo_rest_ids.get(rid, "Value Price"),
                    "matched_category": q,
                }
            )

    candidates.sort(key=lambda x: (x["price_inr"], -float(x.get("restaurant_rating") or 0)))

    return {
        "ok": True,
        "active_address": STATE.active_address_label,
        "filters_applied": {
            "craving_or_category": craving_or_category,
            "max_budget_inr": max_budget_inr,
            "veg_only": veg_only,
            "min_restaurant_rating": min_restaurant_rating,
        },
        "top_cheapest_meals": candidates[:12],
        "promo_80_off_and_bogo_restaurants": promo_restaurants_summary[:8],
    }

