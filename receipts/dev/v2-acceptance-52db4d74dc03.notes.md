# Operator notes for `v2-acceptance-52db4d74dc03`

Kept outside the sealed directory (commitment `b7e6a212…`), which is exactly
as the receipt wrote it.

## Reconciliation: no discrepancy found

Checked independently of the receipt's own gates:

- The seal verifies (`sha256sum -c`), every file is sealed and none is extra.
- The receipt, the campaign report and the tested-bytes record all name
  `52db4d7`, with git tree `a6c47271…` and package digest `5df34b89…`. That is
  the tree recorded during and after the pre-commit suite run, so the
  committed bytes are the tested bytes.
- Campaign: 76/76 PASS, BOUND; its report hash equals the receipt's record.
- pytest: exit 0, 294 passed. The per-test index equals pytest's own summary,
  including the 38 parametrized ids that contain spaces (the D-1 defect of
  the `3c6fad1` receipt, fixed here; that receipt and D-1 are unchanged).
- The candidate's four acceptance conditions hold:
  1. corpus accounting reconciles per kind: 4,237 deliveries, 1,719 distinct
     states, with the deduplication rule recorded;
  2. the refused-snapshot and incomplete-but-valid negative controls ran and
     passed;
  3. the receipt's parser index matches pytest's summary;
  4. the tested-bytes record matches.

## Runs that are not evidence for this revision

| Record | Where | Status |
|---|---|---|
| Pre-commit suite run of an earlier state of these changes, stopped by the session's task control after 85 tests (0 failed) | `historical-interrupted-suite-run.txt` inside the sealed receipt | INCONCLUSIVE, interrupted; its raw log was not retained (deleted by the operator when restarting) |
| Receipt launch with empty paths (a shell-scoping error in the launch line) | `aborted-52db4d74dc03-misconfigured-launch/` | ABORTED by the operator after three sections; no tests run, never sealed |

## Timing

The campaign ran from 15:23:20 to 15:45:37 UTC and the receipt from 15:24:03 to
15:50:34 UTC, both detached from the session, by this machine's clock. Both
wrote their exit codes (0) before the session's worker and the machine were
restarted, at about 19:20 UTC. Nothing ran after the restart except this
reconciliation, which only reads. The publication time is the server-side
creation time of the CI run for the commit that adds these files.
