# Bookly support agent

A customer support agent for a fictional online bookstore, built for the Decagon
Solutions Engineering take-home.

**The thesis in one line: containment is the wrong north star.** The industry
measures the share of tickets resolved without a human, and that pressure
produces agents which confidently do the wrong thing rather than hand over. The
target here is *resolution without harm*, which means the agent has to prove it
knows enough to act before it acts.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install anthropic
export ANTHROPIC_API_KEY=...          # or: ant auth login
.venv/bin/python -m bookly.cli        # interactive demo
.venv/bin/python -m evals.run         # the evaluation set
```

## Three things to try

| Say this | What happens |
|---|---|
| `I'd like to return my copy of Dune. ria@example.com` | Two Dunes on the account, one ebook and one paperback. The agent has nothing to act on and asks. |
| `Refund BK-09988, I didn't enjoy it. sam@example.com` | Delivered 55 days ago. Policy refuses, the agent reports it and offers a person. |
| `I'd like to return BK-10231, it arrived damaged. sam@example.com` | Inside the window. The return completes. |

## How it is put together

```
customer turn
   -> agent.py        orchestration loop, direct Anthropic SDK, no framework
   -> tools.py        find_orders / get_order_status / start_return /
                      lookup_policy / escalate_to_human
   -> policy.py       refund eligibility, as deterministic code
   -> backend.py      mocked Bookly data
```

Memory is the conversation history held on the `Agent` instance, which is
deliberately boring: one conversation, one object, no hidden state.

## The two decisions that mattered

**Policy is code, not prompt.** A prompt saying "only refund within 30 days" is a
suggestion, and a model being helpful to an unhappy customer will find a reading
of it that permits yes. `policy.refund_eligibility` returns
`ineligible: outside_window`, which is inspectable, testable, and returns the
same answer whether the customer is polite or furious. `start_return` calls it
directly, so the model reports the verdict and cannot reach around it. The system
prompt is short as a consequence, and its shortness is the evidence: everything
that could move into code has.

*Traded away:* flexibility. There is no path for a goodwill exception, which a
real deployment needs. That belongs behind an authenticated human, not behind a
persuasive customer.

**The clarifying question is a gate, not a behaviour.** `start_return` requires a
resolved `order_id`, and `find_orders` returns every matching order. When there is
more than one, the model has nothing it can act on, so it asks. Prompting a model
to "ask when unsure" produces something that usually happens; removing the code
path produces something that always does.

*Traded away:* a turn of latency on ambiguous requests. Worth it: the Dune case
is a coin flip between refunding a returnable paperback and a non-returnable
ebook, and the wrong call is visible to the customer.

## Evaluation

`evals/` ships with the agent rather than after it. The cases assert on
**outcomes**, not wording: did a refund actually fire, was a human brought in,
was a state-changing action taken against the wrong order. Four of the six cases
pass only if the agent *refuses* to do something, which is the half that
containment metrics cannot see.

## What I would change first

**Build the eval set from real transcripts.** These six cases test failures I
thought of, which is the weakest kind of test. The first week of a real
deployment would produce the cases that actually matter, and they would not look
like these. Everything else (retrieval for policy instead of a dict, auth before
any state change, a goodwill path behind a human) is ordinary engineering. The
eval set is the thing that decides whether the agent gets better or merely
changes.
