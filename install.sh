#!/usr/bin/env bash
# Diana installer.
#
# Copies Diana's policy and Claude Code base (skills, commands, hooks,
# managed AGENTS.md and CLAUDE.md sections, and the Playwright MCP server
# entry)
# into a target project's .claude/ discovery paths and root .mcp.json.
#
# Usage:
#   ./install.sh [target-project-path]
#
# Safe by design:
#   - Never overwrites an existing file without backing it up first
#     (suffix: .bak.<timestamp>).
#   - Only touches Diana-managed entries inside settings.local.json and the
#     AGENTS.md/CLAUDE.md Diana sections — unrelated content is preserved.
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
install_file "$DIANA_SRC/templates/LOOP.md"        "$CLAUDE_DIR/templates/diana/LOOP.md"
install_file "$DIANA_SRC/templates/STATE.md"       "$CLAUDE_DIR/templates/diana/STATE.md"
install_file "$DIANA_SRC/templates/RUN_LOG.md"     "$CLAUDE_DIR/templates/diana/RUN_LOG.md"
install_file "$DIANA_SRC/templates/BUDGET.md"      "$CLAUDE_DIR/templates/diana/BUDGET.md"

chmod +x "$CLAUDE_DIR/hooks/check-careful.sh"

# --- 3. merge portable and local hook settings ---
SHARED_SETTINGS="$CLAUDE_DIR/settings.json"
LOCAL_SETTINGS="$CLAUDE_DIR/settings.local.json"
DIANA_SHARED_SETTINGS="$SCRIPT_DIR/.claude/settings.json"
DIANA_LOCAL_HOOKS="$DIANA_SRC/hooks/hooks.json"

merge_hook_settings() {
  local settings_path="$1" diana_hooks_path="$2"
  python3 - "$settings_path" "$diana_hooks_path" "$TS" <<'PYEOF'
import json, sys, os, copy

settings_path, diana_hooks_path, ts = sys.argv[1:4]

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
# Remove prior Diana registrations from either scope so upgrades migrate the
# portable safety hook out of local settings without disturbing other hooks.
for event in list(existing["hooks"]):
    existing["hooks"][event] = [
        entry for entry in existing["hooks"][event]
        if not is_diana_entry(event, entry)
    ]
    if not existing["hooks"][event]:
        del existing["hooks"][event]

for event, entries in diana_hooks.items():
    lst = existing["hooks"].setdefault(event, [])
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
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "warning: python3 not found — cannot safely merge hook settings." >&2
  echo "Manually merge $DIANA_SHARED_SETTINGS and $DIANA_LOCAL_HOOKS." >&2
else
  SHARED_RESULT="$(merge_hook_settings "$SHARED_SETTINGS" "$DIANA_SHARED_SETTINGS")"
  case "$SHARED_RESULT" in
    created)   CREATED+=(".claude/settings.json") ;;
    updated)   UPDATED+=(".claude/settings.json  (previous version backed up to .claude/settings.json.bak.$TS)") ;;
    unchanged) UNCHANGED+=(".claude/settings.json") ;;
    invalid-json) echo "warning: $SHARED_SETTINGS is invalid JSON — left untouched." >&2 ;;
  esac

  LOCAL_RESULT="$(merge_hook_settings "$LOCAL_SETTINGS" "$DIANA_LOCAL_HOOKS")"
  case "$LOCAL_RESULT" in
    created)   CREATED+=(".claude/settings.local.json") ;;
    updated)   UPDATED+=(".claude/settings.local.json  (previous version backed up to .claude/settings.local.json.bak.$TS)") ;;
    unchanged) UNCHANGED+=(".claude/settings.local.json") ;;
    invalid-json) echo "warning: $LOCAL_SETTINGS is invalid JSON — left untouched." >&2 ;;
  esac
fi

# --- 3b. merge Playwright MCP config into target .mcp.json ---
MCP_JSON="$TARGET/.mcp.json"
DIANA_MCP_FRAGMENT="$DIANA_SRC/mcp/playwright.json"

