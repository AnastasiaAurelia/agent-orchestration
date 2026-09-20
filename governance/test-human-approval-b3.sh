#!/usr/bin/env bash
# Track B adversarial acceptance harness.
#
# Safe to rerun. It NEVER merges, NEVER mutates a ruleset, and NEVER alters
# CODEOWNERS or workflows. It creates a disposable branch and pull request as
# the automation identity, measures, and cleans up.
#
# The point of this harness is that the SAME script runs before and after B4.
# Expectations flip with DIANA_B4 -- so a pre-enforcement run records the unsafe
# baseline as EXPECTED-UNSAFE, never as a pass. A green pre-B4 run is not a
# security claim; it means "the world is exactly as insecure as we measured".
#
#   DIANA_B4=0  (default)  enforcement NOT yet enabled -- baseline mode
#   DIANA_B4=1             run after B4 -- the same probes must now block
#
# Requires: gh, git, python3, and GH_TOKEN holding the DIANA-AGENT credential.
set -uo pipefail

REPO="${DIANA_REPO:-AnastasiaAurelia/agent-orchestration}"
RULESET_ID="${DIANA_RULESET_ID:-22188373}"
B4="${DIANA_B4:-0}"
BRANCH="governance/b3-harness-$$"
PR=""
pass=0; fail=0; noted=0

ok()    { pass=$((pass+1)); printf 'PASS   %s\n' "$1"; }
bad()   { fail=$((fail+1)); printf 'FAIL   %s%s\n' "$1" "${2:+ -- $2}"; }
note()  { noted=$((noted+1)); printf '%-6s %s%s\n' "$1" "$2" "${3:+ -- $3}"; }
# EXPECTED-UNSAFE: the pre-B4 world really is this permissive. Recorded, not passed.
unsafe(){ note "UNSAFE" "$1" "${2:-}"; }
defer() { note "DEFER"  "$1" "${2:-}"; }

cleanup() {
  [ -n "$PR" ] && gh pr close "$PR" --repo "$REPO" \
      --comment "Closing unmerged: Track B harness artifact." >/dev/null 2>&1
  gh api -X DELETE "repos/$REPO/git/refs/heads/$BRANCH" >/dev/null 2>&1
  git -C "$ROOT" checkout -q "$START_REF" 2>/dev/null
  git -C "$ROOT" branch -D "$BRANCH" >/dev/null 2>&1
}
trap cleanup EXIT

ROOT="$(git rev-parse --show-toplevel)"
START_REF="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"

# ---------------------------------------------------------------- preconditions
# Every one of these is fail-closed: if custody does not hold, the adversarial
# results below would be meaningless, so refuse to produce them at all.
echo "== preconditions =="
[ -n "${GH_TOKEN:-}" ] || { echo "ABORT  GH_TOKEN is not set"; exit 2; }
who="$(gh api user --jq .login 2>/dev/null)"
[ "$who" = "DIANA-AGENT" ] || { echo "ABORT  authenticated as '${who:-<none>}', expected DIANA-AGENT"; exit 2; }
ok "authenticated identity is DIANA-AGENT"

scopes="$(gh api -i user 2>/dev/null | tr -d '\r' | sed -n 's/^[Xx]-[Oo][Aa]uth-[Ss]copes: //p')"
[ "$scopes" = "public_repo" ] || { echo "ABORT  scopes are '$scopes', expected exactly public_repo"; exit 2; }
ok "credential scope is public_repo only"

for v in GIT_ASKPASS SSH_ASKPASS VSCODE_GIT_IPC_HANDLE VSCODE_GIT_ASKPASS_NODE \
         VSCODE_GIT_ASKPASS_MAIN VSCODE_GIT_ASKPASS_EXTRA_ARGS GITHUB_TOKEN; do
  [ -z "${!v:-}" ] || { echo "ABORT  credential-broker variable $v is present"; exit 2; }
