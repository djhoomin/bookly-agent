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
from dataclasses import dataclass, field
from typing import Any, Callable

from .providers import active, build_client
from .tools import TOOLS, Outcome, dispatch
from .trace import TurnTrace
from .grounding import check as ground, fallback
from .moderation import moderate
from .triage import Triage, log_flagged, screen

SYSTEM = """You are Bookly's customer support agent. Bookly is an online bookstore.

How to work:
- Use tools to find things out. Never state an order status, delivery date, refund
  amount or policy from memory: look it up.
- Never construct an email address or an order ID. If the customer has not given one,
  ask for it. A first name is not an identifier.
- Before reading anything on an account, verify it: send a code to the customer's email,
  ask them to read it back, verify it. An order number alone is not access. You never see
  the code and must never state one.
- Before doing anything that changes state, be certain which order you are acting on.
  If more than one order could match, ask the customer which one. Asking is cheap and
  acting on the wrong order is not. Once the order is certain and the customer has
  asked for the return, start it; their request is the confirmation.
- Eligibility for returns is decided by Bookly policy through the tools, not by you.
  Check it before saying anything about whether an order can be refunded, even when
  the order status looks conclusive. Check first; do not ask the customer why they
  want a refund before checking, since the answer rarely depends on it. Report the
  decision and explain it plainly. Do not argue with it, apologise for it
  at length, or imply you could make an exception.
- When policy blocks something the customer wants, say so once, say why, and offer to
  put them through to a person.
- "I never received it" is a claim about delivery, not a return. Check it with the
  reason not_received and let policy say what happens next.

Tone: brief, warm, no filler. Two or three sentences is usually enough."""


#: Sent, unchanged, when a turn carries a self-harm signal. Written by a
#: person, not generated: a support model has no business improvising here.
#: The numbers are for the Netherlands, where Bookly ships from, plus the EU
#: emergency number and an international directory. A deployment must set
#: these for its own region and check them on a schedule.
SAFETY_REPLY = (
    "I'm sorry you're going through this, and I'm glad you said it. I've asked a "
    "colleague to join this conversation as a priority, and they will reply here.\n\n"
    "If you are in the Netherlands, 113 Zelfmoordpreventie is there day and night: "
    "call 0800-0113 (free) or chat at 113.nl. If you are somewhere else, "
    "findahelpline.com lists services near you. If you are in immediate danger, "
    "call 112.\n\n"
    "Your order can wait. You matter more than it does."
)


@dataclass
class Turn:
    role: str
    content: Any


