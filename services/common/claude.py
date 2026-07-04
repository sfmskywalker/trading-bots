"""Thin wrapper around the Anthropic SDK for structured JSON decisions."""
import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"


class RefusalError(Exception):
    pass


def model_name() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def call_structured(system: str, user_content: str, schema: dict,
                    max_tokens: int = 2048,
                    web_search_max_uses: int | None = None) -> tuple[dict, dict]:
    """Call Claude with a JSON schema-constrained response.

    Returns (parsed_json, usage_dict). Raises RefusalError on a safety refusal
    so callers can fall back to their neutral/no-op behavior.

    When ``web_search_max_uses`` is set, the current server-side web search tool
    is enabled. The server-side tool loop can hit its iteration cap and return
    ``stop_reason == "pause_turn"``; we resume automatically by appending the
    assistant response and re-calling (capped to avoid infinite loops).
    """
    client = anthropic.Anthropic()
    kwargs: dict = dict(
        model=model_name(),
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if web_search_max_uses is not None:
        kwargs["tools"] = [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": web_search_max_uses,
        }]

    messages = [{"role": "user", "content": user_content}]
    usage = {"model": model_name(), "input_tokens": 0, "output_tokens": 0,
             "web_search_requests": 0}

    for _ in range(6):  # 1 initial call + up to 5 pause_turn continuations
        response = client.messages.create(messages=messages, **kwargs)
        if response.stop_reason == "refusal":
            raise RefusalError("Claude declined the request")

        usage["model"] = response.model
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        server_tool_use = getattr(response.usage, "server_tool_use", None)
        if server_tool_use is not None:
            usage["web_search_requests"] += getattr(
                server_tool_use, "web_search_requests", 0) or 0

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        # With web search in play the content may hold server_tool_use /
        # web_search_tool_result blocks and multiple text blocks; the
        # schema-constrained JSON is the LAST text block.
        text = next(b.text for b in reversed(response.content)
                    if b.type == "text")
        return json.loads(text), usage

    raise RuntimeError("Exceeded pause_turn continuation cap")
