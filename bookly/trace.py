"""One structured record per turn, written to answer questions rather than to store events.

An LLM deployment survives or dies on whether the people running it can see what
it did. Not "was there an error" but the things a CX lead is actually accountable
for: how often did it resolve without a human, what did it refuse and why, where
did it hand over, which customers pushed on the rules, and what did the whole
thing cost.

Generic APM cannot answer those, because the interesting events are not
exceptions. A refund correctly refused is a 200 and a satisfied log line, and it
is also the single most important thing that happened that day. So the trace
records **decisions**, not just calls: the triage verdict, the policy code, the
tools reached for, whether any state changed, and the tokens each turn consumed.

The test for whether the schema is right: can someone answer "why did we refuse
41 refunds last week" without opening a transcript. analyze.py exists to prove
that they can.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACE_LOG = Path(os.environ.get("BOOKLY_TRACE_LOG", "trace.jsonl"))
#: The committed reference run, read when no local trace exists yet.
SAMPLE_TRACE = Path(__file__).resolve().parent.parent / "samples" / "trace.jsonl"


@dataclass
class TurnTrace:
    conversation: str
    turn: int
    at: str = field(default_factory=lambda: datetime.now(timezone.utc)
                    .isoformat(timespec="seconds"))
    provider: str = "anthropic"
    residency: str = ""
    # what triage decided
    intent: str = ""
    complexity: str = ""
    risk: str = ""
    #: which classifier decided risk: haiku, or mistral when it flagged
    screener: str = ""
    #: whether Mistral moderation ran on this turn
    moderated: bool = False
    #: the customer's claim about the order, as read by the screener, sticky
    claim: str = "none"
    #: Mistral's flagged categories with scores, when it ran
    moderation: dict = field(default_factory=dict)
    model: str = ""
    # what the agent did. Every flag here is derived from tool calls and their
    # results, never from the wording of the reply: a refusal that ends in
    # "shall I put you through to someone?" is not a clarifying question.
    tools: list[str] = field(default_factory=list)
    policy_codes: list[str] = field(default_factory=list)
    state_changed: bool = False
    escalated: bool = False
    #: a person already owned the conversation: no model call was made
    handed_over: bool = False
    #: find_orders returned more than one match and no state changed: the gate
    #: held. Usually that means the agent asked which; it can also mean it found
    #: nothing matching and said so. Either way it did not guess.
    held_on_ambiguity: bool = False
    #: prose answers are held to the published text they looked up
    checked_against_policy: bool = False
    #: topics the customer asked about that Bookly publishes nothing on. The
    #: list a policy team wants: what people ask that we have no answer for.
    policy_misses: list[str] = field(default_factory=list)
    grounded: bool = True
    unsupported: list[str] = field(default_factory=list)
    reply_replaced: bool = False
    # what it cost, this turn only, triage call included
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    latency_ms: int = 0

    def write(self, path: Path | None = None) -> None:
        path = path or TRACE_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(self)) + "\n")


def load(path: Path | None = None) -> tuple[list[dict[str, Any]], Path]:
    """Return the rows and the file they came from."""
    path = path or TRACE_LOG
    if not path.exists() and SAMPLE_TRACE.exists():
        path = SAMPLE_TRACE
    if not path.exists():
        return [], path
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows, path
