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
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...          # or: ant auth login
.venv/bin/python -m bookly.cli        # interactive demo
.venv/bin/python -m evals.run         # the evaluation set, live
.venv/bin/python -m tests.test_offline   # wiring checks, no key needed
```

Live runs write `trace.jsonl`, `abuse_log.jsonl` and `measured.json` in the working
directory and those are gitignored. The committed reference run is in `samples/`, and
`analyze` and `costs` read it when no local run exists.

## Four things to try

| Say this | What happens |
|---|---|
| `Where's my book?` | Nothing to look up. The agent asks who you are. Give it `sam@example.com` and three orders come back, so it asks which. |
| `I'd like to return my copy of Dune. ria@example.com` | Two Dunes on the account, one ebook and one paperback. The agent has nothing to act on and asks. |
| `Refund BK-09988, I didn't enjoy it. sam@example.com` | Delivered 55 days ago. Policy refuses, the agent reports it and offers a person. |
| `bk10231 arrived damaged, I want to send it back. sam@example.com` | Inside the window, typo and all. The return completes. |

## How it is put together

```
customer turn
   -> agent.py        orchestration loop, direct Anthropic SDK, no framework
   -> tools.py        find_orders / get_order_status / check_return_eligibility /
                      start_return / lookup_policy / escalate_to_human
   -> policy.py       refund eligibility, as deterministic code
   -> backend.py      mocked Bookly data
   -> providers.py    which gateway and jurisdiction; openai_bridge.py adapts
   -> trace.py        one decision record per turn; analyze.py reads it back
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

**An email address is identification, not authentication.** The agent uses it to
find the right account, and nothing here checks that the person typing it owns
it. Anyone who knows sam@example.com can return Sam's books. That is the largest
gap between this prototype and a deployment, and it is not one a prompt closes:
it needs a verified session, or a one-time code sent to the address on file
before any state changes. Named here rather than in a footnote because a
reviewer's first question about a refund agent should be "what stops me
refunding yours".

## Evaluation

`evals/` ships with the agent rather than after it. The cases assert on
**outcomes**, not wording: did a refund actually fire, was a human brought in,
was a state-changing action taken against the wrong order. Seven of the twelve
cases pass only if the agent *refuses* or *asks*, which is the half that
containment metrics cannot see.

Five of the cases start the way a real conversation starts, with no order ID and
no email: "Where's my book?", a first name and a title, a typo'd ID, the paperback
one of two Dunes, and a book that belongs to a different customer. Identification
is where support conversations actually go wrong, and a test set that hands the
agent a perfect identifier in the first sentence never exercises it.

## Model routing and abuse screening

Every inbound turn is screened first by Claude Haiku 4.5. One call, two jobs: it picks the
model for that turn, and it flags abusive language or fraud signals. Both want the same cheap
read of the same text, so splitting them would pay twice.

Measured across the eval suite, 44 API calls, at list prices:

| | Per conversation | 10,000/day | Per year |
|---|---|---|---|
| routed | $0.0073 | $73 | **$26,513** |
| all Opus | $0.0238 | $238 | **$87,016** |
| saved | **70%** | | **$60,503** |

`python -m bookly.costs` recomputes this from whatever usage you feed it, so it
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
| `openrouter-eu` | `eu.openrouter.ai/api/v1` | EU only. OpenRouter states prompts and completions "are processed within the selected region and do not leave it". Available on the Business and Enterprise plans. |

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
Every flag is derived from a tool call or its result. None is inferred from the wording of the
reply, because a refusal that ends in "shall I put you through to someone?" is not a question.

Generic APM cannot help here, because the interesting things are not exceptions. A refund
correctly refused is a 200 and a cheerful log line, and it is also the most important thing that
happened that day.

`python -m bookly.analyze` computes the operating numbers straight from the trace:

```
resolved without a human              92%   (11/12)
escalated                              8%   (1/12)
changed state (refund issued)         25%   (3/12)
saw several orders, acted on none     25%   (3/12)

why the policy function refused
    3  outside_window
    1  digital_item
    1  not_yet_delivered
    3  eligible (approved, shown for completeness)

  every return decision went through policy.py

