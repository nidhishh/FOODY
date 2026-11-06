"""
Persistent HTTP/2 JSON-RPC 2.0 client for Swiggy MCP (`/food` and `/dineout`).
Optimized with:
1. Singleton persistent HTTP connection pools (httpx.Limits with keep-alive).
2. Session-aware TTL caching & singleflight request coalescing.
3. Deadline enforcement & safe retries for read-only tools.
4. Tool-output compression to minimize LLM token usage.
5. Zero-PII latency telemetry instrumentation.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .cache import CACHE, MUTATION_TOOLS, build_cache_key
from .config import SWIGGY_DINEOUT_MCP_URL, SWIGGY_FOOD_MCP_URL, get_valid_bearer_token
from .telemetry import TELEMETRY

logger = logging.getLogger("foody.client")


class SwiggyMCPClient:
    """High-performance sync and async client for Swiggy's HTTP MCP endpoints."""

    def __init__(self, endpoint: str = SWIGGY_FOOD_MCP_URL, server: str = "food") -> None:
        self.endpoint = endpoint
        self.server = server
        self._req_id = 1

        # Persistent connection pools
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=120.0)
        timeout = httpx.Timeout(connect=4.0, read=14.0, write=4.0, pool=4.0)

        self._sync_client: Optional[httpx.Client] = httpx.Client(
            limits=limits,
            timeout=timeout,
            http2=False,  # Swiggy MCP handles HTTP/1.1 keep-alive reliably
        )
        self._async_client: Optional[httpx.AsyncClient] = None
        self._limits = limits
        self._timeout = timeout

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                limits=self._limits,
                timeout=self._timeout,
                http2=False,
            )
        return self._async_client

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> Dict[str, str]:
        token = get_valid_bearer_token(self.server)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"foody-google-adk-{self.server}/2.0",
        }

    def _parse_response(self, resp: httpx.Response, tool_name: str) -> Dict[str, Any]:
        if resp.status_code == 401:
            raise RuntimeError(
                "Swiggy MCP returned 401 Unauthorized. Please run `npx swiggy-cli auth init` to refresh your login."
            )
        resp.raise_for_status()

        text_body = resp.text.strip()
        if text_body.startswith("event:") or text_body.startswith("data:"):
            lines = [
                line[len("data:") :].strip()
                for line in text_body.splitlines()
                if line.startswith("data:")
            ]
            payload = json.loads(lines[-1]) if lines else {}
        else:
            payload = resp.json()

        if "error" in payload:
            err = payload["error"]
            return {
                "ok": False,
                "tool": tool_name,
                "error": err.get("message", str(err)) if isinstance(err, dict) else str(err),
            }

        result = payload.get("result", {})
        is_error = bool(result.get("isError", False))
        structured = result.get("structuredContent")

        text_parts: List[str] = []
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                clean_text = item.get("text", "").split("⚠️ A rich UI widget")[0].strip()
                clean_text = clean_text.split("Cart widget is displayed")[0].strip()
                text_parts.append(clean_text)

        combined_text = "\n".join(text_parts).strip()

        if is_error:
            return {
                "ok": False,
                "tool": tool_name,
                "error": combined_text or "Tool returned an error",
                "structured": structured,
            }

        return {
            "ok": True,
            "tool": tool_name,
            "text": combined_text,
            "data": structured if structured is not None else combined_text,
        }

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Synchronous tool call with caching, singleflight coalescing,
        persistent connection pooling, and latency telemetry.
        """
        is_mutation = tool_name in MUTATION_TOOLS
        cache_key = build_cache_key(self.server, tool_name, arguments)

        # 1. Fast Cache Read
        if use_cache and not is_mutation:
            cached_val = CACHE.get(cache_key)
            if cached_val is not None:
                TELEMETRY.record_span(
                    name=tool_name,
                    duration_ms=0.1,
                    ok=True,
                    is_cache_hit=True,
                )
                return cached_val

        # 2. Invalidate cache on mutations
        if is_mutation:
            if tool_name in ("update_food_cart", "apply_food_coupon", "flush_food_cart", "place_food_order"):
                CACHE.invalidate_cart_cache()

        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        def _do_http_call() -> Dict[str, Any]:
            client = self._sync_client or httpx.Client(limits=self._limits, timeout=self._timeout)
            retries = 1 if not is_mutation else 0
            for attempt in range(retries + 1):
                try:
                    resp = client.post(self.endpoint, headers=self._headers(), json=body)
                    parsed = self._parse_response(resp, tool_name)
                    return parsed
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                    if attempt < retries:
                        time.sleep(0.12)
                        continue
                    raise

        start_time = time.perf_counter()
        if not is_mutation:
            # Singleflight request coalescing for read-only calls
            parsed, is_coalesced = CACHE.singleflight.execute_sync(cache_key, _do_http_call)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            if parsed.get("ok"):
                CACHE.set(cache_key, parsed, self.server, tool_name)

            TELEMETRY.record_span(
                name=tool_name,
                duration_ms=elapsed_ms,
                ok=parsed.get("ok", False),
                is_cache_hit=False,
                is_coalesced=is_coalesced,
            )
            return parsed
        else:
            # Mutations run directly without coalescing
            parsed = _do_http_call()
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            TELEMETRY.record_span(
                name=tool_name,
                duration_ms=elapsed_ms,
                ok=parsed.get("ok", False),
                is_cache_hit=False,
                is_coalesced=False,
            )
            return parsed

    async def call_tool_async(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Asynchronous tool call with persistent async HTTP connection pooling,
        caching, singleflight coalescing, and telemetry.
        """
        is_mutation = tool_name in MUTATION_TOOLS
        cache_key = build_cache_key(self.server, tool_name, arguments)

        # 1. Fast Cache Read
        if use_cache and not is_mutation:
            cached_val = CACHE.get(cache_key)
            if cached_val is not None:
                TELEMETRY.record_span(
                    name=tool_name,
                    duration_ms=0.1,
                    ok=True,
                    is_cache_hit=True,
                )
                return cached_val

        # 2. Invalidate cache on mutations
        if is_mutation:
            if tool_name in ("update_food_cart", "apply_food_coupon", "flush_food_cart", "place_food_order"):
                CACHE.invalidate_cart_cache()

        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        async def _do_async_http_call() -> Dict[str, Any]:
            client = self._get_async_client()
            retries = 1 if not is_mutation else 0
            for attempt in range(retries + 1):
                try:
                    resp = await client.post(self.endpoint, headers=self._headers(), json=body)
                    parsed = self._parse_response(resp, tool_name)
                    return parsed
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                    if attempt < retries:
                        await asyncio.sleep(0.12)
                        continue
                    raise

        start_time = time.perf_counter()
        if not is_mutation:
            parsed, is_coalesced = await CACHE.singleflight.execute_async(cache_key, _do_async_http_call)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            if parsed.get("ok"):
                CACHE.set(cache_key, parsed, self.server, tool_name)

            TELEMETRY.record_span(
                name=tool_name,
                duration_ms=elapsed_ms,
                ok=parsed.get("ok", False),
                is_cache_hit=False,
                is_coalesced=is_coalesced,
            )
            return parsed
        else:
            parsed = await _do_async_http_call()
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            TELEMETRY.record_span(
                name=tool_name,
                duration_ms=elapsed_ms,
                ok=parsed.get("ok", False),
                is_cache_hit=False,
                is_coalesced=False,
            )
            return parsed


SWIGGY = SwiggyMCPClient(endpoint=SWIGGY_FOOD_MCP_URL, server="food")
SWIGGY_DINEOUT = SwiggyMCPClient(endpoint=SWIGGY_DINEOUT_MCP_URL, server="dineout")
