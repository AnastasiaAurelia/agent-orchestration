#!/usr/bin/env python3
"""Diana M4: a real Hermes turn with the bounded-mutation envelope.

Reuses M2's proven shape -- production `AIAgent.chat()`, bounded by an iteration
cap and a wall-clock deadline, with an interrupt rather than an abandoned
spending thread -- and changes exactly one thing: the envelope now contains
`write_file`, `patch` and `terminal`.

M2-D9's isolation still applies (`skip_context_files`, `skip_memory`), and
schema narrowing is still presentation rather than enforcement (M2-D2): the
policy at the dispatch boundary is what actually refuses, which is why the
adversarial tests bypass the model entirely and force calls through the real
path.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters"):
    sys.path.insert(0, str(_HERE.parent / _sub))

import blocking  # noqa: E402
import hermes_live as _live  # noqa: E402
import hermes_patches as _patches  # noqa: E402

MAX_ITERATIONS = 24
WALL_CLOCK_SECONDS = 420
INTERRUPT_GRACE_SECONDS = 20


def build_agent(contract_block: dict, *, hermes_home: str | None = None):
    cfg = _live.provider_config(hermes_home)
    home = str(hermes_home or _patches.HERMES_HOME)
    if home not in sys.path:
        sys.path.insert(0, home)
    os.environ["TERMINAL_CWD"] = contract_block["target"]["repo_root"]
    from run_agent import AIAgent

    allowed = set(contract_block["capability_envelope"]["allowed_tools"])
    agent = AIAgent(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        provider=cfg["provider"], quiet_mode=True, save_trajectories=False,
        enabled_toolsets=["file", "terminal"], max_iterations=MAX_ITERATIONS,
        skip_context_files=True, skip_memory=True,
    )
    shown = _live.narrow_tool_schemas(agent, allowed=allowed)
    return agent, cfg, shown


class RemediationDriver:
    """Callable turn driver for one bounded remediation."""

    def __init__(self, *, hermes_home: str | None = None, prompt: str | None = None) -> None:
        self.hermes_home = hermes_home
        self.prompt = prompt
        self.record: dict | None = None

    def _build_prompt(self, contract_block: dict) -> str:
        """The builder's brief: the APPROVED TASK first, then its bounds.

        This used to open with "The <language> project at <root> has a failing
        verification" and never mention `contract_block["task"]` at all. That is
        correct for M4's own fixture, where the task IS "make the failing
        verifier pass", and wrong for every real run: a production run was
        measured whose approved task asked for several changes plus regression
        tests, whose verifier was ALREADY green, and whose builder was therefore
        told to repair a failure that did not exist. It changed one file, the
        suite stayed green, and nothing in the loop had asked for the work.

        So the task is stated in full and first, and the verification command is
        demoted to what it is -- a check, explicitly necessary and not
        sufficient. Naming the bounds here remains presentation and not
        enforcement (M2-D2): the write scope, the command allowlist and the
        terminal policy are refused at the dispatch boundary, and the
        adversarial cases bypass the model entirely to prove it. Telling the
        model the bounds it must work within grants it no say in what they are
        (M1 D14).
        """
        if self.prompt:
            return self.prompt
        envelope = contract_block["capability_envelope"]
        root = contract_block["target"]["repo_root"]
        commands = envelope["allowed_commands"]
        policy = envelope.get("command_policy") or {}
        roots = policy.get("workdir_roots") or [root]
        workdir = roots[0]
        ceiling = policy.get("max_timeout_s", 300)
        task = str(contract_block.get("task") or "").strip()
        write_roots = (envelope.get("write_scope") or {}).get("allowed_roots") or []
        writable = ", ".join(str(r) for r in write_roots) or "(nothing)"
        return (
            f"You are working in the repository at {root}.\n\n"
            f"THE TASK, in full:\n{task}\n\n"
            "Implement every part of that task. If it asks for changes in several "
            "places, make all of them; if it asks for tests, add them. Read the "
            "existing code first and follow what is already there.\n"
            f"You may create or modify files only under: {writable}\n\n"
            f"Run exactly this command to check your work: {commands[0]}\n"
            f"You may ONLY run this exact command: {commands[0]}\n"
            "That command exiting 0 is necessary and NOT sufficient. It does not show "
            "that the task above was done, and a suite that was already passing will "
            "still pass if you change nothing that matters.\n"
            # ERRATA-002 / F-A7: `workdir` and `timeout` are both REQUIRED, and a
            # terminal call omitting either is refused before dispatch.
            f"Every terminal call MUST pass workdir={workdir!r} and an explicit "
            f"integer timeout of at most {ceiling} seconds; omitting either is refused.\n"
            "Do not modify the verification script or the tests in order to make the "
            "command pass; change the code it checks."
        )

    def __call__(self, contract_block: dict, probe_tree=None):
        started = time.monotonic()
        agent, cfg, shown = build_agent(contract_block, hermes_home=self.hermes_home)
        message = self._build_prompt(contract_block)
        outcome = {"final": None, "error": None, "interrupt_error": None}

        def run_turn():
            try:
                outcome["final"] = agent.chat(message)
            except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised as Blocked
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        stopped = None
        with _live.TurnRecord() as record:
            worker = threading.Thread(target=run_turn, name="diana-m4-turn", daemon=True)
            worker.start()
            worker.join(timeout=WALL_CLOCK_SECONDS)
            timed_out = worker.is_alive()
            if timed_out:
                # M2-D12: stop the TURN, not merely stop waiting for it.
                try:
                    agent.interrupt(hard_cancel=True, tool_reason="diana: wall-clock bound exceeded")
                except Exception as exc:  # noqa: BLE001
                    outcome["interrupt_error"] = f"{type(exc).__name__}: {exc}"
                worker.join(timeout=INTERRUPT_GRACE_SECONDS)
                stopped = not worker.is_alive()

        self.record = {
            "live": True, "provider": cfg["provider"], "model": cfg["model"],
            "tool_schemas_shown": shown,
            "tools_attempted": list(record.attempted),
            "tools_refused_by_diana": list(record.refused),
            "iterations_cap": MAX_ITERATIONS,
            "wall_clock_cap_seconds": WALL_CLOCK_SECONDS,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "timed_out": bool(timed_out),
            "stopped_after_interrupt": stopped,
            "interrupt_error": outcome["interrupt_error"],
            "error": outcome["error"],
            "final": (outcome["final"] or "")[:2000] if isinstance(outcome["final"], str) else None,
        }
        if timed_out:
            raise blocking.Blocked(
                blocking.HERMES_TURN_FAILED,
                f"turn exceeded its {WALL_CLOCK_SECONDS}s bound; stopped={stopped}")
        if outcome["error"] is not None:
            raise blocking.Blocked(blocking.HERMES_TURN_FAILED, outcome["error"])
        return []
