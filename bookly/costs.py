"""What routing is actually worth, from measured tokens.

Run after the eval suite: `python -m bookly.costs measured.json`

The input is real usage captured from live runs, not an estimate. Prices are
Anthropic list, June 2026.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "measured.json"

PRICES = {  # USD per million tokens
    "claude-opus-5":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}


def cost(model: str, tin: int, tout: int) -> float:
    p = PRICES[model.split("/")[-1]]  # gateways namespace by vendor
    return tin / 1e6 * p["in"] + tout / 1e6 * p["out"]


def report(rows: list[tuple[str, str, int, int]], conversations: int,
           per_day: int = 10_000):
    by_model: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for _role, model, tin, tout in rows:
        by_model[model][0] += tin
        by_model[model][1] += tout

    routed = sum(cost(m, i, o) for m, (i, o) in by_model.items())
    # Counterfactual: the same resolution work on Opus, with no triage pass at
    # all. Triage calls are excluded rather than repriced, because without
    # routing they would not be made.
    flat = sum(cost("claude-opus-5", tin, tout)
               for role, _model, tin, tout in rows if role == "resolve")

    print(f"measured over {conversations} conversations, {len(rows)} API calls\n")
    print(f"{'model':<20}{'calls':>7}{'in tok':>10}{'out tok':>10}{'cost':>10}")
    for model, (tin, tout) in sorted(by_model.items()):
        n = sum(1 for _r, m, _i, _o in rows if m == model)
        print(f"{model:<20}{n:>7}{tin:>10,}{tout:>10,}{cost(model, tin, tout):>10.4f}")
    print(f"\n{'routed total':<20}{'':>7}{'':>10}{'':>10}${routed:>9.4f}")
    print(f"{'all-Opus total':<20}{'':>7}{'':>10}{'':>10}${flat:>9.4f}")

    saving = 1 - routed / flat if flat else 0
    print(f"\nsaving: {saving:.0%}")
    print(f"\nper conversation:  routed ${routed/conversations:.5f}   "
          f"all-Opus ${flat/conversations:.5f}")
    d_routed = routed / conversations * per_day
    d_flat = flat / conversations * per_day
    print(f"at {per_day:,}/day:   routed ${d_routed:,.2f}/day   all-Opus ${d_flat:,.2f}/day")
    print(f"per year:          routed ${d_routed*365:,.0f}   all-Opus ${d_flat*365:,.0f}   "
          f"difference ${(d_flat-d_routed)*365:,.0f}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "measured.json"
    if not Path(path).exists() and len(sys.argv) == 1:
        path = SAMPLE
    print(f"reading {path}")
    data = json.load(open(path))
    report([tuple(r) for r in data["rows"]], data["conversations"])
