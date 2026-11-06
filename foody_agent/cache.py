"""
Session-Aware TTL Cache, Dependency-Based Invalidation, and Singleflight Request Coalescing.
Strictly ensures zero customer PII is cached or leaked:
Keys are hashed using tool name, arguments, and masked address hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple

from .telemetry import TELEMETRY, mask_address_id

# TTL policy in seconds by MCP tool name
TOOL_TTL_SECONDS: Dict[str, float] = {
    "get_addresses": 300.0,  # 5 minutes: delivery addresses rarely change mid-session
    "get_restaurant_menu": 180.0,  # 3 minutes: menu & prices
    "fetch_food_coupons": 120.0,  # 2 minutes: coupons
    "search_restaurants": 120.0,  # 2 minutes
    "search_menu": 120.0,  # 2 minutes
    "search_restaurants_dineout": 120.0,  # 2 minutes
    "get_restaurant_details": 180.0,  # 3 minutes
    "get_available_slots": 90.0,  # 1.5 minutes
    "get_food_cart": 15.0,  # 15 seconds: short TTL to eliminate duplicate back-to-back reads
    "get_payment_options": 60.0,  # 1 minute
}

# Non-cacheable mutating tools
MUTATING_TOOLS = {
    "update_food_cart",
    "flush_food_cart",
    "apply_food_coupon",
    "place_food_order",
    "book_table",
    "confirm_order",
    "create_address",
    "delete_address",
}


def make_cache_key(server: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Creates a deterministic, PII-safe cache key string."""
    args = arguments or {}
    clean_dict: Dict[str, Any] = {}
    for k, v in sorted(args.items()):
        k_low = k.lower()
        if "addressid" in k_low or k == "address_id":
            clean_dict[k] = mask_address_id(str(v))
        elif k_low in ("query", "craving"):
            clean_dict[k] = str(v).strip().lower()
        elif k_low in ("restaurantid", "slotid", "page", "limit", "date"):
            clean_dict[k] = str(v).strip()
        elif k_low in ("latitude", "longitude"):
            clean_dict[k] = round(float(v), 3) if isinstance(v, (int, float)) else str(v)
        else:
            clean_dict[k] = v

    serialized = json.dumps(clean_dict, sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{server}:{tool_name}:{digest}"


class _Flight:
    __slots__ = ("event", "val", "exc")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.val: Any = None
        self.exc: Optional[Exception] = None


class SingleflightCoalescer:
    """
    Deduplicates concurrent identical requests (Singleflight pattern).
    If multiple callers request the same key concurrently, only one actual
    worker executes the remote network call; all callers receive the exact same result.
    """

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        self._sync_in_flight: Dict[str, _Flight] = {}
        self._async_in_flight: Dict[str, asyncio.Future] = {}

    def run_sync(self, key: str, fn: Callable[[], Any]) -> Tuple[Any, bool]:
        """
        Executes fn() synchronously. Returns (result, is_coalesced).
        """
        with self._sync_lock:
            if key in self._sync_in_flight:
                flight = self._sync_in_flight[key]
                first_caller = False
            else:
                flight = _Flight()
                self._sync_in_flight[key] = flight
                first_caller = True

        if not first_caller:
            flight.event.wait(timeout=25.0)
            if flight.exc is not None:
                raise flight.exc
            return flight.val, True

        try:
            val = fn()
            flight.val = val
            return val, False
        except Exception as exc:
            flight.exc = exc
            raise
        finally:
            flight.event.set()
            with self._sync_lock:
                self._sync_in_flight.pop(key, None)

    async def run_async(self, key: str, coro_fn: Callable[[], Coroutine[Any, Any, Any]]) -> Tuple[Any, bool]:
        """
        Executes coro_fn() asynchronously. Returns (result, is_coalesced).
        """
        loop = asyncio.get_running_loop()
        first_caller = False

        if key in self._async_in_flight:
            future = self._async_in_flight[key]
        else:
            future = loop.create_future()
            self._async_in_flight[key] = future
            first_caller = True

        if not first_caller:
            result = await future
            return result, True

        try:
            result = await coro_fn()
            if not future.done():
                future.set_result(result)
            return result, False
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._async_in_flight.pop(key, None)



    def execute_sync(self, key: str, fn: Callable[[], Any]) -> Tuple[Any, bool]:
        return self.run_sync(key, fn)

    async def execute_async(self, key: str, coro_fn: Callable[[], Coroutine[Any, Any, Any]]) -> Tuple[Any, bool]:
        return await self.run_async(key, coro_fn)


class TTLCache:
    """Thread-safe in-memory cache with dependency-based invalidation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[float, Any, str, str]] = {}  # key -> (expire_time, val, tool_name, server)
        self.singleflight = SingleflightCoalescer()

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            if key not in self._entries:
                return None
            expire_at, val, _, _ = self._entries[key]
            if now > expire_at:
                del self._entries[key]
                return None
            return val

    def set(
        self,
        key: str,
        value: Any,
        server: str = "food",
        tool_name: str = "",
        ttl_seconds: Optional[float] = None,
    ) -> None:
        # If caller passed tool_name before server or vice versa
        t_name = tool_name or server
        ttl = ttl_seconds if ttl_seconds is not None else TOOL_TTL_SECONDS.get(t_name, 60.0)
        expire_at = time.time() + ttl
        with self._lock:
            self._entries[key] = (expire_at, value, t_name, server)

    def invalidate_all(self) -> int:
        """Clears all cached entries across all tools."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    def invalidate_tool(self, tool_name: str) -> int:
        """Invalidates all entries for a specific tool."""
        count = 0
        with self._lock:
            to_del = [k for k, (_, _, t_name, _) in self._entries.items() if t_name == tool_name]
            for k in to_del:
                del self._entries[k]
                count += 1
        return count

    def invalidate_cart_and_coupons(self) -> None:
        """Called whenever the cart is modified, cleared, or coupon applied."""
        self.invalidate_tool("get_food_cart")
        self.invalidate_tool("get_payment_options")

    invalidate_cart_cache = invalidate_cart_and_coupons

    def invalidate_address_change(self) -> None:
        """Called when the active delivery address changes."""
        with self._lock:
            to_del = [
                k for k, (_, _, t_name, srv) in self._entries.items()
                if t_name in ("get_food_cart", "fetch_food_coupons", "search_restaurants", "search_menu", "get_restaurant_menu")
            ]
            for k in to_del:
                del self._entries[k]

    invalidate_address_dependent_cache = invalidate_address_change


GLOBAL_CACHE = TTLCache()
CACHE = GLOBAL_CACHE
SINGLEFLIGHT = GLOBAL_CACHE.singleflight

# Aliases for compatibility
build_cache_key = make_cache_key
MUTATION_TOOLS = MUTATING_TOOLS

