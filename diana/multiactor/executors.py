#!/usr/bin/env python3
"""Diana M6: executor backends at the seam M5 already had (M6-D21, F14).

Phase 0 F14 found the executor is ALREADY an injected callable at
`unattended.execute` and `unattended.run_attempt`. So M6 adds no `AgentExecutor`
class hierarchy: a hierarchy would add a name, not a bound. What M6 adds is that
Diana selects which callable acts and writes the choice down.

A backend is therefore anything satisfying the seam:

    driver(contract_block, item_id) -> None        # may raise; Diana reconciles
    driver.record  -> dict | None                  # observability only (M2-D13)
    driver.verdict -> dict | None                  # reviewer backends only

Two deterministic backends are provided so that **backend replaceability is
proven against a second implementation rather than against a vendor** (M6-D21).
`HermesBuilder` is the real-model backend, reusing M4's `RemediationDriver`
unchanged.

## What `driver.record` is NOT

Phase 0 F9 measured `turn-record-<n>.json` accepting whatever a driver puts in
it -- a driver was given `{"backend_self_report": "hermes-deepseek"}` and Diana
persisted it verbatim. So a backend may name itself there, and that name is
never read to decide anything. The acting actor is the journal's (M6-D6), and
the backend's self-report is not an identity.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "mutation"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import projection as _projection  # noqa: E402


class _Backend:
    """Common seam plumbing. Subclasses implement `_run`."""

    name = "backend"

    def __init__(self) -> None:
        self.record: dict | None = None
        self.calls: list[str | None] = []

    def __call__(self, contract_block: dict, item_id: str | None = None):
        self.calls.append(item_id)
        self.record = {"backend_self_report": self.name, "item": item_id}
        return self._run(contract_block, item_id)

    def _run(self, contract_block: dict, item_id: str | None):  # pragma: no cover
        raise NotImplementedError


class ScriptedBuilder(_Backend):
    """Deterministic backend A: applies a caller-supplied edit map.

    `edits` maps item id -> (relative path, content). A deterministic backend is
    not a weaker proof of the SEAM than a model would be: the seam's contract is
    what it receives and what Diana does with the result, and neither depends on
    how the text inside the turn was produced.
    """

    name = "scripted-builder-A"

    def __init__(self, edits: dict, fail_on=(), no_op_on=()) -> None:
        super().__init__()
        self.edits, self.fail_on, self.no_op_on = edits, set(fail_on), set(no_op_on)

    def _run(self, contract_block: dict, item_id: str | None):
        if item_id in self.fail_on:
            raise RuntimeError(f"{self.name}: deliberate turn failure on {item_id}")
        if item_id in self.no_op_on:
            return None
        plan = self.edits.get(item_id)
        if plan is None:
            return None
        rel, content = plan
        path = Path(contract_block["target"]["repo_root"]) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return None


class TemplateBuilder(ScriptedBuilder):
    """Deterministic backend B: a DIFFERENT implementation at the same seam.

    It reaches the same target state by rewriting through a template rather than
    by writing a literal, so a run that switches from `ScriptedBuilder` to this
    one has genuinely changed backend and not merely relabelled one.
    """

    name = "template-builder-B"

    def _run(self, contract_block: dict, item_id: str | None):
        if item_id in self.fail_on:
            raise RuntimeError(f"{self.name}: deliberate turn failure on {item_id}")
        if item_id in self.no_op_on:
            return None
        plan = self.edits.get(item_id)
        if plan is None:
            return None
        rel, content = plan
        path = Path(contract_block["target"]["repo_root"]) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text() if path.exists() else ""
        rendered = "".join(
            line if line.strip() else line
            for line in content.splitlines(keepends=True)
        )
        path.write_text(rendered if rendered != existing else content)
        return None


class ScriptedReviewer(_Backend):
    """Deterministic reviewer backend: emits a verdict, mutates nothing.

    It is given Diana-supplied evidence (M6-R3) and returns a structured verdict.
    Its read-only-ness is NOT established by this class being well-behaved -- it
    is established by the projection installed before its turn, proven live
    through the real dispatch funnel, and by reconciliation afterwards.
    """

    name = "scripted-reviewer"

    def __init__(self, verdicts) -> None:
        super().__init__()
        self._verdicts = list(verdicts)
        self.verdict: dict | None = None
        self.evidence_seen: list[dict] = []

    def _run(self, contract_block: dict, item_id: str | None):
        self.verdict = self._verdicts.pop(0) if self._verdicts else None
        return None


class SilentReviewer(_Backend):
    """A reviewer that produces no verdict at all -- crashed, stalled, refusing.

    M6-R5 requires this to be indistinguishable from an explicit FAIL, which is
    why it exists as a first-class backend rather than as a test stub.
    """

    name = "silent-reviewer"
    verdict = None

    def _run(self, contract_block: dict, item_id: str | None):
        return None


class HermesBuilder(_Backend):
    """The real-model backend: M4's `RemediationDriver`, reused unmodified."""

    name = "hermes-remediation"

    def __init__(self, *, hermes_home=None, prompt=None) -> None:
        super().__init__()
        sys.path.insert(0, str(_HERE.parent / "mutation"))
        import remediation_driver as _rd

        self._driver = _rd.RemediationDriver(hermes_home=hermes_home, prompt=prompt)

    def _run(self, contract_block: dict, item_id: str | None):
        result = self._driver(contract_block)
        inner = self._driver.record or {}
        self.record = {"backend_self_report": self.name, "item": item_id, **inner}
        return result


