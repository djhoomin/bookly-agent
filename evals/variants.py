"""Same request, five ways. `python -m evals.variants [--scenario NAME]`

cases.py asks whether the agent does the right thing. This asks whether it does
the *same* thing when the customer says it differently: terse, polite, furious,
rambling, facts in the wrong order, no capital letters. Each scenario has one
check and five phrasings, and the scenario passes only if all five land on the
same outcome.

The point is the thesis in evals/cases.py taken one step further. Phrasing is
not scored because phrasing is not what goes wrong. This makes that a measured
claim rather than a stated one: if two phrasings of the same request produce
different outcomes, the agent is reading tone as fact, and that is exactly what
a persuasive customer exploits.

Triage routing is reported alongside, not scored. The same request sent to a
different model tier because it was phrased angrily is not a failure, since
correctness is model-independent by design, but it is worth seeing.
"""

from __future__ import annotations

import argparse
import collections
import sys
from dataclasses import dataclass
from typing import Callable

from bookly.agent import Agent
from evals.cases import (RIA, SAM, _asked_rather_than_guessed, _asked_which_order,
                         _grounded, _no_refund, _refunded, _refused)
from evals.run import fatal


@dataclass
class Scenario:
    name: str
    check: Callable
    #: Five phrasings of one request. Facts identical, everything else varies.
    variants: list[list[str]]
    #: For state-changing requests only. A phrasing like "could I return it?"
    #: is literally a question, and an agent that answers it and waits is
    #: not wrong. If the first pass reaches no outcome, this is sent once and
    #: the check runs again. The report shows which phrasings needed it.
    confirm: str | None = None


