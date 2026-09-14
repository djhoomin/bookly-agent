"""Refund eligibility, as code.

This module is the argument of the whole prototype.

A prompt that says "only refund within 30 days" is a suggestion. A model that
is being helpful, under pressure from an unhappy customer, will find a reading
of that sentence which lets it say yes. A function that returns
`ineligible: outside_window` is a rule: it is inspectable, it is testable, and
it returns the same answer whether the customer is polite or furious.

So the division of labour is: the model decides what to attempt and how to say
it, and this file decides what is permitted. The model never sees a refund
decision it can argue with, only one it must report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .backend import TODAY, Order

RETURN_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    explanation: str
    refund_eur: float = 0.0

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "explanation": self.explanation,
            "refund_eur": round(self.refund_eur, 2),
        }


def refund_eligibility(order: Order, today: date | None = None) -> Decision:
    """Decide, deterministically, whether this order can be refunded.

    Ordered most-specific first so the reason given to the customer is the
    真 reason, not merely the first rule that happened to match.
    """
    today = today or TODAY

    if order.status == "cancelled":
        return Decision(False, "already_cancelled",
                        "This order was already cancelled, so there is nothing to refund.")
    if order.digital:
        return Decision(False, "digital_item",
                        "Digital items cannot be returned once downloaded.")
    if order.status in {"processing", "shipped"}:
        return Decision(False, "not_yet_delivered",
                        "This order has not been delivered yet, so a return cannot be started. "
                        "It can be cancelled instead while it is still in transit.")
    if order.delivered_on is None:
        return Decision(False, "no_delivery_date",
                        "No delivery date is recorded for this order, so the return window "
                        "cannot be calculated. A human agent needs to check this.")

    age = (today - order.delivered_on).days
    if age > RETURN_WINDOW_DAYS:
        return Decision(False, "outside_window",
                        f"Delivered {age} days ago, which is outside the "
                        f"{RETURN_WINDOW_DAYS}-day return window.")
    return Decision(True, "eligible",
                    f"Delivered {age} days ago, inside the {RETURN_WINDOW_DAYS}-day window.",
                    refund_eur=order.price_eur)
