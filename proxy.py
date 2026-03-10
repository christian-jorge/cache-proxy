from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import json

app = FastAPI(title="Anthropic Cache Proxy")

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


@app.get("/")
async def health():
    return {"status": "ok", "service": "anthropic-cache-proxy"}


@app.post("/debug")
async def debug_transform(request: Request):
    """Mostra como o proxy transforma o body SEM enviar para Anthropic"""
    body = await request.json()
    original_system_type = type(body.get("system")).__name__

    if isinstance(body.get("system"), str) and body["system"].strip():
        body["system"] = [
            {
                "type": "text",
                "text": body["system"][:100] + "...(truncado)",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif isinstance(body.get("system"), list) and body["system"]:
        body["system"][-1]["cache_control"] = {"type": "ephemeral"}

    if body.get("tools") and isinstance(body["tools"], list):
        body["tools"][-1]["cache_control"] = {"type": "ephemeral"}

    return {
        "original_system_type": original_system_type,
        "transformed_system": body.get("system"),
        "tools_count": len(body.get("tools", [])),
        "last_tool_has_cache": "cache_control" in (body.get("tools") or [{}])[-1],
    }


@app.post("/v1/messages")
async def proxy_messages(request: Request):
    body = await request.json()

    # Converte system string → array com cache_control
    if isinstance(body.get("system"), str) and body["system"].strip():
        body["system"] = [
            {
                "type": "text",
                "text": body["system"],
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif isinstance(body.get("system"), list) and body["system"]:
        # Garante cache_control no último bloco do array
        body["system"][-1]["cache_control"] = {"type": "ephemeral"}

    # Injeta cache_control na última tool
    if body.get("tools") and isinstance(body["tools"], list):
        body["tools"][-1]["cache_control"] = {"type": "ephemeral"}

    # Monta headers para Anthropic
    api_key = (
        request.headers.get("x-api-key")
        or request.headers.get("authorization", "").replace("Bearer ", "")
    )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    # Repassa streaming se solicitado
    is_streaming = body.get("stream", False)

    async with httpx.AsyncClient(timeout=300) as client:
        if is_streaming:
            async def stream_response():
                async with client.stream(
                    "POST",
                    f"{ANTHROPIC_URL}/v1/messages",
                    json=body,
                    headers=headers,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
            )
        else:
            resp = await client.post(
                f"{ANTHROPIC_URL}/v1/messages",
                json=body,
                headers=headers,
            )
            return JSONResponse(
                content=resp.json(),
                status_code=resp.status_code,
            )
