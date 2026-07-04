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
                    max_tokens: int = 2048) -> tuple[dict, dict]:
    """Call Claude with a JSON schema-constrained response.

    Returns (parsed_json, usage_dict). Raises RefusalError on a safety refusal
    so callers can fall back to their neutral/no-op behavior.
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model_name(),
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user_content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise RefusalError("Claude declined the request")

    text = next(b.text for b in response.content if b.type == "text")
    usage = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return json.loads(text), usage
