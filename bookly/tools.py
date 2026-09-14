"""Tool definitions and dispatch.

Two things here are deliberate.

**The gate.** `start_return` requires a resolved `order_id`. There is no code
path from "I want to refund my book" to a refund without first establishing
which book. When the customer has more than one candidate order, `find_orders`
returns them all and the model has nothing to act on, so it must ask. The
clarifying question is therefore a consequence of the interface rather than an
instruction in the prompt, which is the difference between a behaviour that
usually happens and one that always does.

**The model never decides eligibility.** `start_return` calls
`policy.refund_eligibility` itself and reports the verdict. The model can
phrase the outcome, and cannot change it.
"""

from __future__ import annotations

from typing import Any

from .backend import POLICY_NOTES, find_orders_by_email, get_order
from .policy import refund_eligibility

TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_orders",
        "description": (
            "Look up a customer's orders by email address. Use this when the customer "
            "has not given an order ID. Returns every order on the account, which may "
            "be more than one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Customer email"}},
            "required": ["email"],
        },
    },
    {
        "name": "get_order_status",
        "description": "Fetch the current status and tracking reference for one order.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. BK-10231"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "start_return",
        "description": (
            "Start a return and refund for one specific order. Requires a resolved "
            "order_id: never guess it, and never call this if the customer has more "
            "than one order that could match. Eligibility is decided by Bookly policy, "
            "not by you; this tool reports the decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string", "description": "Customer's stated reason"},
            },
            "required": ["order_id", "reason"],
        },
    },
    {
        "name": "lookup_policy",
        "description": (
            "Read Bookly's published policy on a topic. Topics: returns, shipping, password."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": ["returns", "shipping", "password"]}
            },
            "required": ["topic"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the conversation to a human agent. Use this when the customer asks for "
            "a person, when policy blocks an outcome the customer is unhappy about, or "
            "when you do not have enough information and cannot get it by asking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What the human needs to know"},
                "reason": {"type": "string"},
            },
            "required": ["summary", "reason"],
        },
    },
]

#: Set by the runtime when a return actually completes or an escalation fires,
#: so the evaluator can assert on outcomes rather than on wording.
class Outcome:
    def __init__(self) -> None:
        self.refunds: list[dict[str, Any]] = []
        self.escalations: list[dict[str, Any]] = []
        self.calls: list[str] = []


def dispatch(name: str, args: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
    outcome.calls.append(name)

    if name == "find_orders":
        orders = find_orders_by_email(args.get("email", ""))
        if not orders:
            return {"found": 0, "orders": [],
                    "note": "No orders on that email. Check the address, or escalate."}
        return {
            "found": len(orders),
            "orders": [
                {"order_id": o.order_id, "title": o.title, "status": o.status,
                 "price_eur": o.price_eur,
                 "delivered_on": o.delivered_on.isoformat() if o.delivered_on else None}
                for o in orders
            ],
            "note": ("More than one order on this account. Ask the customer which one "
                     "before taking any action." if len(orders) > 1 else ""),
        }

    if name == "get_order_status":
        order = get_order(args.get("order_id", ""))
        if not order:
            return {"error": "unknown_order", "note": "No such order ID."}
        return {"order_id": order.order_id, "title": order.title, "status": order.status,
                "carrier_ref": order.carrier_ref,
                "ordered_on": order.ordered_on.isoformat(),
                "delivered_on": order.delivered_on.isoformat() if order.delivered_on else None}

    if name == "start_return":
        order = get_order(args.get("order_id", ""))
        if not order:
            return {"error": "unknown_order", "note": "No such order ID. Do not guess one."}
        decision = refund_eligibility(order)
        payload = {"order_id": order.order_id, "title": order.title, **decision.to_dict()}
        if decision.allowed:
            outcome.refunds.append(payload)
            payload["confirmation"] = f"RET-{order.order_id[-5:]}"
        return payload

    if name == "lookup_policy":
        topic = args.get("topic", "")
        return {"topic": topic, "text": POLICY_NOTES.get(topic, "No published policy on that.")}

    if name == "escalate_to_human":
        outcome.escalations.append(dict(args))
        return {"escalated": True, "ticket": "HUM-4471",
                "note": "A human agent will pick this up. Tell the customer."}

    return {"error": "unknown_tool"}
