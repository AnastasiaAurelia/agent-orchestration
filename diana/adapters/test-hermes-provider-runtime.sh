#!/usr/bin/env bash
# Diana live-provider resolution regression.
#
# This is deterministic: it does not call a model. It proves Diana delegates
# provider/auth/runtime selection to Hermes's canonical runtime resolver and
# correctly forwards an external-process launch tuple to AIAgent without
# inventing a static API key or provider-specific OAuth exception.
set -euo pipefail

AD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$AD_DIR/.." && pwd)"

python3 - "$AD_DIR" "$DIANA_DIR" <<'PY'
import sys, types
from pathlib import Path

ad_dir, diana_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, ad_dir)
sys.path.insert(0, str(Path(diana_dir) / "runtime"))

import blocking
import hermes_live as HL

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {label}")
    else:
        failed += 1
        print(f"FAIL  {label} {extra}")

# Install a fake hermes_cli package into sys.modules. provider_config() must use
# these two Hermes-owned APIs; the fixture intentionally provides NO env API key.
pkg = types.ModuleType("hermes_cli")
pkg.__path__ = []
cfg_mod = types.ModuleType("hermes_cli.config")
rp_mod = types.ModuleType("hermes_cli.runtime_provider")

provider_name = "claude-subscription-directsdk-experimental"
model_name = "claude-opus-test"
calls = []

def load_config():
    return {"model": {"provider": provider_name, "default": model_name}}

def resolve_runtime_provider(*, requested=None, explicit_api_key=None,
                             explicit_base_url=None, target_model=None):
    calls.append({
        "requested": requested,
        "explicit_api_key": explicit_api_key,
        "explicit_base_url": explicit_base_url,
        "target_model": target_model,
    })
    return {
        "provider": provider_name,
        "requested_provider": provider_name,
        # Hermes external-process providers intentionally return a non-secret
        # placeholder while the subprocess owns real authentication.
        "api_key": "claude-subscription-directsdk-experimental",
        "base_url": "acp://claude-subscription-directsdk-experimental",
        "api_mode": "chat_completions",
        "command": "/usr/bin/claude",
        "args": ["--acp", "--stdio"],
        "source": "process",
    }

cfg_mod.load_config = load_config
rp_mod.resolve_runtime_provider = resolve_runtime_provider
sys.modules["hermes_cli"] = pkg
sys.modules["hermes_cli.config"] = cfg_mod
sys.modules["hermes_cli.runtime_provider"] = rp_mod

cfg = HL.provider_config("/tmp/fake-certified-hermes")
check("canonical Hermes runtime resolver was called exactly once", len(calls) == 1)
check("configured provider is passed to Hermes unchanged",
      calls[0]["requested"] == provider_name)
check("configured model is passed as target_model",
      calls[0]["target_model"] == model_name)
check("Diana does not inject an explicit API key into resolver",
      calls[0]["explicit_api_key"] is None)
check("external-process provider is accepted without env API key",
      cfg["provider"] == provider_name and cfg["runtime_source"] == "process")
check("external-process command is preserved",
      cfg["command"] == "/usr/bin/claude")
check("external-process args are preserved",
      cfg["args"] == ["--acp", "--stdio"])
check("resolved api_mode is preserved",
      cfg["api_mode"] == "chat_completions")

# Reproduce the real Builder failure class deterministically: Hermes's in-process
# merged-config cache yields an empty model block, while a fresh interpreter sees
# the correct non-secret model selector. Diana must recover through ONLY that
# fresh selector and still use Hermes's canonical runtime resolver for auth.
fresh_calls = []
def empty_load_config():
    return {}
cfg_mod.load_config = empty_load_config
def fake_fresh(home):
    fresh_calls.append(str(home))
    return {"provider": provider_name, "model": model_name, "base_url": ""}
HL._fresh_model_config = fake_fresh
cfg_from_corrupt_cache = HL.provider_config("/tmp/fake-certified-hermes")
check("corrupted in-process model cache triggers fresh selector fallback",
      fresh_calls == ["/tmp/fake-certified-hermes"])
check("fresh selector fallback still resolves through Hermes runtime provider",
      cfg_from_corrupt_cache["provider"] == provider_name)
cfg_mod.load_config = load_config

# Prove the launch tuple reaches AIAgent, not just provider_config().
captured = {}
run_agent = types.ModuleType("run_agent")
class FakeAgent:
    def __init__(self, **kwargs):
        captured.update(kwargs)
        self.tools = []
run_agent.AIAgent = FakeAgent
sys.modules["run_agent"] = run_agent

contract = {"target": {"repo_root": "/tmp/target"}}
agent, built_cfg, shown = HL.build_agent(
    contract, hermes_home="/tmp/fake-certified-hermes", narrow=False
)
check("AIAgent receives resolved provider", captured.get("provider") == provider_name)
check("AIAgent receives requested_provider",
      captured.get("requested_provider") == provider_name)
check("AIAgent receives resolved api_mode",
      captured.get("api_mode") == "chat_completions")
check("AIAgent receives external-process command",
      captured.get("command") == "/usr/bin/claude")
check("AIAgent receives external-process args",
      captured.get("args") == ["--acp", "--stdio"])
check("AIAgent receives Hermes placeholder rather than a Diana-fabricated secret",
      captured.get("api_key") == "claude-subscription-directsdk-experimental")

# Fail closed if Hermes's own resolver refuses. A long token-shaped value in the
# exception must be redacted from Diana's refusal detail.
secret = "sk-test-" + ("A" * 64)
def refusing_resolver(**kwargs):
    raise RuntimeError("provider bootstrap failed " + secret)
rp_mod.resolve_runtime_provider = refusing_resolver
try:
    HL.provider_config("/tmp/fake-certified-hermes")
except blocking.Blocked as exc:
    check("Hermes resolver failure blocks the turn",
          exc.code == blocking.HERMES_PROVIDER_UNAVAILABLE, f"(got {exc.code})")
    check("resolver failure detail is redacted", secret not in str(exc))
else:
    check("Hermes resolver failure blocks the turn", False, "(no Blocked raised)")
    check("resolver failure detail is redacted", False, "(no refusal)")

# A malformed/incomplete runtime may never become an executable turn.
def incomplete_resolver(**kwargs):
    return {
        "provider": provider_name,
        "requested_provider": provider_name,
        "api_key": "",
        "base_url": "acp://broken",
        "api_mode": "chat_completions",
        "command": "/usr/bin/claude",
        "args": [],
        "source": "process",
    }
rp_mod.resolve_runtime_provider = incomplete_resolver
try:
    HL.provider_config("/tmp/fake-certified-hermes")
except blocking.Blocked as exc:
    check("incomplete Hermes runtime blocks",
          exc.code == blocking.HERMES_PROVIDER_UNAVAILABLE, f"(got {exc.code})")
else:
    check("incomplete Hermes runtime blocks", False, "(no Blocked raised)")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
PY
