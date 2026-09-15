"""Run the eval conversations and record measured token usage. `python measure.py`"""
import json
from bookly.agent import Agent
from evals.cases import CASES, run_case

rows, n = [], 0
for case in CASES:
    agent = Agent(conversation_id=case.name)
    ok, note, _replies = run_case(agent, case)
    rows.extend(agent.usage)
    n += 1
    light = agent.provider.model("light")
    heavy = {m for r, m, _i, _o in agent.usage if r == "resolve" and m != light}
    print(f"[{'PASS' if ok else 'FAIL'}] {case.name}: {note}"
          f"  (heavy: {heavy or 'no'})")

json.dump({"conversations": n, "rows": rows}, open("measured.json", "w"))
print(f"\nwrote measured.json: {len(rows)} API calls over {n} conversations")
