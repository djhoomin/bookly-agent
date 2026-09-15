"""Run the eval set. `python -m evals.run [--case NAME]`"""

from __future__ import annotations

import argparse
import sys

from bookly.agent import Agent
from evals.cases import CASES


def fatal(exc: Exception) -> bool:
    """Billing and auth failures will fail every case identically. Stop on the
    first rather than print the same error once per conversation."""
    text = str(exc).lower()
    return any(s in text for s in ("credit balance", "authentication", "api key",
                                   "api_key", "permission"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="Run one case by name")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.case or c.name == args.case]
    if not cases:
        print(f"No case named {args.case!r}", file=sys.stderr)
        return 2

    passed = 0
    for case in cases:
        agent = Agent()
        replies = []
        try:
            for turn in case.turns:
                replies.append(agent.say(turn))
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {case.name}: {type(exc).__name__}: {exc}")
            if fatal(exc):
                print("\nStopping: this will fail every case the same way.", file=sys.stderr)
                return 3
            continue
        ok, note = case.check(agent, replies)
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case.name}: {note}")
        print(f"         tools: {' -> '.join(agent.outcome.calls) or 'none'}")
        if not ok:
            print(f"         why it matters: {case.why}")
            print(f"         last reply: {replies[-1][:160] if replies else '(none)'}")

    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
