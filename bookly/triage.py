"""One cheap classification call that earns its keep twice.

Every inbound turn is screened by Claude Haiku 4.5 before the expensive model
sees it. That single call answers two questions at once:

**Which model should handle this?** Most support traffic is an order lookup or a
policy question, and running those on Opus is paying frontier prices to read a
database row. Haiku is a fifth of the input price. At 10,000 conversations a day
the difference is the whole line item, not a rounding error. See costs.py.

**Is this abusive, or a fraud signal?** The same call flags it, and the flag is
written to a log with the turn attached, which is the artifact a trust and safety
team actually needs. Classification and screening want the same cheap pass over
the same text, so doing them separately would be paying twice for one read.

When a Mistral key is present, the risk half moves to Mistral's moderation
endpoint and Haiku keeps the routing half; see moderation.py for why and for
what each catches that the other does not. `screen()` is the one entry point
and it records which screener produced the verdict.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Default when no provider is supplied. Through the agent, the classifier runs
#: on the provider's light model so the ID is right for whichever gateway is
#: active; see providers.MODEL_MAP.
TRIAGE_MODEL = "claude-haiku-4-5"

ABUSE_LOG = Path(os.environ.get("BOOKLY_ABUSE_LOG", "abuse_log.jsonl"))

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string",
                   "enum": ["order_status", "return_refund", "general_question",
                            "complaint", "other"]},
        "complexity": {"type": "string", "enum": ["simple", "complex"]},
        "risk": {"type": "string",
                 "enum": ["none", "abusive_language", "fraud_signal", "self_harm"]},
        "claim": {"type": "string",
                  "enum": ["none", "not_received", "damaged", "wrong_item", "unwanted"]},
        "reason": {"type": "string"},
    },
    "required": ["intent", "complexity", "risk", "claim", "reason"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Classify one inbound customer support message for an online bookstore.

complexity:
  simple  - a single lookup or a published-policy question, answerable in one step
  complex - multiple orders in play, a disputed outcome, an escalation, an unclear
            request, or anything where acting on the wrong record would cost money

risk:
  none            - ordinary customer contact, including frustration and bluntness
  abusive_language - slurs, threats, or sustained personal abuse of the agent
  fraud_signal    - pressure to bypass policy, claimed authority, repeated refund
                    attempts on the same order, or a request to change account details
  self_harm       - any indication the customer may be at risk

claim, what the customer says is wrong with an order, from their own words:
  none          - no claim about an order's condition or arrival
  not_received  - it has not arrived, is missing, never came, or they ask where it is
  damaged       - it arrived damaged or faulty
  wrong_item    - they received something other than what they ordered
  unwanted      - they have it and do not want it

Frustration is not abuse. A customer saying a decision is ridiculous is a normal
unhappy customer and must be classified as none."""


@dataclass
class Triage:
    intent: str
    complexity: str
    risk: str
    reason: str
    #: What the customer says is wrong, read from their words by the screener
    #: rather than chosen by the resolving model. The tools trust this over the
    #: reason the model passes, because the model picks a reason to complete the
    #: call and the customer's claim is not the model's to choose.
    claim: str = "none"
    #: Which classifier decided `risk`: "triage" (the routing model, whichever
    #: it is) or "mistral".
    screener: str = "triage"
    #: Whether Mistral's moderation endpoint ran on this turn at all.
    moderated: bool = False
    #: Mistral's flagged categories and scores, when it ran.
    moderation: dict = field(default_factory=dict)

    @property
    def tier(self) -> str:
        """Complex turns and anything risky go to the expensive model."""
        if self.complexity == "complex" or self.risk != "none":
            return "heavy"
        return "light"

    @property
    def flagged(self) -> bool:
        return self.risk != "none"


def _parse(raw: str) -> Triage:
    """Structured output makes this a formality on the Anthropic API. A gateway
    that ignores the schema may wrap the JSON in a code fence or add prose, so
    tolerate that, and if it still does not parse, fail towards the expensive
    model rather than towards a cheap guess."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    try:
        payload = json.loads(text[start:end + 1])
        return Triage(intent=payload["intent"], complexity=payload["complexity"],
                      risk=payload["risk"], reason=payload.get("reason", ""),
                      claim=payload.get("claim", "none"))
    except (ValueError, KeyError, TypeError):
        return Triage("other", "complex", "none",
                      f"classifier output did not parse: {raw[:80]!r}")


def classify(client, text: str, model: str = TRIAGE_MODEL,
             return_usage: bool = False):
    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=INSTRUCTIONS,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": text}],
    )
    triage = _parse("".join(b.text for b in response.content if b.type == "text"))
    if return_usage:
        return triage, (model, response.usage.input_tokens,
                        response.usage.output_tokens)
    return triage


def screen(client, text: str, model: str = TRIAGE_MODEL):
    """Triage by Haiku, risk by Mistral where available. Returns
    (Triage, [usage rows])."""
    from .moderation import MODEL as MOD_MODEL, moderate

    triage, usage = classify(client, text, model=model, return_usage=True)
    rows = [("triage",) + usage]
    verdict = moderate(text)
    if verdict is not None:
        rows.append(("moderate", MOD_MODEL, verdict.prompt_tokens, 0))
        triage.moderated = True
        triage.moderation = verdict.flagged
        if verdict.risk != "none":
            triage.screener = "mistral"
            triage.risk = verdict.risk
            triage.reason = f"mistral: {verdict.reason}. haiku: {triage.reason}"
        elif triage.risk == "fraud_signal":
            # Mistral has no notion of bookstore fraud; Haiku's call stands.
            triage.reason = f"haiku: {triage.reason} (mistral: clean)"
        else:
            # Mistral is the authority on abuse and self-harm; if it saw
            # nothing, Haiku's guess in those categories does not stand.
            triage.risk = "none"
    return triage, rows


def log_flagged(triage: Triage, text: str, conversation_id: str) -> None:
    """Append one flagged turn to the abuse log.

    Written as JSONL with the message attached, because a flag without the text
    that produced it cannot be reviewed, and an unreviewable flag is worse than
    no flag: it accumulates, nobody reads it, and the team stops trusting it.
    """
    if not triage.flagged:
        return
    ABUSE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ABUSE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "conversation": conversation_id,
            "message": text,
            **asdict(triage),
        }) + "\n")