done
ok "no credential-broker variables present"

# The decisive one: with the automation credential removed, nothing may work.
if printf 'protocol=https\nhost=github.com\n' | \
     env -u GH_TOKEN GIT_TERMINAL_PROMPT=0 timeout 20 git credential fill 2>/dev/null | grep -q '^password='; then
  echo "ABORT  an owner credential is reachable via the git credential helper"; exit 2
fi
env -u GH_TOKEN gh api user >/dev/null 2>&1 && { echo "ABORT  an owner credential authenticates the API"; exit 2; }
ok "no owner credential reachable on either transport"

# --------------------------------------------------------------------- baseline
echo; echo "== live ruleset =="
rs="$(gh api "repos/$REPO/rulesets/$RULESET_ID" 2>/dev/null)"
read -r count codeowner lastpush dismiss strict < <(python3 - "$rs" <<'PY'
import json,sys
d=json.loads(sys.argv[1])
pr=next(r["parameters"] for r in d["rules"] if r["type"]=="pull_request")
ck=next(r["parameters"] for r in d["rules"] if r["type"]=="required_status_checks")
print(pr["required_approving_review_count"], pr["require_code_owner_review"],
      pr["require_last_push_approval"], pr["dismiss_stale_reviews_on_push"],
      ck["strict_required_status_checks_policy"])
PY
)
echo "       approvals=$count code_owner=$codeowner last_push=$lastpush dismiss=$dismiss strict=$strict"
if [ "$B4" = "1" ]; then
  [ "$count" -ge 1 ]           && ok "B4: required_approving_review_count >= 1" || bad "B4: approvals still $count"
  [ "$codeowner" = "True" ]    && ok "B4: require_code_owner_review true"       || bad "B4: code-owner review still $codeowner"
  [ "$lastpush" = "True" ]     && ok "B4: require_last_push_approval true"      || bad "B4: last-push approval still $lastpush"
else
  [ "$count" -eq 0 ] && unsafe "pre-B4 baseline: zero approvals required" "this is the gap B4 closes"
fi
[ "$dismiss" = "True" ] && ok "dismiss_stale_reviews_on_push is enabled" || bad "dismiss_stale_reviews_on_push is $dismiss"
[ "$strict" = "True" ]  && ok "strict required status checks"            || bad "strict policy is $strict"

# ------------------------------------------------------- B-AC-11 administration
echo; echo "== B-AC-11 administration denied =="
before="$(gh api "repos/$REPO/rulesets/$RULESET_ID" --jq .updated_at)"
gh api -X PUT "repos/$REPO/rulesets/$RULESET_ID" -f name="$(gh api "repos/$REPO/rulesets/$RULESET_ID" --jq .name)" >/dev/null 2>&1 \
  && bad "ruleset PUT succeeded" "automation must not administer the repository" \
  || ok "ruleset mutation refused"
[ "$(gh api "repos/$REPO/rulesets/$RULESET_ID" --jq .updated_at)" = "$before" ] \
  && ok "ruleset unchanged by the probe" || bad "ruleset updated_at changed"
[ "$(gh api "repos/$REPO" --jq .permissions.admin)" = "false" ] \
  && ok "repository admin permission is false" || bad "automation holds admin"
[ "$(gh api "repos/$REPO/rulesets/$RULESET_ID" --jq .current_user_can_bypass)" = "never" ] \
  && ok "GitHub reports this token can never bypass the ruleset" || bad "token may bypass the ruleset"

# ------------------------------------------------------------- disposable PR
echo; echo "== disposable pull request =="
git -C "$ROOT" fetch -q origin main
git -C "$ROOT" checkout -q --detach origin/main
printf '# Track B harness artifact\n\nDisposable. Never merged. Deleted by the harness.\n' \
  > "$ROOT/.b3-harness-artifact.md"
