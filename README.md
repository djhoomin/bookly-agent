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
was a state-changing action taken against the wrong order. Five of the seven cases
pass only if the agent *refuses* to do something, which is the half that
containment metrics cannot see.

## Model routing and abuse screening

Every inbound turn is screened first by Claude Haiku 4.5. One call, two jobs: it picks the
model for that turn, and it flags abusive language or fraud signals. Both want the same cheap
read of the same text, so splitting them would pay twice.

Measured across the eval suite, 25 API calls, at list prices:

| | Per conversation | 10,000/day | Per year |
|---|---|---|---|
| routed | $0.0099 | $99 | **$36,256** |
| all Opus | $0.0222 | $222 | **$80,900** |
| saved | **55%** | | **$44,643** |

`python -m bookly.costs measured.json` recomputes this from whatever usage you feed it, so it
runs against production traffic rather than needing a rewrite.

**Routing is an optimisation, not a safety mechanism.** The classifier called the ambiguous Dune
request *simple* and sent it to Haiku, which is arguably wrong. It did not matter: `find_orders`
returned two matches, the gate held, and the agent asked. Correctness lives in the policy
function and the tool interface, both model-independent, so a triage miss costs a less polished
reply and never a wrong refund.

Flagged turns append to `abuse_log.jsonl` with the message attached. A flag without the text
that produced it cannot be reviewed, and an unreviewable flag accumulates until the team stops
trusting it.

*The same shape, in production elsewhere:* a language-training product I am building with a
partner uses Mistral's moderation endpoint for this job. Different classifier, same
architecture: a small model in front deciding what the large one is allowed to be bothered with.

## Where the data is processed

`providers.py` makes the gateway and the jurisdiction configuration rather than a constant.

| `BOOKLY_PROVIDER` | Endpoint | Residency |
|---|---|---|
| `anthropic` *(default)* | Anthropic API | Anthropic's default regions |
| `openrouter` | `openrouter.ai/api/v1` | Unpinned |
| `openrouter-eu` | `eu.openrouter.ai/api/v1` | EU only. OpenRouter states prompts and completions "are processed within the selected region and do not leave it". Enterprise plan, enabled by request. |

For a European enterprise buyer this is a procurement gate, not a preference. A support agent
handles names, order history and complaints, so it is GDPR-bound by default. An architecture
that cannot answer the residency question does not reach a pilot at a bank, an insurer or a
public body however good the agent is. Here the answer is an environment variable.

`openai_bridge.py` is the adapter, and it is deliberately thin: it translates tool definitions
and tool results, and nothing else. A fatter adapter would be the all-in-one abstraction this
exercise asks us to avoid.

## Observability

`trace.jsonl` records one row per turn, and it records **decisions** rather than events: the
triage verdict, the policy code, the tools reached for, whether state changed, tokens and cost.

Generic APM cannot help here, because the interesting things are not exceptions. A refund
correctly refused is a 200 and a cheerful log line, and it is also the most important thing that
happened that day.

`python -m bookly.analyze` computes the operating numbers straight from the trace:

```
resolved without a human              86%   (6/7)
escalated                             14%   (1/7)
asked before acting                   57%   (4/7)

why the policy function refused
    3  outside_window

flagged turns: 1
  fraud_signal     pressure_to_bypass_policy_is_flagged

cost 0.0752 USD over 7 conversations = $0.01075 each
```

The test for whether the schema is right: can someone answer "why did we refuse 41 refunds last
week" without opening a transcript.

## What I would change first

**The trace found a hole in the architecture it was built to observe.** Four of eight turns
resolved *without calling the policy function*: the agent read the order status and the published
policy text and concluded for itself. The gate stops wrong approvals, because nothing refunds
without `start_return`. It does not force **refusals** through policy, so the model can decline
on its own reading and no code is recorded. That weakens the central claim of this design in a
specific, fixable way, and it is the first thing I would change: route every refund decision
through `policy.py`, including the negative ones.

Second:

**Then stop testing only the failures I thought of.** Two sources replace them, and they test
different layers.

**Real transcripts** give the agent's cases. What goes wrong in the first week of a deployment
never looks like what you invented at a desk, and those cases are the ones that decide whether
the agent improves or merely changes.

**A public adversarial set** gives the triage classifier a number instead of my opinion.
`deepset/prompt-injections` is 662 labelled rows under Apache 2.0, binary legitimate-versus-
injection, and was used to train PromptGuard, so the provenance is good. Caveat worth stating:
it is mostly German, so `xTRam1/safe-guard-prompt-injection` (~10k rows) is the larger
English-leaning alternative. Either turns "the screen seems to work" into precision and recall.

Neither covers the domain-specific social engineering that actually threatens a support agent:
claimed account authority, repeated refund attempts on one order, pressure to change delivery
details. Those cases have to come from real traffic, which is why the transcripts matter more.

Everything else is ordinary engineering: retrieval instead of a dict for policy text,
authentication before any state change, and a goodwill path behind a human.
