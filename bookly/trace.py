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
    model: str = ""
    # what the agent did
    tools: list[str] = field(default_factory=list)
    policy_codes: list[str] = field(default_factory=list)
    state_changed: bool = False
    escalated: bool = False
    asked_clarifying: bool = False
    # what it cost
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    latency_ms: int = 0

    def write(self, path: Path | None = None) -> None:
        path = path or TRACE_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(self)) + "\n")


def load(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or TRACE_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
