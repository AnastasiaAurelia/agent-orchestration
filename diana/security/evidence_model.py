#!/usr/bin/env python3
"""Diana Security result/evidence model (Security Phase 1, corrected).

Deterministic, Python-stdlib-only. No network, no LLM, no security-tool
invocation, no wall-clock reads. Given the Phase 0 control catalog and a
JSON array of "verification run" records (one attempted verification per
control, produced by some future static/dynamic/semantic verifier -- none
of which exist yet), computes one aggregate result PER CONTROL by
combining every run submitted for that control:

    PASS            -- applicability is APPLICABLE, every required_evidence
                       item is SATISFIED by an evidence contribution from a
                       verifier capability permitted for that control, and
                       every capability the control specifically requires
                       (dynamic, human-judgment) actually contributed
    FAIL            -- a permitted verifier reported positive VIOLATED
                       evidence for a required_evidence item
    NOT_APPLICABLE  -- every run declares the control irrelevant
    UNPROVEN        -- the control may be applicable, but required
                       evidence or required verifier-capability coverage
                       is incomplete (never PASS by default)
    ERROR           -- a submitted run is malformed, used a verifier
                       capability the control does not permit, reported a
                       tool/execution failure, or the runs disagree on
                       applicability

Core invariant: "no finding" is never PASS, and EVIDENCE TEXT ALONE IS NOT
PROOF. A control cannot PASS on the strength of evidence-item strings
alone -- the verifier that produced each item must itself be a capability
this control's catalog entry actually permits, and the control's specific
dynamic/human-judgment requirements must be satisfied by a matching
capability, not merely implied by matching text.

## Why per-control aggregation, not per-run

Phase 1's original design evaluated one run in isolation. A human review
found this let a single verifier -- of ANY declared or even undeclared
capability -- manufacture PASS merely by writing SATISFIED next to every
required_evidence string, regardless of whether that verifier was actually
capable of proving what the string claims (e.g. a STATIC_ANALYZER claiming
a cross-account dynamic negative-access test passed). A single run cannot
soundly represent evidence that legitimately comes from more than one
verifier capability, so this module now aggregates every run submitted for
a given control_id before deciding a result.

## verification.modes: PERMITTED capabilities, not an AND-list

`catalog.json`'s `verification.modes` is documented (see Phase 0 README,
"Conservative evidence mapping") as the set of verifier capabilities
appropriate to a control -- a POOL an evidence-contributing verifier's type
must belong to, not a checklist where every listed mode must separately
contribute. This is the narrowest interpretation consistent with the
catalog as actually designed in Phase 0: many controls pair two modes that
are alternative ways to establish the same code-level fact (e.g. SEC-016
Weak Password Storage lists STATIC_ANALYZER and SEMANTIC_REVIEW as
alternative techniques for confirming a hashing-algorithm choice, not as
two independently mandatory proofs), and the catalog's own explicit
per-control constraints are exactly two boolean flags -- dynamic_required
and human_judgment_required -- not a general "every mode is mandatory"
signal. Treating every listed mode as separately mandatory would also make
several controls impossible to PASS with a single legitimate verifier even
when the source's own "Conservative evidence mapping" principle does not
ask for that (it asks specifically for dynamic evidence when
dynamic_required is true, not for every paired mode to double up).

Concretely, this module enforces exactly the two capability requirements
the catalog schema itself expresses, on top of the permitted-set rule:

  1. Permitted set (always): every accepted evidence-contributing run's
     verifier.type must be a member of the control's verification.modes.
     A run from an out-of-set capability is rejected outright and can
     never contribute to PASS or FAIL -- it forces the control's result to
     ERROR (see "Aggregation algorithm" below).
  2. Dynamic gate (when verification.dynamic_required is true): at least
     one ACCEPTED evidence item (a SATISFIED requirement counted toward
     PASS) must come from a verifier whose type is one of
     validate_catalog.DYNAMIC_VERIFIER_TYPES.
  3. Human-judgment gate (when verification.human_judgment_required is
     true): at least one accepted evidence item must come from a verifier
     whose type is HUMAN or SEMANTIC_REVIEW (the only two catalog verifier
     types that represent a human/semantic judgment capability). Every
     human_judgment_required=true control in the current catalog
     (SEC-043, SEC-048, SEC-061) already lists SEMANTIC_REVIEW in its
     modes, so this gate is satisfiable for the whole catalog as it exists
     today; if a future catalog entry set human_judgment_required=true
     without HUMAN or SEMANTIC_REVIEW in its modes, that control could
     never PASS under this rule -- which is the intended fail-closed
     behavior, not a bug to work around.

This design was chosen over a stricter "every listed mode must
independently contribute" rule because the latter is not supported by how
the catalog was actually authored (see above) and would silently regress a
Phase 1 (uncorrected) test case that is not superseded by this fix: a
single comprehensive DYNAMIC_API run satisfying both of SEC-001's
required_evidence items, with dynamic_required=true and
human_judgment_required=false, is a legitimate PASS both before and after
this correction -- SEC-001's SEMANTIC_REVIEW mode is permitted, not
separately mandatory, for that control.

## Aggregation algorithm (per control_id, in order of first appearance)

  1. Classify every submitted run for this control_id:
       MALFORMED            -- fails run-schema validation (bad enum,
                                unknown field, unresolvable control_id,
                                an evidence item whose requirement text is
                                not one of the control's real
                                required_evidence strings, etc.)
       CAPABILITY_VIOLATION -- schema-valid, but verifier.type is not a
                                member of the control's verification.modes
       TOOL_ERROR           -- schema-valid, permitted capability, but
                                tool_error is set
       CLEAN                -- schema-valid, permitted capability, no
                                tool_error
  2. If any CLEAN, APPLICABLE run reports a required_evidence item as
     VIOLATED -> FAIL. Trusted violation evidence is surfaced even if
     other runs in the same batch have problems -- a real finding should
     never be hidden behind an unrelated verifier failure.
  3. Else, if any run in this group is MALFORMED, CAPABILITY_VIOLATION, or
     TOOL_ERROR -> ERROR. An out-of-capability or broken contribution
     makes the whole aggregate for this control unreliable; it is
     reported, not silently discarded, per "evidence provenance and
     verifier capability must participate in PASS semantics."
  4. Else (every run CLEAN), resolve applicability across the clean runs:
     conflicting APPLICABLE/NOT_APPLICABLE runs for the same control -> a
     corrupted-aggregation ERROR; all-NOT_APPLICABLE (optionally mixed
     with UNKNOWN) -> NOT_APPLICABLE; no APPLICABLE run at all (only
     UNKNOWN) -> UNPROVEN ("applicability has not been established").
  5. With applicability resolved to APPLICABLE: collect every SATISFIED
     evidence item from the APPLICABLE clean runs, and the set of verifier
     types that contributed at least one such item.
       - Any required_evidence string with no SATISFIED contribution ->
         UNPROVEN ("missing required evidence: ...").
       - dynamic_required true and no contributing type is a dynamic
         capability -> UNPROVEN.
       - human_judgment_required true and no contributing type is
         HUMAN/SEMANTIC_REVIEW -> UNPROVEN.
       - Otherwise -> PASS.

  A control with zero submitted runs is UNPROVEN ("no verification runs
  submitted for this control") -- distinct from a run that was attempted
  and rejected, which is ERROR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_catalog  # noqa: E402  (reuse the Phase 0 verifier-type/dynamic-type enums + validator)

RESULT_STATES = {"PASS", "FAIL", "NOT_APPLICABLE", "UNPROVEN", "ERROR"}
APPLICABILITY_STATES = {"APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"}
EVIDENCE_STATUSES = {"SATISFIED", "VIOLATED"}

# The only two catalog verifier types that represent a human/semantic
# judgment capability. Diana-designed (the catalog has no separate
# "judgment capability" field); see module docstring for why these two.
HUMAN_JUDGMENT_CAPABLE_TYPES = {"HUMAN", "SEMANTIC_REVIEW"}

ALLOWED_RUN_FIELDS = {"control_id", "applicability", "verifier", "evidence", "tool_error", "observed_at"}
ALLOWED_VERIFIER_FIELDS = {"type", "identity"}
ALLOWED_EVIDENCE_ITEM_FIELDS = {"requirement", "status", "provenance", "detail"}
ALLOWED_TOOL_ERROR_FIELDS = {"message"}


class MalformedRun(ValueError):
    """A run does not conform to the evidence-model schema."""


class CatalogError(ValueError):
    """The control catalog itself could not be loaded/validated."""


def load_controls(catalog_path: str) -> dict[str, dict[str, Any]]:
    """Load and structurally validate the Phase 0 catalog, fail closed."""
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"could not read/parse catalog: {exc}") from exc

    errors = validate_catalog.validate(catalog)
    if errors:
        raise CatalogError(f"catalog is not structurally valid: {errors}")

    return {c["id"]: c for c in catalog["controls"]}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MalformedRun(message)


def _validate_run_schema(run: Any, control: dict[str, Any] | None) -> None:
    """Validate a run's shape. `control` is the resolved catalog control
    (already known to exist), used only to check evidence-item requirement
    text against that control's real required_evidence contract."""
    _require(isinstance(run, dict), "run must be an object")

    unknown = set(run.keys()) - ALLOWED_RUN_FIELDS
    _require(not unknown, f"unknown top-level field(s): {sorted(unknown)}")

    applicability = run.get("applicability")
    _require(
        applicability in APPLICABILITY_STATES,
        f"applicability must be one of {sorted(APPLICABILITY_STATES)}, got {applicability!r}",
    )

    verifier = run.get("verifier")
    _require(isinstance(verifier, dict), "verifier must be an object")
    unknown_v = set(verifier.keys()) - ALLOWED_VERIFIER_FIELDS
    _require(not unknown_v, f"verifier has unknown field(s): {sorted(unknown_v)}")
    v_type = verifier.get("type")
    _require(
        v_type in validate_catalog.ALLOWED_VERIFIER_TYPES,
        f"verifier.type must be a known verifier type, got {v_type!r}",
    )
    v_identity = verifier.get("identity")
    _require(
        isinstance(v_identity, str) and v_identity.strip(),
        "verifier.identity must be a non-empty string",
    )

    evidence = run.get("evidence")
    _require(isinstance(evidence, list), "evidence must be a list")
    required = set(control["required_evidence"]) if control is not None else set()
    seen_requirements: set[str] = set()
    for item in evidence:
        _require(isinstance(item, dict), "each evidence item must be an object")
        unknown_e = set(item.keys()) - ALLOWED_EVIDENCE_ITEM_FIELDS
        _require(not unknown_e, f"evidence item has unknown field(s): {sorted(unknown_e)}")

        req = item.get("requirement")
        _require(
            isinstance(req, str) and req in required,
            f"evidence item requirement does not match this control's required_evidence: {req!r}",
        )
        _require(req not in seen_requirements, f"duplicate evidence item for requirement: {req!r}")
        seen_requirements.add(req)

        status = item.get("status")
        _require(
            status in EVIDENCE_STATUSES,
            f"evidence item status must be one of {sorted(EVIDENCE_STATUSES)}, got {status!r}",
        )

        provenance = item.get("provenance")
        _require(
            isinstance(provenance, str) and provenance.strip(),
            "evidence item provenance must be a non-empty string",
        )

        detail = item.get("detail")
        _require(detail is None or isinstance(detail, str), "evidence item detail must be a string if present")

    tool_error = run.get("tool_error")
    if tool_error is not None:
        _require(isinstance(tool_error, dict), "tool_error must be an object or null")
        unknown_t = set(tool_error.keys()) - ALLOWED_TOOL_ERROR_FIELDS
        _require(not unknown_t, f"tool_error has unknown field(s): {sorted(unknown_t)}")
        message = tool_error.get("message")
        _require(
            isinstance(message, str) and message.strip(),
            "tool_error.message must be a non-empty string",
        )

    observed_at = run.get("observed_at")
    _require(observed_at is None or isinstance(observed_at, str), "observed_at must be a string if present")


