"""Tool definitions and dispatch.

Two things here are deliberate.

**The gate.** `start_return` requires a resolved `order_id`. There is no code
path from "I want to refund my book" to a refund without first establishing
which book. When the customer has more than one candidate order, `find_orders`
returns them all and the model has nothing to act on, so it must ask. The
clarifying question is therefore a consequence of the interface rather than an
instruction in the prompt, which is the difference between a behaviour that
usually happens and one that always does.

**The fourth gate: account data needs a verified email.** An email address and
an order number are both on packing slips and in screenshots; neither proves
anything. `send_verification_code` puts a code in the customer's inbox and
`verify_code` checks it. Until an address is verified in this conversation,
every tool that reads or changes account data refuses, in its result, with the
next step. The code itself never appears in any tool result, so the model cannot
read it, repeat it, or verify on the customer's behalf.

**The model never decides eligibility, in either direction.** `start_return`
calls `policy.refund_eligibility` and reports the verdict, so a wrong approval is
impossible. Both policy tools require a `reason` from a fixed list, because the
policy reads it: a non-receipt claim on a delivered order is a delivery dispute
and there is no code path that refunds one. `check_return_eligibility` is the read-only half: it returns the same
decision without acting, so there is a cheap authoritative answer for the "can I
return this" question and no reason to reason from the published prose instead.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .backend import find_orders_by_email, find_policy, get_order, verification_code
from .policy import REASONS, refund_eligibility

MAX_CODE_ATTEMPTS = 3

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_verification_code",
        "description": (
            "Send a six-digit verification code to an email address. Call this as soon as "
            "the customer gives their email, before any lookup. The reply never confirms "
            "whether the address has an account. You never see the code: ask the customer "
            "to read it from their inbox."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
    {
        "name": "verify_code",
        "description": (
            "Check the code the customer read from their inbox. On success, account data "
            "for that email can be read for the rest of this conversation. After three "
            "wrong codes, hand over to a person."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string"},
                           "code": {"type": "string", "description": "Six digits"}},
            "required": ["email", "code"],
        },
    },
    {
        "name": "find_orders",
        "description": (
            "Look up a customer's orders by email address, once that address has been "
            "verified in this conversation. Returns every order on the account, which "
            "may be more than one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Customer email"}},
            "required": ["email"],
        },
    },
    {
        "name": "get_order_status",
        "description": ("Fetch the current status and tracking reference for one order on a "
                        "verified account. An order number alone is not access: verify the "
                        "email first."),
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
            "not by you; this tool reports the decision. The reason is part of that "
            "decision: not_received is a delivery dispute, never a return."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string", "enum": list(REASONS),
                           "description": "Why the customer wants a refund. not_received "
                                          "means they say it never arrived."},
            },
            "required": ["order_id", "reason"],
        },
    },
    {
        "name": "check_return_eligibility",
        "description": (
            "Ask Bookly policy whether one specific order can be refunded, without "
            "starting anything. Use this whenever a customer asks about returning, "
            "refunding, or not having received a specific order, and use it before "
            "reading anything into the order status yourself. Do not infer the answer "
            "from the published policy text: that text is a summary for customers, and "
            "this is the decision. If the customer has not said why, leave reason out; "
            "do not ask for one just to make this call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string", "enum": list(REASONS),
                           "description": "Only if the customer said why. not_received "
                                          "means they say it never arrived."},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "lookup_policy",
        "description": (
            "Read Bookly's published policy text on a topic, for answering general "
            "questions. Always call this before answering any question about how "
            "Bookly works: shipping, returns, passwords, cancellations, discounts, "
            "anything. If nothing is published on the topic, say so and offer a person; "
            "never fill the gap yourself. This is customer-facing prose, not a decision: "
            "for a specific order use check_return_eligibility."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string",
                          "description": "The customer's question or topic, in plain words"}
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

#: Set by the runtime when a return actually completes, an escalation fires, or
#: a lookup comes back ambiguous, so the evaluator and the trace can assert on
#: what happened rather than on how the reply was worded.
class Outcome:
    def __init__(self) -> None:
        self.refunds: list[dict[str, Any]] = []
        self.escalations: list[dict[str, Any]] = []
        #: find_orders results with more than one match: the gate firing.
        self.ambiguities: list[dict[str, Any]] = []
        self.calls: list[str] = []
        #: Every policy code returned this conversation, in order.
        self.decisions: list[str] = []
        #: Addresses verified by code in this conversation. The only key that
        #: opens account data.
        self.verified_emails: set[str] = set()
        #: Addresses a code was sent to, and wrong-code attempts per address.
        self.codes_sent: list[str] = []
        self.code_attempts: dict[str, int] = {}
        #: The mock inbox: (email, code) pairs. Read by the UI and the CLI so a
        #: person can type the code. Never returned to the model.
        self.mock_inbox: list[tuple[str, str]] = []
        #: Times a tool refused because the account was not verified, and times
        #: account data was actually read. The analyzer proves reads happen only
        #: after verification.
        self.gate_refusals: int = 0
        self.account_reads: int = 0
        #: Customer messages received after a handover. None of them reached a
        #: model; they were appended to the ticket.
        self.after_handover: list[str] = []
        #: Every tool result this conversation, serialised. The grounding check
        #: treats these as source alongside the policy text, so a fact the
        #: tools returned two turns ago is supported when the model repeats it.
        self.tool_results: list[str] = []
        #: Published policy text handed to the model, one entry per lookup_policy
        #: call; empty string when nothing was published. The grounding check
        #: holds the reply to these.
        self.policy_sources: list[str] = []
        #: The customer's claim as the screener read it, sticky for the
        #: conversation. Set by the agent, read by the policy tools. Once a
        #: customer has said an order never arrived, no later "just refund it"
        #: turns that into an ordinary return.
        self.claim: str = "none"
        #: (tool, arguments) in order, so an evaluator can ask not only what
        #: was called but with what: an email the customer never typed is a
        #: fabricated identifier, and only the arguments show it.
        self.invocations: list[tuple[str, dict[str, Any]]] = []


def _reason(passed: str, outcome: Outcome) -> tuple[str, dict[str, str]]:
    """The reason the policy will read, and a note if it differs from what the
    model passed.

    A non-receipt claim from the customer wins over anything the model chose.
    No stated reason means an ordinary return: only not_received changes the
    answer, and it changes it against the customer, so a default cannot be
    gamed in the customer's favour.
    """
    if outcome.claim == "not_received" and passed != "not_received":
        return "not_received", {"note": (
            "The customer said this order never arrived, so policy treats this as a "
            "non-receipt claim regardless of the reason given here.")}
    return (passed or outcome.claim if outcome.claim in REASONS else passed) or "unwanted", {}


def dispatch(name: str, args: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
    result = _dispatch(name, args, outcome)
    outcome.tool_results.append(f"{name}: {json.dumps(result, separators=(',', ':'))}")
    return result


def find_orders_emails() -> set[str]:
    from .backend import ORDERS
    return {o.email.lower() for o in ORDERS.values()}


def _not_verified(outcome: Outcome, email: str = "") -> dict[str, Any]:
    outcome.gate_refusals += 1
    if email:
        return {"error": "not_verified",
                "note": f"{email} has not been verified in this conversation. Send a "
                        "verification code to it and ask the customer for the code."}
    return {"error": "not_verified",
            "note": "No account has been verified in this conversation. Ask the customer "
                    "for the email on the order, send a code to it, and verify the code "
                    "before reading anything."}


def _owned_order(order_id: str, outcome: Outcome):
    """The order, if it exists and belongs to a verified account. The result for
    'not yours' and 'does not exist' is the same on purpose, so order numbers
    cannot be enumerated through the agent."""
    order = get_order(order_id)
    if not outcome.verified_emails:
        return None, _not_verified(outcome)
    if not order or order.email.lower() not in outcome.verified_emails:
        return None, {"error": "unknown_order",
                      "note": "No such order on the verified account. Do not guess another."}
    return order, None


def _dispatch(name: str, args: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
    outcome.calls.append(name)
    outcome.invocations.append((name, dict(args)))

    if name == "send_verification_code":
        email = (args.get("email") or "").strip().lower()
        if not EMAIL.match(email):
            return {"sent": False, "note": "That is not an email address. Ask for one."}
        outcome.codes_sent.append(email)
        outcome.mock_inbox.append((email, verification_code(email)))
        return {"sent": True,
                "note": "If that address has a Bookly account, a six-digit code is in its "
                        "inbox. Ask the customer to read it to you."}

    if name == "verify_code":
        email = (args.get("email") or "").strip().lower()
        code = "".join(ch for ch in str(args.get("code") or "") if ch.isdigit())
        if email not in outcome.codes_sent:
            return {"verified": False, "note": "No code has been sent to that address yet."}
        if outcome.code_attempts.get(email, 0) >= MAX_CODE_ATTEMPTS:
            return {"verified": False, "note": "Too many wrong codes. Hand over to a person."}
        if code == verification_code(email) and email in find_orders_emails():
            outcome.verified_emails.add(email)
            return {"verified": True, "note": "Verified. Account data for this address can "
                                              "be read for the rest of this conversation."}
        outcome.code_attempts[email] = outcome.code_attempts.get(email, 0) + 1
        left = MAX_CODE_ATTEMPTS - outcome.code_attempts[email]
        return {"verified": False,
                "note": f"That code is not right. {left} attempt(s) left; ask the customer "
                        "to check the inbox." if left else
                        "That code is not right and there are no attempts left. Hand over."}

    if name == "find_orders":
        email = (args.get("email") or "").strip().lower()
        if not EMAIL.match(email):
            # A name is not a lookup key. Say so in the result rather than
            # returning an empty list the model might read as "no orders".
            return {"found": 0, "orders": [],
                    "note": "That is not an email address. Ask the customer for the "
                            "email on their account; do not guess one."}
        if email not in outcome.verified_emails:
            return _not_verified(outcome, email)
        outcome.account_reads += 1
        orders = find_orders_by_email(email)
        if not orders:
            return {"found": 0, "orders": [],
                    "note": "No orders on that email. Check the address, or escalate."}
        if len(orders) > 1:
            outcome.ambiguities.append({"email": args.get("email", ""),
                                        "candidates": [o.order_id for o in orders]})
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
        order, refusal = _owned_order(args.get("order_id", ""), outcome)
        if refusal:
            return refusal
        outcome.account_reads += 1
        return {"order_id": order.order_id, "title": order.title, "status": order.status,
                "carrier_ref": order.carrier_ref,
                "ordered_on": order.ordered_on.isoformat(),
                "delivered_on": order.delivered_on.isoformat() if order.delivered_on else None,
                "note": "Status is not a refund decision. For any return or refund question "
                        "call check_return_eligibility."}

    if name == "start_return":
        order, refusal = _owned_order(args.get("order_id", ""), outcome)
        if refusal:
            return refusal
        outcome.account_reads += 1
        reason, note = _reason(args.get("reason", ""), outcome)
        decision = refund_eligibility(order, reason)
        outcome.decisions.append(decision.code)
        payload = {"order_id": order.order_id, "title": order.title,
                   "reason": reason, **decision.to_dict(), **note}
        if decision.allowed:
            outcome.refunds.append(payload)
            payload["confirmation"] = f"RET-{order.order_id[-5:]}"
        return payload

    if name == "check_return_eligibility":
        order, refusal = _owned_order(args.get("order_id", ""), outcome)
        if refusal:
            return refusal
        outcome.account_reads += 1
        reason, note = _reason(args.get("reason", ""), outcome)
        decision = refund_eligibility(order, reason)
        outcome.decisions.append(decision.code)
        return {"order_id": order.order_id, "title": order.title,
                "reason": reason, **decision.to_dict(), **note}

    if name == "lookup_policy":
        topic = args.get("topic", "")
        found = find_policy(topic)
        if not found:
            outcome.policy_sources.append("")
            return {"topic": topic, "found": False, "text": "",
                    "note": "Bookly publishes nothing on this. Tell the customer that plainly "
                            "and offer a person. Do not invent an answer."}
        key, text = found
        outcome.policy_sources.append(text)
        return {"topic": key, "found": True, "text": text}

    if name == "escalate_to_human":
        ticket = f"HUM-{4470 + len(outcome.escalations) + 1}"
        outcome.escalations.append({**args, "ticket": ticket})
        return {"escalated": True, "ticket": ticket,
                "note": "A human agent will pick this up. Tell the customer, and tell them "
                        "the conversation is with that person from here."}

    return {"error": "unknown_tool"}
