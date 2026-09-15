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

from .providers import active, build_client
from .tools import TOOLS, Outcome, dispatch
from .trace import TurnTrace
from .triage import HEAVY_MODEL, classify, log_flagged

MODEL = HEAVY_MODEL

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
    #: Screen each turn with a small model and route accordingly. Off gives
    #: every turn to the expensive model, which is what the cost model compares
    #: against.
    route: bool = True
    conversation_id: str = "local"
    #: Which gateway and jurisdiction. See providers.py; set with BOOKLY_PROVIDER.
    provider: Any = field(default_factory=active)
    turn_no: int = 0
    history: list[Turn] = field(default_factory=list)
    outcome: Outcome = field(default_factory=Outcome)
    triages: list[Any] = field(default_factory=list)
    #: (role, model, input_tokens, output_tokens) per API call, so cost is
    #: measured rather than estimated. Role, not model, identifies the triage
    #: pass: once routing works, resolution calls run on the small model too.
    usage: list[tuple[str, str, int, int]] = field(default_factory=list)
    _client: Any = None
    on_tool: Callable[[str, dict, dict], None] | None = None
    on_triage: Callable[[Any], None] | None = None

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.provider)
        return self._client

    def _messages(self) -> list[dict[str, Any]]:
        return [{"role": t.role, "content": t.content} for t in self.history]

    def say(self, text: str) -> str:
        """Send one customer message, run the tool loop, return the reply.

        The turn is screened first by a small model, which picks the model for
        this turn and flags abuse. Routing is an optimisation, not a safety
        mechanism: correctness comes from the policy function and the tool gate,
        and both are model-independent. A triage miss costs a less polished
        reply, never a wrong refund.
        """
        import time

        started = time.monotonic()
        self.turn_no += 1
        trace = TurnTrace(conversation=self.conversation_id, turn=self.turn_no,
                          provider=self.provider.name,
                          residency=self.provider.residency)
        before_refunds = len(self.outcome.refunds)
        before_escalations = len(self.outcome.escalations)
        before_calls = len(self.outcome.calls)

        model = self.model
        if self.route:
            triage, tri_usage = classify(self.client, text, return_usage=True)
            self.triages.append(triage)
            self.usage.append(("triage",) + tri_usage)
            log_flagged(triage, text, self.conversation_id)
            if self.on_triage:
                self.on_triage(triage)
            model = triage.model
            trace.intent, trace.complexity = triage.intent, triage.complexity
            trace.risk = triage.risk

        self.history.append(Turn("user", text))

        for _ in range(self.max_tool_rounds):
            response = self.client.messages.create(
                model=model,
                max_tokens=1200,
                system=SYSTEM,
                tools=TOOLS,
                messages=self._messages(),
            )
            self.usage.append(("resolve", model, response.usage.input_tokens,
                               response.usage.output_tokens))
            self.history.append(Turn("assistant", response.content))

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                reply = "".join(b.text for b in response.content
                                if b.type == "text").strip()
                trace.model = model
                trace.tokens_in = sum(i for r, _m, i, _o in self.usage
                                      if r == "resolve")
                trace.tokens_out = sum(o for r, _m, _i, o in self.usage
                                       if r == "resolve")
                trace.usd = self._usd()
                trace.tools = self.outcome.calls[before_calls:]
                trace.state_changed = len(self.outcome.refunds) > before_refunds
                trace.escalated = len(self.outcome.escalations) > before_escalations
                trace.asked_clarifying = reply.rstrip().endswith("?")
                trace.latency_ms = int((time.monotonic() - started) * 1000)
                trace.write()
                return reply

            results = []
            for block in tool_uses:
                args = dict(block.input or {})
                result = dispatch(block.name, args, self.outcome)
                if isinstance(result, dict) and result.get("code"):
                    trace.policy_codes.append(result["code"])
                if self.on_tool:
                    self.on_tool(block.name, args, result)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
            self.history.append(Turn("user", results))

        trace.model = model
        trace.escalated = True
        trace.write()
        return ("I am having trouble completing that. Let me put you through to a "
                "colleague who can help.")

    def _usd(self) -> float:
        from .costs import cost

        total = 0.0
        for _role, model, tin, tout in self.usage:
            bare = model.split("/")[-1]
            try:
                total += cost(bare, tin, tout)
            except KeyError:
                pass
        return round(total, 6)
