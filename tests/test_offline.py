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
                                       arguments=json.dumps({"order_id": "BK-09988",
                                                             "reason": "unwanted"}))
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


def test_order_ids_are_normalised():
    from bookly.backend import get_order, normalise_order_id
    for raw in ("bk10231", "BK 10231", "bk-10231", " Bk-10231 "):
        assert normalise_order_id(raw) == "BK-10231", raw
        assert get_order(raw) is not None, raw
    assert get_order("BK-99999") is None
    assert get_order("The Idiot") is None


class ScriptedTools:
    """An Anthropic-shaped client that makes the given tool calls, then stops."""

    def __init__(self, calls):
        self.messages = self
        self.calls = list(calls)

    def create(self, **kw):
        if kw.get("output_config"):
            block = types.SimpleNamespace(
                type="text", text='{"intent":"order_status","complexity":"simple",'
                                  '"risk":"none","reason":""}')
        elif self.calls:
            name, args = self.calls.pop(0)
            block = types.SimpleNamespace(type="tool_use", id=f"t{len(self.calls)}",
                                          name=name, input=args)
        else:
            block = types.SimpleNamespace(type="text", text="Could you share your email?")
        return types.SimpleNamespace(content=[block],
                                     usage=types.SimpleNamespace(input_tokens=1,
                                                                 output_tokens=1))


def test_fabricated_identifier_is_caught_and_probing_is_not():
    from evals.cases import _asked_rather_than_guessed
    msg = "Hi, I'm Sam. I ordered Piranesi last week, where is it?"

    agent = Agent(_client=ScriptedTools([("find_orders", {"email": "sam@example.com"})]))
    agent.say(msg)
    ok, note = _asked_rather_than_guessed(agent, ["..."])
    assert not ok and "never gave" in note, note

    agent = Agent(_client=ScriptedTools([("find_orders", {"email": "sam"})]))
    agent.say(msg)
    ok, _ = _asked_rather_than_guessed(agent, ["..."])
    assert ok, "probing with the customer's own word is harmless"
    assert "not an email address" in json.dumps(agent.history[-2].content)

    agent = Agent(_client=ScriptedTools([]))
    agent.say(msg)
    assert _asked_rather_than_guessed(agent, ["..."])[0]


def test_moderation_mapping_ignores_pii_and_keeps_haiku_fraud(monkeypatch=None):
    from bookly import moderation, triage
    m = moderation.Moderation("m", {"violence_and_threats": 0.79, "hate_and_discrimination": 0.57})
    assert m.risk == "abusive_language" and m.reason.startswith("violence_and_threats 0.79")
    assert moderation.Moderation("m", {}).risk == "none"
    assert moderation.Moderation("m", {"jailbreaking": 0.9}).risk == "fraud_signal"

    # screen(): Mistral clean, Haiku says fraud -> fraud stands, screener recorded
    class HaikuFraud:
        messages = None
        def __init__(self): self.messages = self
        def create(self, **kw):
            block = types.SimpleNamespace(type="text", text='{"intent":"return_refund",'
                '"complexity":"complex","risk":"fraud_signal","reason":"claimed authority"}')
            return types.SimpleNamespace(content=[block],
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))
    orig = triage.__dict__.get("moderate")
    import bookly.moderation as mod
    saved = mod.moderate
    mod.moderate = lambda text, timeout=10.0: moderation.Moderation("m", {}, 5)
    try:
        t, rows = triage.screen(HaikuFraud(), "x")
        assert t.risk == "fraud_signal" and t.screener == "haiku" and t.moderated and len(rows) == 2
        mod.moderate = lambda text, timeout=10.0: moderation.Moderation("m", {"violence_and_threats": 0.8}, 5)
        t, _ = triage.screen(HaikuFraud(), "x")
        assert t.risk == "abusive_language" and t.screener == "mistral" and "mistral:" in t.reason
        mod.moderate = lambda text, timeout=10.0: None
        t, rows = triage.screen(HaikuFraud(), "x")
        assert t.screener == "haiku" and not t.moderated and len(rows) == 1
    finally:
        mod.moderate = saved


def test_not_received_is_never_refunded():
    from bookly.backend import ORDERS
    from bookly.policy import refund_eligibility
    delivered, shipped, ebook = ORDERS["BK-10231"], ORDERS["BK-10244"], ORDERS["BK-10250"]
    assert refund_eligibility(delivered, "damaged").allowed
    assert refund_eligibility(delivered, "not_received").code == "delivery_dispute"
    assert refund_eligibility(shipped, "not_received").code == "in_transit"
    assert refund_eligibility(ebook, "not_received").code == "delivery_dispute"
    assert refund_eligibility(delivered, "because").code == "unknown_reason"
    for o in ORDERS.values():
        assert not refund_eligibility(o, "not_received").allowed


def test_customer_claim_overrides_the_models_reason():
    from bookly.tools import Outcome, dispatch
    out = Outcome()
    out.claim = "not_received"
    r = dispatch("start_return", {"order_id": "BK-10231", "reason": "unwanted"}, out)
    assert r["code"] == "delivery_dispute" and r["reason"] == "not_received" and "note" in r
    assert not out.refunds
    r = dispatch("check_return_eligibility", {"order_id": "BK-10231"}, out)
    assert r["code"] == "delivery_dispute"
    out2 = Outcome()
    out2.claim = "damaged"
    r = dispatch("check_return_eligibility", {"order_id": "BK-10231"}, out2)
    assert r["code"] == "eligible" and r["reason"] == "damaged"


def test_start_return_records_the_decision():
    from bookly.tools import Outcome, dispatch
    out = Outcome()
    r = dispatch("start_return", {"order_id": "BK-10231", "reason": "not_received"}, out)
    assert r["code"] == "delivery_dispute" and not out.refunds
    r = dispatch("start_return", {"order_id": "BK-10231", "reason": "damaged"}, out)
    assert r["code"] == "eligible" and len(out.refunds) == 1
    assert out.decisions == ["delivery_dispute", "eligible"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} offline checks passed")
