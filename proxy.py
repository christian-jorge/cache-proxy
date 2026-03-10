import sys
import re
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

app = FastAPI(title="Anthropic Cache Proxy")

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# Padrões de conteúdo dinâmico para separar do cache
DYNAMIC_PATTERNS = [
    # Data/hora em português e inglês
    r'\*\*Data Hoje\*\*:.*',
    r'\*\*Data atual\*\*:.*',
    r'Data:?\s+\w+,?\s+\w+\s+\d+.*\d{4}.*',
    r'Today\'s date:.*',
    r'Current date:.*',
    # Tags de sistema com data
    r'<sistema>.*?</sistema>',
]

def split_static_dynamic(text: str):
    """
    Separa o system prompt em parte estática (cacheável) e dinâmica (data/hora).
    Retorna (static_part, dynamic_part).
    """
    dynamic_parts = []
    static_text = text

    # Tenta extrair tags <sistema> ou similares com conteúdo dinâmico
    sistema_match = re.search(r'(<sistema>.*?</sistema>)', static_text, re.DOTALL)
    if sistema_match:
        sistema_block = sistema_match.group(1)
        # Verifica se o bloco contém data/hora
        if re.search(r'\d{4}.*\d{2}:\d{2}|\w+day,?\s+\w+\s+\d+', sistema_block):
            dynamic_parts.append(sistema_block.strip())
            static_text = static_text.replace(sistema_block, '').strip()
            return static_text, '\n\n'.join(dynamic_parts)

    # Fallback: procura linhas com padrões de data
    lines = text.split('\n')
    static_lines = []
    dynamic_lines = []
    
    for line in lines:
        is_dynamic = False
        for pattern in DYNAMIC_PATTERNS[:-1]:  # Exclui o padrão de tag (já tratado acima)
            if re.search(pattern, line):
                is_dynamic = True
                break
        if is_dynamic:
            dynamic_lines.append(line)
        else:
            static_lines.append(line)

    if dynamic_lines:
        return '\n'.join(static_lines).strip(), '\n'.join(dynamic_lines).strip()
    
    return text, None


def build_system_blocks(system_text: str):
    """
    Constrói array de blocos do system:
    - Bloco 1: parte estática com cache_control
    - Bloco 2: parte dinâmica (data/hora) sem cache_control (se existir)
    """
    static_part, dynamic_part = split_static_dynamic(system_text)
    
    blocks = [
        {
            "type": "text",
            "text": static_part,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    
    if dynamic_part:
        blocks.append({
            "type": "text",
            "text": dynamic_part,
        })
        print(f"[PROXY] system dividido: estático={len(static_part)} chars, dinâmico={len(dynamic_part)} chars", file=sys.stderr, flush=True)
    else:
        print(f"[PROXY] system sem parte dinâmica detectada", file=sys.stderr, flush=True)

    return blocks


@app.get("/")
async def health():
    return {"status": "ok", "service": "anthropic-cache-proxy"}


@app.post("/debug")
async def debug_transform(request: Request):
    body = await request.json()
    original_system_type = type(body.get("system")).__name__
    
    system_text = body.get("system", "")
    if isinstance(system_text, str):
        static_part, dynamic_part = split_static_dynamic(system_text)
    else:
        static_part, dynamic_part = str(system_text), None

    if body.get("tools") and isinstance(body["tools"], list):
        body["tools"][-1]["cache_control"] = {"type": "ephemeral"}

    return {
        "original_system_type": original_system_type,
        "static_chars": len(static_part),
        "dynamic_part": dynamic_part,
        "has_dynamic": dynamic_part is not None,
        "tools_count": len(body.get("tools", [])),
        "last_tool_has_cache": "cache_control" in (body.get("tools") or [{}])[-1],
    }


@app.post("/v1/messages")
async def proxy_messages(request: Request):
    body = await request.json()

    print(f"[PROXY] model: {body.get('model')}", file=sys.stderr, flush=True)
    print(f"[PROXY] system type entrada: {type(body.get('system')).__name__}", file=sys.stderr, flush=True)

    # Converte system
    if isinstance(body.get("system"), str) and body["system"].strip():
        body["system"] = build_system_blocks(body["system"])
        print(f"[PROXY] system convertido para array com cache_control", file=sys.stderr, flush=True)
    elif isinstance(body.get("system"), list) and body["system"]:
        body["system"][-1]["cache_control"] = {"type": "ephemeral"}
        print(f"[PROXY] cache_control injetado no ultimo bloco do array", file=sys.stderr, flush=True)

    # Injeta cache_control na última tool
    if body.get("tools") and isinstance(body["tools"], list):
        body["tools"][-1]["cache_control"] = {"type": "ephemeral"}
        print(f"[PROXY] cache_control injetado na ultima tool ({len(body['tools'])} tools)", file=sys.stderr, flush=True)

    if isinstance(body.get("system"), list):
        total_chars = sum(len(b.get("text", "")) for b in body["system"])
        print(f"[PROXY] system chars: {total_chars} (~{total_chars//4} tokens estimados)", file=sys.stderr, flush=True)

    # Monta headers
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
