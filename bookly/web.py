"""A minimal web UI for the demo. `python -m bookly.web`, then open the URL it prints.

Standard library only. One page, two panes. The conversation on the left is
the part every agent demo shows. The right pane is the part that matters: what
triage decided about the last turn, which tools were called and with what,
what the policy function said, whether any state changed, and what it cost.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import Agent
from .providers import active

PAGE = Path(__file__).with_name("ui.html")
SESSIONS: dict[str, Agent] = {}


def _agent(session: str) -> Agent:
    if session not in SESSIONS:
        SESSIONS[session] = Agent(conversation_id=f"web:{session[:8]}")
    return SESSIONS[session]


def say(session: str, text: str) -> dict:
    agent = _agent(session)
    tools: list[dict] = []
    triage: list = []
    agent.on_tool = lambda name, args, result: tools.append(
        {"name": name, "args": args, "result": result})
    agent.on_triage = triage.append
    before_refunds = len(agent.outcome.refunds)
    before_escalations = len(agent.outcome.escalations)

    reply = agent.say(text)
    trace = asdict(agent.last_trace) if agent.last_trace else {}
    tri = triage[0] if triage else None
    return {
        "reply": reply,
        "turn": agent.turn_no,
        "triage": ({**asdict(tri), "tier": tri.tier} if tri else None),
        "model": trace.get("model", ""),
        "tools": tools,
        "trace": trace,
        "refunds": agent.outcome.refunds[before_refunds:],
        "escalations": agent.outcome.escalations[before_escalations:],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/meta":
            p = active()
            return self._json({"provider": p.name, "residency": p.residency,
                               "heavy": p.model("heavy"), "light": p.model("light"),
                               "session": uuid.uuid4().hex})
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        session = data.get("session") or uuid.uuid4().hex
        if self.path == "/api/reset":
            SESSIONS.pop(session, None)
            return self._json({"session": uuid.uuid4().hex})
        if self.path == "/api/say":
            text = (data.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            try:
                return self._json(say(session, text))
            except Exception as exc:  # noqa: BLE001 - the demo should say why
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json({"error": "not found"}, 404)


def main() -> int:
    host, port = "127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    p = active()
    print(f"Bookly demo UI at http://{host}:{port}   provider={p.name}")
    with ThreadingHTTPServer((host, port), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
