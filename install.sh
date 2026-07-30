#!/usr/bin/env bash
# Diana installer.
#
# Copies Diana's Claude Code base (skills, commands, hooks, CLAUDE.md section)
# into a target project's .claude/ discovery paths.
#
# Usage:
#   ./install.sh [target-project-path]
#
# Safe by design:
#   - Never overwrites an existing file without backing it up first
#     (suffix: .bak.<timestamp>).
#   - Only touches Diana-managed entries inside settings.local.json and the
#     CLAUDE.md Diana section — it does not disturb unrelated content there.
#   - Re-running is idempotent: unchanged files/entries are left alone and
#     reported as "unchanged", not re-copied or re-backed-up.
#   - Loop templates (LOOP.md, STATE.md, RUN_LOG.md, BUDGET.md) are installed
#     as reference copies under .claude/templates/diana/ only. This script
#     never creates live root-level LOOP.md/STATE.md/RUN_LOG.md/BUDGET.md —
#     those are project state, created on demand by /orchestrate or by
#     explicit user request, never by install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_SRC="$SCRIPT_DIR/diana"
TS="$(date +%Y%m%d%H%M%S)"

TARGET_ARG="${1:-.}"
if [ ! -d "$TARGET_ARG" ]; then
  echo "error: target path '$TARGET_ARG' does not exist" >&2
  exit 1
fi
TARGET="$(cd "$TARGET_ARG" && pwd)"

if [ ! -d "$DIANA_SRC" ]; then
  echo "error: diana/ source folder not found next to this script ($DIANA_SRC)" >&2
  exit 1
fi

CLAUDE_DIR="$TARGET/.claude"

CREATED=()
UPDATED=()
UNCHANGED=()

# --- 1. directories ---
mkdir_report() {
  local dir="$1"
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"
    CREATED+=("${dir#"$TARGET"/}/")
  fi
}

mkdir_report "$CLAUDE_DIR"
mkdir_report "$CLAUDE_DIR/commands"
mkdir_report "$CLAUDE_DIR/skills"
mkdir_report "$CLAUDE_DIR/skills/plan-review"
mkdir_report "$CLAUDE_DIR/skills/research-first"
mkdir_report "$CLAUDE_DIR/skills/minimal-solution"
mkdir_report "$CLAUDE_DIR/skills/loop-design"
mkdir_report "$CLAUDE_DIR/skills/project-loop"
mkdir_report "$CLAUDE_DIR/hooks"
mkdir_report "$CLAUDE_DIR/templates"
mkdir_report "$CLAUDE_DIR/templates/diana"

# --- 2. plain file installs (backup-then-overwrite, skip if identical) ---
install_file() {
  local src="$1" dest="$2"
  local rel="${dest#"$TARGET"/}"
  if [ -f "$dest" ]; then
    if cmp -s "$src" "$dest"; then
      UNCHANGED+=("$rel")
      return
    fi
    cp "$dest" "$dest.bak.$TS"
    cp "$src" "$dest"
    UPDATED+=("$rel  (previous version backed up to $rel.bak.$TS)")
  else
    cp "$src" "$dest"
    CREATED+=("$rel")
  fi
}

install_file "$DIANA_SRC/skills/plan-review.md"    "$CLAUDE_DIR/skills/plan-review/SKILL.md"
install_file "$DIANA_SRC/skills/research-first.md" "$CLAUDE_DIR/skills/research-first/SKILL.md"
install_file "$DIANA_SRC/skills/minimal-solution.md" "$CLAUDE_DIR/skills/minimal-solution/SKILL.md"
install_file "$DIANA_SRC/commands/fix.md"          "$CLAUDE_DIR/commands/fix.md"
install_file "$DIANA_SRC/commands/review.md"       "$CLAUDE_DIR/commands/review.md"
install_file "$DIANA_SRC/commands/ship.md"         "$CLAUDE_DIR/commands/ship.md"
install_file "$DIANA_SRC/hooks/cost-report.md"     "$CLAUDE_DIR/commands/cost-report.md"
install_file "$DIANA_SRC/hooks/check-careful.sh"   "$CLAUDE_DIR/hooks/check-careful.sh"
install_file "$DIANA_SRC/commands/orchestrate.md"  "$CLAUDE_DIR/commands/orchestrate.md"
install_file "$DIANA_SRC/commands/loop-audit.md"   "$CLAUDE_DIR/commands/loop-audit.md"
install_file "$DIANA_SRC/skills/loop-design.md"    "$CLAUDE_DIR/skills/loop-design/SKILL.md"
install_file "$DIANA_SRC/skills/project-loop.md"   "$CLAUDE_DIR/skills/project-loop/SKILL.md"
install_file "$DIANA_SRC/templates/LOOP.md"        "$CLAUDE_DIR/templates/diana/LOOP.md"
install_file "$DIANA_SRC/templates/STATE.md"       "$CLAUDE_DIR/templates/diana/STATE.md"
install_file "$DIANA_SRC/templates/RUN_LOG.md"     "$CLAUDE_DIR/templates/diana/RUN_LOG.md"
install_file "$DIANA_SRC/templates/BUDGET.md"      "$CLAUDE_DIR/templates/diana/BUDGET.md"

chmod +x "$CLAUDE_DIR/hooks/check-careful.sh"