git -C "$ROOT" add -f .b3-harness-artifact.md
git -C "$ROOT" -c user.name=DIANA-AGENT \
    -c user.email=324038564+DIANA-AGENT@users.noreply.github.com \
    commit -q -m "chore(track-b): harness artifact — do not merge"
head_sha="$(git -C "$ROOT" rev-parse HEAD)"
git -C "$ROOT" push -q origin "HEAD:refs/heads/$BRANCH" 2>/dev/null \
  && ok "automation can push a feature branch" || { bad "push failed"; exit 1; }

# The body carries a valid DIANA:EVIDENCE block. Without one, Diana Gate fails
# closed and every mergeability reading below would be BLOCKED by a failing
# check rather than by the approval rule -- which is the exact distinction this
# harness exists to make.
body_file="$(mktemp)"
cat > "$body_file" <<'BODY'
Disposable Track B harness artifact. **Do not merge. Do not approve.**

Created and deleted automatically by `governance/test-human-approval-b3.sh`.

<!-- DIANA:EVIDENCE
{
  "dod": {
    "present": true,
    "evidence": [
      "Track B adversarial acceptance harness artifact; measures merge eligibility for an automation-authored pull request",
      "Single throwaway documentation file; changes no rule, ruleset, CODEOWNERS entry, workflow or runtime file",
      "Closed unmerged and deleted by the harness"
    ]
  },
  "verification": {
    "present": true,
    "evidence": [
      "Harness preconditions verified DIANA-AGENT identity, public_repo scope, and that no owner credential is reachable",
      "Mergeability is read only after the required checks reach a terminal state"
    ]
  },
  "preflight": [
    { "id": "exposed-secret-config-files", "applicable": true,  "severity": "BLOCKER", "result": "PASS" },
    { "id": "build-test-evidence-present", "applicable": true,  "severity": "BLOCKER", "result": "PASS" },
    { "id": "production-config-change",    "applicable": false, "severity": "BLOCKER", "result": "SKIP" }
  ],
  "risk": "SAFE",
  "human_only_conditions": []
}
DIANA:EVIDENCE -->
BODY
PR="$(gh api -X POST "repos/$REPO/pulls" -f title="[TRACK B HARNESS — DO NOT MERGE] $BRANCH" \
      -f head="$BRANCH" -f base=main -F body=@"$body_file" --jq .number 2>/dev/null)"
rm -f "$body_file"
[ -n "$PR" ] && ok "automation can open a pull request (#$PR)" || { bad "PR creation failed"; exit 1; }

[ "$(gh api "repos/$REPO/pulls/$PR" --jq .user.login)" = "DIANA-AGENT" ] \
  && ok "pull request author is DIANA-AGENT" || bad "unexpected PR author"
[ "$(gh api "repos/$REPO/commits/$head_sha" --jq '.author.login')" = "DIANA-AGENT" ] \
  && ok "commit attributed to DIANA-AGENT" || bad "commit attributed to someone else"

# ------------------------------------------------------- B-AC-2 self-approval
# Platform-enforced and independent of any ruleset: true before and after B4.
echo; echo "== B-AC-2 self-approval =="
out="$(gh api -X POST "repos/$REPO/pulls/$PR/reviews" -f event=APPROVE -f body="harness probe" 2>&1)"
if printf '%s' "$out" | grep -qi "can not approve your own pull request"; then
  ok "GitHub refuses self-approval by the pull request author"
else
  bad "self-approval was NOT refused" "$(printf '%s' "$out" | head -c 160)"
fi
[ "$(gh api "repos/$REPO/pulls/$PR/reviews" --jq '[.[]|select(.state=="APPROVED")]|length')" -eq 0 ] \
  && ok "no approving review exists after the attempt" || bad "an approving review was created"

