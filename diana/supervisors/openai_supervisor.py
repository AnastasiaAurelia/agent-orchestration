#!/usr/bin/env python3
"""Diana supervisors: the OpenAI-compatible adapter.

The first automated recovery planner. It is deliberately the thinnest thing that
can satisfy `base.Supervisor`, and it is isolated: nothing in `diana/autonomy/`
imports this file, no model name appears in any policy decision, and the loop
behaves identically whichever adapter is installed.

## Why it speaks a URL and not a vendor SDK

An SDK is a dependency, a credential store and an update cadence Diana would
then own. `urllib` is in the standard library, the request is one POST of
JSON, and any OpenAI-compatible endpoint -- a different provider, a gateway, a
local server -- works by changing `base_url`. That is what makes "provider
neutral" cheap enough to be true.

## Credentials

Read from the environment at call time and never stored, never logged, never
placed in evidence, never written to an audit record. Diana does not manage
credentials for this adapter; it borrows one for the duration of a request.

## Why the response is parsed strictly

The model is asked for one JSON object and the reply is handed to
`schema.validate`, which is closed in both directions. Prose, a fenced block
containing something else, an extra field, an unknown decision -- each is an
escalation. Raw provider text is never authority and never a default.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "autonomy"))
import base as _base  # noqa: E402
import escalation as _esc  # noqa: E402
import schema as _schema  # noqa: E402

# Configuration, all from the environment. No model name is written down here
# because a model name in Diana's source is a policy decision Diana should not
# be making.
ENV_MODEL = "DIANA_SUPERVISOR_MODEL"
ENV_BASE_URL = "DIANA_SUPERVISOR_BASE_URL"
ENV_API_KEY = "DIANA_SUPERVISOR_API_KEY"
FALLBACK_API_KEY = "OPENAI_API_KEY"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

TIMEOUT_SECONDS = 120

SYSTEM_PROMPT = (
    "You are a recovery planner for a deterministic orchestration system. You are "
    "ADVISORY ONLY. You cannot write files, run commands, approve anything, or change "
    "any policy. You observe one failed run and recommend the next bounded step.\n\n"
    "You are given the CEILING of authority that a human already approved. You may "
    "propose work inside it; anything outside it will be refused, so proposing it only "
    "wastes the session's budget. Prefer the narrowest recovery that could work.\n\n"
    "Reply with exactly ONE JSON object and nothing else: no prose, no code fence.\n"
    "Keys, all required:\n"
    '  decision: one of ' + json.dumps(list(_schema.DECISIONS)) + "\n"
    '  reason: a short explanation of the diagnosis\n'
    '  next_goal: the goal for the next run, or null\n'
    '  children: [] except for SPLIT_TASK, where each entry is '
    '{"goal","requested_write_scope","requested_commands"}\n'
    '  requested_write_scope: list of absolute paths, a subset of the approved scope\n'
    '  requested_commands: list of commands, a subset of the approved commands\n'
    '  preserve_changes: repository-relative paths to keep\n'
    '  revert_changes: repository-relative paths to undo\n'
    '  human_required: true only if a human genuinely must decide\n'
    '  confidence: one of ["HIGH","MEDIUM","LOW"]\n'
    "Use exactly those keys and no others. A key you invent causes the whole "
    "recommendation to be rejected."
)


class OpenAISupervisor(_base.Supervisor):
    """An OpenAI-compatible chat-completions recovery planner."""

    name = "openai"

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: int = TIMEOUT_SECONDS,
                 transport=None) -> None:
        self.model = model or os.environ.get(ENV_MODEL) or ""
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL)
                         or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self.timeout = int(timeout)
        # Seam: a test drives the adapter's parsing and failure handling without
        # a network, exactly as the model seam in `hermes_live` is driven.
        self.transport = transport

    # --- credentials ------------------------------------------------------
    def _credential(self) -> str:
        key = (self._api_key or os.environ.get(ENV_API_KEY)
               or os.environ.get(FALLBACK_API_KEY) or "").strip()
        if not key:
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"no supervisor credential is configured; set {ENV_API_KEY} or "
                f"{FALLBACK_API_KEY}. Diana does not store one")
        return key

    def configured(self) -> bool:
        """Is this adapter usable at all? Never raises, never reads the secret."""
        return bool(self.model) and bool(
            os.environ.get(ENV_API_KEY) or os.environ.get(FALLBACK_API_KEY)
            or self._api_key)

    # --- transport --------------------------------------------------------
    def _post(self, payload: dict) -> dict:
        if self.transport is not None:
            return self.transport(payload)
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._credential()}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The body can echo request content; only the status is reported.
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"the supervisor provider returned HTTP {exc.code}") from None
        except TimeoutError:
            raise _esc.Escalation(
                _esc.SUPERVISOR_TIMEOUT,
                f"the supervisor provider did not answer within {self.timeout}s") from None
        except urllib.error.URLError as exc:
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"the supervisor provider could not be reached "
                f"({type(exc.reason).__name__})") from None

    # --- the seam ---------------------------------------------------------
    def diagnose(self, evidence: dict) -> dict:
        if not self.model:
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"no supervisor model is configured; set {ENV_MODEL}")
        body = self._post({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(evidence, sort_keys=True)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        })
        return self._extract(body)

    @staticmethod
    def _extract(body: object) -> dict:
        """The one JSON object, or an escalation. No fallback, no repair."""
        try:
            content = body["choices"][0]["message"]["content"]
        except (TypeError, KeyError, IndexError):
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                "the supervisor provider returned no message content") from None
        if not isinstance(content, str) or not content.strip():
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED, "the supervisor returned an empty message")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                f"the supervisor's reply is not valid JSON "
                f"({exc.msg} at line {exc.lineno} column {exc.colno})") from None
        if not isinstance(parsed, dict):
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                f"the supervisor's reply is a {type(parsed).__name__}, not an object")
        return parsed


def from_environment(**kwargs):
    """The configured automated supervisor, or None when none is configured.

    Returning None rather than a half-configured adapter is what lets the CLI
    fall back to `mock.HumanSupervisor` -- autonomy with no planner escalates
    rather than pretending to plan.
    """
    adapter = OpenAISupervisor(**kwargs)
    return adapter if adapter.configured() else None
