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


# No FastAPI routes; Poke will connect to the MCP endpoint directly.


async def _call_poke(payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {POKE_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Hardcoded inbound SMS webhook endpoint expects {"message": ...}
        response = await client.post(POKE_INBOUND_WEBHOOK_URL, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


@mcp.tool(description="Forward a chat message and context from the frontend to Poke and return the response")
async def forward_message(
    conversation_id: str,
    user_id: str,
    message: str,
    picks_context: Dict[str, Any],
    frontend_token: str,
) -> Dict[str, Any]:
    if FRONTEND_SHARED_SECRET and frontend_token != FRONTEND_SHARED_SECRET:
        raise PermissionError("Invalid frontend token")

    # Align with inbound SMS webhook shape
    payload = {"message": message}

    poke_response = await _call_poke(payload)
    return {
        "conversation_id": conversation_id,
        "response": poke_response,
    }


@mcp.tool(description="Simple health check to verify server readiness")
def health() -> Dict[str, str]:
    return {"status": "ok", "environment": os.environ.get("ENVIRONMENT", "development")}


@mcp.tool(description="Send a test message to Poke for testing the bridge")
async def test_poke_message(message: str) -> Dict[str, Any]:
    """Send a simple test message to Poke and return the response"""
    payload = {"message": message}
    
    try:
        poke_response = await _call_poke(payload)
        return {
            "status": "success",
            "sent_message": message,
            "poke_response": poke_response,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "sent_message": message,
            "error": str(e),
            "timestamp": datetime.datetime.utcnow().isoformat()
        }


@mcp.tool(description="Send a raw JSON payload to Poke inbound SMS webhook for debugging")
async def send_raw_to_poke(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        poke_response = await _call_poke(payload)
        return {"status": "success", "payload": payload, "poke_response": poke_response}
    except Exception as e:
        return {"status": "error", "payload": payload, "error": str(e)}


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
