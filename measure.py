"""Run the eval conversations and record measured token usage. `python measure.py`"""
import json
from bookly.agent import Agent
from evals.cases import CASES

rows, n = [], 0
for case in CASES:
    agent = Agent(conversation_id=case.name)
    for turn in case.turns:
        agent.say(turn)
    rows.extend(agent.usage)
    n += 1
    ok, note = case.check(agent.outcome, [])
    heavy = {m for r, m, _i, _o in agent.usage if r == "resolve" and m != "claude-haiku-4-5"}
    print(f"[{'PASS' if ok else 'FAIL'}] {case.name}: {note}"
          f"  (heavy: {heavy or 'no'})")

json.dump({"conversations": n, "rows": rows}, open("measured.json", "w"))
print(f"\nwrote measured.json: {len(rows)} API calls over {n} conversations")