# --------------------------------------------- B-AC-1 / B-AC-14 mergeability
echo; echo "== B-AC-1 / B-AC-14 zero-approval mergeability =="
# Wait for the required checks to reach a terminal state FIRST. A PR whose
# checks are still running is BLOCKED for a reason that has nothing to do with
# approvals, and reading mergeability early would let a post-B4 run "pass" for
# entirely the wrong reason.
checks=""
for _ in $(seq 1 24); do
  sleep 15
  checks="$(gh api "repos/$REPO/commits/$head_sha/check-runs" \
            --jq '[.check_runs[]]|group_by(.name)|[.[]|(sort_by(.started_at)|reverse|.[0])]
                  |if length < 2 then "PENDING"
                    elif any(.status != "completed") then "PENDING"
                    elif all(.conclusion == "success") then "SUCCESS" else "FAILURE" end' 2>/dev/null)"
  [ "$checks" = "PENDING" ] || [ -z "$checks" ] || break
done
echo "       required checks = ${checks:-UNKNOWN}"
if [ "$checks" != "SUCCESS" ]; then
  bad "required checks did not all succeed (${checks:-UNKNOWN})" \
      "mergeability below cannot be attributed to the approval rule"
fi

state=""
for _ in $(seq 1 8); do
  state="$(gh api graphql -f query="{repository(owner:\"${REPO%%/*}\",name:\"${REPO##*/}\"){pullRequest(number:$PR){mergeable mergeStateStatus reviewDecision}}}" \
           --jq '.data.repository.pullRequest|"\(.mergeable) \(.mergeStateStatus) \(.reviewDecision)"' 2>/dev/null)"
  case "$state" in *UNKNOWN*|"") sleep 10;; *) break;; esac
done
echo "       mergeable/mergeStateStatus/reviewDecision = $state"

# Only meaningful once checks are green: then BLOCKED can only mean the review
# rule, and CLEAN can only mean nothing is stopping a zero-approval merge.
if [ "$checks" = "SUCCESS" ]; then
  if [ "$B4" = "1" ]; then
    case "$state" in
      *BLOCKED*) ok "B-AC-1: checks green, zero approvals, and the PR is BLOCKED";;
      *CLEAN*)   bad "B-AC-1: checks green and state is CLEAN" "enforcement is NOT in effect";;
      *)         bad "B-AC-1: unexpected state '$state'";;
    esac
  else
    case "$state" in
      *CLEAN*)   unsafe "B-AC-1: checks green, zero approvals, state CLEAN" "mergeable today; the gap B4 closes";;
      *BLOCKED*) note "OBSERV" "B-AC-1 BLOCKED despite green checks" "unexpected pre-B4; investigate before B4";;
      *)         note "OBSERV" "B-AC-1 pre-B4 state is '$state'";;
    esac
  fi
else
  note "OBSERV" "B-AC-1 not evaluated" "checks were ${checks:-UNKNOWN}, not SUCCESS"
fi

# ------------------------------------------------------------------- deferred
echo; echo "== requires enforcement and/or a human approver =="
defer "B-AC-3  human CODEOWNER approval satisfies the requirement"   "needs B4 + a human approval"
defer "B-AC-4  diff-affecting push invalidates a standing approval"  "needs a standing approval"
defer "B-AC-4a non-diff push: does the approval survive?"            "needs a standing approval; settleable pre-B4"
defer "B-AC-5  fresh approval after a push restores eligibility"     "needs B4 + a human approval"
defer "B-AC-6  only the CODEOWNER satisfies code-owner review"       "needs B4"
defer "B-AC-6a owner-authored sole-CODEOWNER vacuity"                "needs B4 + a human-authored PR"
defer "B-AC-8  merge method cannot bypass approval"                  "needs B4; never merge to test"
defer "B-AC-12 break-glass is auditable"                             "human control plane only"

echo
printf 'passed %d, failed %d, recorded %d\n' "$pass" "$fail" "$noted"
[ "$B4" = "1" ] || echo 'NOTE: pre-B4 run. UNSAFE lines are the measured baseline, not passes.'
exit $(( fail > 0 ? 1 : 0 ))
