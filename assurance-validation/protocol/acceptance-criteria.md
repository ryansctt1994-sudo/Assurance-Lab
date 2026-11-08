# Acceptance criteria (frozen before any reproduction)

Subject: `protocol/claim-subject-manifest.json` (the 32-obligation baseline
generated from `c175fbd`). Counting rules: `protocol/discrepancy-rubric.md`.
Both files, and the two scripts, are frozen by
`protocol/preregistration-receipt.json`; a test fails if any of them changes
without the receipt being re-issued, which history then shows.

## Outcomes

Every reproduction gets exactly one outcome, decided by `reconcile.py` from
the sealed evidence directory alone. Checks run in this order, and the first
failing stage decides. **The expected values are read only after stages 1
and 2 pass**, so a receipt that fails them is judged without the targets
ever being loaded.

1. **INVALID EVIDENCE** (integrity): the evidence cannot be trusted as a
   record of a run. Any of:
   - a file listed in `output-hashes.sha256` is missing or differs, the hash
     file is malformed, or `environment.json` / `commands.json` / any log or
     report that `commands.json` names is not sealed;
   - `pre-reveal-commitment.json` does not commit to `output-hashes.sha256`,
     or the published commitment differs from it.
2. **INVALID EVIDENCE** (identity): the evidence is not about this subject
   under this protocol. Any of:
   - it was made against a different claim-subject manifest, or by a harness
     whose digest is not the preregistered `reproduce.py`;
   - the commitment and the environment record name different evidence ids,
     or the commitment misdescribes the evidence it seals (label, operator
     declaration, subject commit or tree, source digest, report digest);
   - `commands.json` does not record exactly the frozen commands (role,
     argv, declared report), in order;
   - the checked-out commit, full tree or `assurance_lab/` tree is not the
     subject's; the working tree was not clean (or its status wasn't
     recorded); the harness's own recomputation of the executed-source
     SHA-256 is not the subject's.

   A harness error recorded before the commands completed (for example a
   failed clone) is **INCONCLUSIVE**, provided the sealed record itself
   passes integrity and its manifest and harness identity.
3. **INCONCLUSIVE** (decidability): the evidence is genuine but cannot
   decide the claims. Any of: the setup command failed; the full campaign
   produced no report; the report is not `BOUND`, or is bound to another
   commit or executed-source digest; after reveal, the targets read from git
   are not the preregistered ones (`expected_targets_sha256`, a custodian
   error); the report omits a subject claim.
4. **MISMATCH** (comparison): conclusive evidence in which any subject
   claim's verdict label differs from the baseline, the report states an
   extra or repeated claim, or a subject command (test suite, quick
   campaign, full campaign) exited nonzero. Each differing claim is reported
   with a field-level diff (verdict, method, scope, result).
5. **MATCH**: conclusive evidence in which all 32 claims appear with labels
   identical to the baseline, there are no extra claims, and every command
   exited 0.

Run-dependent metadata never counts: timestamps, durations, interpreter
version, run and receipt ids, worker count, start method.

## What each outcome permits

| Evidence | Permits |
|---|---|
| `R1` MATCH, anchor verified, declarations accepted | G1: "The bounded baseline (32 obligations, `c175fbd`) was independently reproduced by *operator* on *environment*." Nothing about G2 (another engine), G3 (requirements), G4 (adversarial evidence) or deployment |
| `R1` MISMATCH or INCONCLUSIVE | the outcome is retained as is and adjudicated under the rubric; never replaced by a later run |
| `R0-*` (author or author-controlled CI), any outcome | harness validation only; **never** counts toward G1 |

## G1 checklist (all required)

1. The operator had no role in implementing or approving the baseline
   (operator declaration in `environment.json`).
2. The operator declares their prior exposure to the expected values.
   Because `docs/RESULTS.md` is public, blindness cannot be proven; the
   declaration and the absence of target-based debugging are what is relied
   on.
3. `pre-reveal-commitment.json` (or its `commitment` value) was published
   to an independently timestamped location (for example a comment on a
   GitHub issue, or a commit pushed to the operator's own repository)
   **before** `reconcile.py` was run, and the location is recorded in
   `timestamp-anchor.json`.
4. `reconcile.py <dir> --published <commitment>` returns **MATCH**.
5. A second party verifies that the anchor's server-side time precedes the
   reconciliation. `reconcile.py` cannot check anchors offline and says so.
6. Every initial outcome is retained. A re-run gets a new evidence id and
   is reported alongside the first, never instead of it.
