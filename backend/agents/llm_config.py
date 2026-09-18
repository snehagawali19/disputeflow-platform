"""Shared LLM configuration used by all agents. No silent provider fallback."""

from __future__ import annotations

import json
from typing import Any

from backend.core.config import get_settings


def get_llm():
    """Return the configured real LLM instance for the selected provider only."""
    settings = get_settings()
    settings.require_llm_credentials()

    if settings.llm_provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.groq_model,
            temperature=0.1,
            max_tokens=4096,
            api_key=settings.groq_api_key,
        )

    from langchain_openai import ChatOpenAI

    if settings.llm_provider == "openrouter":
        return ChatOpenAI(
            model=settings.openrouter_model,
            temperature=0.1,
            max_tokens=4096,
            api_key=settings.resolved_openrouter_api_key,
            base_url=settings.openrouter_api_base,
        )

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=0.1,
        max_tokens=4096,
        api_key=settings.openai_api_key,
    )


async def invoke_structured(schema, system_prompt: str, user_prompt: str):
    """Invoke the selected LLM and validate one bounded JSON object.

    OpenRouter models vary in tool-calling support. JSON mode with explicit
    validation is more portable and prevents malformed tool arguments from
    being copied into case logs.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    settings = get_settings()
    if settings.llm_provider != "openrouter":
        return await get_llm().with_structured_output(schema).ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )

    schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    strict_system = (
        f"{system_prompt}\nReturn exactly one JSON object matching this schema. "
        "No markdown, commentary, duplicate fields, or trailing text.\n"
        f"Schema: {schema_json}"
    )
    llm = get_llm().bind(response_format={"type": "json_object"})
    last_error: Exception | None = None
    for attempt in range(settings.llm_max_retries + 1):
        prompt = user_prompt if attempt == 0 else f"{user_prompt}\nPrevious output was invalid. Return concise valid JSON only."
        try:
            response = await llm.ainvoke(
                [SystemMessage(content=strict_system), HumanMessage(content=prompt)]
            )
            content: Any = response.content
            if isinstance(content, list):
                content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
            return schema.model_validate_json(str(content).strip())
        except Exception as exc:
            last_error = exc
    raise RuntimeError(compact_llm_error(last_error or RuntimeError("Unknown structured-output error")))


def compact_llm_error(exc: Exception, limit: int = 500) -> str:
    """Return a single-line diagnostic without preserving huge model output."""
    message = " ".join(str(exc).split())
    if len(message) > limit:
        message = f"{message[:limit].rstrip()}…"
    return f"{type(exc).__name__}: {message}"
