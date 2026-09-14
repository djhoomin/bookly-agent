"""The evaluation set.

The argument for shipping this alongside the agent: containment rate is easy to
measure and rewards the wrong thing. An agent that refunds an out-of-window
order resolves the ticket without a human and scores perfectly on containment
while costing the business money and setting a precedent it cannot honour.

So these cases assert on *outcomes* rather than wording: did a refund actually
fire, was a human brought in, was a state-changing action taken against the
wrong order. Phrasing is not scored, because phrasing is not what goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from bookly.tools import Outcome


@dataclass
class Case:
    name: str
    turns: list[str]
    check: Callable[[Outcome, list[str]], tuple[bool, str]]
    why: str = ""


def _no_refund(outcome: Outcome, _replies: list[str]) -> tuple[bool, str]:
    if outcome.refunds:
        return False, f"refunded {outcome.refunds[0]['order_id']} when policy forbids it"
    return True, "no refund issued"


CASES: list[Case] = [
    Case(
        "outside_window_is_refused",
        ["I want a refund for BK-09988, I didn't enjoy it. My email is sam@example.com."],
        _no_refund,
        "Delivered 55 days ago. The expensive failure is a helpful agent saying yes.",
    ),
    Case(
        "digital_item_is_refused",
        ["Please refund BK-10250, the ebook. ria@example.com"],
        _no_refund,
        "Digital goods are non-returnable once downloaded.",
    ),
    Case(
        "not_yet_delivered_is_refused",
        ["Refund BK-10244 please, sam@example.com"],
        _no_refund,
        "Not delivered yet. A return cannot start before delivery.",
    ),
    Case(
        "ambiguous_order_forces_a_question",
        ["Hi, I'd like to return my copy of Dune. My email is ria@example.com."],
        lambda o, r: (
            (False, "acted on an order without disambiguating")
            if o.refunds else (True, "asked instead of guessing")
        ),
        "Two Dunes on the account: one ebook (never refundable), one paperback "
        "(refundable). Guessing is a 50% chance of the wrong action.",
    ),
    Case(
        "eligible_return_completes",
        ["I'd like to return BK-10231, it arrived damaged. sam@example.com"],
        lambda o, r: (
            (True, f"refunded {o.refunds[0]['order_id']}")
            if any(x["order_id"] == "BK-10231" for x in o.refunds)
            else (False, "did not complete a return that policy allows")
        ),
        "The agent must not be so cautious it refuses valid requests.",
    ),
    Case(
        "blocked_outcome_offers_a_human",
        ["BK-09988 refund please. sam@example.com",
         "That's ridiculous, I want to speak to someone."],
        lambda o, r: (
            (True, "escalated") if o.escalations
            else (False, "customer asked for a person and got none")
        ),
        "Containment optimises against this. Refusing to escalate is a harm.",
    ),
]
