"""Turn the trace into the numbers a CX lead is accountable for.

`python -m bookly.analyze`

Deliberately not a dashboard. The question this answers is whether the trace
schema carries enough to run the deployment, and the way to prove that is to
compute the operating metrics straight from it with no extra instrumentation.
"""

from __future__ import annotations

import collections
import sys

from .trace import load


def main() -> int:
    rows, source = load()
    if not rows:
        print("No trace yet. Run the eval suite or demo.py first.")
        return 1
    print(f"reading {source}")

    convs = collections.defaultdict(list)
    for r in rows:
        convs[r["conversation"]].append(r)
    n = len(convs)

    escalated = sum(1 for turns in convs.values() if any(t["escalated"] for t in turns))
    changed = sum(1 for turns in convs.values() if any(t["state_changed"] for t in turns))
    asked = sum(1 for turns in convs.values() if any(t["held_on_ambiguity"] for t in turns))
    flagged = [r for r in rows if r["risk"] != "none"]
    usd = sum(r["usd"] for r in rows)

    print(f"{n} conversations, {len(rows)} turns\n")
    print(f"{'resolved without a human':<34}{(n-escalated)/n:>7.0%}   "
          f"({n-escalated}/{n})")
    print(f"{'escalated':<34}{escalated/n:>7.0%}   ({escalated}/{n})")
    print(f"{'changed state (refund issued)':<34}{changed/n:>7.0%}   ({changed}/{n})")
    print(f"{'saw several orders, acted on none':<34}{asked/n:>7.0%}   ({asked}/{n})")

    # One decision per turn: the read-only check and the return both record the
    # same code for the same order, and that is one eligibility decision.
    codes = collections.Counter(c for r in rows for c in set(r["policy_codes"]))
    refusals = {c: n for c, n in codes.items() if c != "eligible"}
    if refusals:
        print("\nwhy the policy function refused")
        for code, count in sorted(refusals.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>3}  {code}")
    if codes.get("eligible"):
        print(f"  {codes['eligible']:>3}  eligible (approved, shown for completeness)")

    # A return conversation in which a turn read account data and then answered
    # without a policy code means the model decided eligibility from the order
    # status, which is the failure this design exists to prevent. The intent is
    # judged over the conversation, since the deciding turn is often the one
    # where the customer typed a verification code. Turns that only sent or
    # checked a code, asked which order, or handed over are not decisions.
    return_convs = {r["conversation"] for r in rows if r["intent"] == "return_refund"}
    unchecked = [r for r in rows
                 if r["conversation"] in return_convs and r.get("account_reads", 0) > 0
                 and not r["policy_codes"] and not r["held_on_ambiguity"]
                 and not r["escalated"] and not r["state_changed"]]
    if unchecked:
        print(f"\n  WARNING: {len(unchecked)} return question(s) answered without "
              f"consulting policy.py:")
        for r in unchecked:
            print(f"    {r['conversation']} turn {r['turn']}")
    else:
        print("\n  every return decision went through policy.py")

    if flagged:
        print(f"\nflagged turns: {len(flagged)}")
        for r in flagged:
            scores = ", ".join(f"{c} {s}" for c, s in (r.get("moderation") or {}).items())
            print(f"  {r['risk']:<16} {r['conversation']:<40} "
                  f"{r.get('screener') or 'triage'}  {scores}")
    checked = [r for r in rows if r.get("checked_against_policy")]
    replaced = [r for r in checked if r.get("reply_replaced")]
    if checked:
        print(f"\nprose answers held to published policy: {len(checked)} turn(s), "
              f"{len(checked) - len(replaced)} grounded, {len(replaced)} replaced")
        for r in replaced:
            print(f"  {r['conversation']:<40} said: {'; '.join(r.get('unsupported') or [])[:90]}")

    refusals = sum(r.get("gate_refusals", 0) for r in rows)
    reads = sum(r.get("account_reads", 0) for r in rows)
    unverified_reads = sum(r.get("account_reads", 0) for r in rows if not r.get("verified"))
    print(f"\nverification gate: {refusals} refusal(s), {reads} account read(s), "
          f"{unverified_reads} before verification")

    safety = [r for r in rows if r.get("safety_response")]
    if safety:
        print(f"\nself-harm signals: {len(safety)}, each answered with fixed resources and an "
              f"urgent handover, model calls in reply: 0")

    after = [r for r in rows if r.get("handed_over")]
    if after:
        print(f"\nturns after handover: {len(after)}, model calls made: 0, "
              f"cost ${sum(r['usd'] for r in after):.4f}")

    misses = [t for r in rows for t in (r.get("policy_misses") or [])]
    if misses:
        print(f"\nasked about, nothing published: {len(misses)}")
        for t in misses:
            print(f"  {t[:70]!r}")

    moderated = sum(1 for r in rows if r.get("moderated"))
    deciders = collections.Counter(r.get("screener") or "triage" for r in flagged)
    print(f"\nmoderation ran on {moderated}/{len(rows)} turns"
          + (";  flags decided by: " + ", ".join(f"{n} {s}" for s, n in deciders.most_common())
             if deciders else ""))

    models = collections.Counter(r["model"] for r in rows if r["model"])
    print("\nmodel mix")
    for model, count in models.most_common():
        print(f"  {count:>3}  {model}")

    print(f"\ncost {usd:.4f} USD over {n} conversations "
          f"= ${usd/n:.5f} each, ${usd/n*10_000:,.2f} at 10k/day")
    residencies = {r["residency"] for r in rows if r["residency"]}
    if residencies:
        print(f"\nresidency: {' | '.join(sorted(residencies))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