def _classify_run(run: Any, control: dict[str, Any] | None) -> dict[str, Any]:
    """Classify one run against a resolved control (or None if the
    control_id itself is unresolvable). Never raises."""
    if control is None:
        return {"status": "MALFORMED", "reason": "unknown control_id", "run": run if isinstance(run, dict) else None}

    try:
        _validate_run_schema(run, control)
    except MalformedRun as exc:
        return {"status": "MALFORMED", "reason": f"malformed evidence: {exc}", "run": run if isinstance(run, dict) else None}

    verifier_type = run["verifier"]["type"]
    permitted = set(control["verification"]["modes"])
    if verifier_type not in permitted:
        return {
            "status": "CAPABILITY_VIOLATION",
            "reason": (
                f"verifier.type {verifier_type!r} is not permitted for {control['id']} "
                f"(control's verification.modes: {sorted(permitted)})"
            ),
            "run": run,
        }

    if run.get("tool_error") is not None:
        return {
            "status": "TOOL_ERROR",
            "reason": f"verifier reported a tool/execution error: {run['tool_error']['message']}",
            "run": run,
        }

    return {"status": "CLEAN", "reason": None, "run": run}


def _aggregate_group(control_id: Any, control: dict[str, Any] | None, runs: list[Any]) -> dict[str, Any]:
    classified = [_classify_run(run, control) for run in runs]

    base = {"control_id": control_id if isinstance(control_id, str) else None}

    if control is None:
        return {
            **base,
            "result": "ERROR",
            "applicability": None,
            "reasons": [c["reason"] for c in classified],
            "evidence": [],
            "run_issues": [
                {"verifier_type": None, "status": c["status"], "reason": c["reason"]} for c in classified
            ],
        }

    clean = [c["run"] for c in classified if c["status"] == "CLEAN"]
    problems = [c for c in classified if c["status"] != "CLEAN"]

    # Step 2: trusted violation evidence takes priority and is always surfaced.
    violated: list[str] = []
    for run in clean:
        if run["applicability"] != "APPLICABLE":
            continue
        for item in run["evidence"]:
            if item["status"] == "VIOLATED" and item["requirement"] not in violated:
                violated.append(item["requirement"])

    if violated:
        return {
            **base,
            "result": "FAIL",
            "applicability": "APPLICABLE",
            "reasons": [f"required evidence violated: {r}" for r in violated],
            "evidence": _collect_evidence(clean),
            "run_issues": _run_issues(problems),
        }

    # Step 3: any problem run makes the aggregate unreliable.
    if problems:
        return {
            **base,
            "result": "ERROR",
            "applicability": None,
            "reasons": [c["reason"] for c in problems],
            "evidence": _collect_evidence(clean),
            "run_issues": _run_issues(problems),
        }

    if not runs:
        return {
            **base,
            "result": "UNPROVEN",
            "applicability": None,
            "reasons": ["no verification runs submitted for this control"],
            "evidence": [],
            "run_issues": [],
        }

    # Step 4: resolve applicability across clean runs.
    applicabilities = {run["applicability"] for run in clean}
    if "APPLICABLE" in applicabilities and "NOT_APPLICABLE" in applicabilities:
        return {
            **base,
            "result": "ERROR",
            "applicability": None,
            "reasons": ["conflicting applicability determinations across submitted runs"],
            "evidence": _collect_evidence(clean),
            "run_issues": [],
        }

    if "APPLICABLE" not in applicabilities:
        if "NOT_APPLICABLE" in applicabilities:
            return {
                **base,
                "result": "NOT_APPLICABLE",
                "applicability": "NOT_APPLICABLE",
                "reasons": ["control determined not applicable to this target"],
                "evidence": [],
                "run_issues": [],
            }
        return {
            **base,
            "result": "UNPROVEN",
            "applicability": "UNKNOWN",
            "reasons": ["applicability has not been established"],
            "evidence": [],
            "run_issues": [],
        }

    # Step 5: applicability is APPLICABLE -- check evidence + capability gates.
    required: list[str] = control["required_evidence"]
    by_requirement: dict[str, bool] = {}
    contributing_types: set[str] = set()
    for run in clean:
        if run["applicability"] != "APPLICABLE":
            continue
        for item in run["evidence"]:
            if item["status"] == "SATISFIED":
                by_requirement[item["requirement"]] = True
                contributing_types.add(run["verifier"]["type"])

    missing = [r for r in required if r not in by_requirement]
    if missing:
        return {
            **base,
            "result": "UNPROVEN",
            "applicability": "APPLICABLE",
            "reasons": [f"missing required evidence: {r}" for r in missing],
            "evidence": _collect_evidence(clean),
            "run_issues": [],
        }

    if control["verification"]["dynamic_required"] and not (contributing_types & validate_catalog.DYNAMIC_VERIFIER_TYPES):
        return {
            **base,
            "result": "UNPROVEN",
            "applicability": "APPLICABLE",
            "reasons": ["dynamic_required is true but no accepted evidence came from a dynamic verifier capability"],
            "evidence": _collect_evidence(clean),
            "run_issues": [],
        }

    if control["verification"]["human_judgment_required"] and not (contributing_types & HUMAN_JUDGMENT_CAPABLE_TYPES):
        return {
            **base,
            "result": "UNPROVEN",
            "applicability": "APPLICABLE",
            "reasons": [
                "human_judgment_required is true but no accepted evidence came from a "
                "human/semantic-judgment verifier capability"
            ],
            "evidence": _collect_evidence(clean),
            "run_issues": [],
        }

    return {
        **base,
        "result": "PASS",
        "applicability": "APPLICABLE",
        "reasons": [
            f"all {len(required)} required evidence item(s) satisfied by permitted verifier "
            f"capabilities: {sorted(contributing_types)}"
        ],
        "evidence": _collect_evidence(clean),
        "run_issues": [],
    }