# --- 3. merge hooks into .claude/settings.local.json ---
SETTINGS="$CLAUDE_DIR/settings.local.json"
DIANA_HOOKS_JSON="$DIANA_SRC/hooks/hooks.json"

if ! command -v python3 >/dev/null 2>&1; then
  echo "warning: python3 not found — cannot safely merge hook settings." >&2
  echo "Manually add the \"hooks\" key from $DIANA_HOOKS_JSON into $SETTINGS." >&2
else
  MERGE_RESULT="$(python3 - "$SETTINGS" "$DIANA_HOOKS_JSON" "$TS" install <<'PYEOF'
import json, sys, os, copy

settings_path, diana_hooks_path, ts, mode = sys.argv[1:5]

with open(diana_hooks_path) as f:
    diana_hooks = json.load(f).get("hooks", {})

def is_diana_entry(event, entry):
    blob = json.dumps(entry)
    if event == "PreToolUse":
        return "check-careful.sh" in blob
    if event == "Stop":
        return "costs.jsonl" in blob
    return False

existed_before = os.path.exists(settings_path)
raw_before = ""
if existed_before:
    with open(settings_path) as f:
        raw_before = f.read()
    try:
        existing = json.loads(raw_before) if raw_before.strip() else {}
    except Exception:
        print("invalid-json")
        sys.exit(0)
else:
    existing = {}

original = copy.deepcopy(existing)

existing.setdefault("hooks", {})
for event, entries in diana_hooks.items():
    lst = existing["hooks"].setdefault(event, [])
    lst[:] = [e for e in lst if not is_diana_entry(event, e)]
    lst.extend(copy.deepcopy(entries))

if existing == original:
    print("unchanged")
    sys.exit(0)

new_content = json.dumps(existing, indent=2) + "\n"

if existed_before:
    with open(settings_path + ".bak." + ts, "w") as f:
        f.write(raw_before)
with open(settings_path, "w") as f:
    f.write(new_content)

print("created" if not existed_before else "updated")
PYEOF
)"
  case "$MERGE_RESULT" in
    created)   CREATED+=(".claude/settings.local.json") ;;
    updated)   UPDATED+=(".claude/settings.local.json  (previous version backed up to .claude/settings.local.json.bak.$TS)") ;;
    unchanged) UNCHANGED+=(".claude/settings.local.json") ;;
    invalid-json)
      echo "warning: $SETTINGS is not valid JSON — left untouched. Merge the hooks from $DIANA_HOOKS_JSON manually." >&2
      ;;
  esac
fi

# --- 4. Diana section in root CLAUDE.md ---
CLAUDE_MD="$TARGET/CLAUDE.md"
DIANA_CLAUDE_MD="$DIANA_SRC/CLAUDE.md"

MD_RESULT="$(python3 - "$CLAUDE_MD" "$DIANA_CLAUDE_MD" "$TS" install <<'PYEOF'
import sys, os

target_md, diana_md, ts, mode = sys.argv[1:5]

with open(diana_md) as f:
    diana_content = f.read().rstrip("\n")

BEGIN = "<!-- DIANA:BEGIN (managed by diana/install.sh — do not hand-edit between markers) -->"
END = "<!-- DIANA:END -->"
block = BEGIN + "\n\n" + diana_content + "\n\n" + END

if not os.path.exists(target_md):
    with open(target_md, "w") as f:
        f.write(block + "\n")
    print("created")
    sys.exit(0)

with open(target_md) as f:
    raw = f.read()

if BEGIN in raw and END in raw:
    pre = raw.split(BEGIN)[0]
    post = raw.split(END, 1)[1]
    new_raw = pre + block + post
    if new_raw == raw:
        print("unchanged")
        sys.exit(0)
    with open(target_md + ".bak." + ts, "w") as f:
        f.write(raw)
    with open(target_md, "w") as f:
        f.write(new_raw)
    print("updated")
else:
    with open(target_md + ".bak." + ts, "w") as f:
        f.write(raw)
    sep = "" if raw.endswith("\n\n") else ("\n\n" if raw.endswith("\n") else "\n\n")
    with open(target_md, "w") as f:
        f.write(raw + sep + block + "\n")
    print("appended")
PYEOF
)"

case "$MD_RESULT" in
  created)   CREATED+=("CLAUDE.md") ;;
  appended)  UPDATED+=("CLAUDE.md  (Diana section appended; previous version backed up to CLAUDE.md.bak.$TS)") ;;
  updated)   UPDATED+=("CLAUDE.md  (Diana section refreshed; previous version backed up to CLAUDE.md.bak.$TS)") ;;
  unchanged) UNCHANGED+=("CLAUDE.md") ;;
esac

# --- 5. report ---
echo "Diana install → $TARGET"
echo
if [ "${#CREATED[@]}" -gt 0 ]; then
  echo "created:"
  printf '  %s\n' "${CREATED[@]}"
fi
if [ "${#UPDATED[@]}" -gt 0 ]; then
  echo "updated:"
  printf '  %s\n' "${UPDATED[@]}"
fi
if [ "${#UNCHANGED[@]}" -gt 0 ]; then
  echo "unchanged:"
  printf '  %s\n' "${UNCHANGED[@]}"
fi
echo
echo "Run verify.sh $TARGET to confirm the install."
