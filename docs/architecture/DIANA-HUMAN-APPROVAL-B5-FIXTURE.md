# DO NOT MERGE — B5 TEST FIXTURE

**Merging this file into `main` is prohibited.** It is a measurement instrument
for Track B Phase B5, not content. A previous instance of this fixture was
merged by accident (recorded as the B5 incident in
`DIANA-HUMAN-APPROVAL-B5.md`); this one exists to complete the measurement that
incident interrupted.

## What is being measured

Only the part of the approval lifecycle that requires a pull request to stay
**open** across an approval:

| step | who | expected |
|---|---|---|
| 1 | `AnastasiaAurelia` approves | the pull request becomes mergeable — **and must not be merged** |
| 2 | `DIANA-AGENT` pushes a **diff-affecting** commit | **B-AC-4** — the standing approval is dismissed |
| 3 | — | the pull request is blocked again |
| 4 | `AnastasiaAurelia` approves again | **B-AC-5** — eligibility is restored |
| 5 | `DIANA-AGENT` closes it **unmerged** | — |

`B-AC-3` is already settled and is **not** re-measured here.

## Why merging it would be wrong

Merging destroys the instrument: every remaining measurement needs the pull
request to still be open. It would also put a meaningless file on `main` for a
second time.

---

MEASUREMENT MARKER: after-diff-affecting-push
