#!/usr/bin/env bash
# Diana uninstaller.
#
# Removes exactly what install.sh added: the Diana-managed skill/command/hook
# files, the Diana hook entries inside settings.local.json, and Diana-managed
# sections inside root AGENTS.md and CLAUDE.md.
#
# Usage:
#   ./uninstall.sh [target-project-path]
#
# Safe by design:
#   - Only removes files that are Diana's own (never touches other files
#     the user has in .claude/).
#   - Never deletes .claude/, .claude/commands/, .claude/skills/, or
#     .claude/hooks/ themselves — only empty Diana-only subdirectories
#     (skills/plan-review/, skills/research-first/, skills/minimal-solution/,
#     skills/loop-design/, templates/diana/) it created.
#   - Edits to shared files (settings.local.json, AGENTS.md, CLAUDE.md) are
#     backed up before modification because they may contain unrelated content.
#   - Never touches root-level LOOP.md/STATE.md/RUN_LOG.md/BUDGET.md — those
#     are live project state, not Diana-installed files. If found, they're
#     reported as preserved, not removed.
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
CLAUDE_DIR="$TARGET/.claude"

REMOVED=()
UPDATED=()
SKIPPED=()

# --- 1. plain Diana files ---
remove_file() {
  local dest="$1"
  local rel="${dest#"$TARGET"/}"
  if [ -f "$dest" ]; then
    rm -f "$dest"
    REMOVED+=("$rel")
  else
    SKIPPED+=("$rel  (not present)")
  fi
}

remove_file "$CLAUDE_DIR/skills/plan-review/SKILL.md"
remove_file "$CLAUDE_DIR/skills/research-first/SKILL.md"
remove_file "$CLAUDE_DIR/skills/minimal-solution/SKILL.md"
remove_file "$CLAUDE_DIR/commands/fix.md"
remove_file "$CLAUDE_DIR/commands/review.md"
remove_file "$CLAUDE_DIR/commands/ship.md"
remove_file "$CLAUDE_DIR/commands/cost-report.md"
remove_file "$CLAUDE_DIR/hooks/check-careful.sh"
remove_file "$CLAUDE_DIR/commands/orchestrate.md"
remove_file "$CLAUDE_DIR/commands/loop-audit.md"
remove_file "$CLAUDE_DIR/skills/loop-design/SKILL.md"
remove_file "$CLAUDE_DIR/templates/diana/LOOP.md"
remove_file "$CLAUDE_DIR/templates/diana/STATE.md"
remove_file "$CLAUDE_DIR/templates/diana/RUN_LOG.md"
remove_file "$CLAUDE_DIR/templates/diana/BUDGET.md"

# remove Diana-only subdirectories, but only if now empty (never force)
for d in "$CLAUDE_DIR/skills/plan-review" "$CLAUDE_DIR/skills/research-first" "$CLAUDE_DIR/skills/minimal-solution" "$CLAUDE_DIR/skills/loop-design" "$CLAUDE_DIR/templates/diana"; do
  if [ -d "$d" ] && rmdir "$d" 2>/dev/null; then
    REMOVED+=("${d#"$TARGET"/}/  (empty dir removed)")
  fi
done

# --- 2. strip Diana hook entries from settings.local.json ---
SETTINGS="$CLAUDE_DIR/settings.local.json"

if [ -f "$SETTINGS" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "warning: python3 not found — cannot safely strip Diana hooks from $SETTINGS." >&2
    echo "Remove any hook entries referencing check-careful.sh / costs.jsonl manually." >&2
  else
    RESULT="$(python3 - "$SETTINGS" "$TS" <<'PYEOF'
import json, sys, os, copy

settings_path, ts = sys.argv[1:3]

def is_diana_entry(event, entry):
    blob = json.dumps(entry)
    if event == "PreToolUse":
        return "check-careful.sh" in blob
    if event == "Stop":
        return "costs.jsonl" in blob
    return False

with open(settings_path) as f:
    raw_before = f.read()
try:
    existing = json.loads(raw_before) if raw_before.strip() else {}
except Exception:
    print("invalid-json")
    sys.exit(0)

original = copy.deepcopy(existing)

hooks = existing.get("hooks", {})
for event in list(hooks.keys()):
    lst = hooks[event]
    lst[:] = [e for e in lst if not is_diana_entry(event, e)]
    if not lst:
        del hooks[event]
if not hooks and "hooks" in existing:
    del existing["hooks"]

if existing == original:
    print("unchanged")
    sys.exit(0)

with open(settings_path + ".bak." + ts, "w") as f:
    f.write(raw_before)
with open(settings_path, "w") as f:
    f.write(json.dumps(existing, indent=2) + "\n")

print("updated")
PYEOF
)"
    case "$RESULT" in
      updated)   UPDATED+=(".claude/settings.local.json  (Diana hooks removed; previous version backed up to .claude/settings.local.json.bak.$TS)") ;;
      unchanged) SKIPPED+=(".claude/settings.local.json  (no Diana hooks present)") ;;
      invalid-json)
        echo "warning: $SETTINGS is not valid JSON — left untouched." >&2
        ;;
    esac
  fi