def _collect_evidence(clean_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for run in clean_runs:
        for item in run["evidence"]:
            out.append(
                {
                    "requirement": item["requirement"],
                    "status": item["status"],
                    "provenance": item["provenance"],
                    "detail": item.get("detail"),
                    "contributed_by": run["verifier"],
                    "observed_at": run.get("observed_at"),
                }
            )
    return out


def _run_issues(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "verifier_type": (p["run"].get("verifier", {}) or {}).get("type") if isinstance(p["run"], dict) else None,
            "status": p["status"],
            "reason": p["reason"],
        }
        for p in problems
    ]


def evaluate(runs: list[Any], controls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Group runs by control_id (in order of first appearance) and return
    one aggregate result per group. A run whose control_id cannot be
    resolved to a real string is grouped alone (never merged with an
    unrelated run) using its position in the input for uniqueness."""
    groups: dict[Any, list[Any]] = {}
    order: list[Any] = []
    for index, run in enumerate(runs):
        raw_id = run.get("control_id") if isinstance(run, dict) else None
        key = raw_id if isinstance(raw_id, str) else ("__unresolvable__", index)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(run)

    results = []
    for key in order:
        control_id = key if isinstance(key, str) else None
        control = controls.get(key) if isinstance(key, str) else None
        results.append(_aggregate_group(control_id, control, groups[key]))
    return results


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(json.dumps({"version": 1, "error": "usage: evidence_model.py <catalog.json> <runs.json>"}, sort_keys=True))
        return 1

    catalog_path, runs_path = argv[1], argv[2]

    try:
        controls = load_controls(catalog_path)
    except CatalogError as exc:
        print(json.dumps({"version": 1, "error": str(exc)}, sort_keys=True))
        return 1

    try:
        with open(runs_path, "r", encoding="utf-8") as f:
            runs = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"version": 1, "error": f"could not read/parse runs file: {exc}"}, sort_keys=True))
        return 1

    if not isinstance(runs, list):
        print(json.dumps({"version": 1, "error": "runs file must contain a JSON array"}, sort_keys=True))
        return 1

    results = evaluate(runs, controls)
    print(json.dumps({"version": 1, "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
