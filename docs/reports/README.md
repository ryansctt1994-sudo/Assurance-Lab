# Reports, by the commit each is bound to

Every file here is a snapshot-bound campaign report, kept byte-identical to
what the campaign wrote. A report is evidence only for the commit in its
own provenance section, never for a later one. `docs/RESULTS.md` is a copy
of the current development-lineage report.

| File | Bound to | Obligations | Lineage | Status |
|---|---|---|---|---|
| `main-c175fbd.md` | `c175fbd` | 32 | **published main baseline** | historical; author-reported; the subject of the R1 protocol (`assurance-validation/`). Byte-identical to `ba10a0c:docs/RESULTS.md` |
| `dev-748f80a.md` | `748f80a` | 67 | development (PR #2) | historical; byte-identical to `b535b1a:docs/RESULTS.md`; publishes matrix v1 |
| `dev-5a925e5-before-change.md` | `5a925e5` | 70 | development (PR #2) | **before-change receipt**: the `0fe2c2b` code, before the snapshot-integrity correction and matrix v2. Not evidence for v2 |
| `dev-3c6fad143994.md` | `3c6fad1` | 76 | development (PR #2) | historical, superseded by `52db4d7`: development candidate (snapshot integrity, matrix v2). Executed 14:27 to 14:54 UTC on 2026-11-08 (this machine's clock); published in `8b0e515` (GitHub created its CI run at 14:54:59Z). Acceptance receipt `receipts/dev/v2-acceptance-3c6fad143994/`: sealed, 12/12 gates, discrepancy D-1 classified beside it. Two closure items open there; no promotion |
| `dev-52db4d74dc03.md` | `52db4d7` | 76 | development (PR #2) | **current**: development candidate closing `3c6fad1`'s two items (the replica checker records a refused snapshot as a failure; valid-state corpus over the declared alphabet). Executed 15:23 to 15:51 UTC on 2026-11-08 (this machine's clock); published in `faafa74` (GitHub created its CI run at 19:22:27Z). Acceptance receipt `receipts/dev/v2-acceptance-52db4d74dc03/`: sealed, 13/13 gates, reconciled with no discrepancy (`.notes.md` beside it, with the interrupted and aborted runs that are not evidence). No promotion |

The main baseline's code has the `head()` defect that the development
lineage repaired (see `docs/METAMORPHIC.md`); none of the development
reports is evidence about `main`.