else
  SKIPPED+=(".claude/settings.local.json  (not present)")
fi

# --- 3. strip Diana policy section from AGENTS.md ---
AGENTS_MD="$TARGET/AGENTS.md"

if [ -f "$AGENTS_MD" ]; then
  AGENTS_RESULT="$(python3 - "$AGENTS_MD" "$TS" <<'PYEOF'
import sys, os

target_md, ts = sys.argv[1:3]
BEGIN = "<!-- DIANA-POLICY:BEGIN (managed by diana/install.sh — do not hand-edit between markers) -->"
END = "<!-- DIANA-POLICY:END -->"

with open(target_md) as f:
    raw = f.read()
if BEGIN not in raw or END not in raw:
    print("absent")
    sys.exit(0)

pre = raw.split(BEGIN)[0]
post = raw.split(END, 1)[1]
new_raw = pre.rstrip("\n")
if post.strip():
    new_raw += "\n\n" + post.lstrip("\n")
else:
    new_raw += "\n" if new_raw else ""

with open(target_md + ".bak." + ts, "w") as f:
    f.write(raw)
if new_raw.strip() == "":
    os.remove(target_md)
    print("removed-file")
else:
    with open(target_md, "w") as f:
        f.write(new_raw)
    print("updated")
PYEOF
)"
  case "$AGENTS_RESULT" in
    updated)       UPDATED+=("AGENTS.md  (Diana policy removed; previous version backed up to AGENTS.md.bak.$TS)") ;;
    removed-file)  REMOVED+=("AGENTS.md  (only contained Diana policy; previous version backed up to AGENTS.md.bak.$TS)") ;;
    absent)        SKIPPED+=("AGENTS.md  (no Diana policy section present)") ;;
  esac
else
  SKIPPED+=("AGENTS.md  (not present)")
fi

# --- 4. strip Diana section from CLAUDE.md ---
CLAUDE_MD="$TARGET/CLAUDE.md"

if [ -f "$CLAUDE_MD" ]; then
  MD_RESULT="$(python3 - "$CLAUDE_MD" "$TS" <<'PYEOF'
import sys, os

target_md, ts = sys.argv[1:3]
BEGIN = "<!-- DIANA:BEGIN (managed by diana/install.sh — do not hand-edit between markers) -->"
END = "<!-- DIANA:END -->"

with open(target_md) as f:
    raw = f.read()

if BEGIN not in raw or END not in raw:
    print("absent")
    sys.exit(0)

pre = raw.split(BEGIN)[0]
post = raw.split(END, 1)[1]
new_raw = pre.rstrip("\n")
if post.strip():
    new_raw += "\n\n" + post.lstrip("\n")
else:
    new_raw += "\n" if new_raw else ""

with open(target_md + ".bak." + ts, "w") as f:
    f.write(raw)

if new_raw.strip() == "":
    os.remove(target_md)
    print("removed-file")
else:
    with open(target_md, "w") as f:
        f.write(new_raw)
    print("updated")
PYEOF
)"
  case "$MD_RESULT" in
    updated)       UPDATED+=("CLAUDE.md  (Diana section removed; previous version backed up to CLAUDE.md.bak.$TS)") ;;
    removed-file)  REMOVED+=("CLAUDE.md  (only contained the Diana section; previous version backed up to CLAUDE.md.bak.$TS)") ;;
    absent)        SKIPPED+=("CLAUDE.md  (no Diana section present)") ;;
  esac
else
  SKIPPED+=("CLAUDE.md  (not present)")
fi

# --- 5. root loop files are live project state — never touched, just noted ---
ROOT_LOOP_FILES=()
for f in LOOP.md STATE.md RUN_LOG.md BUDGET.md; do
  if [ -f "$TARGET/$f" ]; then
    ROOT_LOOP_FILES+=("$f")
  fi
done

# --- 6. report ---
echo "Diana uninstall → $TARGET"
echo
if [ "${#REMOVED[@]}" -gt 0 ]; then
  echo "removed:"
  printf '  %s\n' "${REMOVED[@]}"
fi
if [ "${#UPDATED[@]}" -gt 0 ]; then
  echo "updated:"
  printf '  %s\n' "${UPDATED[@]}"
fi
if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo "skipped:"
  printf '  %s\n' "${SKIPPED[@]}"
fi
if [ "${#ROOT_LOOP_FILES[@]}" -gt 0 ]; then
  echo
  echo "warning: found root loop file(s), left in place (may contain project state):"
  printf '  %s\n' "${ROOT_LOOP_FILES[@]}"
fi
echo
echo "Note: .claude/, .claude/commands/, .claude/skills/, and .claude/hooks/ are left in place"
echo "(only Diana's own files/entries were removed)."
