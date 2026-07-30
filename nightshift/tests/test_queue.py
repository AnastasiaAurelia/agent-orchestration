"""Tests for the Nightshift deterministic queue -> claim -> lock primitive.

Every test operates inside a fresh tempfile.mkdtemp() directory and never
touches repository fixtures, real queues/locks, home-directory
configuration, Claude credentials, or Git state. Genuine concurrency is
exercised via real OS subprocesses (see test_concurrent_claims_...), not by
calling the module twice in sequence from one thread.
"""

from __future__ import annotations

import glob
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nightshift.runtime import queue as nsq  # noqa: E402


def _task(
    task_id,
    status="pending",
    title="a bounded task",
    attempt_count=0,
    max_attempts=3,
    claimed_pid=None,
    claimed_at=None,
    acceptance_command=None,
    working_dir=None,
    timeout_seconds=5,
    executor_command=None,
    executor_timeout_seconds=5,
    approved_root=None,
):
    # Defaults are trivially-passing commands and an always-existing
    # directory, so Milestone 1/2/3 tests (which don't care about executor
    # or acceptance specifics) don't need to supply a real contract.
    if acceptance_command is None:
        acceptance_command = [sys.executable, "-c", "pass"]
    if working_dir is None:
        working_dir = tempfile.gettempdir()
    if executor_command is None:
        executor_command = [sys.executable, "-c", "pass"]
    if approved_root is None:
        approved_root = working_dir
    return {
        "id": task_id,
        "status": status,
        "title": title,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "claimed_pid": claimed_pid,
        "claimed_at": claimed_at,
        "executor_command": executor_command,
        "executor_timeout_seconds": executor_timeout_seconds,
        "acceptance_command": acceptance_command,
        "working_dir": working_dir,
        "timeout_seconds": timeout_seconds,
        "approved_root": approved_root,
    }


def _write_raw_queue(path, tasks):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, indent=2, sort_keys=True)
        f.write("\n")


def _read_raw_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _spawn_alive_process():
    """Spawn a real subprocess guaranteed to still be running."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def _spawn_and_reap_dead_pid():
    """Spawn a real subprocess, wait for it to exit, and return its PID.

    Immediately after wait() reaps it, os.kill(pid, 0) will raise
    ProcessLookupError, proving it is genuinely dead -- not merely assumed.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


_HOLD_LOCK_SCRIPT = (
    "import fcntl, os, sys, time\n"
    "lock_path, sentinel_path, hold_seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])\n"
    "fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "with open(sentinel_path, 'w') as f:\n"
    "    f.write('locked')\n"
    "time.sleep(hold_seconds)\n"
    "fcntl.flock(fd, fcntl.LOCK_UN)\n"
    "os.close(fd)\n"
)


