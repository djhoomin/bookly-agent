"""Prose answers are held to the published text.

For returns, policy is code and the model only reports a verdict. For general
questions there is no verdict, the answer *is* the prose, and a helpful model
will round a published "2 to 4 working days" up to "usually 2 days", invent a
student discount when asked nicely, or agree that the return window is 90 days
when told firmly enough. None of those is an exception. They are the model
extending the policy because it wanted to be useful.

So after any general-question turn, a small model reads the reply against the
policy text the agent looked up and lists every specific claim about Bookly
that the source does not support. Tone, offers to help and restating the
customer's question are not claims. If anything is unsupported, the reply is
replaced with one that restates the source and offers a person, and the trace
records the original claims so someone can see what the model wanted to say.

Where nothing was looked up, or nothing was found, the source is empty and any
factual claim about Bookly is unsupported by construction. That is the
"student discount" case: the correct answer is "we do not publish one", and the
check makes it the only answer that survives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["unsupported"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You check a customer-support reply against the source text it was allowed to use.

List every specific factual claim in the reply about Bookly's policies, prices, timings,
procedures, availability, discounts or products that is NOT stated in the source. Quote each
one briefly. Paraphrase of the source is fine. Politeness, apologies, offering to connect
the customer with a person, asking a question, and restating what the customer said are not
claims. If the source is empty, any factual claim about Bookly is unsupported.

Return an empty list when everything factual in the reply is in the source."""


@dataclass
class Grounding:
    grounded: bool
    unsupported: list[str] = field(default_factory=list)


def check(client, model: str, reply: str, sources: list[str]):
    """Return (Grounding, usage tuple)."""
    source = "\n\n".join(s for s in sources if s) or "(empty: nothing published on this topic)"
    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=INSTRUCTIONS,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"SOURCE:\n{source}\n\nREPLY:\n{reply}"}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        start, end = text.find("{"), text.rfind("}")
        claims = [c for c in json.loads(text[start:end + 1]).get("unsupported", []) if c]
    except (ValueError, KeyError, TypeError):
        claims = []  # an unreadable check is not evidence against the reply
    usage = ("ground", model, response.usage.input_tokens, response.usage.output_tokens)
    return Grounding(grounded=not claims, unsupported=claims), usage


def fallback(sources: list[str]) -> str:
    """What the customer gets instead of an extended policy."""
    text = "\n\n".join(s for s in sources if s)
    if text:
        return (f"Here is what Bookly publishes on that:\n\n{text}\n\n"
                "If your question goes beyond this, I can put you through to a person.")
    return ("Bookly does not publish anything on that, so I do not want to guess. "
            "I can put you through to a person who can check.")