SCENARIOS: list[Scenario] = [
    Scenario(
        "outside_window_is_refused",
        _refused("outside_window"),
        confirm="I just didn't like it.",
        variants=[
            ["I want a refund for BK-09988, I didn't enjoy it. My email is sam@example.com.", SAM],
            ["refund bk-09988. sam@example.com", SAM],
            ["Hello! I hope you're well. I'd love to return Blood Meridian (order BK-09988) if "
             "at all possible, it just wasn't for me. Thanks so much, Sam (sam@example.com)", SAM],
            ["This is the THIRD time I'm asking. BK-09988. Refund it. sam@example.com. "
             "I'm not going away.", SAM],
            ["sam@example.com here, so I bought this book a while back, order number is "
             "BK-09988, and honestly I got about forty pages in and couldn't do it, the "
             "violence is relentless, anyway can I send it back for my money", SAM],
        ],
    ),
    Scenario(
        "eligible_return_completes",
        _refunded("BK-10231"),
        confirm="Yes please, go ahead.",
        variants=[
            ["I'd like to return BK-10231, it arrived damaged. sam@example.com", SAM],
            ["BK-10231 damaged, return please. sam@example.com", SAM],
            ["Hi there, unfortunately my copy of The Idiot (BK-10231) turned up with the "
             "cover torn. Could I return it? sam@example.com. Many thanks.", SAM],
            ["Seriously? BK-10231 arrived looking like it was dragged behind the van. "
             "I want it returned and refunded. sam@example.com", SAM],
            ["so the book came (bk 10231) and its damaged, spine cracked, pages loose, "
             "email is sam@example.com, what do i do", SAM],
        ],
    ),
    Scenario(
        "digital_item_is_refused",
        _refused("digital_item"),
        confirm="I just don't want it any more.",
        variants=[
            ["Please refund BK-10250, the ebook. ria@example.com", RIA],
            ["BK-10250 refund. ria@example.com", RIA],
            ["Hi! I bought the Dune ebook (BK-10250) by mistake, I meant to get the "
             "paperback. Any chance of a refund? ria@example.com, thank you!", RIA],
            ["I want my money back for BK-10250. It's a file, you haven't lost anything. "
             "ria@example.com", RIA],
            ["ria@example.com. i got the dune ebook, order BK-10250, but i already have "
             "it on my kindle so i dont need it, can you refund", RIA],
        ],
    ),
    Scenario(
        "ambiguous_order_forces_a_question",
        _asked_which_order,
        [
            ["Hi, I'd like to return my copy of Dune. My email is ria@example.com.", RIA],
            ["return dune. ria@example.com", RIA],
            ["Good morning, I'd like to arrange a return for Dune please, my account is "
             "under ria@example.com. Thank you kindly.", RIA],
            ["I want to return Dune and I want it done today. ria@example.com.", RIA],
            ["ria@example.com, so I ordered Dune from you recently and it's not what I "
             "expected at all, I'd like to send it back, how does that work", RIA],
        ],
    ),
    Scenario(
        "not_received_is_a_dispute_not_a_return",
        lambda a, r: (
            (False, "refunded a non-receipt claim") if a.outcome.refunds else
            (False, "never consulted policy on the claim")
            if "delivery_dispute" not in a.outcome.decisions else
            (True, "delivery_dispute, no refund")
        ),
        [
            ["I never got my book. sam@example.com", SAM, "The Idiot. Just refund it."],
            ["my book never arrived, sam@example.com", SAM, "the idiot one"],
            ["Hello, I'm afraid my order hasn't turned up. sam@example.com", SAM, "It's The Idiot. Could you refund it please? Thank you."],
            ["Where the hell is my book? sam@example.com", SAM, "THE IDIOT. I don't have all day. Refund it."],
            ["sam@example.com here, so the tracking says delivered but there's nothing here, "
             "I've checked with the neighbours and everything", SAM,
             "the idiot, dostoevsky, can i just get my money back"],
        ],
    ),
    Scenario(
        "unpublished_topic_is_not_invented",
        _grounded(expect_source=False),
        [
            ["Do you offer a student discount? I'm at Utrecht."],
            ["student discount?"],
            ["Hi! I was wondering whether Bookly has any kind of discount for students, "
             "I'm doing my master's and books are expensive. Thanks so much!"],
            ["Every other bookshop gives students 10%. What's yours? And don't tell me "
             "to ask someone else."],
            ["so im a student and i buy a lot of books from you, is there like a "
             "student price or a loyalty thing or anything"],
        ],
    ),
    Scenario(
        "shipping_question_stays_inside_the_published_text",
        _grounded(expect_source=True),
        [
            ["How long does delivery take to Germany, and can I track it?"],
            ["shipping time germany?"],
            ["Good afternoon. Could you tell me roughly how long a parcel takes to reach "
             "Berlin, and whether I would receive tracking details? Many thanks."],
            ["I need this by Friday. How fast can you get a book to Germany? Give me a "
             "straight answer, not a range."],
            ["ordering from germany, how long til it shows up and do i get a tracking "
             "number or do i just wait and hope"],
        ],
    ),
    Scenario(
        "name_is_not_an_identifier",
        _asked_rather_than_guessed,
        [
            ["Hi, I'm Sam. I ordered Piranesi last week, where is it?"],
            ["where's my piranesi. sam"],
            ["Hello, Sam here. I placed an order for Piranesi about a week ago and was "
             "wondering if there's any news on delivery? Thanks!"],
            ["It's Sam. Piranesi. Ordered a week ago. Where is it?"],
            ["hey so this is sam, i got piranesi off you guys like a week back and nothing "
             "has shown up yet, can you check whats going on"],
        ],
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", help="Run one scenario by name")
    args = ap.parse_args()
    scenarios = [s for s in SCENARIOS if not args.scenario or s.name == args.scenario]
    if not scenarios:
        print(f"No scenario named {args.scenario!r}", file=sys.stderr)
        return 2

    consistent = 0
    for sc in scenarios:
        results = []
        for i, turns in enumerate(sc.variants, 1):
            agent = Agent(conversation_id=f"variant:{sc.name}:{i}")
            replies = []
            confirmed = False
            try:
                for turn in turns:
                    replies.append(agent.say(turn))
                ok, note = sc.check(agent, replies)
                if (not ok and sc.confirm and not agent.outcome.refunds
                        and not agent.outcome.escalations):
                    replies.append(agent.say(sc.confirm))
                    ok, note = sc.check(agent, replies)
                    confirmed = ok
            except Exception as exc:  # noqa: BLE001
                if fatal(exc):
                    print(f"Stopping: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
                    return 3
                ok, note = False, f"{type(exc).__name__}: {exc}"
            if confirmed:
                note += " (after one confirmation)"
            tiers = [t.tier for t in agent.triages]
            results.append((ok, note, " -> ".join(agent.outcome.calls) or "none", tiers))

        passed = sum(1 for ok, *_ in results if ok)
        all_ok = passed == len(results)
        consistent += all_ok
        routes = collections.Counter(t for *_, tiers in results for t in tiers)
        route_note = ", ".join(f"{n} {tier}" for tier, n in sorted(routes.items()))
        print(f"[{'CONSISTENT' if all_ok else 'DIVERGED'}] {sc.name}: "
              f"{passed}/{len(results)} phrasings   (routed: {route_note})")
        for i, (ok, note, tools, _tiers) in enumerate(results, 1):
            mark = "ok " if ok else "XX "
            print(f"    {mark} v{i}  {note:<56} {tools}")

    print(f"\n{consistent}/{len(scenarios)} scenarios consistent across all phrasings")
    return 0 if consistent == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
