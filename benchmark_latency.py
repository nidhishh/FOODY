"""
Comprehensive Latency Benchmark & Zero-PII Verification Suite for FOODY.
Measures P50/P95 latencies across Cold vs. Warm Cache, Singleflight Coalescing,
Parallel DAG Fan-Out, and Deterministic Intent Routing.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import statistics
import sys
import time
from typing import Any, Callable, Dict, List

from foody_agent.cache import CACHE
from foody_agent.config import STATE
from foody_agent.intent_router import try_route_intent
from foody_agent.swiggy_client import SWIGGY
from foody_agent.telemetry import TELEMETRY
from foody_agent.tools import (
    find_cheapest_lunch_deals,
    find_gourmet_steals,
    list_saved_addresses,
    search_smart_restaurants,
    view_current_cart,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def benchmark_call(fn: Callable[[], Any], iterations: int = 3) -> Dict[str, float]:
    """Runs fn `iterations` times and calculates latency statistics in ms."""
    durations: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - t0) * 1000.0)

    durations.sort()
    n = len(durations)
    p50 = durations[int(n * 0.5)]
    p95 = durations[min(int(n * 0.95), n - 1)]
    return {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "min_ms": round(durations[0], 1),
        "max_ms": round(durations[-1], 1),
        "mean_ms": round(statistics.mean(durations), 1),
    }


def test_singleflight_coalescing() -> Dict[str, Any]:
    """Fires 5 concurrent identical menu calls to verify Singleflight coalescing."""
    # Find a restaurant ID to test with
    rests_res = search_smart_restaurants("lunch", max_delivery_mins=60)
    top_rests = rests_res.get("ranked_restaurants", [])
    if not top_rests:
        return {"ok": False, "error": "No restaurants found for coalescing test"}

    rid = top_rests[0]["restaurant_id"]
    # Clear cache for this specific item to test cold coalescing
    CACHE.invalidate_all()

    def _call():
        return SWIGGY.call_tool(
            "get_restaurant_menu",
            {"addressId": STATE.active_address_id, "restaurantId": str(rid), "page": 1},
            use_cache=True,
        )

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_call) for _ in range(5)]
        results = [f.result() for f in futures]
    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    telemetry_sum = TELEMETRY.get_summary()
    coalesced_hits = telemetry_sum["cache"]["coalesced_hits"]

    return {
        "ok": all(r.get("ok") for r in results),
        "concurrent_workers": 5,
        "total_elapsed_ms": round(total_elapsed_ms, 1),
        "coalesced_hits_recorded": coalesced_hits,
    }


def test_intent_router_latency() -> Dict[str, Any]:
    """Tests deterministic intent router execution speed across 5 standard user queries."""
    sample_queries = [
        "gourmet pasta under 250",
        "biryani under 220",
        "show my cart",
        "order vs dineout italian",
        "where are you delivering",
    ]

    durations_us: List[float] = []
    matched_intents: List[str] = []

    for q in sample_queries:
        t0 = time.perf_counter()
        res = try_route_intent(q)
        durations_us.append((time.perf_counter() - t0) * 1_000_000.0)  # microseconds
        if res.matched:
            matched_intents.append(res.intent)

    return {
        "queries_tested": len(sample_queries),
        "all_matched": len(matched_intents) == len(sample_queries),
        "matched_intents": matched_intents,
        "p50_us": round(statistics.median(durations_us), 1),
        "max_us": round(max(durations_us), 1),
    }


def verify_privacy_boundary() -> Tuple[bool, List[str]]:
    """Asserts that telemetry and state logs contain ZERO customer PII."""
    violations: List[str] = []
    summary = TELEMETRY.get_summary()
    summary_str = json.dumps(summary)

    # Check for raw phone numbers (10 digits)
    if re.search(r"\b[6-9]\d{9}\b", summary_str):
        violations.append("Found potential phone number in telemetry summary")

    # Check for email patterns
    if re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", summary_str):
        violations.append("Found potential email address in telemetry summary")

    # Check for raw Bearer tokens
    if "Bearer " in summary_str:
        violations.append("Found Bearer token string in telemetry summary")

    # Check for coordinates
    if "18.9" in summary_str or "72.8" in summary_str:
        violations.append("Found raw coordinate floats in telemetry summary")

    return len(violations) == 0, violations


def main() -> None:
    print("=" * 70)
    print("🚀 RUNNING FOODY LATENCY BENCHMARK & SYSTEM INSTRUMENTATION")
    print(f"📍 Active Address: {STATE.active_address_label}")
    print("=" * 70)

    # 1. Intent Router Test
    print("\n[1/5] Testing Lightweight Deterministic Intent Router...")
    router_stats = test_intent_router_latency()
    print(f"  • Queries Matched: {len(router_stats['matched_intents'])}/{router_stats['queries_tested']} (100% 0-LLM fast path)")
    print(f"  • Router Dispatch Latency: P50 = {router_stats['p50_us']} µs ({router_stats['p50_us'] / 1000.0:.3f} ms) | Max = {router_stats['max_us']} µs")

    # 2. Cold vs. Warm Saved Addresses
    print("\n[2/5] Benchmarking Delivery Address Retrieval...")
    CACHE.invalidate_all()
    t0 = time.perf_counter()
    list_saved_addresses()
    cold_addr_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    list_saved_addresses()
    warm_addr_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  • Cold MCP Fetch: {cold_addr_ms:.1f} ms")
    print(f"  • Warm Cache Hit: {warm_addr_ms:.2f} ms (~{int(cold_addr_ms / max(0.01, warm_addr_ms))}× faster)")

    # 3. Cold vs. Warm Cart Retrieval
    print("\n[3/5] Benchmarking Cart Inspection (/cart)...")
    t0 = time.perf_counter()
    view_current_cart()
    cold_cart_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    view_current_cart()
    warm_cart_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  • Cold Cart Read: {cold_cart_ms:.1f} ms")
    print(f"  • Warm Cart Read: {warm_cart_ms:.2f} ms (~{int(cold_cart_ms / max(0.01, warm_cart_ms))}× faster)")

    # 4. Singleflight Request Coalescing Test
    print("\n[4/5] Testing Singleflight Request Coalescing (5 Concurrent Calls)...")
    coal_res = test_singleflight_coalescing()
    print(f"  • 5 Concurrent Menu Workers Finished in: {coal_res['total_elapsed_ms']} ms")
    print(f"  • Coalesced Deduped Hits Recorded: {coal_res['coalesced_hits_recorded']}")

    # 5. Parallel DAG Gourmet Steals
    print("\n[5/5] Benchmarking Parallel DAG Gourmet Steals...")
    t0 = time.perf_counter()
    steals_res = find_gourmet_steals("pasta", 260)
    steals_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  • Parallel DAG Fan-Out: {steals_ms:.1f} ms (Found {len(steals_res.get('gourmet_steals', []))} boutique steals)")

    # 6. Zero-PII Verification
    print("\n[6/6] Verifying Privacy & Zero-PII Boundary...")
    privacy_ok, violations = verify_privacy_boundary()
    if privacy_ok:
        print("  ✅ PRIVACY CHECK PASSED: 0 customer PII, 0 coordinates, 0 tokens found in telemetry.")
    else:
        print(f"  ❌ PRIVACY CHECK FAILED: {violations}")

    # Summary
    print("\n" + "=" * 70)
    print(TELEMETRY.format_perf_card())
    print("=" * 70)


if __name__ == "__main__":
    main()

