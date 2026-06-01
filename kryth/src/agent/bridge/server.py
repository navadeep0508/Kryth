"""Local provider bridge server.

Exposes an OpenAI-compatible REST + WebSocket API on localhost so the
existing agent/llm.py can talk to browser-based providers (Gemini,
Claude, OpenAI web) without any API key.

Endpoints:
    GET  /health                    — liveness check
    GET  /v1/models                 — list available models
    POST /v1/chat/completions       — chat (streaming or non-streaming)
    WS   /v1/stream                 — WebSocket streaming endpoint

Run directly:
    python -m agent.bridge.server --port 8765 --provider gemini

Or via the REPL:
    /bridge start --provider gemini
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

# FastAPI + WebSockets
try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import StreamingResponse, JSONResponse
    import uvicorn
except ImportError:
    raise ImportError(
        "Bridge server requires fastapi and uvicorn.\n"
        "Install with: pip install fastapi uvicorn"
    )

from pydantic import BaseModel

from agent.env import getenv, getenv_bool, setenv
from agent.bridge.providers import get_provider
from agent.bridge.providers.base import BaseProvider, ProviderError
from agent.bridge.session_store import is_authenticated

logger = logging.getLogger("aicoder.bridge.server")

# ---------------------------------------------------------------------------
# Global provider instance (one per server process)
# ---------------------------------------------------------------------------

_provider: Optional[BaseProvider] = None
_provider_name: str = "gemini"


async def _get_provider() -> BaseProvider:
    """Return the active provider, initialising it if needed."""
    global _provider
    if _provider is None:
        raise HTTPException(status_code=503, detail="Provider not initialised")
    return _provider


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int = 4096
    temperature: float = 0.0  # accepted but ignored (browser UI doesn't expose it)


# ---------------------------------------------------------------------------
# OpenAI-compatible response builders
# ---------------------------------------------------------------------------

def _make_chunk(content: str, model: str, finish: bool = False) -> dict:
    """Build an OpenAI-compatible streaming chunk."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": content} if not finish else {},
            "finish_reason": "stop" if finish else None,
        }],
    }


def _make_completion(content: str, model: str) -> dict:
    """Build an OpenAI-compatible non-streaming completion."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise the provider. Shutdown: tear it down."""
    global _provider, _provider_name

    provider_name = getenv("XEROCODEAI_BRIDGE_PROVIDER", _provider_name)
    headless = getenv_bool("XEROCODEAI_BRIDGE_HEADLESS")

    logger.info(f"[bridge] starting provider: {provider_name}")

    ProviderClass = get_provider(provider_name)
    _provider = ProviderClass(headless=headless)

    await _provider.setup()

    # Check if we need to authenticate
    if not await _provider.is_ready():
        logger.info(f"[bridge] {provider_name} needs authentication")
        await _provider.authenticate()

    logger.info(f"[bridge] {provider_name} provider ready")
    yield

    # Shutdown
    if _provider:
        await _provider.teardown()
    logger.info("[bridge] server shut down")


app = FastAPI(
    title="XerocodeAI Local Bridge",
    description="OpenAI-compatible local API bridge for browser-based providers",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness check — returns 200 when the server is ready."""
    provider = await _get_provider()
    return {
        "status": "ok",
        "provider": provider.name,
        "model": provider.default_model,
    }


@app.get("/v1/models")
async def list_models():
    """List available models — OpenAI-compatible format."""
    provider = await _get_provider()
    models = await provider.list_models()
    return {
        "object": "list",
        "data": [
            {
                "id": m,
                "object": "model",
                "created": int(time.time()),
                "owned_by": provider.name,
            }
            for m in models
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """OpenAI-compatible chat completions endpoint.

    Supports both streaming (stream=true) and non-streaming responses.
    """
    provider = await _get_provider()
    messages = [m.dict() for m in request.messages]
    model = request.model or provider.default_model

    if request.stream:
        # Streaming response — SSE format
        async def event_stream() -> AsyncIterator[str]:
            try:
                async for token in provider.chat_stream(
                    messages, model=model, max_tokens=request.max_tokens
                ):
                    chunk = _make_chunk(token, model)
                    yield f"data: {json.dumps(chunk)}\n\n"

                # Send the final [DONE] chunk
                done_chunk = _make_chunk("", model, finish=True)
                yield f"data: {json.dumps(done_chunk)}\n\n"
                yield "data: [DONE]\n\n"

            except ProviderError as e:
                error_chunk = {
                    "error": {"message": str(e), "type": "provider_error"}
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    else:
        # Non-streaming — collect all tokens and return at once
        full_response = ""
        try:
            async for token in provider.chat_stream(
                messages, model=model, max_tokens=request.max_tokens
            ):
                full_response += token
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))

        return JSONResponse(_make_completion(full_response, model))


@app.websocket("/v1/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket streaming endpoint.

    Client sends a JSON message:
        {"messages": [...], "model": "...", "max_tokens": 4096}

    Server streams back JSON chunks:
        {"token": "...", "done": false}
        {"token": "", "done": true}
    """
    await websocket.accept()
    provider = await _get_provider()

    try:
        # Receive the request
        data = await websocket.receive_json()
        messages = data.get("messages", [])
        model = data.get("model", provider.default_model)
        max_tokens = data.get("max_tokens", 4096)

        # Stream tokens back
        async for token in provider.chat_stream(
            messages, model=model, max_tokens=max_tokens
        ):
            await websocket.send_json({"token": token, "done": False})

        # Signal completion
        await websocket.send_json({"token": "", "done": True})

    except ProviderError as e:
        await websocket.send_json({"error": str(e), "done": True})
    except WebSocketDisconnect:
        logger.debug("[bridge] WebSocket client disconnected")
    except Exception as e:
        logger.error(f"[bridge] WebSocket error: {e}")
        try:
            await websocket.send_json({"error": str(e), "done": True})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth endpoint — re-run login for a provider
# ---------------------------------------------------------------------------

@app.post("/v1/auth/{provider_name}")
async def re_authenticate(provider_name: str):
    """Force re-authentication for a provider (clears saved session)."""
    from agent.bridge.session_store import clear_session
    clear_session(provider_name)

    provider = await _get_provider()
    if provider.name == provider_name:
        await provider.authenticate()
        return {"status": "authenticated", "provider": provider_name}

    raise HTTPException(
        status_code=400,
        detail=f"Active provider is '{provider.name}', not '{provider_name}'. "
               "Restart the bridge with --provider {provider_name}."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="XerocodeAI Local Provider Bridge")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument(
        "--provider", default="gemini",
        choices=["gemini", "claude", "openai"],
        help="Browser provider to use",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headlessly")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Pass config to the lifespan handler via env vars
    setenv("XEROCODEAI_BRIDGE_PROVIDER", args.provider)
    setenv("XEROCODEAI_BRIDGE_HEADLESS", "1" if args.headless else "0")

    print(f"\n  XerocodeAI Bridge starting on http://localhost:{args.port}")
    print(f"  Provider: {args.provider}")
    print(f"  OpenAI-compatible endpoint: http://localhost:{args.port}/v1\n")

    uvicorn.run(
        app,
        host="127.0.0.1",  # localhost only — never expose externally
        port=args.port,
        log_level="warning",  # suppress uvicorn's own verbose logs
    )


if __name__ == "__main__":
    main()