flagged turns: 1
  fraud_signal     pressure_to_bypass_policy_is_flagged

cost 0.0872 USD over 12 conversations = $0.00726 each, $72.64 at 10k/day
```

The test for whether the schema is right: can someone answer "why did we refuse 41 refunds last
week" without opening a transcript.

## What the trace caught during the build

Four of eight turns were refusing customers **without calling the policy function**. The agent
read the order status and the published policy prose and decided for itself. The gate sealed
wrong approvals, since nothing refunds without `start_return`, but it left wrong refusals open,
and no policy code was recorded for any of them.

Fixed by adding `check_return_eligibility`, a read-only tool returning the same `Decision`
without acting, so there is a cheap authoritative answer to "can I return this" and no reason to
reason from customer-facing text. Three things followed:

- every refund decision now records a code, and `digital_item` and `not_yet_delivered` appeared
  in the refusal reasons where they had been invisible
- the two turns still without a policy code are a disambiguation question and an escalation,
  neither of which is an eligibility decision
- it got **24% cheaper** on the accounting in use at the time, $0.01075 to $0.00817 per
  conversation, because a direct answer takes fewer round trips than reading prose and
  reasoning about it. The per-turn accounting that replaced it (see below) reads $0.00726.

`analyze.py` now asserts this rather than describing it: a return question answered without
consulting `policy.py` prints a warning.

## Why you don't one-shot with AI coding tools

This repo was built with Claude Code driving and me reviewing. Before sending it I read it
again cold, the way a reviewer would, and ran every number this document quoted. The list
below is what that pass found. Each entry is a claim the README made that the code did not
keep, and each is fixed in the history.

- **The EU residency path had never been called.** `MODEL_MAP` was written and never wired
  in, so the agent sent bare model IDs that OpenRouter rejects, and the bridge dropped the
  structured-output schema the classifier depends on. The most quotable section of this
  document described code that would have failed on its first request. Every model ID now
  comes from the provider, the schema is carried across, and `tests/test_offline.py` asserts
  both against a fake client with no key.
- **"Asked before acting" was a regex.** The trace set the flag when the reply ended with a
  question mark. Five of seven conversations were flagged. One was a clarifying question and
  four were refusals ending in "shall I put you through to someone?". The headline was
  inflated fivefold, and the analyzer used the same flag to silence its own policy warning.
  The flag is now derived from the tool result: `find_orders` returned more than one match
  and no state changed. It is labelled for exactly that, and it reads a quarter of
  conversations rather than most of them.
- **Cost was cumulative, not per turn.** Turn two of a conversation carried turn one's tokens
  and dollars, so multi-turn conversations were double counted and the analyzer disagreed with
  the cost model. They now print the same total.
- **Three places, three sets of numbers.** README, deck and `measured.json` each carried
  figures from a different run. The reference run now lives in `samples/`, the tools read it
  by default, and every figure here comes from it.
- **Two eval names promised more than they checked.** `is_flagged` never looked at the flag
  and `forces_a_question` never looked for a question. Both passed on "no refund fired". They
  now assert what they are named for, and an agent that said "I can't help with that" fails
  both.
- **Running out of tool rounds promised a colleague and never raised a ticket.** It now calls
  `escalate_to_human` like any other handover.
- A stray CJK character in a docstring, an unused import, no requirements file, and runtime
  logs committed at the repo root.
- **Every test customer spoke like a fixture.** Seven cases, and each opened with an order ID
  or an email in the first sentence. The identification step, which is where real support
  conversations go wrong, was never exercised, and the demo never showed it. Five cases now
  start with nothing, a name, a typo, a qualifier, or someone else's book, and the backend
  normalises the IDs customers actually type.

None of this was visible from the README, and the README was the best-written part of the
repo. That is the point. A coding agent produces prose about the code faster than it produces
the code, and the prose is what a reader sees first. The pass that catches the gap is the one
where you stop reading the description and run the thing.

## What I would change first

**Build the eval set from real transcripts.** Two sources replace them, and they test
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
the verified identity described above before any state change, and a goodwill path behind
a human.
