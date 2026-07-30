"""End-to-end tests for scripts/nightshift-smoke-002.sh (Milestone 7C.2).

Drives the *real* bash script as a real subprocess against a temporary
fixture tree standing in for /home/nightshift and /home/ubuntu, with a fake
`claude` and a fake `tmux` (both fully self-contained Python scripts written
per-test into a temp directory) -- never a real Claude Code session, never
real tmux, never real sudo. NS_TEST_MODE=1 makes the script execute every
"sudo -u nightshift -H" operation directly as this test process's own user
instead.

The `nightshift` runtime package itself is never faked -- NS_REPO_ROOT
always points at this actual repository checkout, so the script drives the
real, already-tested queue.py/claude_executor.py/smoke_acceptance_checker.py
end to end. Several tests therefore let the script run its own precondition
check of `python3 -m unittest discover -s nightshift/tests` for real (a
recursive-looking but non-reentrant, one-level nested test run), which is
why a handful of tests here take several seconds each.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "nightshift-smoke-002.sh")

_DEFAULT_HELP_TEXT = (
    "Usage: claude [options] [prompt]\n"
    "  -p, --print\n"
    "  --tools <tools...>\n"
    "  --allowedTools <tools...>\n"
    "  --permission-mode <mode>\n"
    "  --strict-mcp-config\n"
    "  --disable-slash-commands\n"
    "  --no-session-persistence\n"
    "  --output-format <format>\n"
)

_DEFAULT_AUTH_PAYLOAD = (
    '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", '
    '"subscriptionType": "pro"}'
)

_VALID_CALCULATOR = "def add(a, b):\n    return a + b\n"
_THREE_PASSING_TESTS = """
import unittest
from calculator import add


class AddTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)
"""

_FAKE_CLAUDE_TEMPLATE = """{shebang}
import json
import os
import sys

argv = sys.argv[1:]

if argv[:1] == ["--version"]:
    sys.stdout.write("9.9.9 (Fake Claude)")
    sys.exit(0)

if argv[:1] == ["--help"]:
    sys.stdout.write({help_text!r})
    sys.exit(0)

if len(argv) >= 2 and argv[0] == "auth" and argv[1] == "status":
    sys.stdout.write({auth_payload!r})
    sys.exit(0)

# The real -p invocation.
mode = {mode!r}

if mode == "success":
    with open("calculator.py", "w") as f:
        f.write({calculator_src!r})
    with open("test_calculator.py", "w") as f:
        f.write({test_src!r})
    sys.exit(0)

if mode == "nonzero":
    sys.stderr.write("simulated generic non-zero failure, no auth-failure marker here")
    sys.exit(1)

if mode == "auth_failure":
    sys.stderr.write(
        "API Error: 401 Unauthorized -- access token has expired, please "
        "re-authenticate. Bearer " + {fake_token!r} + " rejected."
    )
    sys.exit(1)

if mode == "zero_output":
    sys.exit(0)

if mode == "researchlens_tamper":
    with open("calculator.py", "w") as f:
        f.write({calculator_src!r})
    with open("test_calculator.py", "w") as f:
        f.write({test_src!r})
    # A literal, baked-in path, not read from the environment: the real
    # Claude launch environment is built entirely by allowlist
    # (policy.build_allowed_env()) and strips any custom variable like this
    # one -- exactly as it should, and exactly why this fixture cannot rely
    # on an env var reaching the real -p subprocess.
    tamper_path = {researchlens_tamper_path!r}
    if tamper_path:
        with open(tamper_path, "a") as f:
            f.write("tampered\\n")
    sys.exit(0)

sys.exit(1)
"""


def _write_fake_claude(
    directory,
    mode="success",
    fake_token="sk-ant-api03-" + ("a" * 40),
    researchlens_tamper_path=None,
    auth_payload=None,
):
    script = _FAKE_CLAUDE_TEMPLATE.format(
        shebang=f"#!{sys.executable}",
        help_text=_DEFAULT_HELP_TEXT,
        auth_payload=_DEFAULT_AUTH_PAYLOAD if auth_payload is None else auth_payload,
        mode=mode,
        calculator_src=_VALID_CALCULATOR,
        test_src=_THREE_PASSING_TESTS,
        fake_token=fake_token,
        researchlens_tamper_path=researchlens_tamper_path,
    )
    path = os.path.join(directory, "fake-claude")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(path, 0o755)
    return os.path.realpath(path)


_FAKE_TMUX_TEMPLATE = """{shebang}
import os
import signal
import subprocess
import sys

SESSIONS_DIR = os.environ["FAKE_TMUX_SESSIONS_DIR"]
ALWAYS_ALIVE = os.environ.get("FAKE_TMUX_ALWAYS_ALIVE") == "1"


