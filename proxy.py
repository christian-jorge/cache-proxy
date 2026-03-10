import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

app = FastAPI(title="Anthropic Cache Proxy")

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


@app.get("/")
async def health():
    return {"status": "ok", "service": "anthropic-cache-proxy"}


@app.post("/debug")
async def debug_transform(request: Request):
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

    print(f"[PROXY] model: {body.get('model')}", file=sys.stderr, flush=True)
    print(f"[PROXY] system type entrada: {type(body.get('system')).__name__}", file=sys.stderr, flush=True)

    if isinstance(body.get("system"), str) and body["system"].strip():
        body["system"] = [
            {
                "type": "text",
                "text": body["system"],
                "cache_control": {"type": "ephemeral"},
            }
        ]
        print(f"[PROXY] system convertido para array com cache_control", file=sys.stderr, flush=True)
    elif isinstance(body.get("system"), list) and body["system"]:
        body["system"][-1]["cache_control"] = {"type": "ephemeral"}
        print(f"[PROXY] cache_control injetado no ultimo bloco do array", file=sys.stderr, flush=True)

    if body.get("tools") and isinstance(body["tools"], list):
        body["tools"][-1]["cache_control"] = {"type": "ephemeral"}
        print(f"[PROXY] cache_control injetado na ultima tool ({len(body['tools'])} tools)", file=sys.stderr, flush=True)

    if isinstance(body.get("system"), list):
        total_chars = sum(len(b.get("text", "")) for b in body["system"])
        print(f"[PROXY] system chars: {total_chars} (~{total_chars//4} tokens estimados)", file=sys.stderr, flush=True)

    api_key = (
        request.headers.get("x-api-key")
        or request.headers.get("authorization", "").replace("Bearer ", "")
    )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

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
            response_data = resp.json()

            usage = response_data.get("usage", {})
            print(f"[PROXY] cache_creation_input_tokens: {usage.get('cache_creation_input_tokens', 'N/A')}", file=sys.stderr, flush=True)
            print(f"[PROXY] cache_read_input_tokens: {usage.get('cache_read_input_tokens', 'N/A')}", file=sys.stderr, flush=True)
            print(f"[PROXY] input_tokens: {usage.get('input_tokens', 'N/A')}", file=sys.stderr, flush=True)

            return JSONResponse(
                content=response_data,
                status_code=resp.status_code,
            )
