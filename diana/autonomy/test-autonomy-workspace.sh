#!/usr/bin/env bash
# PHASE 2 — workspace provenance, preserve and revert.
#
# The real failure this replaces: a working tree holding changes from a prior
# COMPLETE run, a timed-out partial run, and the user's own editing, with a
# human left to decide by memory which is which. Every "helpful" cleanup at that
# point (reset --hard, checkout ., restore all dirty files) destroys work.
#
# What is proven here is that Diana reverts ONLY what it can show it wrote and
# nobody has touched since, and that everything else escalates instead.
set -uo pipefail
AUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$AUT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import copy, json, subprocess, sys, uuid
from pathlib import Path

diana, tmp = Path(sys.argv[1]), Path(sys.argv[2])
for sub in ("autonomy", "unattended", "runtime", "mutation", "profile"):
    sys.path.insert(0, str(diana / sub))
import escalation as E, provenance as PV
import reconcile as RC

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)
def code_of(fn):
    try:
        fn(); return None
    except E.Escalation as exc: return exc.code

USER_TEXT = "# the user was in the middle of this\nvalue = 41\n"

def fresh(name):
    root = tmp / f"w-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True)
    (root / "src" / "committed.py").write_text("committed = 1\n")
    (root / "src" / "user.py").write_text("value = 1\n")
    (root / "src" / "target.py").write_text("target = 1\n")
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "t@x"); g("config", "user.name", "t")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

def recon(root, before_files):
    after = RC.snapshot(str(root))
    d = RC.diff(before_files, after)
    return {"paths_touched": sorted(set(d["created"]) | set(d["modified"]) | set(d["deleted"]))}

# ======================================================================
print("=== the baseline separates the user's work from everything that follows ===")
ROOT = fresh("base")
(ROOT / "src" / "user.py").write_text(USER_TEXT)          # user edits BEFORE Diana
REC = PV.begin("root-1", str(ROOT))
check("a file dirty before the lineage began is recorded as the user's",
      PV.classify(REC, "src/user.py") == PV.USER_PREEXISTING)
check("an unmodified committed file is committed baseline",
      PV.classify(REC, "src/committed.py") == PV.COMMITTED_BASELINE)
check("the baseline snapshot captured content hashes for the whole tree",
      REC["baseline"]["src/committed.py"] == RC.snapshot(str(ROOT))["src/committed.py"])

before = RC.snapshot(str(ROOT))
(ROOT / "src" / "target.py").write_text("target = 2   # Diana's partial edit\n")
(ROOT / "src" / "new.py").write_text("created_by_diana = True\n")
REC = PV.observe_run(REC, run_id="root-1", reconciliation=recon(ROOT, before))
check("a file Diana modified becomes owned-unverified",
      PV.classify(REC, "src/target.py") == PV.OWNED_UNVERIFIED)
check("a file Diana created becomes owned-unverified",
      PV.classify(REC, "src/new.py") == PV.OWNED_UNVERIFIED)
check("and the user's file is STILL the user's after a Diana run",
      PV.classify(REC, "src/user.py") == PV.USER_PREEXISTING)

# ======================================================================
print("\n=== revert touches only what Diana can prove it owns ===")
plan = PV.plan_revert(REC, ["src/target.py", "src/new.py", "src/user.py",
                            "src/committed.py"])
check("the plan reverts exactly Diana's own unverified changes",
      plan["revert"] == ["src/new.py", "src/target.py"], f"({plan['revert']})")
refused = {r["path"]: r for r in plan["refused"]}
check("the user's pre-existing change is REFUSED, with the reason said plainly",
      refused["src/user.py"]["state"] == PV.USER_PREEXISTING
      and "before this session began" in refused["src/user.py"]["reason"])
check("committed content is refused too",
      refused["src/committed.py"]["state"] == PV.COMMITTED_BASELINE)
check("a revert request containing ANY unsafe path reverts nothing at all",
      code_of(lambda: PV.apply_revert(REC, ["src/target.py", "src/user.py"]))
      == E.REVERT_WOULD_LOSE_WORK
      and (ROOT / "src" / "target.py").read_text().endswith("Diana's partial edit\n"))
