"""Refund eligibility, as code.

This module is the argument of the whole prototype.

Don't prompt what should have been code. A prompt that says "only refund
within 30 days" is a suggestion. A model that
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

#: Why the customer wants their money back. The policy reads this, because
#: "I never received it" and "I did not like it" are different claims with
#: different rules, and a refund function that cannot tell them apart will
#: refund a non-receipt claim on an order the carrier marks delivered, which
#: is the most common e-commerce fraud there is.
REASONS = ("damaged", "unwanted", "wrong_item", "not_received")


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


def refund_eligibility(order: Order, reason: str = "unwanted",
                       today: date | None = None) -> Decision:
    """Decide, deterministically, whether this order can be refunded.

    Ordered most-specific first so the reason given to the customer is the
    real reason, not merely the first rule that happened to match.
    """
    today = today or TODAY

    if reason not in REASONS:
        return Decision(False, "unknown_reason",
                        f"Reason must be one of {', '.join(REASONS)}.")
    if order.status == "cancelled":
        return Decision(False, "already_cancelled",
                        "This order was already cancelled, so there is nothing to refund.")

    if reason == "not_received":
        # A return means the customer has the item to send back. A non-receipt
        # claim is a dispute about delivery, and nothing here refunds one.
        if order.status == "delivered":
            when = order.delivered_on.isoformat() if order.delivered_on else "an unrecorded date"
            return Decision(False, "delivery_dispute",
                            f"The carrier recorded this as delivered on {when}. A non-receipt "
                            "claim on a delivered order is a delivery dispute: a human agent "
                            "has to raise a carrier trace before anything is refunded.")
        if order.status in {"processing", "shipped"}:
            return Decision(False, "in_transit",
                            "This order has not been delivered yet, so it is not lost. It can "
                            "be cancelled while in transit if the customer no longer wants it.")
        return Decision(False, "delivery_dispute",
                        "Delivery state is unclear. A human agent needs to check with the carrier.")

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
