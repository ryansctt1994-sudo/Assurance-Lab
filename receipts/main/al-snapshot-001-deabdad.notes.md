# AL-SNAPSHOT-001 receipt, notes

This file interprets the sealed receipt in
[`al-snapshot-001-deabdad/`](al-snapshot-001-deabdad/). It is not part of
the seal. Its numbers come from `receipt.json` and `scope/scope-summary.md`,
which are sealed.

**Status:** REPAIR VERIFIED BY THE AUTHOR · UNSIGNED · NOT INDEPENDENTLY
REPRODUCED · NOT MERGED · NO PROMOTION.

## Subject

| | Commit | Tree | Role |
|---|---|---|---|
| Repair | `deabdad605d3` | `114fab71` | the code under test (PR #3) |
| Base | `ba10a0c3d58e` | `d4f189ee` | `main`, the merge of PR #1. Its `assurance_lab/` tree (`9301006`) is byte-identical to the published baseline `c175fbd`'s |
| First failing regression | `dbdbed76d6d0` | `67e61c96` | the regression committed alone, before any fix |

The commit that adds this receipt is a publication commit. It adds files under
`receipts/main/` only, so the tested code is still `deabdad`'s.

## What the receipt shows (12/12 gates, computed from the sealed files)

| Gate | Result |
|---|---|
| G1 | Campaign at `deabdad`: exit 0, BOUND to the commit, import origins verified, source unchanged, 32/32 PASS |
| G2 | Its 32 claims and its residual-risk section are identical to the published baseline report (`ba10a0c:docs/RESULTS.md`), recomputed by the assembler |
| G3 | The regression as first committed fails on its own code: 4 failed, 1 passed, at `replica.py:221` and `:245` (both published `KeyError` sites) |
| G4 | The repair's 8 tests on the published code: 7 fail, 1 passes (genuine snapshots are accepted there too) |
| G5 | The same 8 tests on the repair: 8 pass |
| G6 | Test inventory: 126 tests at the base, 134 at the repair: exactly the 8 new ones added, none removed or renamed |
| G7 | Full suite in a fresh clone at `deabdad`: 134 passed, exit 0; the clone was clean and at the commit's tree before and after |
| G8 | Every test run's tested tree equals its expected tree, before and after the run |
| G9 | CE-1 and CE-2: on the published code `head()` raises exactly the recorded `KeyError`s; on the repair, restoring them is refused (INVALID_SNAPSHOT), and with validation bypassed `head()` answers UNKNOWN |
| G10 | Over all 897 damaged snapshots, `head()` on the repair raises nowhere |
| G11 | All 164 genuine snapshots restore on the repair, with heads equal to a live replica's |
| G12 | The scope measurement is byte-identical across three runs (isolated; two hash seeds) |

Thirteen negative controls (`controls/assembler-negative-controls.txt`) each
damage one input and require the assembler to exit 1 with the intended gate
failing. All 13 were caught.

Three model-checker tests fail on the published code only because the
outcomes they patch (`UNKNOWN`, the reference check) do not exist there. They
are controls for the repair, not reproductions of a second defect on `main`.

## What it does not show: the repair's scope, and a second finding

The repair is narrow by design. It refuses snapshots whose records reference
records that are not admitted, which is the condition behind both `KeyError`
sites. On such states `head()` answers UNKNOWN. It is **not** a
snapshot-integrity check.

The measurement replays 897 damaged snapshots from the development lineage's
13 damage classes against the published code, the repair, and the
development lineage (`52db4d7`). For each damaged snapshot it compares the
result with the head of the genuine snapshot it was derived from:

| Outcome | Published `main` | Repair | Dev lineage |
|---|---|---|---|
| `head()` raises `KeyError` | 53 | **0** | 0 |
| refused at restore (INVALID_SNAPSHOT) | 0 | 61 | 810 |
| accepted; head differs from the genuine snapshot's | 154 | 154 | 5 |
| accepted; same head now, different after further deliveries | 175 | 171 | 0 |
| accepted; same head now and after further deliveries | 351 | 347 | 82 |
| restore raises `ValueError` (truncated snapshot) | 164 | 164 | 0 |

All 61 refusals fall in the "drop one admitted record" class, where the
published code crashed on 53 snapshots. In every other class the repair's
outcomes are identical to the published code's (`scope/scope-summary.md`).

**AL-SNAPSHOT-002 (recorded here, not fixed in this PR).** On `main`, with or
without this repair, `thaw` accepts self-contradictory snapshots and `head()`
then derives a different authority answer. That is 149 snapshots in classes a
snapshot-local check can refuse: dropped invalidations, edges, resolutions or
admitted records, and injected backward edges or self-authorized
resolutions. The development lineage refuses all of them. The other 5 of the
154 are rolled-back invalidations, which no snapshot-local check can refuse;
they need authenticated snapshots on every lineage. Truncated snapshots fail
closed, but with a bare `ValueError` instead of INVALID_SNAPSHOT.

Exposure today is latent. On this branch `thaw` is called only by the replica
model checker, on snapshots it has just produced with `freeze()`. A damaged
snapshot reaches `thaw` only once snapshots are stored or transferred.
AL-RUNTIME-001 will do exactly that, so the full check must be in place
first. Porting the development lineage's integrity check would widen this PR
beyond the scope authorized for it; it is left to the owner.

## Runs that are not evidence

- `invalid-runs/full-suite-in-archive-export.*`: the full suite in a
  `git archive` export gave 129 passed, 5 errors. The export has no git
  repository, so the five provenance tests that need one error at setup
  (`git read-tree HEAD`, exit 128). This is a defect of the runner, not a
  result about the code. G7 supersedes it, and the files are kept unchanged.
- `historical/first-failing-regression-2026-11-09.*`: the first run of the
  failing regression. It did not record its tested tree; G3 re-runs it with
  the tree recorded.

## Custody

- **Independence:** none. Every run was made by the author's agent in the
  author's container.
- **Signing:** UNSIGNED. The bytes to sign are exactly
  `output-hashes.sha256`, whose SHA-256 is the commitment
  `dd46d1f2474d33261677b70191b1afc64eb4eddbe7186b9652f2a2e1c5f91985`. With
  a dedicated Ed25519 key held outside this repository:

  ```
  ssh-keygen -Y sign -f <key> -n assurance-lab-receipt output-hashes.sha256
  ssh-keygen -Y verify -f <allowed_signers> -I <signer identity> -n assurance-lab-receipt \
      -s output-hashes.sha256.sig < output-hashes.sha256
  ```

- **Time:** execution times are this machine's clock (2026-11-10 UTC):
  campaign from 23:23:07, test runs 23:37 to 23:41, scope runs and assembly
  after that. There is no trusted timestamp. Publication time is when GitHub
  received the push; its CI run's `created_at` is recorded in PR #3, not
  here.
- **Check the seal:** `cd al-snapshot-001-deabdad && sha256sum -c output-hashes.sha256`.