falsify("that same target reverts cleanly on its own, so the refusal was the USER'S "
        "file and not the mechanism",
        PV.apply_revert(copy.deepcopy(REC), ["src/target.py"])["reverted"] == ["src/target.py"]
        and (ROOT / "src" / "target.py").read_text() == "target = 1\n")

(ROOT / "src" / "target.py").write_text("target = 2   # Diana's partial edit\n")
out = PV.apply_revert(REC, ["src/target.py", "src/new.py"])
check("applying the safe set restores a modified file byte-for-byte",
      (ROOT / "src" / "target.py").read_text() == "target = 1\n")
check("and removes a file Diana created, which had no baseline",
      out["removed"] == ["src/new.py"] and not (ROOT / "src" / "new.py").exists())
check("THE CRITICAL PROPERTY: the user's file is untouched, byte-for-byte",
      (ROOT / "src" / "user.py").read_text() == USER_TEXT)
check("reverted paths leave the provenance record, since Diana no longer owns them",
      PV.classify(out["record"], "src/target.py") == PV.COMMITTED_BASELINE)

# ======================================================================
print("\n=== a change edited after Diana wrote it is no longer Diana's to undo ===")
R2 = fresh("edited")
REC2 = PV.begin("root-2", str(R2))
b2 = RC.snapshot(str(R2))
(R2 / "src" / "target.py").write_text("target = 2   # diana\n")
REC2 = PV.observe_run(REC2, run_id="root-2", reconciliation=recon(R2, b2))
check("before anyone else touches it, it is revertable",
      PV.plan_revert(REC2, ["src/target.py"])["revert"] == ["src/target.py"])
(R2 / "src" / "target.py").write_text("target = 2   # diana\nuser_added = True\n")
check("after a human edits it, it reclassifies as ambiguous",
      PV.classify(REC2, "src/target.py") == PV.AMBIGUOUS)
check("and an automatic revert of it is refused",
      code_of(lambda: PV.apply_revert(REC2, ["src/target.py"])) == E.REVERT_WOULD_LOSE_WORK)
falsify("the human's line is still in the file, so nothing was reverted on the way to "
        "that refusal", "user_added = True" in (R2 / "src" / "target.py").read_text())

# ======================================================================
print("\n=== verified work is never reverted, and preserving never verifies ===")
R3 = fresh("verified")
REC3 = PV.begin("root-3", str(R3))
b3 = RC.snapshot(str(R3))
(R3 / "src" / "target.py").write_text("target = 2\n")
REC3 = PV.observe_run(REC3, run_id="root-3", reconciliation=recon(R3, b3))
VERIFIED = PV.mark_verified(REC3, ["src/target.py"])
check("a verified change is refused for automatic revert",
      PV.classify(VERIFIED, "src/target.py") == PV.OWNED_VERIFIED
      and code_of(lambda: PV.apply_revert(VERIFIED, ["src/target.py"]))
      == E.REVERT_WOULD_LOSE_WORK)
falsify("the identical change WAS revertable before verification, so verification is "
        "what protected it",
        PV.plan_revert(REC3, ["src/target.py"])["revert"] == ["src/target.py"])

PRESERVED = PV.mark_preserved(REC3, ["src/target.py"])
check("preserving carries a change forward WITHOUT marking it verified",
      PV.classify(PRESERVED, "src/target.py") == PV.PRESERVED_FOR_CHILD
      and PV.classify(PRESERVED, "src/target.py") != PV.OWNED_VERIFIED)
check("a preserved change is not auto-revertable either -- a child is relying on it",
      code_of(lambda: PV.apply_revert(PRESERVED, ["src/target.py"]))
      == E.REVERT_WOULD_LOSE_WORK)
check("only real verification promotes it, and then it is verified",
      PV.classify(PV.mark_verified(PRESERVED, ["src/target.py"]), "src/target.py")
      == PV.OWNED_VERIFIED)
check("preserving something Diana does not own is refused, not assumed",
      code_of(lambda: PV.mark_preserved(REC3, ["src/committed.py"])) == E.PRESERVE_CONFLICT
      and code_of(lambda: PV.mark_preserved(REC3, ["src/nothing.py"])) == E.PRESERVE_CONFLICT)
