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
check_file "$CLAUDE_DIR/commands/orchestrate.md"         "command: /orchestrate"
check_file "$CLAUDE_DIR/commands/loop-audit.md"          "command: /loop-audit"
check_file "$CLAUDE_DIR/skills/loop-design/SKILL.md"     "skill: loop-design"
check_file "$CLAUDE_DIR/templates/diana/LOOP.md"         "loop template: LOOP.md"
check_file "$CLAUDE_DIR/templates/diana/STATE.md"        "loop template: STATE.md"
check_file "$CLAUDE_DIR/templates/diana/RUN_LOG.md"      "loop template: RUN_LOG.md"
check_file "$CLAUDE_DIR/templates/diana/BUDGET.md"       "loop template: BUDGET.md"

# --- 2. check-careful.sh executable ---
HOOK_SCRIPT="$CLAUDE_DIR/hooks/check-careful.sh"
if [ -x "$HOOK_SCRIPT" ]; then
  pass "check-careful.sh is executable"
elif [ -f "$HOOK_SCRIPT" ]; then
  fail "check-careful.sh exists but is not executable (chmod +x it)"
else
  fail "check-careful.sh is not executable (file missing)"
fi

# --- 3. shared settings contain portable hook; local settings contain cost hook ---
SHARED_SETTINGS="$CLAUDE_DIR/settings.json"
if [ ! -f "$SHARED_SETTINGS" ]; then
  fail "settings.json contains portable Diana hook  (file missing: .claude/settings.json)"
elif command -v python3 >/dev/null 2>&1; then
  RESULT="$(python3 - "$SHARED_SETTINGS" <<'PYEOF'
import json, sys

path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    print("invalid-json")
    sys.exit(0)

blob = json.dumps(data.get("hooks", {}).get("PreToolUse", []))
print("present" if "check-careful.sh" in blob else "missing")
PYEOF
)"
  case "$RESULT" in
    present) pass "settings.json: portable PreToolUse hook present" ;;
    missing) fail "settings.json: portable PreToolUse hook missing" ;;
    invalid-json) fail "settings.json is not valid JSON" ;;
  esac
else
  if grep -q "check-careful.sh" "$SHARED_SETTINGS" 2>/dev/null; then
    pass "settings.json: portable hook referenced (substring check)"
  else
    fail "settings.json: portable hook missing (substring check)"
  fi
fi

LOCAL_SETTINGS="$CLAUDE_DIR/settings.local.json"
if [ ! -f "$LOCAL_SETTINGS" ]; then
  fail "settings.local.json contains Diana cost hook  (file missing)"
elif command -v python3 >/dev/null 2>&1; then
  RESULT="$(python3 - "$LOCAL_SETTINGS" <<'PYEOF'
import json, sys
try:
    data=json.load(open(sys.argv[1]))
except Exception:
    print("invalid-json")
    raise SystemExit
blob=json.dumps(data.get("hooks", {}).get("Stop", []))
print("present" if "costs.jsonl" in blob else "missing")
PYEOF
)"
  case "$RESULT" in
    present) pass "settings.local.json: Stop hook (cost log) present" ;;
    missing) fail "settings.local.json: Stop hook (cost log) missing" ;;
    invalid-json) fail "settings.local.json is not valid JSON" ;;
  esac
else
  if grep -q "costs.jsonl" "$LOCAL_SETTINGS" 2>/dev/null; then
    pass "settings.local.json: costs.jsonl referenced (substring check, python3 unavailable)"
  else
    fail "settings.local.json: costs.jsonl not found (substring check, python3 unavailable)"
  fi
fi

# --- 3b. .mcp.json contains Diana's playwright MCP entry ---
MCP_JSON="$TARGET/.mcp.json"
if [ ! -f "$MCP_JSON" ]; then
  fail ".mcp.json contains the playwright MCP entry  (file missing: .mcp.json)"
elif command -v python3 >/dev/null 2>&1; then
  RESULT="$(python3 - "$MCP_JSON" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    print("invalid-json")
    sys.exit(0)
entry = data.get("mcpServers", {}).get("playwright")
if entry is None:
    print("missing")
elif "@playwright/mcp" in json.dumps(entry):
    print("present")
else:
    print("foreign")
PYEOF
)"
  case "$RESULT" in
    present) pass ".mcp.json: playwright MCP entry present" ;;
    missing) fail ".mcp.json: playwright MCP entry missing" ;;
    foreign) fail ".mcp.json: playwright key present but not Diana's entry (foreign 'playwright' server — install.sh will not overwrite it)" ;;
    invalid-json) fail ".mcp.json is not valid JSON" ;;
  esac
else
  if grep -q "@playwright/mcp" "$MCP_JSON" 2>/dev/null; then
    pass ".mcp.json: playwright MCP entry referenced (substring check)"
  else
    fail ".mcp.json: playwright MCP entry missing (substring check)"
  fi
fi

# --- 4. AGENTS.md contains canonical Diana policy ---
AGENTS_MD="$TARGET/AGENTS.md"
AGENTS_BEGIN="<!-- DIANA-POLICY:BEGIN"
AGENTS_END="<!-- DIANA-POLICY:END -->"
if [ -f "$AGENTS_MD" ] && grep -qF "$AGENTS_BEGIN" "$AGENTS_MD" && grep -qF "$AGENTS_END" "$AGENTS_MD"; then
  pass "AGENTS.md contains the canonical Diana policy"
else
  fail "AGENTS.md does not contain the canonical Diana policy"
fi

# --- 5. CLAUDE.md contains the Diana section ---
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
