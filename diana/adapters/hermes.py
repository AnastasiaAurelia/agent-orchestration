#!/usr/bin/env python3
"""Diana adapter: the fail-closed Hermes preflight (spec TCB table, AC-1).

`check()` refuses to proceed unless it can PROVE every trusted-computing-base
property. Each failure carries its own distinct reason code, because the eight
preflight-failure fixtures must each fail for their own reason -- one
over-broad check passing all eight would prove nothing about any of them.

## What the grill changed about this list

The original list carried three items that did not survive contact with the
code, and lacked three that matter:

* `HERMES_SAFE_MODE` is **required to be 1**, not unset. It skips plugin
  discovery, user shell hooks, MCP config, and outbound webhook registration --
  four whole subsystems that otherwise run in-process and could clear or re-patch
  Diana's boundaries. Diana's own controls are direct in-process patches and do
  not need plugins, so safe mode is a TCB *reduction* (D23).
* `approvals.mode` is **recorded context, not a control** (D28). With only
  `read_file` and `search_files` reachable and confined, no approval-worthy
  action exists; calling it a control would misdescribe the security path.
* `subagent_auto_approve` is **subsumed** (D30): `delegate_task` is not in the
  envelope, so it is denied by set membership with no dedicated control.
* Added: `background_review.enabled == false` (its default is **True**), side
  question disabled, and the version pin -- plus the two behavioral patch probes,
  which are the only evidence that enforcement is live rather than merely
  installed.

`.hermes.md` and `AGENTS.override.md` are **context-integrity** controls, not
capability ones (D29). Neither can widen the capability envelope, but both can
change what the run means, so their presence blocks a run whose whole purpose is
a reproducible measurement.

## Fail-closed reading of Hermes config

`agent/background_review.py:185 load_background_review_settings` is explicitly
fail-open -- a broken config leaves reviews ENABLED. Diana therefore reads the
config itself and blocks when it cannot determine the value, rather than
inheriting an optimistic default.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import hermes_patches as _patches  # noqa: E402
import selftest as _selftest  # noqa: E402

CONTEXT_INTEGRITY_FILES = (".hermes.md", "AGENTS.override.md")


def _truthy(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def load_hermes_config(hermes_home: str | None = None) -> dict:
    """Read Hermes config. Unreadable config is a BLOCK, never a default."""
    home = Path(hermes_home or _patches.HERMES_HOME)
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception as exc:
        raise blocking.Blocked(
            blocking.HERMES_UNREACHABLE, f"could not read Hermes config: {exc}"
        ) from None
    return config if isinstance(config, dict) else {}


def _auxiliary(config: dict, name: str) -> dict:
    aux = config.get("auxiliary")
    block = aux.get(name) if isinstance(aux, dict) else None
    return block if isinstance(block, dict) else {}


def check(
    *,
    repo_root: str,
    env: dict | None = None,
    config: dict | None = None,
    hermes_home: str | None = None,
    probe_tree: dict | None = None,
    require_patches: bool = True,
) -> dict:
    """Prove the TCB, or raise Blocked with the specific reason code."""
    env = dict(os.environ if env is None else env)
    home = Path(hermes_home or _patches.HERMES_HOME)
    report: dict = {"controls": [], "recorded_context": {}}

    def record(name: str, detail: str = "") -> None:
        report["controls"].append({"control": name, "status": "PASS", "detail": detail})

    # 1 -- reachable
    if not (home / "model_tools.py").is_file() or not (home / "hermes_cli").is_dir():
        raise blocking.Blocked(blocking.HERMES_UNREACHABLE, f"no Hermes installation at {home}")
    record("hermes-reachable", str(home))

    # 2/3 -- version pin (C1). The behavioral probes below are the real
    # tripwire; the pin is what makes an upstream change legible.
    version = _patches.hermes_version(str(home))
    if version != _patches.PINNED_VERSION:
        raise blocking.Blocked(
            blocking.HERMES_VERSION_PIN_MISMATCH,
            f"found {version!r}, pinned {_patches.PINNED_VERSION!r}",
        )
    record("hermes-version-pin", version)
    commit = _patches.hermes_commit(str(home))
    if commit != _patches.PINNED_COMMIT:
        raise blocking.Blocked(
            blocking.HERMES_COMMIT_PIN_MISMATCH,
            f"found {commit!r}, pinned {_patches.PINNED_COMMIT!r}",
        )
    record("hermes-commit-pin", commit)

    # 4 -- safe mode ON (D23)
    if str(env.get("HERMES_SAFE_MODE", "")).strip() != "1":
        raise blocking.Blocked(
            blocking.SAFE_MODE_NOT_ENABLED,
            f"HERMES_SAFE_MODE={env.get('HERMES_SAFE_MODE')!r}; M1 requires 1 so plugin "
            "discovery, user shell hooks, MCP config and outbound webhooks stay off",
        )
    record("hermes-safe-mode", "1")

    if config is None:
        config = load_hermes_config(str(home))

    # 5 -- background review OFF. Default is True, and Hermes's own reader is
    # fail-open, so absence of the key means ENABLED and therefore blocks.
    review = _auxiliary(config, "background_review")
    if _truthy(review.get("enabled"), default=True):
        raise blocking.Blocked(
            blocking.BACKGROUND_REVIEW_ENABLED,
            "auxiliary.background_review.enabled is true (its default); a review fork runs on its "
            "own thread with its own tool whitelist, outside this run's ledger",
        )
    record("background-review-disabled")

    # 6 -- side question OFF. Its only invocation is an explicit CLI command
    # (hermes_cli/cli_commands_mixin.py:2087), so an absent block means no auto
    # spawn; an explicitly enabled block still blocks.
    side = _auxiliary(config, "side_question")
    if _truthy(side.get("enabled"), default=False):
        raise blocking.Blocked(
            blocking.SIDE_QUESTION_ENABLED,
            "auxiliary.side_question.enabled is true; auxiliary execution runs outside this run's ledger",
        )
    record("side-question-disabled")

    # 7/8 -- context integrity (D29)
    search_roots = [Path(repo_root), Path(home), Path.home() / ".hermes"]
    for filename, code in (
        (".hermes.md", blocking.HERMES_MD_PRESENT),
        ("AGENTS.override.md", blocking.AGENTS_OVERRIDE_PRESENT),
    ):
        for root in search_roots:
            candidate = root / filename
            if candidate.is_file():
                raise blocking.Blocked(
                    code,
                    f"{candidate} would change what this run means; M1 requires a reproducible run",
                )
        record(f"no-{filename}")

    # 9/10 -- the patches, proven behaviorally rather than merely installed (D26)
    if require_patches:
        if not _patches.confinement_live():
            raise blocking.Blocked(
                blocking.CONFINEMENT_PATCH_NOT_LIVE, "_resolve_path_for_task is not wrapped"
            )
        if not _patches.capability_live():
            raise blocking.Blocked(
                blocking.CAPABILITY_PATCH_NOT_LIVE, "handle_function_call is not wrapped"
            )
        if probe_tree is not None:
            _selftest.assert_all(
                _selftest.confinement_probes(probe_tree), blocking.CONFINEMENT_PATCH_NOT_LIVE
            )
            _selftest.assert_all(
                _selftest.capability_probes(probe_tree), blocking.CAPABILITY_PATCH_NOT_LIVE
            )
            record("confinement-self-test")
            record("capability-self-test")
        else:
            record("confinement-patch-live")
            record("capability-patch-live")

    # Recorded context, deliberately NOT a control (D28).
    approvals = config.get("approvals") if isinstance(config.get("approvals"), dict) else {}
    report["recorded_context"] = {
        "approvals.mode": approvals.get("mode"),
        "note": "approvals.mode is recorded, not enforced: with only read_file and search_files "
                "reachable and confined, no approval-worthy action exists on the security path",
    }
    return report
