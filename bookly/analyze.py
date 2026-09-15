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
    asked = sum(1 for turns in convs.values() if any(t["asked_which_order"] for t in turns))
    flagged = [r for r in rows if r["risk"] != "none"]
    usd = sum(r["usd"] for r in rows)

    print(f"{n} conversations, {len(rows)} turns\n")
    print(f"{'resolved without a human':<34}{(n-escalated)/n:>7.0%}   "
          f"({n-escalated}/{n})")
    print(f"{'escalated':<34}{escalated/n:>7.0%}   ({escalated}/{n})")
    print(f"{'changed state (refund issued)':<34}{changed/n:>7.0%}   ({changed}/{n})")
    print(f"{'asked which order before acting':<34}{asked/n:>7.0%}   ({asked}/{n})")

    codes = collections.Counter(c for r in rows for c in r["policy_codes"])
    refusals = {c: n for c, n in codes.items() if c != "eligible"}
    if refusals:
        print("\nwhy the policy function refused")
        for code, count in sorted(refusals.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>3}  {code}")
    if codes.get("eligible"):
        print(f"  {codes['eligible']:>3}  eligible (approved, shown for completeness)")

    # A return question answered without consulting the policy function means the
    # model decided for itself, which is the failure this design exists to
    # prevent. Turns with other intents legitimately have no policy code: asking
    # which order, or handing over to a person, are not eligibility decisions.
    unchecked = [r for r in rows
                 if r["intent"] == "return_refund" and not r["policy_codes"]
                 and not r["asked_which_order"] and not r["escalated"]]
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
            print(f"  {r['risk']:<16} {r['conversation']}")

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
