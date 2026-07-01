#!/usr/bin/env bash
# Diana install verifier.
#
# Checks whether Diana is correctly installed in a target project and
# prints PASS/FAIL for each item. Read-only — never modifies anything.
#
# Usage:
#   ./verify.sh [target-project-path]
set -uo pipefail

TARGET_ARG="${1:-.}"
if [ ! -d "$TARGET_ARG" ]; then
  echo "error: target path '$TARGET_ARG' does not exist" >&2
  exit 1
fi
TARGET="$(cd "$TARGET_ARG" && pwd)"
CLAUDE_DIR="$TARGET/.claude"

FAIL_COUNT=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

echo "Diana verify → $TARGET"
echo

# --- 1. required files present ---
check_file() {
  local path="$1" label="$2"
  if [ -f "$path" ]; then
    pass "$label"
  else
    fail "$label  (missing: ${path#"$TARGET"/})"
  fi
}

check_file "$CLAUDE_DIR/skills/plan-review/SKILL.md"    "skill: plan-review"
check_file "$CLAUDE_DIR/skills/research-first/SKILL.md" "skill: research-first"
check_file "$CLAUDE_DIR/skills/minimal-solution/SKILL.md" "skill: minimal-solution"
check_file "$CLAUDE_DIR/commands/fix.md"                "command: /fix"
check_file "$CLAUDE_DIR/commands/review.md"              "command: /review"
check_file "$CLAUDE_DIR/commands/ship.md"                "command: /ship"
check_file "$CLAUDE_DIR/commands/cost-report.md"         "command: /cost-report"
check_file "$CLAUDE_DIR/hooks/check-careful.sh"          "hook script: check-careful.sh"

# --- 2. check-careful.sh executable ---
HOOK_SCRIPT="$CLAUDE_DIR/hooks/check-careful.sh"
if [ -x "$HOOK_SCRIPT" ]; then
  pass "check-careful.sh is executable"
elif [ -f "$HOOK_SCRIPT" ]; then
  fail "check-careful.sh exists but is not executable (chmod +x it)"
else
  fail "check-careful.sh is not executable (file missing)"
fi

# --- 3. settings.local.json contains the Diana hook config ---
SETTINGS="$CLAUDE_DIR/settings.local.json"
if [ ! -f "$SETTINGS" ]; then
  fail "settings.local.json contains Diana hooks  (file missing: .claude/settings.local.json)"
elif command -v python3 >/dev/null 2>&1; then
  RESULT="$(python3 - "$SETTINGS" <<'PYEOF'
import json, sys

path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    print("invalid-json")
    sys.exit(0)

hooks = data.get("hooks", {})

def has(event, needle):
    for entry in hooks.get(event, []):
        if needle in json.dumps(entry):
            return True
    return False

pre = has("PreToolUse", "check-careful.sh")
stop = has("Stop", "costs.jsonl")

if pre and stop:
    print("both")
elif pre:
    print("pretooluse-only")
elif stop:
    print("stop-only")
else:
    print("neither")
PYEOF
)"
  case "$RESULT" in
    both)
      pass "settings.local.json: PreToolUse hook (check-careful.sh) present"
      pass "settings.local.json: Stop hook (cost log) present"
      ;;
    pretooluse-only)
      pass "settings.local.json: PreToolUse hook (check-careful.sh) present"
      fail "settings.local.json: Stop hook (cost log) missing"
      ;;
    stop-only)
      fail "settings.local.json: PreToolUse hook (check-careful.sh) missing"
      pass "settings.local.json: Stop hook (cost log) present"
      ;;
    neither)
      fail "settings.local.json: PreToolUse hook (check-careful.sh) missing"
      fail "settings.local.json: Stop hook (cost log) missing"
      ;;
    invalid-json)
      fail "settings.local.json is not valid JSON"
      ;;
  esac
else
  # no python3 — fall back to a plain substring check
  if grep -q "check-careful.sh" "$SETTINGS" 2>/dev/null; then
    pass "settings.local.json: check-careful.sh referenced (substring check, python3 unavailable)"
  else
    fail "settings.local.json: check-careful.sh not found (substring check, python3 unavailable)"
  fi
  if grep -q "costs.jsonl" "$SETTINGS" 2>/dev/null; then
    pass "settings.local.json: costs.jsonl referenced (substring check, python3 unavailable)"
  else
    fail "settings.local.json: costs.jsonl not found (substring check, python3 unavailable)"
  fi
fi

# --- 4. CLAUDE.md contains the Diana section ---
CLAUDE_MD="$TARGET/CLAUDE.md"
BEGIN_MARKER="<!-- DIANA:BEGIN"
END_MARKER="<!-- DIANA:END -->"
if [ -f "$CLAUDE_MD" ] && grep -qF "$BEGIN_MARKER" "$CLAUDE_MD" && grep -qF "$END_MARKER" "$CLAUDE_MD"; then
  pass "CLAUDE.md contains the Diana section"
else
  fail "CLAUDE.md does not contain the Diana section"
fi

echo
if [ "$FAIL_COUNT" -eq 0 ]; then
  echo "All checks passed."
  exit 0
else
  echo "$FAIL_COUNT check(s) failed. Run install.sh $TARGET to fix."
  exit 1
fi
