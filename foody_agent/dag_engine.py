"""
Parallel Async Execution Engine (DAG) & Speculative Prefetcher for FOODY.
Handles bounded concurrent tool fan-out with per-node deadlines,
graceful straggler degradation, and background cache warming.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, TypeVar

from .config import STATE
from .swiggy_client import SWIGGY
from .telemetry import TELEMETRY

logger = logging.getLogger("foody.dag")
T = TypeVar("T")


def parallel_fanout_with_deadline(
    tasks: Dict[str, Callable[[], T]],
    deadline_seconds: float = 2.5,
    max_workers: int = 6,
) -> Dict[str, Optional[T]]:
    """
    Executes multiple independent synchronous tasks in parallel.
    Enforces a hard deadline: returns all results that complete within the deadline
    and drops any stragglers, preventing tail latency spikes.
    """
    results: Dict[str, Optional[T]] = {name: None for name in tasks}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {executor.submit(fn): name for name, fn in tasks.items()}
        done, not_done = concurrent.futures.wait(
            future_to_name.keys(),
            timeout=deadline_seconds,
            return_when=concurrent.futures.ALL_COMPLETED,
        )

        for fut in done:
            name = future_to_name[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                logger.debug("Task '%s' failed in parallel fan-out: %s", name, exc)
                results[name] = None

        for fut in not_done:
            name = future_to_name[fut]
            logger.info("Dropping straggler task '%s' after deadline %.1fs", name, deadline_seconds)
            fut.cancel()

    return results


def _prefetch_worker(restaurant_ids: List[str], address_id: str) -> None:
    """Background worker thread warming menus and coupons for top candidate restaurants."""
    for rid in restaurant_ids[:3]:
        if not rid:
            continue
        try:
            # Prefetch page 1 menu
            SWIGGY.call_tool(
                "get_restaurant_menu",
                {"addressId": address_id, "restaurantId": str(rid), "page": 1},
                use_cache=True,
            )
            # Prefetch coupons
            SWIGGY.call_tool(
                "fetch_food_coupons",
                {"addressId": address_id, "restaurantId": str(rid)},
                use_cache=True,
            )
        except Exception as exc:
            logger.debug("Speculative prefetch error for restaurant %s: %s", rid, exc)


def trigger_speculative_prefetch(restaurant_ids: List[str]) -> None:
    """
    Fires a non-blocking background thread to prefetch menus and coupons
    for the top 3 displayed restaurants. Does NOT block user response.
    """
    if not restaurant_ids:
        return
    addr_id = STATE.active_address_id
    t = threading.Thread(
        target=_prefetch_worker,
        args=(restaurant_ids[:3], addr_id),
        name=f"foody-prefetch-{len(restaurant_ids)}",
        daemon=True,
    )
    t.start()

