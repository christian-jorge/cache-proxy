# Anthropic Cache Proxy

Proxy transparente que injeta `cache_control` automaticamente em requests para a API da Anthropic.

## Problema resolvido

O n8n (via LangChain) envia o `system` como string pura, mas a Anthropic só aceita `cache_control` em array de content blocks. Este proxy faz a conversão automaticamente antes de repassar o request.

## O que ele faz

- Converte `system` de string → array com `cache_control: ephemeral`
- Injeta `cache_control` na última tool (cacheia todas as tools juntas)
- Suporta streaming e requests normais
- Repassa a API key do header original

## Resultado esperado

Primeira chamada:
```json
"cache_creation_input_tokens": 13000,
"cached_tokens": 0
```

Chamadas seguintes:
```json
"cache_creation_input_tokens": 0,
"cached_tokens": 13000
```

## Deploy no Coolify

1. Cria novo serviço → Public Git Repository
2. Aponta para este repo
3. Port: `8080`
4. Adiciona domínio (ex: `proxy.seudominio.com.br`)
5. Deploy

## Configuração no n8n

Nas credentials do Anthropic, troca a base URL para:
```
https://proxy.seudominio.com.br
```

A API key continua sendo a da Anthropic — o proxy repassa ela automaticamente.
