"""
Direct HTTP JSON-RPC 2.0 client for Swiggy Food MCP (`https://mcp.swiggy.com/food`).
Uses OAuth 2.1 Bearer token from ~/.swiggy/auth.json with automatic token refresh.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from .config import SWIGGY_DINEOUT_MCP_URL, SWIGGY_FOOD_MCP_URL, get_valid_bearer_token


class SwiggyMCPClient:
    """Sync and Async client for Swiggy's Streamable HTTP MCP endpoints (Food, Dineout, Instamart)."""

    def __init__(self, endpoint: str = SWIGGY_FOOD_MCP_URL, server: str = "food") -> None:
        self.endpoint = endpoint
        self.server = server
        self._req_id = 1

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> Dict[str, str]:
        token = get_valid_bearer_token(self.server)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"foody-google-adk-{self.server}/1.0",
        }

    def _parse_response(self, resp: httpx.Response, tool_name: str) -> Dict[str, Any]:
        if resp.status_code == 401:
            raise RuntimeError(
                "Swiggy MCP returned 401 Unauthorized. Please run `npx swiggy-cli auth init` to refresh your login."
            )
        resp.raise_for_status()

        text_body = resp.text.strip()
        # Handle SSE stream if server ever responds with `data: {...}`
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
                # Strip widget boilerplate hints intended only for ChatGPT UI
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

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Synchronous MCP tool call (used by standard Google ADK function tools)."""
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(self.endpoint, headers=self._headers(), json=body)
            return self._parse_response(resp, tool_name)

    async def call_tool_async(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Asynchronous MCP tool call (used for parallel deal hunting & Telegram bot)."""
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(self.endpoint, headers=self._headers(), json=body)
            return self._parse_response(resp, tool_name)


SWIGGY = SwiggyMCPClient(endpoint=SWIGGY_FOOD_MCP_URL, server="food")
SWIGGY_DINEOUT = SwiggyMCPClient(endpoint=SWIGGY_DINEOUT_MCP_URL, server="dineout")

