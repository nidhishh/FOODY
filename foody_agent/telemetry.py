"""
Zero-PII Latency Telemetry & Performance Instrumentation for FOODY.
Tracks P50/P95 latencies, MCP call counts, cache hits, coalesced hits,
and Gemini LLM token consumption while strictly enforcing privacy rules:
NEVER logs or retains customer names, phone numbers, GPS coordinates,
exact addresses, payment VPAs, or raw OAuth tokens.
"""

from __future__ import annotations

import hashlib
import statistics
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


def mask_address_id(address_id: str) -> str:
    """Returns an opaque, non-reversible SHA-256 prefix for address IDs."""
    if not address_id:
        return "addr#none"
    digest = hashlib.sha256(address_id.encode("utf-8")).hexdigest()[:8]
    return f"addr#{digest}"


@dataclass
class SpanRecord:
    name: str
    duration_ms: float
    ok: bool
    is_cache_hit: bool = False
    is_coalesced: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class TelemetryRegistry:
    """Thread-safe telemetry store recording tool, LLM, and workflow metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: List[SpanRecord] = []
        self._tool_latencies: Dict[str, List[float]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._coalesced_hits: int = 0
        self._prefetch_hits: int = 0
        self._llm_roundtrips: int = 0
        self._llm_prompt_tokens: int = 0
        self._llm_completion_tokens: int = 0
        self._intent_router_hits: int = 0
        self._llm_fallback_hits: int = 0

    def record_span(
        self,
        name: str,
        duration_ms: float,
        ok: bool = True,
        is_cache_hit: bool = False,
        is_coalesced: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            # Strip any accidental customer PII from metadata
            safe_meta: Dict[str, Any] = {}
            if metadata:
                for k, v in metadata.items():
                    k_low = k.lower()
                    if any(bad in k_low for bad in ("name", "phone", "email", "address", "token", "lat", "lng", "vpa", "upi")):
                        if "addressid" in k_low or k == "address_id":
                            safe_meta[k] = mask_address_id(str(v))
                        continue
                    safe_meta[k] = v

            record = SpanRecord(
                name=name,
                duration_ms=round(duration_ms, 2),
                ok=ok,
                is_cache_hit=is_cache_hit,
                is_coalesced=is_coalesced,
                metadata=safe_meta,
            )
            self._spans.append(record)
            if len(self._spans) > 2000:
                self._spans = self._spans[-1000:]

            if name not in self._tool_latencies:
                self._tool_latencies[name] = []
            self._tool_latencies[name].append(duration_ms)
            if len(self._tool_latencies[name]) > 500:
                self._tool_latencies[name] = self._tool_latencies[name][-300:]

            if is_cache_hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

            if is_coalesced:
                self._coalesced_hits += 1

    def record_prefetch_hit(self) -> None:
        with self._lock:
            self._prefetch_hits += 1

    def record_llm_call(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self._llm_roundtrips += 1
            self._llm_prompt_tokens += prompt_tokens
            self._llm_completion_tokens += completion_tokens

    def record_router_hit(self, is_fast_path: bool) -> None:
        with self._lock:
            if is_fast_path:
                self._intent_router_hits += 1
            else:
                self._llm_fallback_hits += 1

    @contextmanager
    def measure(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Context manager to measure synchronous latency of a block."""
        ctx: Dict[str, Any] = {"is_cache_hit": False, "is_coalesced": False, "ok": True}
        start = time.perf_counter()
        try:
            yield ctx
        except Exception:
            ctx["ok"] = False
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.record_span(
                name=name,
                duration_ms=elapsed_ms,
                ok=ctx.get("ok", True),
                is_cache_hit=ctx.get("is_cache_hit", False),
                is_coalesced=ctx.get("is_coalesced", False),
                metadata=metadata,
            )

    def get_summary(self) -> Dict[str, Any]:
        """Calculates rolling P50, P95, hit rates, and token efficiency."""
        with self._lock:
            total_cache_ops = self._cache_hits + self._cache_misses
            hit_rate_pct = round((self._cache_hits / total_cache_ops * 100.0), 1) if total_cache_ops > 0 else 0.0

            total_routed = self._intent_router_hits + self._llm_fallback_hits
            fast_path_pct = round((self._intent_router_hits / total_routed * 100.0), 1) if total_routed > 0 else 0.0

            tool_stats: Dict[str, Dict[str, float]] = {}
            for tool_name, samples in self._tool_latencies.items():
                if not samples:
                    continue
                sorted_samples = sorted(samples)
                n = len(sorted_samples)
                p50 = sorted_samples[int(n * 0.50)]
                p95 = sorted_samples[min(int(n * 0.95), n - 1)]
                tool_stats[tool_name] = {
                    "count": float(n),
                    "p50_ms": round(p50, 1),
                    "p95_ms": round(p95, 1),
                    "min_ms": round(sorted_samples[0], 1),
                    "max_ms": round(sorted_samples[-1], 1),
                    "mean_ms": round(statistics.mean(sorted_samples), 1),
                }

            return {
                "cache": {
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate_pct": hit_rate_pct,
                    "coalesced_hits": self._coalesced_hits,
                    "speculative_prefetch_hits": self._prefetch_hits,
                },
                "intent_router": {
                    "fast_path_hits": self._intent_router_hits,
                    "llm_fallback_hits": self._llm_fallback_hits,
                    "fast_path_pct": fast_path_pct,
                },
                "llm": {
                    "roundtrips": self._llm_roundtrips,
                    "prompt_tokens": self._llm_prompt_tokens,
                    "completion_tokens": self._llm_completion_tokens,
                    "total_tokens": self._llm_prompt_tokens + self._llm_completion_tokens,
                },
                "tools": tool_stats,
            }

    def format_perf_card(self) -> str:
        """Generates a compact Telegram-ready performance report."""
        summary = self.get_summary()
        c = summary["cache"]
        r = summary["intent_router"]
        llm = summary["llm"]
        tools = summary["tools"]

        lines = [
            "⚡ *FOODY Telemetry & Latency Dashboard*",
            f"• *Cache & Network:* {c['hit_rate_pct']}% Hit Rate ({c['hits']} hits, {c['misses']} misses | Coalesced: {c['coalesced_hits']})",
            f"• *Fast-Path Router:* {r['fast_path_pct']}% 0-LLM Fast Path ({r['fast_path_hits']} fast, {r['llm_fallback_hits']} LLM | Tokens: {llm['total_tokens']})",
        ]

        # Top 3 heaviest/most frequent tools
        top_tools = sorted(tools.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
        if top_tools:
            tool_strs = [f"`{name}`: P50={st['p50_ms']}ms / P95={st['p95_ms']}ms" for name, st in top_tools]
            lines.append("• *Tool Latencies:* " + " · ".join(tool_strs))

        return "\n".join(lines)


TELEMETRY = TelemetryRegistry()

