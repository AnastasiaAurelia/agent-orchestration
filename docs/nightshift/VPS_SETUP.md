# Nightshift VPS Isolation Setup (Milestone 7B1)

**Status: not executed.** Every command in this document is for a human to
run manually, later, on the actual VPS, after reading and understanding
what it does. Nothing in this milestone runs any of these commands. There
is no automation here and none is implied.

This is the human-run half of the isolation contract that
`nightshift/runtime/isolation.py` validates the path-level half of. Read
that module's docstring first — it is explicit that path/overlap checks
from inside one Python process are not, and cannot be, a substitute for the
actual OS-level separation these commands create.

Replace `RESEARCHLENS_PATH` below with the real absolute path to the
production ResearchLens checkout/deployment on your VPS before running
anything. Do not run any command in this document against a host you have
not confirmed is the intended VPS.

## 1. Create the dedicated `nightshift` Linux user

```bash
sudo adduser --disabled-password --gecos "Diana Nightshift" --home /home/nightshift nightshift
```

`--disabled-password` means no password login is possible for this
account — access should be via `sudo -u nightshift ...` from an
already-authenticated admin session, or a dedicated SSH key added
separately for this user only (not the admin's own key, not an agent-
forwarded key — see the adversarial review's note on `SSH_AUTH_SOCK`).

Confirm the user was created with its own home, separate from any existing
account that runs ResearchLens:

```bash
getent passwd nightshift
```

## 2. Create the dedicated workspace root and state directories

```bash
sudo -u nightshift mkdir -p /home/nightshift/workspace
sudo -u nightshift mkdir -p /home/nightshift/state
sudo -u nightshift mkdir -p /home/nightshift/logs
sudo -u nightshift mkdir -p /home/nightshift/reports
```

## 3. Set ownership and restrictive permissions

```bash
sudo chown -R nightshift:nightshift /home/nightshift
sudo chmod 700 /home/nightshift
sudo chmod 700 /home/nightshift/workspace
sudo chmod 700 /home/nightshift/state
sudo chmod 700 /home/nightshift/logs
sudo chmod 700 /home/nightshift/reports
```

`700` (owner read/write/execute, nothing for group or other) means no
other Linux account on the box — including whatever account runs
ResearchLens — can read, list, or write into any of these directories,
regardless of what any application-level path check claims.

## 4. Verify the `nightshift` user cannot read ResearchLens secrets

Run each of these and confirm they fail with `Permission denied` (or
`No such file or directory` if the path itself is already inaccessible to
enumerate) — a `0` exit code or visible file contents here means the
isolation is not actually in place yet, and nothing in this milestone's
code can detect that from the outside:

```bash
sudo -u nightshift cat RESEARCHLENS_PATH/.env
sudo -u nightshift ls RESEARCHLENS_PATH
sudo -u nightshift cat ~researchlens/.aws/credentials 2>&1
sudo -u nightshift cat ~researchlens/.ssh/id_rsa 2>&1
sudo -u nightshift env | grep -E 'TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL|DATABASE_URL'
```

The last command should print nothing — the `nightshift` account's own
login environment should never have been given production credentials in
the first place. If it prints anything, stop and fix the account's
environment before proceeding; do not rely on this codebase's environment
allowlist (`nightshift/runtime/policy.py`) to clean up a credential that
should never have been reachable by this user at all.

Also confirm the reverse is blocked where applicable — that the account
running ResearchLens cannot read into the nightshift workspace:

```bash
sudo -u researchlens cat /home/nightshift/state/queue.json 2>&1
```

## 5. Clone or copy the branch into the isolated workspace

Run this as the `nightshift` user, into the `nightshift` workspace only —
never into or alongside the ResearchLens checkout:

```bash
sudo -u nightshift git clone <this-repository-url> /home/nightshift/workspace/agent-orchestration
sudo -u nightshift git -C /home/nightshift/workspace/agent-orchestration checkout feature/nightshift-capability
```

If cloning over HTTPS with credentials, use a token scoped to this
repository only, stored in a location only the `nightshift` user can read
(e.g. `git credential.helper store` writing to a file under
`/home/nightshift`, `chmod 600`) — never the admin's or another account's
existing GitHub token.

## 6. Configure the isolation contract for `nightshift/runtime/isolation.py`

Once the above is done, the deterministic check this milestone adds can be
pointed at the real paths:

```python
from nightshift.runtime import isolation

decision = isolation.validate_isolation(
    nightshift_root="/home/nightshift/workspace",
    forbidden_paths=["RESEARCHLENS_PATH"],
)
assert decision.allowed, decision.reason
```

This proves the *configured paths* don't overlap by literal path or
symlink. It does not, by itself, prove step 4's permission checks were
actually done — run step 4 for real, on the real host, and keep its output
as your own evidence that the Linux-level isolation (not just the path
check) is in place.

## What this document does not cover

- Firewall/network-level isolation between the two services.
- Resource limits (cgroups, `ulimit`) preventing one account's workload
  from starving the other's.
- Log rotation or retention policy for `/home/nightshift/logs` — set this
  up per your own operational preference; nothing in Nightshift assumes a
  particular rotation scheme yet.
- Anything related to scheduling, unattended execution, or real Claude
  invocation — those remain out of scope until a separate, explicitly
  approved milestone.