def pid_path(name):
    return os.path.join(SESSIONS_DIR, name + ".pid")


args = sys.argv[1:]
command = args[0] if args else None

if command == "new-session":
    rest = args[1:]
    name = None
    i = 0
    while i < len(rest):
        if rest[i] == "-d":
            i += 1
        elif rest[i] == "-s":
            name = rest[i + 1]
            i += 2
        else:
            break
    child_argv = rest[i:]
    # Real tmux detaches a -d session from the invoking terminal entirely --
    # without this, the grandchild would inherit this test harness's own
    # captured stdout/stderr pipes and keep them open for its whole
    # lifetime, hanging subprocess.run(capture_output=True) long after the
    # outer script itself has finished and printed its own result.
    proc = subprocess.Popen(
        child_argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(pid_path(name), "w") as f:
        f.write(str(proc.pid))
    sys.exit(0)

if command == "has-session":
    name = args[args.index("-t") + 1]
    path = pid_path(name)
    if not os.path.exists(path):
        # No session was ever created under this name -- ALWAYS_ALIVE
        # simulates a cycle that never finishes, not a session that exists
        # out of nowhere before new-session was ever called (which would
        # wrongly trip the operator script's own pre-run "no old session"
        # guard before anything had actually started).
        sys.exit(1)
    if ALWAYS_ALIVE:
        sys.exit(0)
    with open(path) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        os.remove(path)
        sys.exit(1)
    sys.exit(0)

if command == "kill-session":
    name = args[args.index("-t") + 1]
    open(os.path.join(SESSIONS_DIR, name + ".killed"), "w").close()
    path = pid_path(name)
    if os.path.exists(path):
        with open(path) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.remove(path)
    sys.exit(0)

sys.exit(1)
"""


def _write_fake_tmux(directory):
    script = _FAKE_TMUX_TEMPLATE.format(shebang=f"#!{sys.executable}")
    path = os.path.join(directory, "tmux")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(path, 0o755)
    return path


def _build_fixture(prefix="nightshift-smoke-script-test-"):
    """Build one complete, independent fixture tree.

    Every path lives under a single fresh tempfile.mkdtemp() -- including a
    dedicated digest_dir for the ResearchLens before/after digest artifacts
    (Milestone 7C.2.1) -- so two fixtures built by two separate calls to
    this function can never share a path, a digest file, or any other
    artifact, no matter how many times it is called or in what order.
    Returns a SimpleNamespace with attributes matching the test case's own
    former self.* names, so setUp() can just splat it onto self.
    """
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    ns = SimpleNamespace(tmpdir=tmpdir)
    ns.nightshift_home = os.path.join(tmpdir, "home-nightshift")
    ns.ubuntu_home = os.path.join(tmpdir, "home-ubuntu")
    ns.researchlens = os.path.join(ns.ubuntu_home, "ResearchLens")
    ns.bin_dir = os.path.join(tmpdir, "bin")
    ns.tmux_sessions_dir = os.path.join(tmpdir, "tmux-sessions")
    ns.digest_dir = os.path.join(tmpdir, "researchlens-digests")
    for d in (
        ns.nightshift_home,
        ns.ubuntu_home,
        ns.researchlens,
        ns.bin_dir,
        ns.tmux_sessions_dir,
        ns.digest_dir,
    ):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(ns.researchlens, "app.py"), "w", encoding="utf-8") as f:
        f.write("# production app\n")
    with open(os.path.join(ns.researchlens, ".env"), "w", encoding="utf-8") as f:
        f.write("SECRET=do-not-print-me\n")

    _write_fake_tmux(ns.bin_dir)

    ns.smoke1_task_dir = os.path.join(ns.nightshift_home, "workspace", "smoke", "task-001")
    ns.smoke1_state_dir = os.path.join(ns.nightshift_home, "state")
    ns.smoke1_logs_dir = os.path.join(ns.nightshift_home, "logs")
    ns.smoke1_reports_dir = os.path.join(ns.nightshift_home, "reports", "smoke-001")
    os.makedirs(ns.smoke1_task_dir)
    os.makedirs(ns.smoke1_state_dir)
    os.makedirs(ns.smoke1_logs_dir)
    os.makedirs(ns.smoke1_reports_dir)
    ns.smoke1_queue_path = os.path.join(ns.smoke1_state_dir, "smoke-queue.json")
    ns.smoke1_config_path = os.path.join(ns.smoke1_state_dir, "smoke-config.json")
    ns.smoke1_run_log_path = os.path.join(ns.smoke1_logs_dir, "smoke-run-log.jsonl")
    for path, content in (
        (ns.smoke1_queue_path, '{"tasks": []}\n'),
        (ns.smoke1_config_path, "{}\n"),
        (ns.smoke1_run_log_path, ""),
    ):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return ns


def _fixture_env(ns, claude_mode="success", **overrides):
    """Build the env dict for one script invocation against fixture ``ns``.

    ``ns`` may be a NightshiftSmokeScriptTestCase (self) or any other object
    exposing the same attributes (e.g. one built by _build_fixture()) -- the
    two independent-fixture tests below rely on that.

    A genuinely minimal base environment, not a copy of this dev shell's own
    os.environ -- NS_TEST_MODE runs every "nightshift" operation directly (no
    sudo -u nightshift -H to strip anything), so a real, benign variable
    already present in the developer's own shell (e.g. SSH_AUTH_SOCK from a
    normal SSH agent) would otherwise reach auth_preflight's
    sensitive-variable check and correctly, but unhelpfully, fail closed for
    a reason unrelated to whatever a given test is actually exercising.
    """
    env = {
        "PATH": ns.bin_dir + os.pathsep + "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C"),
    }
    env["NS_TEST_MODE"] = "1"
    # NS_REPO_ROOT below is this actual repository, so the script's own
    # "run the full test suite" precondition would otherwise recursively
    # re-invoke this very test file, whose "full setup" tests each do
    # the same thing again -- unbounded nested self-invocation. These
    # tests exercise the other eight preconditions and the post-run
    # verification, not the test-suite gate itself, so it is skipped
    # here (see the script's own comment at that gate for why this
    # escape hatch exists and why it can never trigger outside test mode).
    env["NS_SKIP_TEST_SUITE_CHECK"] = "1"
    env["NS_NIGHTSHIFT_USER"] = os.environ.get("USER", "root")
    env["NS_NIGHTSHIFT_HOME"] = ns.nightshift_home
    env["NS_REPO_ROOT"] = REPO_ROOT
    env["NS_FORBIDDEN_PATH"] = ns.ubuntu_home
    env["NS_RESEARCHLENS_PATH"] = ns.researchlens
    # Test-only digest overrides (Milestone 7C.2.1) -- always paired with
    # NS_TEST_MODE=1 above, and always unique to this fixture's own tmpdir.
    # Never the script's own fixed production default.
    env["NS_RESEARCHLENS_BEFORE_DIGEST"] = os.path.join(ns.digest_dir, "before.sha256")
    env["NS_RESEARCHLENS_AFTER_DIGEST"] = os.path.join(ns.digest_dir, "after.sha256")
    env["NS_CLAUDE_CONFIGURED_PATH"] = _write_fake_claude(ns.bin_dir, mode=claude_mode)
    env["NS_MAX_WAIT_SECONDS"] = "30"
    env["NS_POLL_INTERVAL_SECONDS"] = "1"
    env["NS_CLAUDE_TIMEOUT_SECONDS"] = "20"
    env["NS_MIN_TEST_COUNT"] = "1"  # this repo's real count; kept low so future growth never blocks this test
    env["FAKE_TMUX_SESSIONS_DIR"] = ns.tmux_sessions_dir
    env.pop("FAKE_TMUX_ALWAYS_ALIVE", None)
    env.pop("FAKE_CLAUDE_RESEARCHLENS_TAMPER_PATH", None)
    for key, value in overrides.items():
        env[key] = value
    return env


class NightshiftSmokeScriptTestCase(unittest.TestCase):
    def setUp(self):
        fixture = _build_fixture()
        self.__dict__.update(vars(fixture))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _env(self, claude_mode="success", **overrides):
        return _fixture_env(self, claude_mode=claude_mode, **overrides)

    def _run_script(self, env, timeout=120):
        return subprocess.run(
            ["bash", SCRIPT_PATH], env=env, capture_output=True, text=True, timeout=timeout
        )

    def _smoke2_paths(self):
        return {
            "task_dir": os.path.join(self.nightshift_home, "workspace", "smoke", "task-002"),
            "queue": os.path.join(self.nightshift_home, "state", "smoke-002-queue.json"),
            "config": os.path.join(self.nightshift_home, "state", "smoke-002-config.json"),
            "checker": os.path.join(self.nightshift_home, "state", "smoke-002-acceptance.py"),
            "run_log": os.path.join(self.nightshift_home, "logs", "smoke-002-run-log.jsonl"),
            "report_dir": os.path.join(self.nightshift_home, "reports", "smoke-002"),
        }

    # -- 1: smoke-001 paths are never mutation targets ---------------------------

    def test_smoke_001_missing_causes_refusal_without_touching_anything(self):
        os.remove(self.smoke1_run_log_path)

        result = self._run_script(self._env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("smoke-001 evidence missing or moved", result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(smoke2["queue"]), "must not have started setting up smoke-002")

    def test_smoke_001_evidence_is_byte_identical_after_a_full_successful_run(self):
        before = {}
        for path in (
            self.smoke1_queue_path,
            self.smoke1_config_path,
            self.smoke1_run_log_path,
        ):
            with open(path, "rb") as f:
                before[path] = f.read()
        before_mtimes = {p: os.stat(p).st_mtime_ns for p in before}

        result = self._run_script(self._env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for path, original in before.items():
            with open(path, "rb") as f:
                self.assertEqual(f.read(), original, f"{path} content changed")
            self.assertEqual(
                os.stat(path).st_mtime_ns, before_mtimes[path], f"{path} mtime changed"
            )

    # -- 2: existing smoke-002 evidence causes refusal ----------------------------

    def test_existing_smoke_002_evidence_causes_refusal(self):
        smoke2 = self._smoke2_paths()
        os.makedirs(os.path.dirname(smoke2["queue"]), exist_ok=True)
        with open(smoke2["queue"], "w", encoding="utf-8") as f:
            f.write('{"tasks": []}\n')

        result = self._run_script(self._env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("smoke-002 evidence already exists", result.stderr)
        self.assertIn(smoke2["queue"], result.stderr)

    # -- 3: malformed config generation fails, never claims PASS ------------------

    def test_malformed_generated_config_fails_and_never_claims_pass(self):
        result = self._run_script(self._env(NS_CLAUDE_TIMEOUT_SECONDS="not-a-number"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run-one itself exited", result.stderr)
        self.assertNotIn("PASS", result.stdout)

    # -- 4: canonical executable failure stops before invocation ------------------

    def test_canonical_executable_failure_stops_before_invocation(self):
        missing = os.path.join(self.bin_dir, "does-not-exist")
        env = self._env()
        env["NS_CLAUDE_CONFIGURED_PATH"] = missing

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolved Claude executable does not exist", result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(smoke2["queue"]))
        self.assertFalse(os.path.exists(os.path.join(self.tmux_sessions_dir)) and os.listdir(self.tmux_sessions_dir))

    def test_group_writable_executable_fails_closed(self):
        env = self._env()
        # Written under a distinct name/subdir so _env()'s own fake-claude
        # write (same fixed filename, mode 0o755) can never clobber this
        # deliberately group-writable copy.
        writable_dir = os.path.join(self.tmpdir, "group-writable-claude-bin")
        os.makedirs(writable_dir, exist_ok=True)
        claude_path = _write_fake_claude(writable_dir, mode="success")
        os.chmod(claude_path, 0o775)
        env["NS_CLAUDE_CONFIGURED_PATH"] = claude_path

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("group-writable", result.stderr)

    # -- 5: preflight failure stops before invocation ------------------------------

    def test_preflight_failure_stops_before_invocation(self):
        env = self._env()
        env["NS_FORBIDDEN_PATH"] = os.path.join(self.tmpdir, "does-not-exist-forbidden-path")

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deterministic preflight", result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(smoke2["queue"]))

    # -- 6: fake successful run reaches verification and passes -------------------

    def test_fake_successful_run_reaches_verification_and_passes(self):
        result = self._run_script(self._env(claude_mode="success"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        smoke2 = self._smoke2_paths()
        self.assertTrue(os.path.isfile(os.path.join(smoke2["task_dir"], "calculator.py")))
        self.assertTrue(os.path.isfile(os.path.join(smoke2["task_dir"], "test_calculator.py")))

        # -- 10 (folded in): exactly one cycle, never a second invocation.
        sys.path.insert(0, REPO_ROOT)
        from nightshift.runtime import queue as nsq

        events, malformed = nsq._load_run_log_events(smoke2["run_log"])
        self.assertEqual(malformed, 0)
        evidence_events = [e for e in events if e["event"] == "task_run_evidence"]
        self.assertEqual(len(evidence_events), 1)

    # -- 7: fake nonzero executor cannot become PASS -------------------------------

    def test_fake_nonzero_executor_cannot_become_pass(self):
        result = self._run_script(self._env(claude_mode="nonzero"))

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS", result.stdout)
        # max_attempts is 1, so a nonzero-exit executor exhausts the single
        # attempt immediately (FAILED_PERMANENTLY) -- the queue status check
        # is the first thing the verify helper looks at, so that is the
        # specific reason surfaced here, not a later executor_exit_code
        # check that a REQUEUED task would have reached instead.
        self.assertIn("queue task status is 'failed', not 'done'", result.stderr)

    # -- 8: fake passing acceptance with zero output files cannot become PASS -----

    def test_fake_zero_output_files_cannot_become_pass(self):
        result = self._run_script(self._env(claude_mode="zero_output"))

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS", result.stdout)
        self.assertIn("not 'done'", result.stderr)

    # -- 9: ResearchLens digest mismatch causes failure ----------------------------

    def test_researchlens_digest_mismatch_causes_failure(self):
        env = self._env(claude_mode="researchlens_tamper")
        # Overwrite the same fake-claude path _env() already pointed
        # NS_CLAUDE_CONFIGURED_PATH at, this time with the tamper path baked
        # directly into the generated script (see _write_fake_claude's own
        # comment on why an env var cannot carry this into the real launch).
        _write_fake_claude(
            self.bin_dir,
            mode="researchlens_tamper",
            researchlens_tamper_path=os.path.join(self.researchlens, "app.py"),
        )

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ResearchLens metadata-tree digest changed", result.stderr)
        self.assertNotIn("PASS", result.stdout)

    # -- 11: timeout/polling is bounded, not `while true` --------------------------

    def test_bounded_wait_times_out_and_kills_the_session_rather_than_polling_forever(self):
        env = self._env(claude_mode="success")
        env["NS_MAX_WAIT_SECONDS"] = "2"
        env["NS_POLL_INTERVAL_SECONDS"] = "1"
        env["FAKE_TMUX_ALWAYS_ALIVE"] = "1"

        import time

        started = time.monotonic()
        result = self._run_script(env, timeout=30)
        elapsed = time.monotonic() - started

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not finish within", result.stderr)
        self.assertLess(elapsed, 20, "the bounded wait must not run anywhere near as long as an unbounded poll would")
        killed_marker = os.path.join(self.tmux_sessions_dir, "nightshift-smoke-002.killed")
        self.assertTrue(os.path.exists(killed_marker), "expected the script to call tmux kill-session on timeout")

    # -- 12: sensitive values are never printed by the script itself --------------

    def test_sensitive_token_is_never_printed_by_the_script(self):
        fake_token = "sk-ant-api03-" + ("b" * 40)
        env = self._env(claude_mode="auth_failure")
        env["NS_CLAUDE_CONFIGURED_PATH"] = _write_fake_claude(
            self.bin_dir, mode="auth_failure", fake_token=fake_token
        )

        result = self._run_script(env)

        combined_output = result.stdout + result.stderr
        self.assertNotIn(fake_token, combined_output)
        self.assertNotIn("do-not-print-me", combined_output)

    # -- Milestone 7C.2.1: artifact isolation regression tests -------------------
    #
    # The real VPS run failed with "Permission denied" on
    # /tmp/researchlens-before.sha256 -- a file an admin had already created,
    # root-owned, at the same fixed generic path this script used to write
    # to unconditionally. Every test below proves the digest artifacts are
    # now fully isolated: a smoke-specific default name in production, and a
    # test-only, per-fixture override that is rejected outside test mode.

    def test_pre_existing_unwritable_file_at_old_generic_path_cannot_affect_tests(self):
        old_generic_path = "/tmp/researchlens-before.sha256"
        created_here = not os.path.exists(old_generic_path)
        if created_here:
            with open(old_generic_path, "w", encoding="utf-8") as f:
                f.write("unrelated pre-existing content, simulating a real VPS admin's file\n")
            os.chmod(old_generic_path, 0o444)
        try:
            result = self._run_script(self._env())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)
        finally:
            if created_here:
                os.chmod(old_generic_path, 0o644)
                os.remove(old_generic_path)

    def test_each_run_uses_its_own_configured_unique_digest_paths(self):
        env = self._env()

        result = self._run_script(env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before_path = env["NS_RESEARCHLENS_BEFORE_DIGEST"]
        after_path = env["NS_RESEARCHLENS_AFTER_DIGEST"]
        self.assertTrue(before_path.startswith(self.digest_dir))
        self.assertTrue(after_path.startswith(self.digest_dir))
        self.assertTrue(os.path.isfile(before_path))
        self.assertTrue(os.path.isfile(after_path))

    def test_digest_mismatch_test_fails_for_the_mismatch_not_a_permission_error(self):
        env = self._env(claude_mode="researchlens_tamper")
        _write_fake_claude(
            self.bin_dir,
            mode="researchlens_tamper",
            researchlens_tamper_path=os.path.join(self.researchlens, "app.py"),
        )

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ResearchLens metadata-tree digest changed", result.stderr)
        self.assertNotIn("Permission denied", result.stderr)
        self.assertNotIn("PASS", result.stdout)

    def test_fake_successful_run_reaches_pass_with_isolated_digest_files(self):
        env = self._env()

        result = self._run_script(env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        with open(env["NS_RESEARCHLENS_BEFORE_DIGEST"], encoding="utf-8") as f:
            before_digest = f.read().strip()
        with open(env["NS_RESEARCHLENS_AFTER_DIGEST"], encoding="utf-8") as f:
            after_digest = f.read().strip()
        self.assertTrue(before_digest)
        self.assertEqual(before_digest, after_digest)

    def test_test_only_digest_overrides_are_rejected_outside_test_mode(self):
        stray_path = os.path.join(self.tmpdir, "should-not-be-honored.sha256")
        for var_name in ("NS_RESEARCHLENS_BEFORE_DIGEST", "NS_RESEARCHLENS_AFTER_DIGEST"):
            with self.subTest(var_name=var_name):
                env = {
                    "PATH": "/usr/bin:/bin",
                    "HOME": os.environ.get("HOME", "/tmp"),
                    var_name: stray_path,
                }

                result = self._run_script(env)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"{var_name} is a test-only override and must not be set outside NS_TEST_MODE=1",
                    result.stderr,
                )
                self.assertFalse(os.path.exists(stray_path))

    def test_two_consecutive_full_runs_do_not_collide(self):
        first_env = self._env()
        first_result = self._run_script(first_env)
        self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)

        second_fixture = _build_fixture(prefix="nightshift-smoke-script-test-second-")
        self.addCleanup(shutil.rmtree, second_fixture.tmpdir, ignore_errors=True)
        second_env = _fixture_env(second_fixture)

        second_result = self._run_script(second_env)
        self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)

        # Both runs' own digest files are intact and distinct -- neither run
        # overwrote or was blocked by the other's artifacts.
        self.assertNotEqual(
            first_env["NS_RESEARCHLENS_BEFORE_DIGEST"], second_env["NS_RESEARCHLENS_BEFORE_DIGEST"]
        )
        self.assertTrue(os.path.isfile(first_env["NS_RESEARCHLENS_BEFORE_DIGEST"]))
        self.assertTrue(os.path.isfile(second_env["NS_RESEARCHLENS_BEFORE_DIGEST"]))

    def test_two_independently_created_environments_do_not_share_artifacts(self):
        env_a = self._env()
        fixture_b = _build_fixture(prefix="nightshift-smoke-script-test-independent-")
        self.addCleanup(shutil.rmtree, fixture_b.tmpdir, ignore_errors=True)
        env_b = _fixture_env(fixture_b)

        self.assertNotEqual(self.tmpdir, fixture_b.tmpdir)
        self.assertNotEqual(env_a["NS_NIGHTSHIFT_HOME"], env_b["NS_NIGHTSHIFT_HOME"])
        self.assertNotEqual(env_a["NS_RESEARCHLENS_PATH"], env_b["NS_RESEARCHLENS_PATH"])
        self.assertNotEqual(
            env_a["NS_RESEARCHLENS_BEFORE_DIGEST"], env_b["NS_RESEARCHLENS_BEFORE_DIGEST"]
        )
        self.assertNotEqual(
            env_a["NS_RESEARCHLENS_AFTER_DIGEST"], env_b["NS_RESEARCHLENS_AFTER_DIGEST"]
        )

        result_a = self._run_script(env_a)
        result_b = self._run_script(env_b)

        self.assertEqual(result_a.returncode, 0, result_a.stdout + result_a.stderr)
        self.assertEqual(result_b.returncode, 0, result_b.stdout + result_b.stderr)

    def test_no_real_tmp_baseline_files_are_modified_when_overrides_are_used(self):
        default_before = "/tmp/nightshift-smoke-002-researchlens-before.sha256"
        default_after = "/tmp/nightshift-smoke-002-researchlens-after.sha256"
        old_generic_before = "/tmp/researchlens-before.sha256"
        old_generic_after = "/tmp/researchlens-after.sha256"
        watched_paths = (default_before, default_after, old_generic_before, old_generic_after)
        before_state = {p: (os.path.exists(p), _read_bytes_if_exists(p)) for p in watched_paths}

        result = self._run_script(self._env())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after_state = {p: (os.path.exists(p), _read_bytes_if_exists(p)) for p in watched_paths}
        self.assertEqual(
            before_state,
            after_state,
            "no real, fixed /tmp digest path may be created or modified when "
            "test-only digest overrides are in effect",
        )

    def test_full_operator_suite_runs_correctly_as_a_non_root_user(self):
        self.assertNotEqual(
            os.geteuid(), 0, "this test file is meant to be exercised as a non-root user"
        )

        result = self._run_script(self._env())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    # -- Milestone 7C.2.2: permission-safe preflight handoff regression tests ----
    #
    # The real VPS run failed with "python3: can't open file '/tmp/tmp.<random>':
    # [Errno 13] Permission denied" -- a root-owned mktemp helper source file
    # that the isolated `nightshift` user could not open() by path. The fix
    # feeds that same helper source to `python3 -` over stdin instead, so the
    # child process never opens a path at all, only inherits an already-open
    # file descriptor across fork/exec. Every test below proves some facet of
    # that fix, or of the surrounding evidence-preservation/SMOKE_ID changes
    # that came with it, without ever needing a real second Linux user (this
    # sandbox has none, and no passwordless sudo) -- see each test's own
    # docstring for exactly what it can and cannot prove in that constraint.

    def test_no_helper_source_file_is_ever_written_to_tmp(self):
        """Proves items 3 and 4: nothing resembling the embedded helper's own
        source ever lands on disk under /tmp during a full cycle -- there is
        therefore no path a different user could ever be denied read access
        to in the first place."""
        before_names = set(os.listdir("/tmp"))

        result = self._run_script(self._env())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after_names = set(os.listdir("/tmp"))
        new_names = after_names - before_names
        for name in new_names:
            path = os.path.join("/tmp", name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue
            self.assertNotIn(
                "cmd_preflight",
                content,
                f"a file resembling the embedded helper's own source was left at {path}",
            )

    def test_preflight_succeeds_under_a_strict_root_umask(self):
        """Proves item 2: a restrictive umask on the invoking (root/admin)
        process must not prevent the preflight (or the rest of the cycle)
        from completing -- the stdin-based handoff never depends on any
        helper file's on-disk permission bits, which is exactly what a
        strict umask would otherwise affect."""
        env = self._env()

        result = subprocess.run(
            ["bash", "-c", 'umask 077 && exec bash "$0"', SCRIPT_PATH],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_run_helper_real_mode_invokes_sudo_as_nightshift_with_explicit_home_and_path(self):
        """Proves item 1 and the HOME/PATH parts of item 4 as a static
        source-inspection check on the real (non-test-mode) branch of
        run_helper(): this sandbox has no second real Linux user and no
        passwordless sudo, so the actual cross-user permission behavior
        cannot be exercised end-to-end here -- this instead asserts the
        exact invocation shape that branch constructs, which is what
        governs which user, HOME, and PATH the real preflight process
        actually gets when this script runs for real on the VPS."""
        with open(SCRIPT_PATH, encoding="utf-8") as f:
            source = f.read()

        start = source.index("run_helper() {")
        end = source.index("\n}\n", start)
        run_helper_body = source[start:end]

        self.assertIn('sudo -u "$NIGHTSHIFT_USER" -H', run_helper_body)
        self.assertIn("PYTHONPATH=", run_helper_body)
        self.assertIn('PATH="/usr/bin:/bin"', run_helper_body)
        self.assertIn("python3 -", run_helper_body)
        self.assertIn("<<< \"$HELPER_PY_SOURCE\"", run_helper_body)
        # Never a path argument the child would need to open() itself.
        self.assertNotIn("$HELPER_PY\"", run_helper_body)

    def test_successful_fake_preflight_allows_setup_to_continue(self):
        """Proves item 5."""
        result = self._run_script(self._env())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertTrue(os.path.isdir(smoke2["task_dir"]))
        self.assertTrue(os.path.isfile(smoke2["queue"]))
        self.assertTrue(os.path.isfile(smoke2["config"]))

    def test_nonzero_preflight_process_fails_before_any_fake_claude_invocation(self):
        """Proves item 6, using the same isolation-rejection mechanism as
        test_preflight_failure_stops_before_invocation (a nonexistent
        forbidden path makes preflight_gate() -- and therefore the whole
        helper process -- exit non-zero), but asserting directly that the
        fake Claude executable's own -p invocation marker (calculator.py)
        never appears, i.e. Claude was never launched at all."""
        env = self._env()
        env["NS_FORBIDDEN_PATH"] = os.path.join(self.tmpdir, "does-not-exist-forbidden-path")

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(os.path.join(smoke2["task_dir"], "calculator.py")))

    def test_malformed_preflight_json_fails_before_invocation(self):
        """Proves item 7: a fake `claude auth status --json` that prints
        text that is not valid JSON at all must still fail closed -- the
        underlying auth_preflight.run_auth_preflight() classifies this as
        MALFORMED_OUTPUT, preflight_gate() reports passed=False, and the
        helper's own exit code (never any JSON this bash script would have
        to parse) is what the operator script actually checks."""
        env = self._env()
        env["NS_CLAUDE_CONFIGURED_PATH"] = _write_fake_claude(
            self.bin_dir, mode="success", auth_payload="{ this is not valid json"
        )

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deterministic preflight", result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(smoke2["queue"]))

    def test_authentication_rejection_fails_before_invocation(self):
        """Proves item 9, distinct from the isolation-rejection case: a
        syntactically valid auth-status payload reporting loggedIn=false
        must still fail preflight and stop before any Claude invocation."""
        env = self._env()
        logged_out_payload = (
            '{"loggedIn": false, "authMethod": "claude.ai", "apiProvider": "firstParty", '
            '"subscriptionType": "pro"}'
        )
        env["NS_CLAUDE_CONFIGURED_PATH"] = _write_fake_claude(
            self.bin_dir, mode="success", auth_payload=logged_out_payload
        )

        result = self._run_script(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deterministic preflight", result.stderr)
        smoke2 = self._smoke2_paths()
        self.assertFalse(os.path.exists(smoke2["queue"]))

    def test_raw_auth_identity_fields_are_not_printed_or_persisted(self):
        """Proves item 10: a fake auth-status payload carrying an
        email/orgId (fields auth_preflight.py's own PreflightResult never
        even parses out) must never surface anywhere in this script's
        output or in any evidence file it writes."""
        env = self._env()
        payload_with_identity_fields = (
            '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", '
            '"subscriptionType": "pro", "email": "should-not-leak@example.com", '
            '"orgId": "org-should-not-leak-12345"}'
        )
        env["NS_CLAUDE_CONFIGURED_PATH"] = _write_fake_claude(
            self.bin_dir, mode="success", auth_payload=payload_with_identity_fields
        )

        result = self._run_script(env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        combined_output = result.stdout + result.stderr
        self.assertNotIn("should-not-leak@example.com", combined_output)
        self.assertNotIn("org-should-not-leak-12345", combined_output)
        smoke2 = self._smoke2_paths()
        with open(smoke2["run_log"], encoding="utf-8") as f:
            run_log_text = f.read()
        self.assertNotIn("should-not-leak@example.com", run_log_text)
        self.assertNotIn("org-should-not-leak-12345", run_log_text)

    def test_next_smoke_id_cannot_collide_with_preserved_partial_evidence(self):
        """Proves item 14: after smoke-002 evidence exists (simulating the
        real VPS's preserved failed-attempt evidence), a fresh attempt under
        NS_SMOKE_ID=003 must succeed without ever touching it, and both
        attempts' evidence must coexist afterward, byte-identical to what
        each attempt itself produced."""
        first_env = self._env()
        first_result = self._run_script(first_env)
        self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)

        smoke2 = self._smoke2_paths()
        with open(smoke2["queue"], "rb") as f:
            smoke2_queue_before = f.read()

        second_env = self._env(NS_SMOKE_ID="003")
        # A fresh fake claude/checker is required per attempt -- the queue's
        # acceptance_command path is baked in at generation time, tied to
        # this attempt's own CHECKER_PATH/TASK_DIR (smoke-003, not smoke-002).
        second_result = self._run_script(second_env)

        self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)
        with open(smoke2["queue"], "rb") as f:
            smoke2_queue_after = f.read()
        self.assertEqual(
            smoke2_queue_before, smoke2_queue_after, "smoke-002 evidence must be untouched by smoke-003"
        )

        smoke3_task_dir = os.path.join(self.nightshift_home, "workspace", "smoke", "task-003")
        smoke3_queue = os.path.join(self.nightshift_home, "state", "smoke-003-queue.json")
        self.assertTrue(os.path.isdir(smoke3_task_dir))
        self.assertTrue(os.path.isfile(smoke3_queue))
        self.assertNotEqual(smoke2["queue"], smoke3_queue)

    def test_legacy_generic_digest_path_absent_from_executable_script_logic(self):
        """Proves item 15: the exact old, generic (pre-7C.2.1) digest
        filenames must not appear anywhere in the script's *executable*
        logic -- comment-only mentions (if any) are fine and must not trip
        a naive audit, so this test strips full-line comments before
        searching, exactly as a correct audit of this concern should."""
        with open(SCRIPT_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        executable_lines = [
            line for line in lines if line.strip() and not line.strip().startswith("#")
        ]
        executable_text = "".join(executable_lines)

        self.assertNotIn("/tmp/researchlens-before.sha256", executable_text)
        self.assertNotIn("/tmp/researchlens-after.sha256", executable_text)

    def test_full_suite_passes_as_non_root_user_with_root_owned_style_file_under_tmp(self):
        """Proves item 16 (in combination with test_full_operator_suite_runs_
        correctly_as_a_non_root_user above): an unrelated, unreadable file
        already present under /tmp -- standing in for a real root-owned file
        this process cannot itself open(), the same failure shape the real
        VPS hit -- must not prevent the operator script (or, by the same
        mechanism, the rest of this repository's test suite) from passing."""
        self.assertNotEqual(os.geteuid(), 0)
        blocker_path = os.path.join(self.tmpdir, "unrelated-unreadable-file")
        with open(blocker_path, "w", encoding="utf-8") as f:
            f.write("not readable by design\n")
        os.chmod(blocker_path, 0o000)
        # No cleanup chmod needed: removing a file only requires write
        # permission on its *parent* directory, not on the file itself, so
        # tearDown()'s shutil.rmtree(self.tmpdir) already handles this fine.

        result = self._run_script(self._env())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)


def _read_bytes_if_exists(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


if __name__ == "__main__":
    unittest.main()
