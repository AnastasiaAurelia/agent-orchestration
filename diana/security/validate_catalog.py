#!/usr/bin/env python3
"""Deterministic, dependency-free structural validator for
diana/security/catalog.json (Diana Security Phase 0).

No network. No LLM. No security-tool invocation. Reads the given catalog
file and validates its structure only -- it does not evaluate whether this
repository passes any control. Exits 0 only when the catalog is structurally
valid; exits 1 with the full list of problems otherwise (fail closed).
"""

import json
import sys

ALLOWED_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

ALLOWED_VERIFIER_TYPES = {
    "DETERMINISTIC_REPO",
    "STATIC_ANALYZER",
    "SECRET_SCANNER",
    "DEPENDENCY_SCANNER",
    "INFRA_CONFIG",
    "DYNAMIC_API",
    "DYNAMIC_BROWSER",
    "DYNAMIC_DB",
    "DYNAMIC_CONCURRENCY",
    "PROVIDER_SANDBOX",
    "SEMANTIC_REVIEW",
    "HUMAN",
}

DYNAMIC_VERIFIER_TYPES = {
    "DYNAMIC_API",
    "DYNAMIC_BROWSER",
    "DYNAMIC_DB",
    "DYNAMIC_CONCURRENCY",
    "PROVIDER_SANDBOX",
}

REQUIRED_CONTROL_FIELDS = {
    "id",
    "source_number",
    "slug",
    "title",
    "severity",
    "source_summary",
    "applicability",
    "verification",
    "required_evidence",
}

# applicability.notes and verification.human_judgment_required are optional
# documented extensions; everything else on a control must be one of
# REQUIRED_CONTROL_FIELDS.
ALLOWED_CONTROL_FIELDS = REQUIRED_CONTROL_FIELDS

REQUIRED_SOURCE_SUMMARY_FIELDS = {
    "what_it_is",
    "usual_cause",
    "potential_impact",
    "prevention",
}

REQUIRED_VERIFICATION_FIELDS = {"modes", "dynamic_required", "human_judgment_required"}
ALLOWED_APPLICABILITY_FIELDS = {"signals", "notes"}


def fail(errors, msg):
    errors.append(msg)


