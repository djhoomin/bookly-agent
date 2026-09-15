"""Scripted walkthrough of the four cases worth showing. `python demo.py`

A fresh Agent per scenario, so each one is a clean conversation rather than a
single thread where earlier context does the work.
"""
from bookly.agent import Agent
from bookly.backend import verification_code as CODE

DIM, RESET, BOLD = "\033[2m", "\033[0m", "\033[1m"

SCENARIOS = [
    ("A customer who has not said who they are, and a code before any data",
     ["Where's my book?",
      "sam@example.com",
      CODE("sam@example.com"),
      "The Piranesi one"]),
    ("Ambiguity forces a question",
     ["Hi, I'd like to return my copy of Dune. My email is ria@example.com.",
      CODE("ria@example.com")]),
    ("Policy refuses, and the agent does not argue",
     ["I want a refund for BK-09988, I didn't enjoy it. My email is sam@example.com.",
      CODE("sam@example.com"),
      "That's ridiculous, I want to speak to someone."]),
    ("A valid return completes, typo and all",
     ["bk10231 arrived damaged and I want to send it back. sam@example.com",
      CODE("sam@example.com")]),
]

for title, turns in SCENARIOS:
    print(f"\n{BOLD}== {title} {'=' * (62 - len(title))}{RESET}")
    agent = Agent(on_tool=lambda n, a, r: print(f"   {DIM}[tool] {n}({a})\n          -> {r}{RESET}"))
    for turn in turns:
        print(f"\n{BOLD}you>{RESET} {turn}")
        seen = len(agent.outcome.mock_inbox)
        print(f"\n{BOLD}bookly>{RESET} {agent.say(turn)}")
        for email, code in agent.outcome.mock_inbox[seen:]:
            print(f"   {DIM}[mock inbox for {email}] Your Bookly verification code is {code}{RESET}")
    if agent.outcome.refunds:
        print(f"\n   {BOLD}outcome:{RESET} refund issued for "
              f"{agent.outcome.refunds[0]['order_id']} "
              f"(EUR {agent.outcome.refunds[0]['refund_eur']})")
    elif agent.outcome.escalations:
        print(f"\n   {BOLD}outcome:{RESET} escalated to a human")
    else:
        print(f"\n   {BOLD}outcome:{RESET} no state changed")
print()
