#!/usr/bin/env python3
"""Diana supervisors: the adapter that reuses Diana's OWN provider machinery.

## Why this exists, measured

The first automated adapter (`openai.py`) speaks OpenAI `/v1/chat/completions`
and reads its own environment variables. Against this installation that is
unusable, and the audit that found it is worth writing down: Hermes resolves
`provider=openai-codex`, `api_mode=codex_responses`,
`base_url=https://chatgpt.com/backend-api/codex`, authenticated by an OAuth
credential from Hermes's own auth store. That is a different wire protocol at a
different endpoint. A user with working Diana/Hermes auth would have had to buy
a SECOND, separate API key to use autonomous recovery at all -- while Diana sat
next to a working credential it already knew how to use.

Reimplementing `codex_responses` here was considered and rejected: it is the
OpenAI SDK client, SSE streaming, request sanitisation, a watchdog and session
state. Duplicating that would be a second, worse copy of a thing Diana already
drives correctly.

So this adapter reuses the primitive Diana already has: `LiveTurnDriver`, the
same bounded turn the Builder and Reviewer run on. It therefore inherits, for
free and without a second credential path:

  * provider/model/base_url/credential from `hermes_live.provider_config`,
    including the OAuth resolution added for `openai-codex`;
  * a hard wall-clock bound with a real interrupt;
  * the complete final response held in memory, never written to an artifact;
  * no credential in any record.

## Why the supervisor turn has NO tools

A recovery planner observes and recommends. It has no business reading the
repository directly: everything it may know is in the evidence Diana composed,
and a planner that could read files would be a second, unreviewed actor inside
the target.

So the turn is built with `allowed_tools=()` -- an empty schema set -- AND the
capability boundary is put into its deny-everything state first. The first is
presentation; the second is enforcement (M2-D2), and the reason both are here is
that presentation has never been the boundary in this codebase and is not
becoming one now.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
for _sub in ("autonomy", "adapters", "runtime", "multiactor", "mutation", "unattended"):
    sys.path.insert(0, str(_HERE.parent / _sub))
import base as _base  # noqa: E402
import escalation as _esc  # noqa: E402
import schema as _schema  # noqa: E402

# A planner is not a builder: it reads a prepared record and answers. It needs
# far less wall clock than a coding turn, and a bound it cannot exceed is the
# point of having one.
WALL_CLOCK_SECONDS = 180

PROMPT_HEADER = (
    "You are a RECOVERY PLANNER for a deterministic orchestration system.\n"
    "You are ADVISORY ONLY. You have no tools. You cannot read files, run commands, "
    "approve anything, or change any policy. You observe one failed run, described "
    "entirely by the JSON below, and you recommend the next bounded step.\n\n"
    "`standing_authority` is the CEILING a human already approved. Work inside it: "
    "anything outside it is refused and only spends the session's budget. Prefer the "
    "narrowest recovery that could plausibly work.\n\n"
    "Reply with exactly ONE JSON object and nothing else -- no prose, no code fence.\n"
    "Required keys, all of them, and no others:\n"
    '  decision: one of ' + json.dumps(list(_schema.DECISIONS)) + "\n"
    "  reason: a short diagnosis\n"
    "  next_goal: the goal for the next run, or null\n"
    '  children: [] unless decision is SPLIT_TASK, where each entry is exactly '
    '{"goal","requested_write_scope","requested_commands"}\n'
    "  requested_write_scope: absolute paths, a subset of the approved write scope\n"
    "  requested_commands: commands, a subset of the approved commands\n"
    "  preserve_changes: repository-relative paths to keep\n"
    "  revert_changes: repository-relative paths to undo\n"
    "  human_required: true only if a person genuinely must decide\n"
    '  confidence: one of ["HIGH","MEDIUM","LOW"]\n'
    "A key you invent causes the whole recommendation to be rejected.\n\n"
    "THE FAILED RUN:\n"
)


class HermesSupervisor(_base.Supervisor):
    """A recovery planner on Diana's own bounded model turn."""

    name = "hermes"

    def __init__(self, *, hermes_home=None, repo_root=None,
                 wall_clock_seconds: int = WALL_CLOCK_SECONDS, driver_factory=None) -> None:
        self.hermes_home = hermes_home
        self.repo_root = repo_root
        self.wall_clock_seconds = int(wall_clock_seconds)
        # Test seam, on the M2-D7 precedent: the adapter's parsing and its
        # fail-closed paths are provable without a live provider.
        self.driver_factory = driver_factory
        self.model = None
        self.provider = None

    # --- availability -----------------------------------------------------
    def configured(self) -> bool:
        """Can this installation resolve a provider at all? Never raises, never
        reads the credential value."""
        if self.driver_factory is not None:
            return True
        try:
            import hermes_live as _live

            cfg = _live.provider_config(self.hermes_home)
        except BaseException:  # noqa: BLE001 - "not configured" is the only answer
            return False
        return bool(cfg.get("provider") and cfg.get("model"))

    def describe(self) -> dict:
        """Provider and model identity for the audit record. No credential."""
        try:
            import hermes_live as _live

            cfg = _live.provider_config(self.hermes_home)
            return {"provider": cfg["provider"], "model": cfg["model"]}
        except BaseException:  # noqa: BLE001
            return {"provider": None, "model": None}

    # --- the seam ---------------------------------------------------------
    def diagnose(self, evidence: dict) -> dict:
        import hermes_live as _live

        root = self.repo_root or evidence.get("workspace_provenance", {}).get("_root") or "."
        contract_block = {"target": {"repo_root": str(root)},
                          "repo_profile": {"inventory": []}}
        prompt = PROMPT_HEADER + json.dumps(evidence, indent=2, sort_keys=True)

        if self.driver_factory is not None:
            driver = self.driver_factory(prompt)
        else:
            # Enforcement first, and only then the turn. The planner has no
            # tools presented AND no tool may execute: an empty allowed-set is
            # refused by the same guard every tool call passes through.
            self._deny_everything()
            driver = _live.LiveTurnDriver(
                hermes_home=self.hermes_home, prompt=prompt, allowed_tools=(),
                wall_clock_seconds=self.wall_clock_seconds)
        driver(contract_block)
        record = getattr(driver, "record", None) or {}
        self.provider, self.model = record.get("provider"), record.get("model")
        if record.get("tool_schemas_shown"):
            # A planner that was shown a tool is not the turn Diana asked for.
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                f"the supervisor turn was presented tool schemas "
                f"{record['tool_schemas_shown']}; a recovery planner is given none")
        return self._extract(getattr(driver, "final_text", None))

    @staticmethod
    def _deny_everything() -> None:
        try:
            import actors as _actors

            _actors.deny_all()
        except BaseException as exc:  # noqa: BLE001
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"the capability boundary could not be closed before consulting the "
                f"supervisor ({type(exc).__name__}); the turn was not run") from None

    @staticmethod
    def _extract(final_text) -> dict:
        """One JSON object out of the complete in-memory final response.

        Reuses `executors.extract_verdict`'s scanner rather than a second
        parser: it is the deterministic, fail-closed, brace-counting extraction
        Diana already trusts for reviewer verdicts, and two parsers for "one
        JSON object from model text" could be made to disagree.
        """
        import executors as _executors

        if not final_text:
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED, "the supervisor produced no final response")
        parsed, transport = _executors.extract_verdict(final_text)
        if parsed is None:
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                f"the supervisor's reply did not yield exactly one JSON object "
                f"(parse_status={transport['parse_status']}, "
                f"candidates={transport['parsed_object_count']})")
        return parsed


def from_environment(**kwargs):
    """The Hermes-backed planner, or None when no provider is configured."""
    adapter = HermesSupervisor(**kwargs)
    return adapter if adapter.configured() else None