def validate(catalog):
    errors = []

    if not isinstance(catalog, dict):
        return ["top-level catalog must be a JSON object"]

    if catalog.get("catalog_version") != 1:
        fail(errors, f"catalog_version must be 1, got {catalog.get('catalog_version')!r}")

    verifier_types = catalog.get("verifier_types")
    if not isinstance(verifier_types, list) or not verifier_types:
        fail(errors, "verifier_types must be a non-empty list")
        verifier_types = []
    unknown_declared = [v for v in verifier_types if v not in ALLOWED_VERIFIER_TYPES]
    if unknown_declared:
        fail(errors, f"verifier_types declares unknown types: {unknown_declared}")

    controls = catalog.get("controls")
    if not isinstance(controls, list):
        fail(errors, "controls must be a list")
        controls = []

    if len(controls) != 75:
        fail(errors, f"expected exactly 75 controls, got {len(controls)}")

    ids_seen = []
    source_numbers_seen = []
    slugs_seen = []

    for i, control in enumerate(controls):
        where = f"controls[{i}]"
        if not isinstance(control, dict):
            fail(errors, f"{where}: control must be an object")
            continue

        control_id = control.get("id")
        where = f"{where} ({control_id!r})"

        missing = REQUIRED_CONTROL_FIELDS - set(control.keys())
        if missing:
            fail(errors, f"{where}: missing required field(s): {sorted(missing)}")

        unknown = set(control.keys()) - ALLOWED_CONTROL_FIELDS
        if unknown:
            fail(errors, f"{where}: unknown top-level control field(s): {sorted(unknown)}")

        # id
        if isinstance(control_id, str):
            ids_seen.append(control_id)
        else:
            fail(errors, f"{where}: id must be a string")

        # source_number
        source_number = control.get("source_number")
        if isinstance(source_number, int) and not isinstance(source_number, bool):
            source_numbers_seen.append(source_number)
            expected_id = f"SEC-{source_number:03d}"
            if isinstance(control_id, str) and control_id != expected_id:
                fail(errors, f"{where}: id {control_id!r} does not match source_number {source_number!r} (expected {expected_id!r})")
        else:
            fail(errors, f"{where}: source_number must be an integer")

        # slug
        slug = control.get("slug")
        if isinstance(slug, str) and slug:
            slugs_seen.append(slug)
        else:
            fail(errors, f"{where}: slug must be a non-empty string")

        # title
        title = control.get("title")
        if not isinstance(title, str) or not title.strip():
            fail(errors, f"{where}: title must be non-empty")

        # severity
        severity = control.get("severity")
        if severity not in ALLOWED_SEVERITIES:
            fail(errors, f"{where}: severity must be one of {sorted(ALLOWED_SEVERITIES)}, got {severity!r}")

        # source_summary
        source_summary = control.get("source_summary")
        if isinstance(source_summary, dict):
            missing_fields = REQUIRED_SOURCE_SUMMARY_FIELDS - set(source_summary.keys())
            if missing_fields:
                fail(errors, f"{where}: source_summary missing field(s): {sorted(missing_fields)}")
            for field in REQUIRED_SOURCE_SUMMARY_FIELDS:
                value = source_summary.get(field)
                if field in source_summary and (not isinstance(value, str) or not value.strip()):
                    fail(errors, f"{where}: source_summary.{field} must be a non-empty string")
        else:
            fail(errors, f"{where}: source_summary must be an object")

        # applicability
        applicability = control.get("applicability")
        if isinstance(applicability, dict):
            unknown_app_fields = set(applicability.keys()) - ALLOWED_APPLICABILITY_FIELDS
            if unknown_app_fields:
                fail(errors, f"{where}: applicability has unknown field(s): {sorted(unknown_app_fields)}")
            signals = applicability.get("signals")
            if not isinstance(signals, list) or not signals:
                fail(errors, f"{where}: applicability.signals must be a non-empty list")
            elif any(not isinstance(s, str) or not s.strip() for s in signals):
                fail(errors, f"{where}: applicability.signals must contain only non-empty strings")
        else:
            fail(errors, f"{where}: applicability must be an object")

        # verification
        verification = control.get("verification")
        dynamic_required = None
        modes = None
        if isinstance(verification, dict):
            missing_v = REQUIRED_VERIFICATION_FIELDS - set(verification.keys())
            if missing_v:
                fail(errors, f"{where}: verification missing field(s): {sorted(missing_v)}")

            modes = verification.get("modes")
            if not isinstance(modes, list) or not modes:
                fail(errors, f"{where}: verification.modes must be a non-empty list")
            else:
                unknown_modes = [m for m in modes if m not in ALLOWED_VERIFIER_TYPES]
                if unknown_modes:
                    fail(errors, f"{where}: verification.modes has unknown verifier type(s): {unknown_modes}")

            dynamic_required = verification.get("dynamic_required")
            if not isinstance(dynamic_required, bool):
                fail(errors, f"{where}: verification.dynamic_required must be a boolean")

            human_judgment_required = verification.get("human_judgment_required")
            if not isinstance(human_judgment_required, bool):
                fail(errors, f"{where}: verification.human_judgment_required must be a boolean")

            if dynamic_required is True and isinstance(modes, list):
                if not any(m in DYNAMIC_VERIFIER_TYPES for m in modes):
                    fail(
                        errors,
                        f"{where}: dynamic_required is true but verification.modes contains no dynamic "
                        f"verifier type ({sorted(DYNAMIC_VERIFIER_TYPES)})",
                    )
        else:
            fail(errors, f"{where}: verification must be an object")

        # required_evidence
        required_evidence = control.get("required_evidence")
        if not isinstance(required_evidence, list) or not required_evidence:
            fail(errors, f"{where}: required_evidence must be a non-empty list")
        else:
            if any(not isinstance(e, str) or not e.strip() for e in required_evidence):
                fail(errors, f"{where}: required_evidence must contain only non-empty strings")

    # cross-control uniqueness / contiguity
    if len(ids_seen) != len(set(ids_seen)):
        dupes = sorted({x for x in ids_seen if ids_seen.count(x) > 1})
        fail(errors, f"duplicate control id(s): {dupes}")

    if len(source_numbers_seen) != len(set(source_numbers_seen)):
        dupes = sorted({x for x in source_numbers_seen if source_numbers_seen.count(x) > 1})
        fail(errors, f"duplicate source_number(s): {dupes}")

    if len(slugs_seen) != len(set(slugs_seen)):
        dupes = sorted({x for x in slugs_seen if slugs_seen.count(x) > 1})
        fail(errors, f"duplicate slug(s): {dupes}")

    expected_ids = {f"SEC-{n:03d}" for n in range(1, 76)}
    if set(ids_seen) != expected_ids:
        missing_ids = sorted(expected_ids - set(ids_seen))
        extra_ids = sorted(set(ids_seen) - expected_ids)
        if missing_ids:
            fail(errors, f"missing control id(s): {missing_ids}")
        if extra_ids:
            fail(errors, f"unexpected control id(s): {extra_ids}")

    expected_numbers = set(range(1, 76))
    if set(source_numbers_seen) != expected_numbers:
        missing_nums = sorted(expected_numbers - set(source_numbers_seen))
        extra_nums = sorted(set(source_numbers_seen) - expected_numbers)
        if missing_nums:
            fail(errors, f"missing source_number(s): {missing_nums}")
        if extra_nums:
            fail(errors, f"unexpected source_number(s): {extra_nums}")

    return errors


def main(argv):
    if len(argv) != 2:
        print("usage: validate_catalog.py <catalog.json>", file=sys.stderr)
        return 1

    path = argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: could not read/parse {path}: {e}", file=sys.stderr)
        return 1

    errors = validate(catalog)

    if errors:
        print(f"FAIL: {len(errors)} problem(s) found in {path}:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"PASS: {path} is a structurally valid Diana Security catalog (75 controls).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
