"""Tests for the Nightshift deterministic queue -> claim -> lock primitive.

Every test operates inside a fresh tempfile.mkdtemp() directory and never
touches repository fixtures, real queues/locks, home-directory
configuration, Claude credentials, or Git state. Genuine concurrency is
exercised via real OS subprocesses (see test_concurrent_claims_...), not by
calling the module twice in sequence from one thread.
"""

from __future__ import annotations

import glob
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
):
    return {
        "id": task_id,
        "status": status,
        "title": title,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "claimed_pid": claimed_pid,
        "claimed_at": claimed_at,
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
