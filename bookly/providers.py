"""Where the inference happens, as a deployment decision rather than a constant.

For a European enterprise buyer, "which jurisdiction processes my customers'
messages" is a procurement gate, not a preference. A support agent handles names,
order history and complaints, so it falls under GDPR by default and, increasingly,
under the EU AI Act's transparency obligations. An architecture that cannot answer
the residency question does not reach a pilot at a bank, an insurer or a public
body, however good the agent is.

So the provider is a seam rather than an import. Three options, all speaking the
same request shape:

  anthropic   direct to the Anthropic API. The default, one key, nothing to set up.
  openrouter  OpenAI-compatible gateway, many models behind one key.
  openrouter-eu  the same gateway pinned to https://eu.openrouter.ai, where
              OpenRouter states prompts and completions "are processed within the
              selected region and do not leave it". Available on the Business and
              Enterprise plans.

The point being made is not that one is better. It is that the choice is
configuration, so a deployment that must stay in the EU changes an environment
variable rather than an architecture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT = "anthropic"


def _load_dotenv(path: Path | None = None) -> None:
    """Read KEY=value lines from .env at the repo root into the environment.

    Only keys not already set, so a real environment always wins. No
    dependency, no interpolation, no surprises: a key, an equals sign, a value.
    """
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

#: Model identifiers differ by gateway. Anthropic's own IDs are bare; OpenRouter
#: namespaces them by vendor.
MODEL_MAP = {
    "anthropic": {
        "heavy": "claude-opus-5",
        "light": "claude-haiku-4-5",
    },
    "openrouter": {
        "heavy": "anthropic/claude-opus-5",
        "light": "anthropic/claude-haiku-4-5",
    },
}


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None
    api_key_env: str
    residency: str

    def model(self, tier: str) -> str:
        key = "openrouter" if self.name.startswith("openrouter") else "anthropic"
        return MODEL_MAP[key][tier]


PROVIDERS = {
    "anthropic": Provider(
        "anthropic", None, "ANTHROPIC_API_KEY",
        "Anthropic's default regions. No residency guarantee configured."),
    "openrouter": Provider(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        "Unpinned. OpenRouter routes to whichever host serves the model."),
    "openrouter-eu": Provider(
        "openrouter-eu", "https://eu.openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        "EU only. Prompts and completions are processed within the region and do "
        "not leave it. Business and Enterprise plans."),
}


def active() -> Provider:
    name = os.environ.get("BOOKLY_PROVIDER", DEFAULT).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown BOOKLY_PROVIDER {name!r}. Options: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]


def build_client(provider: Provider | None = None):
    """Return a client speaking the Anthropic Messages shape.

    The OpenRouter paths use the OpenAI SDK against an OpenAI-compatible
    endpoint, wrapped so the agent loop does not need to know which it got.
    Keeping the wrapper thin is deliberate: an adapter that hides too much is
    the all-in-one platform this exercise asks us not to reach for.
    """
    provider = provider or active()
    if provider.name == "anthropic":
        import anthropic

        return anthropic.Anthropic()

    from .openai_bridge import OpenAICompatClient

    key = os.environ.get(provider.api_key_env)
    if not key:
        raise RuntimeError(
            f"{provider.api_key_env} is not set, which {provider.name} needs. "
            f"Unset BOOKLY_PROVIDER to fall back to the Anthropic API.")
    return OpenAICompatClient(base_url=provider.base_url, api_key=key)
