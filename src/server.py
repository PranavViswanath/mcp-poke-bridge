#!/usr/bin/env python3
"""Minimal MCP server that proxies chat messages between a frontend and Poke."""

from __future__ import annotations

import os
from typing import Any, Dict
import json
import datetime

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastapi import FastAPI, Request


# Try load_dotenv first, fallback to manual loading if needed (Windows BOM issue)
load_dotenv()
if not os.environ.get('POKE_API_KEY'):
    try:
        with open('.env', 'r', encoding='utf-8-sig') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value
    except FileNotFoundError:
        pass

POKE_API_BASE_URL = os.environ.get("POKE_API_BASE_URL", "https://poke.com/api/v1")
POKE_API_KEY = os.environ.get("POKE_API_KEY")
FRONTEND_SHARED_SECRET = os.environ.get("FRONTEND_SHARED_SECRET")
POKE_CHANNEL = os.environ.get("POKE_CHANNEL", "whatsapp")
POKE_INBOUND_WEBHOOK_URL = "https://poke.com/api/v1/inbound-sms/webhook"

"""No webhook server needed when Poke connects via MCP directly."""


if POKE_API_KEY is None:
    raise RuntimeError("POKE_API_KEY environment variable must be set")


mcp = FastMCP("PrizePicks Poke Bridge")


# Expose a FastAPI app so we can accept webhooks from Poke
try:
    app = mcp.app  # FastMCP usually exposes its FastAPI app here
except AttributeError:
    app = FastAPI()
    # Best-effort: attach for runtimes that read mcp.app
    try:
        setattr(mcp, "app", app)
    except Exception:
        pass


async def _call_poke(payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {POKE_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Hardcoded inbound SMS webhook endpoint expects {"message": ...}
        response = await client.post(POKE_INBOUND_WEBHOOK_URL, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


last_response: Dict[str, Any] | None = None


@mcp.tool(description="Simple health check to verify server readiness")
def health() -> Dict[str, str]:
    return {"status": "ok", "environment": os.environ.get("ENVIRONMENT", "development")}


@mcp.tool(description="Send a message to Poke and record the latest response")
async def message_poke(message: str) -> Dict[str, Any]:
    """Send a message to Poke and return the immediate API response. Also stores it as last_response."""
    global last_response
    payload = {"message": message}
    try:
        poke_response = await _call_poke(payload)
        last_response = {
            "status": "success",
            "sent_message": message,
            "poke_response": poke_response,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    except Exception as e:
        last_response = {
            "status": "error",
            "sent_message": message,
            "error": str(e),
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    return last_response

@mcp.tool(description="Get the most recent response from message_poke")
def get_response() -> Dict[str, Any]:
    return {"last_response": last_response}


@app.post("/webhook")
async def poke_webhook(request: Request) -> Dict[str, Any]:
    """Webhook endpoint for asynchronous responses from Poke.

    Poke should POST JSON here when it generates a reply (e.g., a WhatsApp reply).
    We store the payload in-memory so `get_response` can surface it.
    """
    global last_response
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw_body": (await request.body()).decode("utf-8", errors="replace")}

    last_response = {
        "status": "received",
        "source": "poke_webhook",
        "poke_payload": payload,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    return {"ok": True}


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    HOST = "0.0.0.0"

    print(f"Starting MCP server on {HOST}:{PORT}")

    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
        stateless_http=True,
    )