@dataclass
class Agent:
    """One conversation. Holds its own history, so memory is explicit."""

    #: Override the resolution model. Empty means the provider's heavy model,
    #: or whichever tier triage picks when routing is on.
    model: str = ""
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
    #: The decision record for the most recent turn, for callers that want
    #: to show it rather than read it back from the log.
    last_trace: Any = None
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
        before_ambiguities = len(self.outcome.ambiguities)
        before_calls = len(self.outcome.calls)
        before_sources = len(self.outcome.policy_sources)
        self._policy_topics_this_turn: list[str] = []
        self._tools_this_turn: list[str] = []
        usage_start = len(self.usage)

        if self.outcome.escalations:
            return self._after_handover(text, trace, started)

        model = self.model or self.provider.model("heavy")
        if self.route:
            triage, rows = screen(self.client, text, model=self.provider.model("light"))
            self.triages.append(triage)
            self.usage.extend(rows)
            log_flagged(triage, text, self.conversation_id)
            if self.on_triage:
                self.on_triage(triage)
            model = self.model or self.provider.model(triage.tier)
            trace.intent, trace.complexity = triage.intent, triage.complexity
            trace.risk, trace.screener = triage.risk, triage.screener
            trace.moderated = triage.moderated
            trace.moderation = dict(triage.moderation)
            # The claim is sticky, and a non-receipt claim is never downgraded
            # by a later turn: "just refund it" does not un-say "it never came".
            if triage.claim != "none" and self.outcome.claim != "not_received":
                self.outcome.claim = triage.claim
            trace.claim = self.outcome.claim
            if triage.risk == "self_harm":
                return self._safety_response(text, trace, started)
        gate_before = self.outcome.gate_refusals
        reads_before = self.outcome.account_reads

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
                self._tools_this_turn = self.outcome.calls[before_calls:]
                reply = self._ground(reply, trace, before_sources)
                this_turn = self.usage[usage_start:]
                trace.model = model
                trace.tokens_in = sum(i for _r, _m, i, _o in this_turn)
                trace.tokens_out = sum(o for _r, _m, _i, o in this_turn)
                trace.usd = self._usd(this_turn)
                trace.tools = self.outcome.calls[before_calls:]
                trace.state_changed = len(self.outcome.refunds) > before_refunds
                trace.escalated = len(self.outcome.escalations) > before_escalations
                trace.held_on_ambiguity = (
                    len(self.outcome.ambiguities) > before_ambiguities
                    and not trace.state_changed)
                trace.verified = sorted(self.outcome.verified_emails)
                trace.gate_refusals = self.outcome.gate_refusals - gate_before
                trace.account_reads = self.outcome.account_reads - reads_before
                trace.latency_ms = int((time.monotonic() - started) * 1000)
                trace.write()
                self.last_trace = trace
                return reply

            results = []
            for block in tool_uses:
                args = dict(block.input or {})
                result = dispatch(block.name, args, self.outcome)
                if block.name == "lookup_policy":
                    self._policy_topics_this_turn.append(args.get("topic", ""))
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

        # Out of rounds. Hand over for real, so the ticket the customer is
        # promised actually exists, rather than only saying the words.
        dispatch("escalate_to_human",
                 {"summary": "Agent exhausted its tool budget without resolving the turn.",
                  "reason": "tool_rounds_exhausted"}, self.outcome)
        trace.model = model
        trace.escalated = True
        trace.tools = self.outcome.calls[before_calls:]
        trace.usd = self._usd(self.usage[usage_start:])
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.write()
        self.last_trace = trace
        return ("I am having trouble completing that. Let me put you through to a "
                "colleague who can help.")

    def _safety_response(self, text: str, trace: TurnTrace, started: float) -> str:
        """A self-harm signal is not a support ticket, and the model does not
        get to answer it. Fixed text with crisis resources, an urgent handover,
        and from here the conversation belongs to a person: the after-handover
        rule takes every later turn."""
        import time

        self.history.append(Turn("user", text))
        dispatch("escalate_to_human",
                 {"summary": "Customer message carries a self-harm signal. Contact as a "
                             "priority; the customer has been given crisis resources.",
                  "reason": "self_harm", "priority": "urgent"}, self.outcome)
        self.history.append(Turn("assistant", [{"type": "text", "text": SAFETY_REPLY}]))
        trace.tools = ["escalate_to_human"]
        trace.escalated = True
        trace.safety_response = True
        trace.model = ""
        trace.usd = self._usd(self.usage[len(self.usage) - 2:]) if len(self.usage) >= 2 else 0.0
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.write()
        self.last_trace = trace
        return SAFETY_REPLY

    def _after_handover(self, text: str, trace: TurnTrace, started: float) -> str:
        """Once a person owns the conversation, the agent stops.

        No model call, no tools, no state. The customer gets a fixed reply
        with the ticket number and the message is appended to the ticket for
        the person to read. Moderation still runs, because a threat made
        after handover is exactly what the trust and safety log is for, and
        the endpoint is free. This closes the obvious abuse path: keep the
        bot talking after it has handed over and see what it can be pushed
        into. There is nothing left to push.
        """
        import time

        ticket = self.outcome.escalations[-1].get("ticket", "")
        self.history.append(Turn("user", text))
        verdict = moderate(text)
        if verdict is not None:
            triage = Triage(intent="other", complexity="simple", risk=verdict.risk,
                            reason=f"after handover; mistral: {verdict.reason or 'clean'}",
                            screener="mistral" if verdict.risk != "none" else "triage",
                            moderated=True, moderation=verdict.flagged)
            self.triages.append(triage)
            log_flagged(triage, text, self.conversation_id)
            trace.risk, trace.screener = triage.risk, triage.screener
            trace.moderated, trace.moderation = True, dict(verdict.flagged)
        reply = (f"A colleague has this conversation now, ticket {ticket}, and will reply "
                 "here. I have added your message to the ticket so they see it.")
        if self.outcome.escalations[-1].get("reason") == "self_harm" or (
                verdict is not None and verdict.risk == "self_harm"):
            reply = (f"A colleague is joining as a priority, ticket {ticket}. Your message is "
                     "with them. If you need someone right now: 0800-0113 or 113.nl in the "
                     "Netherlands, findahelpline.com elsewhere, 112 in an emergency.")
        self.history.append(Turn("assistant", [{"type": "text", "text": reply}]))
        self.outcome.after_handover.append(text)
        trace.intent = "other"
        trace.handed_over = True
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.write()
        self.last_trace = trace
        return reply

    def _ground(self, reply: str, trace: TurnTrace, before_sources: int) -> str:
        """Hold a general-question reply to the published text it looked up.

        Runs when the turn read a policy note, or when triage called it a
        general question and nothing was looked up at all. Refund verdicts are
        already code and are not re-checked. A reply that states a Bookly fact
        the source does not contain is replaced, and the claims are recorded.
        """
        sources = self.outcome.policy_sources[before_sources:]
        tools_this_turn = self._tools_this_turn
        answered_from_nothing = trace.intent == "general_question" and not tools_this_turn
        if not sources and not answered_from_nothing:
            # Facts from find_orders or get_order_status are not policy and the
            # grounder has no source for them; checking would flag the truth.
            return reply
        trace.policy_misses = [t for t, src in zip(self._policy_topics_this_turn, sources)
                               if not src]
        verdict, usage = ground(self.client, self.provider.model("light"), reply, sources,
                                self.outcome.tool_results)
        self.usage.append(usage)
        trace.grounded = verdict.grounded
        trace.unsupported = list(verdict.unsupported)
        trace.checked_against_policy = True
        if verdict.grounded:
            return reply
        replacement = fallback(sources)
        # The history must say what the customer actually saw.
        self.history[-1] = Turn("assistant", [{"type": "text", "text": replacement}])
        trace.reply_replaced = True
        return replacement

    def _usd(self, rows) -> float:
        from .costs import cost

        total = 0.0
        for _role, model, tin, tout in rows:
            try:
                total += cost(model, tin, tout)
            except KeyError:
                pass
        return round(total, 6)
