"""A mocked Bookly backend.

Deliberately dumb: fixed data, no logic beyond lookup. Everything interesting
lives in policy.py, because the point of this prototype is that the rules are
code rather than prose, and rules you can see are rules you can test.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta

TODAY = date(2026, 9, 14)


@dataclass(frozen=True)
class Order:
    order_id: str
    email: str
    title: str
    price_eur: float
    status: str           # processing | shipped | delivered | cancelled
    ordered_on: date
    delivered_on: date | None
    carrier_ref: str | None
    digital: bool = False


ORDERS: dict[str, Order] = {
    "BK-10231": Order("BK-10231", "sam@example.com", "The Idiot", 14.99,
                      "delivered", TODAY - timedelta(days=9), TODAY - timedelta(days=5),
                      "PN-773310"),
    "BK-10244": Order("BK-10244", "sam@example.com", "Piranesi", 12.50,
                      "shipped", TODAY - timedelta(days=3), None, "PN-773411"),
    # Outside the 30-day window. The agent must not refund this one.
    "BK-09988": Order("BK-09988", "sam@example.com", "Blood Meridian", 11.00,
                      "delivered", TODAY - timedelta(days=61), TODAY - timedelta(days=55),
                      "PN-770021"),
    # Digital goods are non-returnable once downloaded.
    "BK-10250": Order("BK-10250", "ria@example.com", "Dune (ebook)", 8.99,
                      "delivered", TODAY - timedelta(days=2), TODAY - timedelta(days=2),
                      None, digital=True),
    "BK-10251": Order("BK-10251", "ria@example.com", "Dune (paperback)", 13.99,
                      "delivered", TODAY - timedelta(days=2), TODAY - timedelta(days=2),
                      "PN-773500"),
}

#: Answers to the general questions, kept short on purpose. A real deployment
#: would retrieve these; hard-coding them keeps the prototype's surface honest
#: about what has and has not been built.
POLICY_NOTES: dict[str, str] = {
    "returns": "Physical books can be returned within 30 days of delivery for a full refund. "
               "Digital items are non-returnable once downloaded.",
    "shipping": "Standard delivery is 2 to 4 working days in the Netherlands, 3 to 7 elsewhere "
                "in the EU. Bookly ships within the EU only. Tracking is emailed when the "
                "parcel leaves the warehouse.",
    "password": "Use 'Forgot password' on the sign-in page. The reset link is valid for one hour.",
    "cancellation": "An order can be cancelled free of charge until it leaves the warehouse. "
                    "After that, wait for delivery and start a return.",
}

#: Words that point at each note. A free-text topic is matched here so the
#: model never has to guess an enum, and "nothing published" is a real result
#: rather than a tool it could not call.
POLICY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "returns": ("return", "refund", "send back", "window", "money back", "exchange"),
    "shipping": ("ship", "deliver", "delivery", "post", "tracking", "how long", "arrive",
                 "countries", "country", "abroad", "international"),
    "password": ("password", "log in", "login", "sign in", "reset", "locked out", "account access"),
    "cancellation": ("cancel", "cancellation", "change my order", "stop the order"),
}


def find_policy(topic: str) -> tuple[str, str] | None:
    """The best-matching published note for a free-text topic, or None."""
    needle = (topic or "").lower()
    best, score = None, 0
    for key, words in POLICY_KEYWORDS.items():
        hits = sum(1 for w in words if w in needle) + (2 if key in needle else 0)
        if hits > score:
            best, score = key, hits
    return (best, POLICY_NOTES[best]) if best else None


def verification_code(email: str) -> str:
    """The six-digit code the mock inbox receives. Deterministic per address so
    the eval set and the demo can type it; a real deployment generates one per
    request, expires it, and sends it. The model never sees this function's
    output: it goes to the inbox, and the inbox is the customer's."""
    digest = hashlib.sha256(email.lower().strip().encode()).hexdigest()
    return f"{int(digest[:8], 16) % 1_000_000:06d}"


def find_orders_by_email(email: str) -> list[Order]:
    return [o for o in ORDERS.values() if o.email.lower() == email.lower().strip()]


def normalise_order_id(raw: str) -> str:
    """Customers type "bk10231", "BK 10231" and "bk-10231". All mean BK-10231.

    Accepting them in code means the model does not have to notice and repair
    the typo, and the trace records the ID the customer gave rather than one
    the model guessed at.
    """
    compact = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    m = re.fullmatch(r"BK(\d{4,6})", compact)
    return f"BK-{m.group(1)}" if m else (raw or "").upper().strip()


def get_order(order_id: str) -> Order | None:
    return ORDERS.get(normalise_order_id(order_id))