# --- verdict transport ----------------------------------------------------
#
# The reviewer's judgement crosses one untrusted boundary: a block of model text.
# This is the whole of that crossing, and it is deterministic and fail-closed.
#
# What it replaces was `re.compile(r"\{.*\}", re.S)` applied to a 2000-character
# observation. Both halves were wrong. The regex is greedy, so two unrelated
# objects in one reply splice into one unparseable candidate; and the bounded
# observation is an artifact, not a transport, so a valid verdict longer than
# the bound arrived cut in half. A real production run ended in
# `review-verdict-absent` for exactly this reason.
#
# Nothing here loosens `verdict.py`. A candidate that parses is handed to that
# closed schema unchanged; everything else yields NO verdict, which M6-R5 makes
# identical to a rejection.

PARSE_ABSENT = "absent"          # no final response, or no '{' in it at all
PARSE_UNBALANCED = "unbalanced"  # an object was opened and never closed: truncation
PARSE_MALFORMED = "malformed"    # a balanced candidate that is not a JSON object
PARSE_AMBIGUOUS = "ambiguous"    # more than one distinct object: Diana will not guess
PARSE_PARSED = "parsed"

# One fenced block. Deliberately non-greedy and anchored on the fence's own
# newline, so two fences are two candidates rather than one spliced one.
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", re.S)


def _top_level_objects(text: str) -> list[str]:
    """Every balanced depth-0 ``{...}`` run, string- and escape-aware.

    A brace scan rather than a regex because a regex cannot count. Nested
    objects are inside their parent's run and are never separate candidates, and
    an object that is opened and never closed produces no candidate at all --
    which is how truncated transport stays distinguishable from absent output.
    """
    found: list[str] = []
    depth = 0
    start = None
    in_string = escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            if depth > 0:
                in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                found.append(text[start:index + 1])
                start = None
    return found


