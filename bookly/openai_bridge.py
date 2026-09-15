"""A thin adapter so an OpenAI-compatible gateway speaks the Messages shape.

Only as thick as it needs to be. The agent loop reads `response.content` as
blocks, `response.usage.input_tokens`, and appends tool results as Anthropic
content blocks; this translates those three things and nothing else. Anything
more would be the all-in-one abstraction the exercise asks us to avoid, and
would hide exactly the orchestration we are meant to be showing.

Two shapes differ and both are handled here:

  tools     Anthropic {name, description, input_schema}
            OpenAI    {type: function, function: {name, description, parameters}}
  results   Anthropic a user message of tool_result blocks
            OpenAI    one message per result, role "tool", keyed by tool_call_id
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage
    stop_reason: str = "end_turn"


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in tools]


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue
        # A user message carrying tool_result blocks becomes one "tool" message each.
        results = [b for b in content if isinstance(b, dict)
                   and b.get("type") == "tool_result"]
        if results:
            for block in results:
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                            "content": block["content"]})
            continue
        # An assistant turn: text plus any tool calls it made.
        text = "".join(getattr(b, "text", "") for b in content
                       if getattr(b, "type", "") == "text")
        calls = [b for b in content if getattr(b, "type", "") == "tool_use"]
        entry: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in calls
            ]
        out.append(entry)
    return out


class _Messages:
    def __init__(self, client):
        self._client = client

    def create(self, *, model: str, max_tokens: int, system: str = "",
               messages: list[dict], tools: list[dict] | None = None,
               **_ignored) -> _Response:
        completion = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=_to_openai_messages(system, messages),
            tools=_to_openai_tools(tools) if tools else None,
        )
        choice = completion.choices[0].message
        blocks: list[Any] = []
        if choice.content:
            blocks.append(_Text(choice.content))
        for call in (choice.tool_calls or []):
            blocks.append(_ToolUse(id=call.id, name=call.function.name,
                                   input=json.loads(call.function.arguments or "{}")))
        usage = completion.usage
        return _Response(
            content=blocks,
            usage=_Usage(getattr(usage, "prompt_tokens", 0),
                         getattr(usage, "completion_tokens", 0)),
            stop_reason="tool_use" if choice.tool_calls else "end_turn",
        )


class OpenAICompatClient:
    """Exposes `.messages.create(...)`, which is all the agent loop uses."""

    def __init__(self, base_url: str, api_key: str):
        from openai import OpenAI

        self._inner = OpenAI(base_url=base_url, api_key=api_key)
        self.messages = _Messages(self._inner)
