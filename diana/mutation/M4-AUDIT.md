# M4 Audit Record — Bounded Write + Shell + Tests

Spec: [`HERMES-RUNTIME-M4.md`](../../docs/architecture/HERMES-RUNTIME-M4.md).
Branch: `feature/hermes-bounded-mutation`, based on accepted M1+M2+M3 `main` (`6753996`).

This file is M4's audit evidence. It records a **process deviation** and the defects the
audit found. It does not modify, extend, reinterpret, or soften the frozen specification.

---

## 1. Process deviation — stated, not corrected

The roadmap requires five cycle steps per milestone. M4's history differs from M1–M3 in
exactly one respect, and that difference is recorded here rather than papered over:

- `HERMES-RUNTIME-M4.md` **was** written and treated as frozen before implementation
  began. Filesystem evidence: the spec's mtime is `2026-09-14 03:43:01`, and the first
  implementation module (`mutation_policy.py`) is `03:44:19` — the spec predates all code.
- **Unlike M1–M3, no separate git freeze commit was created before implementation.** The
  spec and the implementation therefore enter git history in the *same* commit.
- The implementing session was interrupted (see §4) **before any M4 commit existed**, so
  at resume time nothing M4-related was in git at all.

**No retroactive freeze commit was fabricated to make this history look like M1–M3's.**
Doing so would have manufactured evidence of a control that was not actually exercised.
The consequence is stated plainly: for M4, and only M4, "the spec was frozen before
implementation" rests on filesystem mtimes and the `.gitignore` allowlist history rather
than on a git commit boundary. That is weaker evidence than M1–M3 have, and it is the
reason this section exists.

This is a process deviation to be carried forward as a known weakness, **not** a licence
to rewrite history.

## 2. Nature of this audit — also stated plainly

The roadmap's standard is review "as if by someone who did not write it". This audit was
performed in a **fresh session with no implementation context**, reading the pinned Hermes
source directly rather than the M4 code's own claims about it. That is a meaningful
distance from the implementation, and it was sufficient to find two real defects.

It is **not** an independent third-party audit, and is not claimed as one. A reviewer who
did not write any part of M4 has still not examined it.

## 3. Findings

Both were proven empirically against pinned `hermes-agent 0.21.1` @ `b8e8639`, not argued
from documentation. Both are fixed, and both now carry acceptance assertions so they
cannot silently regress.

### F-A1 — `extract_paths` missed a path channel in V4A mode (M4-D7 violation)

`patch_tool` (`tools/file_tools.py`) executes `_paths_to_check = [path] if path else []`
**before** it branches on `mode`. A `mode="patch"` call may therefore carry a `path`
argument that Hermes still resolves, even though V4A mode's documented shape does not use
it. The extractor read only the V4A body, so:

```
{mode: "patch", patch: <in-scope V4A body>, path: "/etc/passwd"}  ->  Verdict(allow)
```

M4-D7's title is literally "Every path a call could touch is extracted, not just
`args["path"]`". This was that decision's mirror image: a path the call could touch,
missed because it arrived through an argument the mode was assumed not to use.

**Fix:** V4A mode now also extracts `args["path"]` when present, and a non-string `path`
makes the call uninterpretable (M4-D8 fail-closed).
**Locked by:** `M4-AC-4 a V4A call ALSO carrying 'path' has that path extracted too`,
`M4-AC-5 a V4A call carrying a non-string 'path' is uninterpretable`.

### F-A2 — the timeout ceiling was reachable only if the model opted in (M4-D11 violation)

`decide_command` guarded the ceiling under `if timeout is not None:`. Hermes resolves
`effective_timeout = timeout or config["timeout"]`, where `config["timeout"]` is
`_parse_env_var("TERMINAL_TIMEOUT", "180")`. An **omitted** `timeout` therefore ran the
command on *Hermes's* 180s bound, wholly independent of anything Diana declared:

```
ceiling = 5s;  {command: <allowed>, timeout: 99999}  ->  Verdict(deny)   # enforced
ceiling = 5s;  {command: <allowed>}                  ->  Verdict(allow)  # 180s, silently
```

M4-D11 exists so "the run's bound is never quietly different from the one requested".
An absent `workdir` is safe to default because the session cwd is still scope-checked; an
absent `timeout` is not analogous, because the default comes from Hermes's environment.

**This defect was live, not theoretical.** The two end-to-end turns differ exactly here:

```
before fix  attempted=[terminal, search_files, read_file, read_file, patch, terminal]             refused=[]
after fix   attempted=[terminal, search_files, terminal, read_file, read_file, patch, terminal]   refused=[terminal]
```

The real model's first `terminal` call omitted `timeout` and was silently granted 180s.
Post-fix it is refused, the model retried with an explicit bound, and the run still fixed
the planted defect — so the control holds without making the milestone's thesis unachievable.

**Fix:** an omitted `timeout` is refused, so the declared ceiling is the actual bound.
**Locked by:** `M4-AC-11 an OMITTED timeout is refused, not silently given Hermes's 180s default`,
`M4-AC-11 the ceiling is unreachable by omission at policy level`.

### Audit checks that found nothing

- The V4A header regexes faithfully mirror Hermes's `_V4A_SINGLE_HEADER_RE` /
  `_V4A_MOVE_HEADER_RE` — same leniency after `***`, same case sensitivity, both `Move`
  endpoints captured. Group numbering differs (non-capturing prefix); semantics match.
- M1's confinement choke point is installed unchanged, as F4 claimed.
- Exact-match command membership has no normalization path, as M4-D9 requires.

## 4. Interruption and cleanup forensics

The implementing session was killed during an orphan-process cleanup. Determined at resume:

- `/proc/vmstat` `oom_kill 0`; no `out of memory` / `oom-kill` / `Killed process` in
  `/var/log/kern.log`; `systemd-oomd` logged no kill actions; memory was not tight.
- **The kill came from the cleanup command, not the OOM killer.** Claude Code spawns MCP
  servers into **its own process group**: `claude` was PID/PGID `646027`, and its
  `playwright-mcp` chain reported the same PGID. Killing an orphan's reported PGID
  therefore kills Claude itself.

No M4 file was lost or truncated. At resume no Playwright/Chromium process remained, so
the resumption killed nothing.

## 5. Verdict

Pending: post-commit re-run, required because `M4-REG-2` and `M4-REG-3` evaluate
`6753996..HEAD` and were **vacuous** while no M4 commit existed. Their verdict is only
evidence once that diff is non-empty. Recorded in §6 below once established.