def _spawn_lock_holder(lock_path, sentinel_path, hold_seconds):
    """Spawn a real subprocess that grabs the same flock queue.py uses.

    Uses the identical os.open(..., O_CREAT | O_RDWR) + fcntl.flock(LOCK_EX)
    sequence as queue._os_lock, so it contends on the real mechanism, not a
    stand-in. Writes ``sentinel_path`` only after the lock is actually held,
    so callers can wait deterministically instead of guessing with a sleep.
    """
    return subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SCRIPT, lock_path, sentinel_path, str(hold_seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_path(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} never appeared within {timeout}s")
        time.sleep(0.001)


def _wait_until_pid_dead(pid, timeout=2.0):
    """Poll until a PID is truly gone, not merely a killed-but-unreaped zombie.

    SIGKILL terminates a process immediately, but if its parent dies in the
    same stroke (as happens here: the direct child and any grandchild it
    spawned are both killed by the same process-group signal), the
    grandchild becomes an orphaned zombie until the kernel reparents it to
    init/a subreaper and reaps it. A zombie still occupies a PID slot, so
    os.kill(pid, 0) keeps succeeding until that reap actually happens --
    this is standard POSIX behavior, not a bug in the kill itself. Polling
    briefly for that to settle is the correct check, not a flakiness
    workaround.
    """
    deadline = time.monotonic() + timeout
    while nsq._is_pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class NightshiftQueueTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-test-")
        self.queue_path = os.path.join(self.tmpdir, "queue.json")
        self._live_procs = []

    def tearDown(self):
        for proc in self._live_procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- 1. Basic claim ----------------------------------------------------

    def test_basic_claim_succeeds_and_records_pid_timestamp_and_attempt(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])

        result = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(result.task_id, "t1")
        self.assertEqual(result.attempt_count, 1)

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "claimed")
        self.assertEqual(task["claimed_pid"], os.getpid())
        self.assertIsNotNone(task["claimed_at"])
        datetime.fromisoformat(task["claimed_at"])  # must parse cleanly
        self.assertEqual(task["attempt_count"], 1)

    # -- 2. No pending task --------------------------------------------------

    def test_no_pending_task_returns_clean_no_work_result_and_leaves_queue_untouched(
        self,
    ):
        _write_raw_queue(self.queue_path, [])
        before = _read_raw_bytes(self.queue_path)

        result = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.NO_PENDING_TASK)
        self.assertIsNone(result.task_id)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "queue file must be byte-identical when there is no work")

    # -- 3. Concurrent claim (genuine separate processes) --------------------

    def test_concurrent_claims_produce_exactly_one_winner_no_double_assignment(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        sentinel_path = os.path.join(self.tmpdir, "start.sentinel")

        stdout_paths = [
            os.path.join(self.tmpdir, "out1.json"),
            os.path.join(self.tmpdir, "out2.json"),
        ]
        procs = []
        for out_path in stdout_paths:
            out_f = open(out_path, "w", encoding="utf-8")
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nightshift.runtime.queue",
                    "claim",
                    self.queue_path,
                    "--stale-threshold",
                    "300",
                    "--wait-for",
                    sentinel_path,
                ],
                cwd=REPO_ROOT,
                stdout=out_f,
                stderr=subprocess.DEVNULL,
            )
            procs.append((proc, out_f))
        self._live_procs.extend(p for p, _ in procs)

        # Give both children a moment to reach the busy-wait loop before the
        # sentinel appears, to maximize genuine overlap. Not required for
        # correctness (flock serializes regardless of timing) -- only
        # improves how "concurrent" the race actually is.
        time.sleep(0.2)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("go")

        results = []
        for proc, out_f in procs:
            proc.wait(timeout=10)
            out_f.close()
        for out_path, (proc, _) in zip(stdout_paths, procs):
            with open(out_path, encoding="utf-8") as f:
                results.append(json.loads(f.read()))

        claimed_results = [r for r in results if r["outcome"] == "claimed"]
        rejected_results = [r for r in results if r["outcome"] != "claimed"]
        self.assertEqual(len(claimed_results), 1, f"expected exactly one winner, got {results}")
        self.assertEqual(len(rejected_results), 1)
        # With a non-blocking OS mutex, exactly *which* rejection the loser
        # gets is itself a race: it may fail to acquire the mutex at all
        # ("lock_busy"), or acquire it just after the winner released it and
        # then find the task already claimed ("lock_held") or, in principle,
        # find no work left ("no_pending_task"). All three are safe,
        # documented rejections; the invariant this test actually proves is
        # the one checked above and below -- exactly one winner, one task,
        # one attempt-count increment.
        self.assertIn(
            rejected_results[0]["outcome"], ("lock_busy", "lock_held", "no_pending_task")
        )
        self.assertEqual(claimed_results[0]["task_id"], "t1")
        self.assertEqual(claimed_results[0]["attempt_count"], 1)

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(len(data["tasks"]), 1)
        task = data["tasks"][0]
        self.assertEqual(task["status"], "claimed")
        self.assertEqual(task["attempt_count"], 1, "attempt count must increment exactly once for the race")
        winner_pid = next(
            p.pid for p, out_path in zip((p for p, _ in procs), stdout_paths)
            if json.loads(_read_raw_bytes(out_path))["outcome"] == "claimed"
        )
        self.assertEqual(task["claimed_pid"], winner_pid)

    # -- 4. Live lock cannot be stolen ----------------------------------------

    def test_live_lock_cannot_be_stolen(self):
        alive_proc = _spawn_alive_process()
        self._live_procs.append(alive_proc)
        self.assertTrue(nsq._is_pid_alive(alive_proc.pid), "precondition: process must be alive")

        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=alive_proc.pid,
                    claimed_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
        )
        before = _read_raw_bytes(self.queue_path)

        result = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.LOCK_HELD)
        self.assertEqual(result.blocking_task_id, "t1")
        self.assertEqual(result.recovered_stale_task_ids, ())
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "a live claim must not be modified at all")

    # -- Adversarial follow-up: OS mutex must never be waited on indefinitely --

    def test_second_process_returns_lock_busy_promptly_without_waiting_for_release(
        self,
    ):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        before = _read_raw_bytes(self.queue_path)

        lock_path = self.queue_path + ".lock"
        held_sentinel = os.path.join(self.tmpdir, "held.sentinel")
        hold_seconds = 6.0
        holder = _spawn_lock_holder(lock_path, held_sentinel, hold_seconds)
        self._live_procs.append(holder)

        # Deterministic sync: proceed only once the holder has *confirmed*
        # (via the sentinel file it writes right after flock() succeeds)
        # that it truly holds the OS mutex -- not after a guessed sleep.
        _wait_for_path(held_sentinel, timeout=10)

        out_path = os.path.join(self.tmpdir, "second.json")
        start = time.monotonic()
        with open(out_path, "w", encoding="utf-8") as out_f:
            second = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "nightshift.runtime.queue",
                    "claim",
                    self.queue_path,
                ],
                cwd=REPO_ROOT,
                stdout=out_f,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        elapsed = time.monotonic() - start

        self.assertEqual(second.returncode, 0)
        self.assertLess(
            elapsed,
            hold_seconds / 2,
            "the second claim must not wait anywhere near as long as the holder "
            "keeps the lock -- it must back off promptly instead",
        )

        with open(out_path, encoding="utf-8") as f:
            result = json.loads(f.read())
        self.assertEqual(result["outcome"], "lock_busy")

        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(
            before, after, "the queue file must never be opened when the OS lock is busy"
        )

        # Cleanup: release the holder now rather than waiting out its sleep.
        holder.terminate()
        holder.wait(timeout=5)

    # -- 5. Dead but not old enough -------------------------------------------

    def test_dead_but_fresh_lock_is_not_reclaimed(self):
        dead_pid = _spawn_and_reap_dead_pid()
        self.assertFalse(nsq._is_pid_alive(dead_pid), "precondition: process must be dead")

        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=dead_pid,
                    claimed_at=datetime.now(timezone.utc).isoformat(),  # fresh
                )
            ],
        )
        before = _read_raw_bytes(self.queue_path)

        result = nsq.claim_next(self.queue_path, stale_threshold_seconds=3600)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.LOCK_HELD)
        self.assertEqual(result.recovered_stale_task_ids, (), "must not recover before the stale threshold")
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after)

    # -- 6. Dead stale lock recovery ------------------------------------------

    def test_dead_stale_lock_is_recovered_reclaimed_once_then_rejected_on_subsequent_call(
        self,
    ):
        dead_pid = _spawn_and_reap_dead_pid()
        self.assertFalse(nsq._is_pid_alive(dead_pid))

        old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=dead_pid,
                    claimed_at=old_timestamp,
                )
            ],
        )

        first = nsq.claim_next(self.queue_path, stale_threshold_seconds=1)
        self.assertEqual(first.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(first.task_id, "t1")
        self.assertEqual(first.recovered_stale_task_ids, ("t1",))
        self.assertEqual(first.attempt_count, 2, "attempt count must increment exactly once across recovery+reclaim")

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["claimed_pid"], os.getpid())
        after_first = _read_raw_bytes(self.queue_path)

        second = nsq.claim_next(self.queue_path, stale_threshold_seconds=1)
        self.assertEqual(
            second.outcome,
            nsq.ClaimOutcome.LOCK_HELD,
            "a subsequent claim must not succeed a second time",
        )
        after_second = _read_raw_bytes(self.queue_path)
        self.assertEqual(after_first, after_second, "the second call must not modify the queue")

    # -- 7. Malformed queue ----------------------------------------------------

    def test_malformed_queue_variants_fail_clearly_and_preserve_original_bytes(self):
        variants = {
            "invalid_json": "{not valid json at all",
            "missing_required_field": json.dumps(
                {
                    "tasks": [
                        {
                            "id": "t1",
                            "status": "pending",
                            "title": "x",
                            "claimed_pid": None,
                            "claimed_at": None,
                            # attempt_count missing
                        }
                    ]
                }
            ),
            "duplicate_task_id": json.dumps(
                {"tasks": [_task("dup"), _task("dup")]}
            ),
            "unsupported_status": json.dumps(
                {"tasks": [_task("t1", status="in_progress")]}
            ),
            "negative_attempt_count": json.dumps(
                {"tasks": [_task("t1", attempt_count=-1)]}
            ),
            "non_integer_attempt_count": json.dumps(
                {"tasks": [_task("t1", attempt_count="one")]}
            ),
        }

        for name, raw_content in variants.items():
            with self.subTest(variant=name):
                with open(self.queue_path, "w", encoding="utf-8") as f:
                    f.write(raw_content)
                before = _read_raw_bytes(self.queue_path)

                result = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

                self.assertEqual(result.outcome, nsq.ClaimOutcome.MALFORMED_QUEUE)
                self.assertIsNotNone(result.message)
                after = _read_raw_bytes(self.queue_path)
                self.assertEqual(
                    before, after, f"variant {name!r} must leave the original file byte-for-byte unchanged"
                )

    # -- 8. Atomic write failure protection -------------------------------------

    def test_injected_failure_before_replace_leaves_canonical_queue_untouched(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        before = _read_raw_bytes(self.queue_path)

        with mock.patch.object(nsq.os, "replace", side_effect=OSError("simulated disk failure")):
            result = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.INTERNAL_FAILURE)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "canonical file must be untouched when replace fails")

        leftover_temp_files = glob.glob(os.path.join(self.tmpdir, ".queue-*.tmp"))
        self.assertEqual(leftover_temp_files, [], "temp artifacts must be cleaned up after a failed write")

    # -- 9. Canonical state validity across successful operations ---------------

    def test_canonical_queue_stays_valid_and_ids_unique_after_successive_claims(self):
        _write_raw_queue(
            self.queue_path,
            [_task("t1", attempt_count=0), _task("t2", attempt_count=0)],
        )

        first = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))  # raises if invalid
        ids = [t["id"] for t in data["tasks"]]
        self.assertEqual(len(ids), len(set(ids)), "task ids must remain unique")

        second = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        ids = [t["id"] for t in data["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))

        self.assertEqual({first.task_id, second.task_id}, {"t1", "t2"})
        third = nsq.claim_next(self.queue_path, stale_threshold_seconds=300)
        self.assertEqual(third.outcome, nsq.ClaimOutcome.LOCK_HELD)

    # -- Adversarial follow-up: the CLI's self-recorded PID is not durable ------

    def test_cli_claim_records_a_pid_that_is_already_dead_once_the_command_returns(
        self,
    ):
        """Documents (and proves) the CLI's ownership contract.

        The bare CLI records its own PID as claimant_pid, then exits. By the
        time the command returns, that PID is dead -- so it must never be
        mistaken for a durable, ongoing claim. A subsequent claim_next() call
        with a permissive stale threshold can recover it almost immediately,
        because there is no live process behind that ownership.
        """
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])

        completed = subprocess.run(
            [sys.executable, "-m", "nightshift.runtime.queue", "claim", self.queue_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0)
        first = json.loads(completed.stdout)
        self.assertEqual(first["outcome"], "claimed")

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        recorded_pid = data["tasks"][0]["claimed_pid"]

        # The CLI subprocess has already exited (subprocess.run only returns
        # after it does), so its recorded PID must already read as dead.
        self.assertFalse(
            nsq._is_pid_alive(recorded_pid),
            "the CLI's self-recorded claimant must be dead the instant the command returns",
        )

        second = nsq.claim_next(self.queue_path, stale_threshold_seconds=0)
        self.assertEqual(
            second.outcome,
            nsq.ClaimOutcome.CLAIMED,
            "a claim made only via the bare CLI must not behave as durable "
            "ownership -- it must be immediately recoverable",
        )
        self.assertEqual(second.recovered_stale_task_ids, ("t1",))

    # -- Milestone 2: completion and retry transitions ---------------------------

    def test_valid_owner_completes_a_claimed_task(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        claim = nsq.claim_next(self.queue_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        result = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(result.outcome, nsq.TransitionOutcome.DONE)
        self.assertEqual(result.task_id, "t1")
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["claimed_pid"])
        self.assertIsNone(task["claimed_at"])

    def test_wrong_owner_cannot_complete_task(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        claim = nsq.claim_next(self.queue_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)
        before = _read_raw_bytes(self.queue_path)

        impostor_pid = os.getpid() + 1  # exact-match check only; liveness is irrelevant here
        result = nsq.complete_task(self.queue_path, "t1", owner_pid=impostor_pid)

        self.assertEqual(result.outcome, nsq.TransitionOutcome.WRONG_OWNER)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "a wrong-owner completion attempt must not modify the queue")

    def test_pending_task_cannot_be_completed(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        before = _read_raw_bytes(self.queue_path)

        result = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(result.outcome, nsq.TransitionOutcome.INVALID_STATE)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after)

    def test_done_task_cannot_be_completed_again(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        nsq.claim_next(self.queue_path)
        first = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(first.outcome, nsq.TransitionOutcome.DONE)
        before = _read_raw_bytes(self.queue_path)

        second = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(second.outcome, nsq.TransitionOutcome.INVALID_STATE)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "completing an already-done task must not modify the queue")

    def test_first_failure_below_retry_limit_requeues_task(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0, max_attempts=2)])
        claim = nsq.claim_next(self.queue_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(claim.attempt_count, 1)

        result = nsq.fail_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(result.outcome, nsq.TransitionOutcome.REQUEUED)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "pending")
        self.assertIsNone(task["claimed_pid"])
        self.assertIsNone(task["claimed_at"])
        self.assertEqual(task["attempt_count"], 1, "fail_task must not itself change attempt_count")

        reclaim = nsq.claim_next(self.queue_path)
        self.assertEqual(reclaim.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(reclaim.attempt_count, 2, "the requeued task must be claimable again")

    def test_failure_at_retry_limit_marks_task_permanently_failed(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0, max_attempts=2)])
        nsq.claim_next(self.queue_path)  # attempt_count -> 1
        first_fail = nsq.fail_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(first_fail.outcome, nsq.TransitionOutcome.REQUEUED)

        nsq.claim_next(self.queue_path)  # attempt_count -> 2 == max_attempts
        result = nsq.fail_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(result.outcome, nsq.TransitionOutcome.FAILED_PERMANENTLY)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIsNone(task["claimed_pid"])
        self.assertIsNone(task["claimed_at"])
        self.assertEqual(task["attempt_count"], 2)

    def test_failed_task_cannot_be_claimed(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0, max_attempts=1)])
        claim = nsq.claim_next(self.queue_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(claim.attempt_count, 1)

        failed = nsq.fail_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(failed.outcome, nsq.TransitionOutcome.FAILED_PERMANENTLY)

        result = nsq.claim_next(self.queue_path)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.NO_PENDING_TASK)

    def test_done_task_cannot_be_claimed(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        nsq.claim_next(self.queue_path)
        completed = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(completed.outcome, nsq.TransitionOutcome.DONE)

        result = nsq.claim_next(self.queue_path)

        self.assertEqual(result.outcome, nsq.ClaimOutcome.NO_PENDING_TASK)

    def test_concurrent_completion_attempts_produce_exactly_one_valid_transition(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        claim = nsq.claim_next(self.queue_path, claimant_pid=999999)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        sentinel_path = os.path.join(self.tmpdir, "start.sentinel")
        stdout_paths = [os.path.join(self.tmpdir, f"complete{i}.json") for i in range(2)]
        procs = []
        for out_path in stdout_paths:
            out_f = open(out_path, "w", encoding="utf-8")
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nightshift.runtime.queue",
                    "complete",
                    self.queue_path,
                    "t1",
                    "--owner-pid",
                    "999999",
                    "--wait-for",
                    sentinel_path,
                ],
                cwd=REPO_ROOT,
                stdout=out_f,
                stderr=subprocess.DEVNULL,
            )
            procs.append((proc, out_f))
        self._live_procs.extend(p for p, _ in procs)

        time.sleep(0.2)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("go")

        for proc, out_f in procs:
            proc.wait(timeout=10)
            out_f.close()
        results = [json.loads(_read_raw_bytes(p).decode("utf-8")) for p in stdout_paths]

        done_results = [r for r in results if r["outcome"] == "done"]
        rejected_results = [r for r in results if r["outcome"] != "done"]
        self.assertEqual(len(done_results), 1, f"expected exactly one valid completion, got {results}")
        self.assertEqual(len(rejected_results), 1)
        self.assertIn(rejected_results[0]["outcome"], ("lock_busy", "invalid_state"))

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "done")

    def test_transition_on_nonexistent_task_id_is_rejected_and_leaves_queue_unchanged(
        self,
    ):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        before = _read_raw_bytes(self.queue_path)

        complete_result = nsq.complete_task(self.queue_path, "does-not-exist", owner_pid=os.getpid())
        self.assertEqual(complete_result.outcome, nsq.TransitionOutcome.TASK_NOT_FOUND)
        self.assertEqual(_read_raw_bytes(self.queue_path), before)

        fail_result = nsq.fail_task(self.queue_path, "does-not-exist", owner_pid=os.getpid())
        self.assertEqual(fail_result.outcome, nsq.TransitionOutcome.TASK_NOT_FOUND)
        self.assertEqual(_read_raw_bytes(self.queue_path), before)

    def test_malformed_transition_input_leaves_canonical_state_unchanged(self):
        with open(self.queue_path, "w", encoding="utf-8") as f:
            f.write("{not valid json for a transition attempt")
        before = _read_raw_bytes(self.queue_path)

        complete_result = nsq.complete_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(complete_result.outcome, nsq.TransitionOutcome.MALFORMED_QUEUE)
        after_complete = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after_complete)

        fail_result = nsq.fail_task(self.queue_path, "t1", owner_pid=os.getpid())
        self.assertEqual(fail_result.outcome, nsq.TransitionOutcome.MALFORMED_QUEUE)
        after_fail = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after_fail)

    # -- Milestone 3: task contract and deterministic acceptance -----------------

    def test_passing_acceptance_marks_task_done(self):
        _write_raw_queue(self.queue_path, [_task("t1", attempt_count=0)])
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_acceptance_and_record(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertTrue(run.acceptance.passed)
        self.assertEqual(run.acceptance.reason, nsq.AcceptanceOutcome.PASSED)
        self.assertEqual(run.acceptance.exit_code, 0)
        self.assertEqual(run.transition.outcome, nsq.TransitionOutcome.DONE)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "done")

    def test_failing_acceptance_enters_retry_flow(self):
        _write_raw_queue(
            self.queue_path,
            [_task("t1", attempt_count=0, max_attempts=2, acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"])],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)
        self.assertEqual(claim.attempt_count, 1)

        run = nsq.run_acceptance_and_record(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertFalse(run.acceptance.passed)
        self.assertEqual(run.acceptance.reason, nsq.AcceptanceOutcome.FAILED)
        self.assertEqual(run.acceptance.exit_code, 1)
        self.assertEqual(run.transition.outcome, nsq.TransitionOutcome.REQUEUED)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "pending")

    def test_timeout_enters_retry_flow(self):
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=2,
                    acceptance_command=[sys.executable, "-c", "import time; time.sleep(5)"],
                    timeout_seconds=1,
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_acceptance_and_record(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertFalse(run.acceptance.passed)
        self.assertEqual(run.acceptance.reason, nsq.AcceptanceOutcome.TIMED_OUT)
        self.assertEqual(run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

    def test_malformed_contract_is_rejected(self):
        task = _task("t1", acceptance_command="not-a-list-of-strings")

        result = nsq.run_acceptance(task)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason, nsq.AcceptanceOutcome.MALFORMED_CONTRACT)
        self.assertIsNone(result.exit_code)

    def test_missing_command_is_rejected(self):
        task = _task("t1", acceptance_command=["/path/does/not/exist/nightshift-fixture-binary"])

        result = nsq.run_acceptance(task)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason, nsq.AcceptanceOutcome.MISSING_COMMAND)
        self.assertIsNone(result.exit_code)

    def test_stdout_and_stderr_are_captured(self):
        task = _task(
            "t1",
            acceptance_command=[
                sys.executable,
                "-c",
                "import sys; print('out-line'); print('err-line', file=sys.stderr)",
            ],
        )

        result = nsq.run_acceptance(task)

        self.assertTrue(result.passed)
        self.assertIn("out-line", result.stdout)
        self.assertIn("err-line", result.stderr)

    def test_executor_text_cannot_override_a_failed_acceptance_result(self):
        task = _task(
            "t1",
            acceptance_command=[
                sys.executable,
                "-c",
                "print('SUCCESS! all tests passed! nothing to see here'); "
                "import sys; sys.exit(1)",
            ],
        )

        result = nsq.run_acceptance(task)

        self.assertFalse(
            result.passed,
            "stdout claiming success must never override a non-zero exit code",
        )
        self.assertEqual(result.reason, nsq.AcceptanceOutcome.FAILED)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("SUCCESS", result.stdout)

    def test_command_arguments_containing_spaces_are_handled_safely(self):
        task = _task(
            "t1",
            acceptance_command=[
                sys.executable,
                "-c",
                "import sys; sys.exit(0 if sys.argv[1] == 'has spaces' else 1)",
                "has spaces",
            ],
        )

        result = nsq.run_acceptance(task)

        self.assertTrue(
            result.passed,
            "an argv element containing spaces must survive as one argument, unsplit",
        )

    # -- Milestone 4A: bounded executor process ----------------------------------

    def test_successful_executor_followed_by_passing_acceptance(self):
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    working_dir=self.tmpdir,
                    executor_command=[sys.executable, "-c", "open('marker.txt', 'w').close()"],
                    acceptance_command=[
                        sys.executable,
                        "-c",
                        "import os, sys; sys.exit(0 if os.path.exists('marker.txt') else 1)",
                    ],
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(run.executor.outcome, nsq.ExecutorOutcome.COMPLETED)
        self.assertEqual(run.executor.exit_code, 0)
        self.assertIsNotNone(run.executor.pid)
        self.assertTrue(run.acceptance_run.acceptance.passed)
        self.assertEqual(run.acceptance_run.transition.outcome, nsq.TransitionOutcome.DONE)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "marker.txt")))

    def test_executor_nonzero_exit_does_not_prevent_acceptance_from_running(self):
        # Milestone 7C.1: this test used to assert the opposite of what's
        # below -- that a passing acceptance command still marked the task
        # "done" even though the executor itself exited non-zero. That was
        # the exact flaw a real supervised Claude smoke test exposed (a
        # failed-authentication Claude session, exit code 1, was reported
        # "done" because a lenient acceptance command happened to pass).
        # Acceptance must still *run* here (it remains useful diagnostic
        # evidence, asserted below), but its PASSED result must never be
        # sufficient for a "done" transition when the executor did not
        # succeed -- see queue.run_acceptance_and_record()'s Completion
        # Invariant.
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=3,
                    working_dir=self.tmpdir,
                    executor_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                    acceptance_command=[sys.executable, "-c", "pass"],
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(run.executor.outcome, nsq.ExecutorOutcome.COMPLETED)
        self.assertEqual(run.executor.exit_code, 1)
        self.assertTrue(
            run.acceptance_run.acceptance.passed,
            "acceptance still runs and is recorded as diagnostic evidence, "
            "even though its own passing result cannot drive completion here",
        )
        self.assertEqual(run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "pending")

    def test_executor_nonzero_exit_at_retry_limit_permanently_fails_despite_passing_acceptance(self):
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=1,
                    working_dir=self.tmpdir,
                    executor_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                    acceptance_command=[sys.executable, "-c", "pass"],
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertTrue(run.acceptance_run.acceptance.passed)
        self.assertEqual(
            run.acceptance_run.transition.outcome, nsq.TransitionOutcome.FAILED_PERMANENTLY
        )

    def test_durable_run_log_records_executor_and_acceptance_evidence_before_transition(self):
        run_log_path = os.path.join(self.tmpdir, "run_log.jsonl")
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=3,
                    working_dir=self.tmpdir,
                    executor_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                    acceptance_command=[sys.executable, "-c", "pass"],
                )
            ],
        )
        claim = nsq.claim_next(
            self.queue_path, claimant_pid=os.getpid(), run_log_path=run_log_path
        )
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(
            self.queue_path, "t1", owner_pid=os.getpid(), run_log_path=run_log_path
        )
        self.assertEqual(run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

        events, malformed = nsq._load_run_log_events(run_log_path)
        self.assertEqual(malformed, 0)
        evidence_events = [e for e in events if e["event"] == "task_run_evidence"]
        self.assertEqual(len(evidence_events), 1)
        evidence = evidence_events[0]
        self.assertEqual(evidence["executor_outcome"], "completed")
        self.assertEqual(evidence["executor_exit_code"], 1)
        self.assertEqual(evidence["acceptance_outcome"], "passed")
        self.assertEqual(evidence["acceptance_exit_code"], 0)

        # The evidence entry must exist strictly before the transition entry
        # it justifies -- a "requeued"/"done" event must never appear
        # without the supporting evidence already durably recorded ahead of
        # it (the ordering, not just presence, is the actual requirement).
        transition_events = [e for e in events if e["event"] == "requeued"]
        self.assertEqual(len(transition_events), 1)
        self.assertLess(
            events.index(evidence_events[0]), events.index(transition_events[0])
        )

    def test_executor_times_out(self):
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[sys.executable, "-c", "import time; time.sleep(30)"],
            executor_timeout_seconds=1,
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        self.assertIsNotNone(result.pid)

    def test_child_process_is_cleaned_up_after_timeout(self):
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[sys.executable, "-c", "import time; time.sleep(30)"],
            executor_timeout_seconds=1,
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        self.assertFalse(
            nsq._is_pid_alive(result.pid),
            "the direct child must already be reaped by the time run_executor returns",
        )

    def test_child_spawned_subprocess_is_also_cleaned_up(self):
        grandchild_pid_file = os.path.join(self.tmpdir, "grandchild_pid.txt")
        executor_script = (
            "import subprocess, sys, time\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"with open({grandchild_pid_file!r}, 'w') as f:\n"
            "    f.write(str(gc.pid))\n"
            "time.sleep(30)\n"
        )
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[sys.executable, "-c", executor_script],
            executor_timeout_seconds=2,
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        with open(grandchild_pid_file, encoding="utf-8") as f:
            grandchild_pid = int(f.read().strip())
        self.assertFalse(nsq._is_pid_alive(result.pid), "the direct child must be dead")
        self.assertTrue(
            _wait_until_pid_dead(grandchild_pid),
            "a grandchild spawned by the executor must also die from the "
            "process-group kill, not just the direct child (allowing brief "
            "time for the kernel to reap the orphaned zombie)",
        )

    def test_missing_executable_fails_cleanly(self):
        task = _task(
            "t1", executor_command=["/path/does/not/exist/nightshift-fixture-binary"]
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.MISSING_EXECUTABLE)
        self.assertIsNone(result.pid)
        self.assertIsNone(result.exit_code)

    def test_working_directory_boundary_is_respected(self):
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[
                sys.executable,
                "-c",
                "import os; open('cwd_marker.txt', 'w').write(os.getcwd())",
            ],
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        marker_path = os.path.join(self.tmpdir, "cwd_marker.txt")
        self.assertTrue(os.path.exists(marker_path), "executor must have run with cwd=working_dir")
        with open(marker_path, encoding="utf-8") as f:
            recorded_cwd = f.read()
        self.assertEqual(os.path.realpath(recorded_cwd), os.path.realpath(self.tmpdir))

    def test_output_evidence_is_retained_even_on_timeout(self):
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[
                sys.executable,
                "-c",
                "import sys, time; print('before-timeout'); sys.stdout.flush(); time.sleep(30)",
            ],
            executor_timeout_seconds=1,
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        self.assertIn("before-timeout", result.stdout)

    # -- Milestone 5: abandoned-run recovery -------------------------------------

    def test_killed_executor_owner_is_recovered(self):
        proc = _spawn_alive_process()
        proc.kill()
        proc.wait(timeout=5)
        self.assertFalse(nsq._is_pid_alive(proc.pid), "precondition: process must be dead")

        old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    max_attempts=3,
                    claimed_pid=proc.pid,
                    claimed_at=old_timestamp,
                )
            ],
        )

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=1)

        self.assertEqual(result.outcome, nsq.ReapOutcome.RECOVERED)
        self.assertEqual(result.requeued_task_ids, ("t1",))
        self.assertEqual(result.failed_task_ids, ())
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "pending")

    def test_active_executor_owner_is_not_reaped(self):
        alive_proc = _spawn_alive_process()
        self._live_procs.append(alive_proc)

        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=alive_proc.pid,
                    claimed_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
        )
        before = _read_raw_bytes(self.queue_path)

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ReapOutcome.NOTHING_TO_REAP)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after, "an active owner's claim must never be touched")

    def test_dead_but_fresh_executor_owner_is_not_reaped_early(self):
        dead_pid = _spawn_and_reap_dead_pid()

        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=dead_pid,
                    claimed_at=datetime.now(timezone.utc).isoformat(),  # fresh
                )
            ],
        )
        before = _read_raw_bytes(self.queue_path)

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=3600)

        self.assertEqual(result.outcome, nsq.ReapOutcome.NOTHING_TO_REAP)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after)

    def test_dead_stale_executor_owner_is_recovered(self):
        dead_pid = _spawn_and_reap_dead_pid()
        old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    max_attempts=5,
                    claimed_pid=dead_pid,
                    claimed_at=old_timestamp,
                )
            ],
        )

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=1)

        self.assertEqual(result.outcome, nsq.ReapOutcome.RECOVERED)
        self.assertEqual(result.requeued_task_ids, ("t1",))
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "pending")
        self.assertIsNone(task["claimed_pid"])
        self.assertIsNone(task["claimed_at"])

    def test_retry_limit_is_respected_during_abandoned_run_recovery(self):
        dead_pid = _spawn_and_reap_dead_pid()
        old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=2,
                    max_attempts=2,  # already at the limit
                    claimed_pid=dead_pid,
                    claimed_at=old_timestamp,
                )
            ],
        )

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=1)

        self.assertEqual(result.outcome, nsq.ReapOutcome.RECOVERED)
        self.assertEqual(
            result.failed_task_ids,
            ("t1",),
            "a task abandoned at its retry limit must be recovered to 'failed', not 'pending'",
        )
        self.assertEqual(result.requeued_task_ids, ())
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        task = data["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIsNone(task["claimed_pid"])
        self.assertIsNone(task["claimed_at"])

        # Confirms the queue is not permanently blocked: claim_next() must
        # still work normally afterward (nothing left claimable here, but
        # the call itself must complete cleanly, not error).
        claim = nsq.claim_next(self.queue_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.NO_PENDING_TASK)

    def test_recovery_with_nothing_abandoned_is_a_clean_no_op(self):
        _write_raw_queue(self.queue_path, [_task("t1", status="pending")])
        before = _read_raw_bytes(self.queue_path)

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ReapOutcome.NOTHING_TO_REAP)
        self.assertEqual(result.requeued_task_ids, ())
        self.assertEqual(result.failed_task_ids, ())
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after)

    def test_recovery_on_malformed_queue_is_rejected_and_leaves_queue_unchanged(self):
        with open(self.queue_path, "w", encoding="utf-8") as f:
            f.write("{not valid json for reap either")
        before = _read_raw_bytes(self.queue_path)

        result = nsq.reap_abandoned_tasks(self.queue_path, stale_threshold_seconds=300)

        self.assertEqual(result.outcome, nsq.ReapOutcome.MALFORMED_QUEUE)
        after = _read_raw_bytes(self.queue_path)
        self.assertEqual(before, after)

    def test_concurrent_reap_and_completion_produce_one_valid_terminal_transition(self):
        alive_proc = _spawn_alive_process()
        self._live_procs.append(alive_proc)

        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    status="claimed",
                    attempt_count=1,
                    claimed_pid=alive_proc.pid,
                    claimed_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
        )

        sentinel_path = os.path.join(self.tmpdir, "start.sentinel")
        complete_out = os.path.join(self.tmpdir, "complete.json")
        reap_out = os.path.join(self.tmpdir, "reap.json")

        complete_f = open(complete_out, "w", encoding="utf-8")
        complete_proc = subprocess.Popen(
            [
                sys.executable, "-m", "nightshift.runtime.queue", "complete",
                self.queue_path, "t1", "--owner-pid", str(alive_proc.pid),
                "--wait-for", sentinel_path,
            ],
            cwd=REPO_ROOT, stdout=complete_f, stderr=subprocess.DEVNULL,
        )
        reap_f = open(reap_out, "w", encoding="utf-8")
        reap_proc = subprocess.Popen(
            [
                sys.executable, "-m", "nightshift.runtime.queue", "reap",
                self.queue_path, "--stale-threshold", "0",
                "--wait-for", sentinel_path,
            ],
            cwd=REPO_ROOT, stdout=reap_f, stderr=subprocess.DEVNULL,
        )
        self._live_procs.extend([complete_proc, reap_proc])

        time.sleep(0.2)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("go")

        complete_proc.wait(timeout=10)
        complete_f.close()
        reap_proc.wait(timeout=10)
        reap_f.close()

        with open(complete_out, encoding="utf-8") as f:
            complete_result = json.loads(f.read())
        with open(reap_out, encoding="utf-8") as f:
            reap_result = json.loads(f.read())

        self.assertIn(
            reap_result["outcome"],
            ("nothing_to_reap", "lock_busy"),
            "reap must never recover an actively-owned claim -- 'recovered' would mean it stole it",
        )

        # The very first completion attempt can legitimately lose the
        # non-blocking OS-mutex race to reap -- that's the same lock_busy
        # contract every operation in this module has (see Milestone 1's
        # concurrent-claim test: which named process wins the mutex is a
        # race, never assumed). A real caller retries on lock_busy; do the
        # same here rather than assuming a single bare attempt must win.
        attempts = [complete_result]
        retries = 0
        while complete_result["outcome"] == "lock_busy" and retries < 20:
            completed = subprocess.run(
                [
                    sys.executable, "-m", "nightshift.runtime.queue", "complete",
                    self.queue_path, "t1", "--owner-pid", str(alive_proc.pid),
                ],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
            )
            complete_result = json.loads(completed.stdout)
            attempts.append(complete_result)
            retries += 1

        self.assertEqual(
            complete_result["outcome"],
            "done",
            f"completion must succeed once retried past transient lock contention; attempts={attempts}",
        )

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(
            data["tasks"][0]["status"],
            "done",
            "the live, legitimate completion must win -- reap must never steal an active run",
        )

    # -- Milestone 6: deterministic local report ---------------------------------

    def test_report_with_all_successful_runs(self):
        queue_data = {
            "tasks": [
                _task("t1", status="done", attempt_count=1),
                _task("t2", status="done", attempt_count=1),
            ]
        }
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "done", "attempt_count": 1, "detail": None},
            {"timestamp": "2026-07-29T10:01:00+00:00", "task_id": "t2", "event": "done", "attempt_count": 1, "detail": None},
        ]

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("Done: 2", report)
        self.assertIn("Permanently failed: 0", report)
        self.assertIn("`t1`", report)
        self.assertIn("`t2`", report)
        self.assertIn("done", report)

    def test_report_with_mixed_success_and_failure(self):
        queue_data = {
            "tasks": [
                _task("t1", status="done", attempt_count=1),
                _task("t2", status="failed", attempt_count=2, max_attempts=2),
                _task("t3", status="pending", attempt_count=1, max_attempts=3),
            ]
        }
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "done", "attempt_count": 1, "detail": None},
            {"timestamp": "2026-07-29T10:01:00+00:00", "task_id": "t2", "event": "requeued", "attempt_count": 1, "detail": "failed"},
            {"timestamp": "2026-07-29T10:02:00+00:00", "task_id": "t2", "event": "failed_permanently", "attempt_count": 2, "detail": "failed"},
            {"timestamp": "2026-07-29T10:03:00+00:00", "task_id": "t3", "event": "requeued", "attempt_count": 1, "detail": "failed"},
        ]

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("Done: 1", report)
        self.assertIn("Permanently failed: 1", report)
        self.assertIn("Pending retry: 1", report)
        self.assertIn("permanently failed", report)
        self.assertIn("requeued for another attempt", report)

    def test_report_distinguishes_timeout_from_other_rejection_reasons(self):
        queue_data = {"tasks": [_task("t1", status="pending", attempt_count=1, max_attempts=3)]}
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "requeued", "attempt_count": 1, "detail": "timed_out"},
        ]

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("timed_out", report)

    def test_crash_recovery_report_shows_recovered_events(self):
        queue_data = {
            "tasks": [
                _task("t1", status="pending", attempt_count=1, max_attempts=3),
                _task("t2", status="failed", attempt_count=2, max_attempts=2),
            ]
        }
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "recovered_requeued", "attempt_count": 1, "detail": nsq._ABANDONED_RUN_DETAIL},
            {"timestamp": "2026-07-29T10:01:00+00:00", "task_id": "t2", "event": "recovered_failed", "attempt_count": 2, "detail": nsq._ABANDONED_RUN_DETAIL},
        ]

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("recovered from an abandoned run, requeued", report)
        self.assertIn("recovered from an abandoned run, permanently failed", report)
        self.assertIn("abandoned", report)

    def test_report_with_no_successful_runs(self):
        queue_data = {
            "tasks": [
                _task("t1", status="failed", attempt_count=2, max_attempts=2),
                _task("t2", status="pending", attempt_count=1, max_attempts=2),
            ]
        }
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "failed_permanently", "attempt_count": 2, "detail": "failed"},
        ]

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("Done: 0", report)
        self.assertNotIn("Traceback", report)

    def test_report_with_no_runs_at_all(self):
        queue_data = {"tasks": []}

        report = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=[],
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertIn("Total tasks: 0", report)
        self.assertIn("No runs recorded yet.", report)
        self.assertIn("(none)", report)

    def test_malformed_run_log_lines_are_handled_explicitly(self):
        run_log_path = os.path.join(self.tmpdir, "run_log.jsonl")
        with open(run_log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "done", "attempt_count": 1, "detail": None}) + "\n")
            f.write("{this is not valid json\n")
            f.write("also not json\n")

        events, malformed_count = nsq._load_run_log_events(run_log_path)

        self.assertEqual(len(events), 1)
        self.assertEqual(malformed_count, 2)

        report = nsq.render_report(
            queue_data={"tasks": []}, queue_error=None, log_events=events,
            generated_at="2026-07-29T11:00:00+00:00", malformed_log_line_count=malformed_count,
        )
        self.assertIn("2 malformed run-log line(s)", report)

    def test_report_output_is_deterministic_for_identical_input(self):
        queue_data = {
            "tasks": [
                _task("t1", status="done", attempt_count=1),
                _task("t2", status="pending", attempt_count=0),
            ]
        }
        log_events = [
            {"timestamp": "2026-07-29T10:00:00+00:00", "task_id": "t1", "event": "done", "attempt_count": 1, "detail": None},
        ]

        report_1 = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )
        report_2 = nsq.render_report(
            queue_data=queue_data, queue_error=None, log_events=log_events,
            generated_at="2026-07-29T11:00:00+00:00",
        )

        self.assertEqual(report_1, report_2)

    def test_report_generation_has_no_model_or_network_or_subprocess_dependency(self):
        source = "".join(
            inspect.getsource(fn)
            for fn in (
                nsq.render_report,
                nsq.write_report,
                nsq._load_run_log_events,
                nsq._atomic_write_text,
            )
        )
        lowered = source.lower()
        for term in ("subprocess", "socket", "urllib", "requests", "claude", "http.client"):
            self.assertNotIn(term, lowered, f"report generation must not reference {term!r}")

    def test_write_report_reflects_a_real_crash_recovery_end_to_end(self):
        run_log_path = os.path.join(self.tmpdir, "run_log.jsonl")
        _write_raw_queue(
            self.queue_path,
            [_task("t1", attempt_count=0, max_attempts=2, working_dir=self.tmpdir)],
        )

        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid(), run_log_path=run_log_path)
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)
        complete = nsq.complete_task(
            self.queue_path, "t1", owner_pid=os.getpid(), run_log_path=run_log_path
        )
        self.assertEqual(complete.outcome, nsq.TransitionOutcome.DONE)

        report_dir = os.path.join(self.tmpdir, "reports")
        fixed_now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
        report_path = nsq.write_report(
            self.queue_path, report_dir, run_log_path=run_log_path, now=fixed_now
        )

        self.assertEqual(os.path.basename(report_path), "2026-07-29.md")
        with open(report_path, encoding="utf-8") as f:
            report_text = f.read()
        self.assertIn("Done: 1", report_text)
        self.assertIn("`t1`", report_text)
        self.assertIn("done", report_text)

    def test_write_report_is_useful_when_queue_does_not_exist_yet(self):
        missing_queue_path = os.path.join(self.tmpdir, "does-not-exist.json")
        report_dir = os.path.join(self.tmpdir, "reports")

        report_path = nsq.write_report(missing_queue_path, report_dir)

        with open(report_path, encoding="utf-8") as f:
            report_text = f.read()
        self.assertIn("Queue unreadable", report_text)
        self.assertIn("No runs recorded yet.", report_text)

    # -- Milestone 7A: deterministic task-policy security boundary ---------------

    def test_forbidden_command_never_spawns(self):
        marker_path = os.path.join(self.tmpdir, "should-not-exist.txt")
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=["bash", "-c", f"touch {marker_path}"],
        )

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)
        self.assertFalse(
            os.path.exists(marker_path),
            "a rejected command's argv must never actually execute, even indirectly",
        )

    def test_policy_rejection_is_captured_in_evidence(self):
        task = _task("t1", working_dir=self.tmpdir, executor_command=["sudo", "ls"])

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)
        self.assertIsNotNone(result.message)
        self.assertIn("sudo", result.message.lower())

    def test_policy_rejection_enters_the_retry_flow(self):
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=2,
                    working_dir=self.tmpdir,
                    executor_command=["sudo", "ls"],
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(run.executor.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)
        self.assertEqual(
            run.acceptance_run.transition.outcome,
            nsq.TransitionOutcome.REQUEUED,
            "a policy-rejected executor must drive the normal retry flow, not DONE",
        )
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "pending")

    def test_policy_rejection_at_retry_limit_permanently_fails(self):
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    attempt_count=0,
                    max_attempts=1,
                    working_dir=self.tmpdir,
                    executor_command=["sudo", "ls"],
                )
            ],
        )
        claim = nsq.claim_next(self.queue_path, claimant_pid=os.getpid())
        self.assertEqual(claim.outcome, nsq.ClaimOutcome.CLAIMED)

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertEqual(run.acceptance_run.transition.outcome, nsq.TransitionOutcome.FAILED_PERMANENTLY)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "failed")

    def test_a_trivially_passing_acceptance_command_does_not_mask_a_policy_rejected_executor(self):
        # This is the exact gap found and fixed while implementing this
        # milestone: without the run_task() fix, this scenario would mark
        # the task "done" because the acceptance command (unrelated to the
        # executor) always passes on its own.
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    "t1",
                    working_dir=self.tmpdir,
                    executor_command=["sudo", "ls"],
                    acceptance_command=[sys.executable, "-c", "pass"],
                )
            ],
        )
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        run = nsq.run_task(self.queue_path, "t1", owner_pid=os.getpid())

        self.assertNotEqual(run.acceptance_run.transition.outcome, nsq.TransitionOutcome.DONE)

    def test_environment_allowlist_is_actually_applied_to_a_real_child_process(self):
        marker_path = os.path.join(self.tmpdir, "env_dump.txt")
        task = _task(
            "t1",
            working_dir=self.tmpdir,
            executor_command=[
                sys.executable,
                "-c",
                (
                    "import os, json; "
                    f"open({marker_path!r}, 'w').write(json.dumps(dict(os.environ)))"
                ),
            ],
        )
        os.environ["NIGHTSHIFT_TEST_SECRET_KEY"] = "must-not-appear-in-child"
        try:
            result = nsq.run_executor(task)
        finally:
            del os.environ["NIGHTSHIFT_TEST_SECRET_KEY"]

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        with open(marker_path, encoding="utf-8") as f:
            child_env = json.loads(f.read())
        self.assertNotIn("NIGHTSHIFT_TEST_SECRET_KEY", child_env)
        self.assertIn("PATH", child_env)

    def test_working_directory_outside_approved_root_is_rejected_end_to_end(self):
        outside_dir = tempfile.mkdtemp(prefix="nightshift-outside-root-")
        try:
            task = _task(
                "t1",
                working_dir=outside_dir,
                approved_root=self.tmpdir,
            )
            result = nsq.run_executor(task)
            self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)
            self.assertIn("approved_root", result.message)
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_malformed_policy_relevant_fields_fail_closed(self):
        task = _task("t1", working_dir=self.tmpdir, approved_root="/path/does/not/exist")

        result = nsq.run_executor(task)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)

    # -- 10. Repository isolation ------------------------------------------------

    def test_state_lives_only_in_a_temporary_directory_outside_the_repository(self):
        self.assertTrue(self.tmpdir.startswith(tempfile.gettempdir()))
        self.assertFalse(
            os.path.abspath(self.tmpdir).startswith(REPO_ROOT),
            "test state must never live inside the repository tree",
        )
        _write_raw_queue(self.queue_path, [_task("t1")])
        nsq.claim_next(self.queue_path, stale_threshold_seconds=300)

        repo_runtime_dir = os.path.join(REPO_ROOT, "nightshift", "runtime")
        repo_tests_dir = os.path.join(REPO_ROOT, "nightshift", "tests")
        stray_files = [
            name
            for name in os.listdir(repo_runtime_dir) + os.listdir(repo_tests_dir)
            if name.endswith((".json", ".lock"))
        ]
        self.assertEqual(stray_files, [], "no queue/lock artifacts may leak into the source tree")


if __name__ == "__main__":
    unittest.main()
