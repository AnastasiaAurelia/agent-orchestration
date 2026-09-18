#!/usr/bin/env python3
"""Diana M6: the run lease, as used by the multi-actor executor.

M6-D11 promises at most one live actor per run. Audit finding M6-A4 established
that with an exclusive `flock` taken here, in `actors.execute`.

**That was not enough, and this module no longer pretends otherwise.**
Independent-review finding R-2 measured a peer bypassing this entry point
entirely -- calling M5's still-public `unattended.execute` -- and reconciling a
run that was durably `TURN_ACTIVE` while its owner was alive. An exclusion taken
at one entry point is an exclusion between callers that take it, and nothing more.

So the lease now lives at the EFFECTS rather than at this entry point:
`diana/runtime/runlease.py` holds the primitive, and `discharge_obligation` and
`run_attempt` check it as their first statement (M6-ERRATA-002, M6-E2-D2). This
module is what M6's executor uses to ACQUIRE the lease; it is deliberately a thin
caller rather than a second implementation, so the two can never disagree about
what holding a lease means.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import runlease as _runlease  # noqa: E402

LOCK_NAME = _runlease.LOCK_NAME
probe = _runlease.probe
require_lease = _runlease.require_lease


class RunLock(_runlease.RunLease):
    """The name `actors.execute` uses. Behavior is `RunLease`'s, unmodified."""
