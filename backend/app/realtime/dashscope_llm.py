from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..logging_utils import get_logger

logger = get_logger("dashscope_llm")

DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


async def stream_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Stream chat completions from DashScope OpenAI-compatible API.

    Yields text deltas as they arrive.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": False},
        # 关闭深度思考模式，否则 qwen3.5-flash 首字延迟高达 16~24s
        "enable_thinking": False,
    }

    logger.info(
        "llm_stream_start model=%s messages_count=%s",
        model,
        len(messages),
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        async with client.stream("POST", DASHSCOPE_CHAT_URL, headers=headers, json=payload) as response:
            if response.status_code != 200:
                body = await response.aread()
                logger.error("llm_stream_error status=%s body=%s", response.status_code, body[:500])
                raise RuntimeError(f"LLM request failed: {response.status_code}")

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content

    logger.info("llm_stream_end model=%s", model)
