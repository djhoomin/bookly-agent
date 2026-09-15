"""Interactive demo. `python -m bookly.cli`"""

from __future__ import annotations

import sys

from .agent import Agent

BANNER = """Bookly support. Type your message, or 'quit'.

Try:
  "where's my order?"                       -> asks for email, then disambiguates
  "I want to return my Dune"                -> two Dunes on the account, one refundable
  "refund BK-09988, I didn't like it"       -> policy denies it, agent does not argue
"""


def main() -> int:
    print(BANNER)
    agent = Agent(on_tool=lambda n, a, r: print(f"    \033[2m[tool] {n}({a}) -> {r}\033[0m"))
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"quit", "exit"}:
            return 0
        if not text:
            continue
        try:
            seen = len(agent.outcome.mock_inbox)
            print(f"\nbookly> {agent.say(text)}")
            for email, code in agent.outcome.mock_inbox[seen:]:
                print(f"    \033[2m[mock inbox for {email}] Your Bookly verification code is {code}\033[0m")
        except Exception as exc:  # noqa: BLE001 - a demo should say why it broke
            print(f"\n[error] {type(exc).__name__}: {exc}", file=sys.stderr)
            if "api_key" in str(exc).lower() or "ANTHROPIC" in str(exc):
                print("Set ANTHROPIC_API_KEY, or run `ant auth login`.", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
