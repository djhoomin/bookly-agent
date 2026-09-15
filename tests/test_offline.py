"""Checks that need no API key. `python -m tests.test_offline`

These exist because the OpenRouter path shipped once without ever having been
called: the model map was written and never wired in, and the bridge dropped the
structured-output schema on the floor. A fake client that records the request
catches both, for free, on every run.
"""
from __future__ import annotations

import json
import os
import types

os.environ["BOOKLY_TRACE_LOG"] = os.devnull
os.environ["BOOKLY_ABUSE_LOG"] = os.devnull

from bookly import openai_bridge, providers, triage  # noqa: E402
from bookly.agent import Agent  # noqa: E402


def _completion(content=None, tool_calls=None):
    msg = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                 usage=types.SimpleNamespace(prompt_tokens=100,
                                                             completion_tokens=10))


class FakeOpenAI:
    """Records every request; answers triage, then a tool call, then text."""

    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("response_format"):
            return _completion('{"intent":"return_refund","complexity":"simple",'
                               '"risk":"none","reason":"r"}')
        if not any(m["role"] == "tool" for m in kw["messages"]):
            fn = types.SimpleNamespace(name="check_return_eligibility",
                                       arguments=json.dumps({"order_id": "BK-09988"}))
            return _completion(tool_calls=[types.SimpleNamespace(id="call_1", function=fn)])
        return _completion("That one is outside the window.")


class AlwaysToolUse:
    """An Anthropic-shaped client that never stops calling tools."""

    def __init__(self):
        self.messages = self
        self.n = 0

    def create(self, **kw):
        self.n += 1
        if kw.get("output_config"):
            block = types.SimpleNamespace(
                type="text", text='{"intent":"order_status","complexity":"simple",'
                                  '"risk":"none","reason":""}')
            usage = types.SimpleNamespace(input_tokens=10, output_tokens=1)
        else:
            block = types.SimpleNamespace(type="tool_use", id=f"t{self.n}",
                                          name="get_order_status",
                                          input={"order_id": "BK-10231"})
            usage = types.SimpleNamespace(input_tokens=1000, output_tokens=10)
        return types.SimpleNamespace(content=[block], usage=usage)


def test_parse_tolerates_fences_and_fails_heavy():
    fenced = '```json\n{"intent":"other","complexity":"simple","risk":"none","reason":"x"}\n```'
    assert triage._parse(fenced).tier == "light"
    bad = triage._parse("not json at all")
    assert bad.tier == "heavy" and "did not parse" in bad.reason


def test_openrouter_path_sends_namespaced_ids_and_schema():
    fake = FakeOpenAI()
    client = openai_bridge.OpenAICompatClient.__new__(openai_bridge.OpenAICompatClient)
    client._inner, client.messages = fake, openai_bridge._Messages(fake)
    agent = Agent(provider=providers.PROVIDERS["openrouter-eu"], _client=client,
                  conversation_id="offline")
    reply = agent.say("Refund BK-09988 please, sam@example.com")

    assert [c["model"] for c in fake.calls] == ["anthropic/claude-haiku-4-5"] * 3
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    tool_msgs = [m for m in fake.calls[2]["messages"] if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["code"] == "outside_window"
    assert agent.usage[0][:2] == ("triage", "anthropic/claude-haiku-4-5")
    assert agent._usd(agent.usage) > 0, "vendor-prefixed IDs must still price"
    assert reply == "That one is outside the window."


def test_exhausted_tool_budget_escalates_and_usage_is_per_turn():
    agent = Agent(_client=AlwaysToolUse(), conversation_id="loop", max_tool_rounds=2)
    agent.say("first")
    agent.say("second")
    assert len(agent.outcome.escalations) == 2
    assert agent.outcome.escalations[0]["reason"] == "tool_rounds_exhausted"
    assert len(agent.usage) == 6  # triage + 2 rounds, twice
    assert agent._usd(agent.usage[3:]) == agent._usd(agent.usage[:3])


def test_ambiguity_is_recorded_from_the_tool_result():
    from bookly.tools import Outcome, dispatch
    out = Outcome()
    dispatch("find_orders", {"email": "ria@example.com"}, out)
    assert len(out.ambiguities) == 1 and len(out.ambiguities[0]["candidates"]) == 2
    dispatch("find_orders", {"email": "nobody@example.com"}, out)
    assert len(out.ambiguities) == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} offline checks passed")