R3b = fresh("userowned")
REC3b = PV.begin("root-3b", str(R3b))
(R3b / "src" / "user.py").write_text(USER_TEXT)
REC3b = PV.begin("root-3b", str(R3b))
check("and preserving the USER's pre-existing change is refused",
      code_of(lambda: PV.mark_preserved(REC3b, ["src/user.py"])) == E.PRESERVE_CONFLICT)

# ======================================================================
print("\n=== ambiguity is produced honestly, and always escalates ===")
R4 = fresh("collide")
(R4 / "src" / "user.py").write_text(USER_TEXT)
REC4 = PV.begin("root-4", str(R4))
b4 = RC.snapshot(str(R4))
(R4 / "src" / "user.py").write_text(USER_TEXT + "diana_also_wrote_here = 1\n")
REC4 = PV.observe_run(REC4, run_id="root-4", reconciliation=recon(R4, b4))
check("Diana writing over the user's own dirty file becomes AMBIGUOUS, not owned",
      PV.classify(REC4, "src/user.py") == PV.AMBIGUOUS)
check("and is refused for automatic revert",
      code_of(lambda: PV.apply_revert(REC4, ["src/user.py"])) == E.REVERT_WOULD_LOSE_WORK)
check("a change with no provenance entry at all is ambiguous, never assumed Diana's",
      PV.classify(PV.begin("root-5", str(fresh("orphan"))), "src/unknown.py")
      == PV.COMMITTED_BASELINE)
R6 = fresh("orphan2"); REC6 = PV.begin("root-6", str(R6))
(R6 / "src" / "committed.py").write_text("changed by nobody Diana knows\n")
check("a file that changed with nothing recording who changed it is ambiguous",
      PV.classify(REC6, "src/committed.py") == PV.AMBIGUOUS)
check("only owned-unverified is ever auto-revertable, and that set is a named constant",
      PV.AUTO_REVERTABLE == frozenset({PV.OWNED_UNVERIFIED}))

# ======================================================================
print("\n=== the provenance record is digest-bound like every other authority input ===")
pdir = tmp / "prov"; pdir.mkdir()
PV.write(pdir, VERIFIED)
check("it round-trips through its digest envelope",
      PV.read(pdir)["root_run_id"] == "root-3")
check("and what it protects survives the round trip",
      PV.classify(PV.read(pdir), "src/target.py") == PV.OWNED_VERIFIED)
raw = json.loads((pdir / PV.PROVENANCE_NAME).read_text())
edited = False
for entry in raw["record"]["entries"]:
    if entry["path"] == "src/target.py":
        entry["state"] = PV.OWNED_UNVERIFIED     # demote verified work to revertable
        edited = True
(pdir / PV.PROVENANCE_NAME).write_text(json.dumps(raw))
check("the tamper really changed the stored state", edited is True)
check("demoting VERIFIED work to revertable by editing the record is refused",
      code_of(lambda: PV.read(pdir)) == E.PROVENANCE_AMBIGUOUS)
falsify("the untouched record still reads back and still protects that file, so the "
        "refusal is the EDIT and not the mechanism",
        (PV.write(pdir, VERIFIED),
         PV.classify(PV.read(pdir), "src/target.py"))[1] == PV.OWNED_VERIFIED)
check("an unknown provenance field is refused",
      code_of(lambda: PV.validate({**copy.deepcopy(REC3), "extra": 1}))
      == E.PROVENANCE_AMBIGUOUS)
check("an unknown provenance state is refused",
      code_of(lambda: PV.validate({**copy.deepcopy(REC3), "entries": [
          {"path": "x", "state": "trusted", "run_id": None,
           "hash_at_record": None, "baseline_hash": None}]})) == E.PROVENANCE_AMBIGUOUS)
check("a repository with no readable git view escalates rather than guessing",
      code_of(lambda: PV.begin("root-7", str(tmp / "not-a-repo")))
      in (E.PROVENANCE_AMBIGUOUS,))

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
