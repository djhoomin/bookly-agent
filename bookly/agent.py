"""The orchestration loop. Direct Anthropic SDK, no agent framework.

The system prompt is short on purpose, and its shortness is part of the
argument. Every rule that could be enforced in code has been moved into code,
so what remains here is tone and the handful of behaviours that genuinely are
judgement: when to ask rather than assume, and when to hand over to a person.

If you find yourself wanting to add "never refund outside 30 days" to this
string, that is the signal that a rule has leaked out of policy.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .tools import TOOLS, Outcome, dispatch

MODEL = "claude-opus-5"

SYSTEM = """You are Bookly's customer support agent. Bookly is an online bookstore.

How to work:
- Use tools to find things out. Never state an order status, delivery date, refund
  amount or policy from memory: look it up.
- Before doing anything that changes state, be certain which order you are acting on.
  If more than one order could match, ask the customer which one. Asking is cheap and
  acting on the wrong order is not.
- Eligibility for returns is decided by Bookly policy through the tools, not by you.
  Report the decision and explain it plainly. Do not argue with it, apologise for it
  at length, or imply you could make an exception.
- When policy blocks something the customer wants, say so once, say why, and offer to
  put them through to a person.

Tone: brief, warm, no filler. Two or three sentences is usually enough."""


@dataclass
class Turn:
    role: str
    content: Any


@dataclass
class Agent:
    """One conversation. Holds its own history, so memory is explicit."""

    model: str = MODEL
    max_tool_rounds: int = 6
    history: list[Turn] = field(default_factory=list)
    outcome: Outcome = field(default_factory=Outcome)
    _client: Any = None
    on_tool: Callable[[str, dict, dict], None] | None = None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _messages(self) -> list[dict[str, Any]]:
        return [{"role": t.role, "content": t.content} for t in self.history]

    def say(self, text: str) -> str:
        """Send one customer message, run the tool loop, return the reply."""
        self.history.append(Turn("user", text))

        for _ in range(self.max_tool_rounds):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=SYSTEM,
                tools=TOOLS,
                messages=self._messages(),
            )
            self.history.append(Turn("assistant", response.content))

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return "".join(b.text for b in response.content if b.type == "text").strip()

            results = []
            for block in tool_uses:
                args = dict(block.input or {})
                result = dispatch(block.name, args, self.outcome)
                if self.on_tool:
                    self.on_tool(block.name, args, result)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
            self.history.append(Turn("user", results))

        return ("I am having trouble completing that. Let me put you through to a "
                "colleague who can help.")