merge_mcp_settings() {
  python3 - "$MCP_JSON" "$DIANA_MCP_FRAGMENT" "$TS" <<'PYEOF'
import json, sys, os

mcp_path, fragment_path, ts = sys.argv[1:4]

with open(fragment_path) as f:
    diana_entry = json.load(f)["mcpServers"]["playwright"]

def is_diana_entry(entry):
    return "@playwright/mcp" in json.dumps(entry)

existed_before = os.path.exists(mcp_path)
raw_before = ""
if existed_before:
    with open(mcp_path) as f:
        raw_before = f.read()
    try:
        existing = json.loads(raw_before) if raw_before.strip() else {}
    except Exception:
        print("invalid-json")
        sys.exit(0)
else:
    existing = {}

servers = existing.setdefault("mcpServers", {})
current = servers.get("playwright")

# A pre-existing "playwright" key that doesn't carry Diana's @playwright/mcp
# signature belongs to the user (or another tool) — never overwrite it.
if current is not None and not is_diana_entry(current):
    print("foreign")
    sys.exit(0)

if current == diana_entry:
    print("unchanged")
    sys.exit(0)

servers["playwright"] = diana_entry
new_content = json.dumps(existing, indent=2) + "\n"

if existed_before:
    with open(mcp_path + ".bak." + ts, "w") as f:
        f.write(raw_before)
with open(mcp_path, "w") as f:
    f.write(new_content)

print("created" if not existed_before else "updated")
PYEOF
}

if command -v python3 >/dev/null 2>&1; then
  MCP_RESULT="$(merge_mcp_settings)"
  case "$MCP_RESULT" in
    created)   CREATED+=(".mcp.json") ;;
    updated)   UPDATED+=(".mcp.json  (playwright MCP entry refreshed; previous version backed up to .mcp.json.bak.$TS)") ;;
    unchanged) UNCHANGED+=(".mcp.json") ;;
    foreign)   echo "warning: $MCP_JSON already has a non-Diana 'playwright' MCP server entry — left untouched." >&2 ;;
    invalid-json) echo "warning: $MCP_JSON is invalid JSON — left untouched." >&2 ;;
  esac
fi

# --- 4. provider-neutral Diana section in root AGENTS.md ---
AGENTS_MD="$TARGET/AGENTS.md"
DIANA_AGENTS_MD="$SCRIPT_DIR/AGENTS.md"

AGENTS_RESULT="$(python3 - "$AGENTS_MD" "$DIANA_AGENTS_MD" "$TS" <<'PYEOF'
import sys, os

target_md, diana_md, ts = sys.argv[1:4]
with open(diana_md) as f:
    diana_content = f.read().rstrip("\n")

BEGIN = "<!-- DIANA-POLICY:BEGIN (managed by diana/install.sh — do not hand-edit between markers) -->"
END = "<!-- DIANA-POLICY:END -->"
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
else:
    sep = "" if raw.endswith("\n\n") else ("\n" if raw.endswith("\n") else "\n\n")
    new_raw = raw + sep + block + "\n"

with open(target_md + ".bak." + ts, "w") as f:
    f.write(raw)
with open(target_md, "w") as f:
    f.write(new_raw)
print("updated" if BEGIN in raw else "appended")
PYEOF
)"

case "$AGENTS_RESULT" in
  created)   CREATED+=("AGENTS.md") ;;
  appended)  UPDATED+=("AGENTS.md  (Diana policy appended; previous version backed up to AGENTS.md.bak.$TS)") ;;
  updated)   UPDATED+=("AGENTS.md  (Diana policy refreshed; previous version backed up to AGENTS.md.bak.$TS)") ;;
  unchanged) UNCHANGED+=("AGENTS.md") ;;
esac

# --- 5. Claude-specific Diana section in root CLAUDE.md ---
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

# --- 6. report ---
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