def extract_verdict(text: object) -> tuple[dict | None, dict]:
    """(verdict or None, transport metadata). The only verdict transport.

    Accepted: one raw JSON object, with harmless surrounding whitespace, and --
    because real models do it -- one object inside a single fenced block. Every
    other shape returns None, because a reviewer malfunction must never be
    readable as approval.

    The returned metadata is SAFE by construction: presence, lengths and counts,
    plus the candidate's key names. It never carries the response itself, a
    transcript, or anything credential-shaped.
    """
    body = (text or "")
    body = body.strip() if isinstance(body, str) else ""
    transport = {"final_present": bool(body), "final_chars": len(body),
                 "fenced_blocks": 0, "candidate_count": 0, "parsed_object_count": 0,
                 "parse_status": PARSE_ABSENT, "verdict_keys": []}
    if not body:
        return None, transport

    fences = [m.group(1).strip() for m in _FENCE.finditer(body)]
    transport["fenced_blocks"] = len(fences)
    candidates: list[str] = []
    for block in fences:
        candidates.extend(_top_level_objects(block))
    # Also scan the whole reply: a fence is not required, and a stray unbalanced
    # brace in prose must not be able to hide a well-formed object inside one.
    candidates.extend(_top_level_objects(body))
    transport["candidate_count"] = len(candidates)

    if not candidates:
        transport["parse_status"] = PARSE_UNBALANCED if "{" in body else PARSE_ABSENT
        return None, transport

    seen: set[str] = set()
    objects: list[dict] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        key = json.dumps(value, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        objects.append(value)

    transport["parsed_object_count"] = len(objects)
    if len(objects) == 1:
        transport["parse_status"] = PARSE_PARSED
        transport["verdict_keys"] = sorted(str(k)[:64] for k in objects[0])
        return objects[0], transport
    if len(objects) > 1:
        # Two readings of the same reply is not a verdict. Picking one would be
        # Diana deciding what the reviewer meant, which is the thing verdict.py
        # refuses to do for a self-contradictory verdict, for the same reason.
        transport["parse_status"] = PARSE_AMBIGUOUS
        return None, transport
    transport["parse_status"] = PARSE_MALFORMED
    return None, transport


def _reviewer_prompt(evidence: dict) -> str:
    """The reviewer's brief. Every fact in it is Diana's, none is the builder's.

    M6-R2 gives Diana the verification command and the reviewer none, so the
    reviewer is told the RESULT and asked not to re-measure it. The emphasis on
    "a passing verifier is not a completed task" is deliberate and general: an
    unchanged suite that still passes says nothing about work never done, and
    the reviewer is the only semantic completion gate the run has.
    """
    envelope = evidence.get("envelope") or {}
    listed = lambda values: ", ".join(str(v) for v in (values or [])) or "(none)"
    outside = [e.get("path", e) if isinstance(e, dict) else e
               for e in (evidence.get("paths_outside_write_scope") or [])]
    return "\n".join([
        "You are the REVIEWER of one unit of completed work. You have READ-ONLY access:",
        "you can read files and search the repository, and you can do nothing else.",
        "",
        "THE TASK THAT WAS REQUESTED. This is the acceptance standard, in full:",
        str(evidence.get("task")),
        "",
        f"Work item: {evidence.get('item_id')}",
        f"Builder attempt under review: {evidence.get('reviewed_attempt')}",
        "",
        "FACTS MEASURED BY THE ORCHESTRATOR. These are observations, not claims by",
        "the builder. Do not contradict them and do not try to re-measure them:",
        f"  tools the builder was allowed: {listed(envelope.get('allowed_tools'))}",
        f"  write scope in force: {listed(envelope.get('write_scope'))}",
        f"  commands the builder was allowed: {listed(envelope.get('allowed_commands'))}",
        f"  paths this attempt actually changed: {listed(evidence.get('paths_touched'))}",
        f"  paths outside the write scope: {listed(outside)}",
        f"  the diff stayed inside the envelope: {evidence.get('within_envelope')}",
        f"  git status before the attempt: {evidence.get('git_status_before') or '(clean)'}",
        f"  git status after the attempt: {evidence.get('git_status_after') or '(clean)'}",
        "  deterministic verification command: "
        + ("PASSED" if evidence.get("verification_passed") else "FAILED"),
        "",
        "HOW TO JUDGE.",
        "The verification result above only means an already-existing command exited",
        "zero. It is NOT evidence that the requested behaviour was implemented, and an",
        "unchanged test suite that still passes proves nothing about work never done.",
        "Derive the acceptance criteria from the task text yourself. Read every path",
        "listed above, and use search_files to look for whatever the task required that",
        "is NOT in that list. A task asking for several changes, or for new regression",
        "tests, is satisfied only when all of them are present in the code.",
        "Return PASS only when every requirement is actually implemented. Otherwise",
        "return FAIL with one finding per missing or incorrect part, each naming the",
        "file it should have been in or stating that no file contains it.",
        "Put one dod_checks entry per acceptance criterion you derived.",
        "",
        "OUTPUT.",
        "Reply with exactly ONE JSON object and nothing else: no prose before or after",
        "it, no code fence, and do not repeat any structure shown to you above.",
        '{"decision":"PASS"|"FAIL","summary":"...",'
        '"findings":[{"description":"...","evidence":"..."}],'
        '"dod_checks":[{"criterion":"...","result":"PASS"|"FAIL","evidence":"..."}]}',
        "Use exactly those keys and no others. Every string must be non-empty.",
        "findings may be empty only when the decision is PASS.",
    ])


class HermesReviewer(_Backend):
    """A real-model reviewer turn under the REVIEWER projection.

    The model is asked to end with a JSON verdict. Diana parses it out of the
    COMPLETE in-memory final response and hands it to `verdict.validate`, so a
    model that returns prose, truncates, or refuses yields NO verdict -- which
    M6-R5 makes identical to a rejection. That is the designed behavior.

    ## Why the schemas are the role's own, not this module's default

    The reviewer is read-only, and a real run was measured presenting it
    `patch`, `read_file`, `search_files` and `write_file`, because the turn was
    built with the "show everything" option. Presentation is still not the
    security boundary -- `projection.install_projection` is, and it is proven
    live through Hermes's real dispatch funnel before this turn runs. But
    showing a read-only actor two mutating tools it may not use is a defect in
    its own right: it spends the turn's bounded iterations on calls that can
    only be refused. The schemas presented here are therefore derived from
    `projection.REVIEWER_TOOLS`, the same frozen constant the enforced envelope
    is compared against, so the two cannot drift apart.

    ## What Diana tells this backend before the turn

    `reviewed_attempt` and `verification_passed` are set by the run loop
    immediately before the call, in memory, from the journal and from Diana's
    own verification callback. They are how the evidence is bound to the EXACT
    builder attempt this review was scheduled for rather than to whatever
    reconciliation happens to be newest -- which, during a reviewer turn, is the
    reviewer's own in-flight attempt. Nothing here is authority: the verdict is
    still admitted only by `verdict.accept` against the digest-covered journal.
    """

    name = "hermes-reviewer"

    def __init__(self, *, evidence_for, hermes_home=None) -> None:
        super().__init__()
        self._evidence_for = evidence_for
        self._hermes_home = hermes_home
        self.verdict: dict | None = None
        self.evidence_seen: list[dict] = []
        # Set by the run loop before each reviewer turn (see actors.brief_reviewer).
        self.reviewed_attempt: int | None = None
        self.verification_passed: bool | None = None

    def _run(self, contract_block: dict, item_id: str | None):
        sys.path.insert(0, str(_HERE.parent / "adapters"))
        import hermes_live as _live

        # A stale verdict from a previous attempt must never survive into this
        # one: a reviewer backend is constructed once and called many times, and
        # a PASS left behind would bless work it never saw.
        self.verdict = None
        evidence = self._evidence_for(
            contract_block, item_id,
            reviewed_attempt=self.reviewed_attempt,
            verification_passed=self.verification_passed)
        self.evidence_seen.append(evidence)

        driver = _live.LiveTurnDriver(
            hermes_home=self._hermes_home, prompt=_reviewer_prompt(evidence),
            allowed_tools=_projection.REVIEWER_TOOLS)
        try:
            driver(contract_block)
        finally:
            # Parsed from the COMPLETE final response held in memory by the
            # driver -- never from the bounded observation, which is an artifact
            # and truncates. Recorded in a `finally` so a turn that failed still
            # leaves an operator able to tell which failure it was.
            verdict, transport = extract_verdict(getattr(driver, "final_text", None))
            inner = driver.record or {}
            self.record = {"backend_self_report": self.name, "item": item_id,
                           "reviewed_attempt": self.reviewed_attempt,
                           **inner, "verdict_transport": transport}
            self.verdict = verdict
        return None


def _clean_build(record: dict, number: object):
    """The journal's entry for `number`, only if it is a clean BUILDER attempt.

    Delegates to `actors.clean_build_by_number` rather than re-deriving the
    predicate: finding M6-A2 is that an attempt number alone does not say what
    the attempt WAS, and two copies of that rule could be made to disagree.
    Imported lazily so this module stays importable on its own.
    """
    import actors as _actors

    return _actors.clean_build_by_number(record, number)


def evidence_builder(run_directory, record_provider, verify):
    """Diana-supplied reviewer evidence, composed exactly as M6-R3 fixes it.

    Phase 0 F16: the reviewer has no tool path to the run directory at all, so
    whatever it sees, Diana hands it. What is NOT handed over is as deliberate
    as what is: no journal, no contract digest, no run policy, no other actor's
    prompt, and nothing under `~/.hermes/`.

    ## Which attempt the evidence describes

    This used to read `attempts[-1]`. During a reviewer turn the newest attempt
    is the REVIEWER's own, still in flight and not yet reconciled -- so the
    evidence resolved to nothing at all, and the reviewer was asked to judge a
    build it was told had touched no files. Worse, in a multi-item run the newest
    reconciliation can belong to a different item's build.

    `reviewed_attempt` is therefore required to come from the caller: the run
    loop passes the exact build number it scheduled this review for, and that
    number is re-validated here against the digest-covered journal, so a number
    naming a reviewer attempt, a failed build, or a build that left the envelope
    resolves to a refusal rather than to the wrong evidence. Nothing is read from
    an unauthenticated sidecar file, and the number never becomes authority: the
    verdict is still admitted only by `verdict.accept` against the same journal.

    `verification_passed` is Diana's own verification result for this item,
    passed in from the loop that computed it. It falls back to calling `verify`
    only when the caller supplied nothing -- the reviewer never runs a command
    either way (M6-R2).
    """
    def build(contract_block: dict, item_id: str | None,
              *, reviewed_attempt=None, verification_passed=None) -> dict:
        record = record_provider()
        if reviewed_attempt is None:
            raise blocking.Blocked(
                blocking.REVIEW_VERDICT_WRONG_ACTOR,
                "no builder attempt was named for this review, so there is no "
                "evidence to compose; Diana does not guess which build is under review")
        attempt = _clean_build(record, reviewed_attempt)
        if attempt is None:
            raise blocking.Blocked(
                blocking.REVIEW_VERDICT_WRONG_ACTOR,
                f"attempt {reviewed_attempt!r} is not a clean BUILDER attempt in this "
                "run's journal, so no reviewer evidence can be composed for it")
        reconciliation = {}
        name = attempt.get("reconciliation_file")
        if name:
            try:
                reconciliation = json.loads(
                    (Path(run_directory) / name).read_bytes().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                reconciliation = {}
        if not reconciliation:
            # A clean build whose reconciliation cannot be read leaves Diana
            # unable to say what the attempt did, and "I cannot tell" is never
            # "nothing happened" (M5-D9).
            raise blocking.Blocked(
                blocking.RECONCILIATION_MISMATCH,
                f"attempt {reviewed_attempt!r} has no readable reconciliation record, "
                "so the reviewer cannot be given truthful evidence")
        envelope = contract_block["capability_envelope"]
        if verification_passed is None:
            verification_passed = verify(contract_block, item_id)
        return {
            "task": contract_block["task"],
            "item_id": item_id,
            "reviewed_attempt": attempt["attempt"],
            "envelope": {"allowed_tools": envelope.get("allowed_tools"),
                         "write_scope": (envelope.get("write_scope") or {}).get("allowed_roots"),
                         "allowed_commands": envelope.get("allowed_commands")},
            "paths_touched": reconciliation.get("paths_touched", []),
            "paths_outside_write_scope": reconciliation.get("paths_outside_write_scope", []),
            "within_envelope": reconciliation.get("within_envelope"),
            "git_status_before": reconciliation.get("git_status_before", ""),
            "git_status_after": reconciliation.get("git_status_after", ""),
            "verification_passed": bool(verification_passed),
        }
    return build
