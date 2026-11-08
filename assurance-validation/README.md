# assurance-validation

Independent validation package for the published baseline: `docs/RESULTS.md`
on `main`, 32 obligations, generated from a snapshot of `c175fbd`.
Current classification: **AUTHOR-REPORTED · INDEPENDENT_REPRODUCTION_ABSENT**.
Nothing in this directory changes that until an independent R1 run passes the
G1 checklist in `protocol/acceptance-criteria.md`.

## For a reproducer (R1)

You need `git` and Python ≥ 3.11 with the `venv` module (on Debian and
Ubuntu: `apt install python3-venv`; otherwise pass `--no-venv` and run inside
an environment you created fresh). The two scripts are stdlib-only and import
nothing from the system under test. Read them, plus
`protocol/claim-subject-manifest.json` (which does **not** contain the
expected values), before running anything.

1. **Run** in a new, empty output directory:

   ```bash
   python assurance-validation/reproduce.py --out r1 --label R1 \
     --source https://github.com/ryansctt1994-sudo/Assurance-Lab.git \
     --operator "<name>; role in the baseline: none; prior exposure to the expected values: <...>"
   ```

   It clones the subject into a fresh directory, records the checkout and
   environment, recomputes the executed-source digest itself, runs the frozen
   commands in a fresh venv (`pip install pytest`, `pytest -q`, the quick
   campaign, the full snapshot-bound campaign: roughly 10–20 minutes), and
   seals the directory.

2. **Commit before reveal.** Check the seal with
   `sha256sum -c output-hashes.sha256` (inside `r1/`), then publish the
   `commitment` from `r1/pre-reveal-commitment.json` somewhere with an
   independent timestamp, such as a comment on a GitHub issue or a commit
   pushed to your own repository. Record where in `r1/timestamp-anchor.json`,
   for example `{"url": "...", "published_utc": "..."}`. Do not look at
   `docs/RESULTS.md` or compare numbers before this step.

3. **Reconcile** (the reveal):

   ```bash
   python assurance-validation/reconcile.py r1 --published <commitment>
   ```

   It verifies the seal and the subject identity first and only then reads
   the expected values from git. It writes `r1/reconciliation.json` with
   one outcome (MATCH, MISMATCH, INCONCLUSIVE, INVALID EVIDENCE), a
   per-claim, field-level diff, and the run's G1 status.

**Limits.** The expected values are public in git history, so this cannot
prove you never saw them. The commitment proves the sealed output existed
before the reveal step; it does not prove absence of prior knowledge. That
is why the operator declaration is required.

## Roadmap status (P0, P1)

Done means done for *this* package; nothing here substitutes for an
independent operator.

| Item | Status | Evidence |
|---|---|---|
| P0-01 commit, tree, tracked files | done | manifest `subject`; `baseline/tracked-files-c175fbd.txt`; `test_frozen_identities_are_git_facts` re-derives all of it from git, including that main's `assurance_lab/` is the subject's tree |
| P0-02 preserve report, snapshot, receipt | partial | preserved as git objects (`ba10a0c:docs/RESULTS.md`, `c175fbd`), referenced by id; no copy outside this repository |
| P0-03 frozen claim inventory | done | manifest `claims`: 32 entries (method, scope, source, evidence class, status); targets withheld |
| P0-04 discrepancy rules | done | `protocol/discrepancy-rubric.md`, preregistered |
| P0-05 outcome definitions | done | `protocol/acceptance-criteria.md`, preregistered |
| P0-06 immutable manifest, verifiable timestamp | partial | pushed to GitHub in `5a925e5` (server-side push time); a branch can be rewritten, and GitHub is not a third-party append-only log |
| P0-07 exclusions | done | manifest `excluded` |
| P1-01 independent operator | **open** | needs a person |
| P1-02 custodian package, metrics withheld | partial | metrics withheld and hash-committed, but the author prepared the package, not a separate custodian |
| P1-03 blindness limits | done | this README; acceptance criteria |
| P1-04 to P1-06 retrieve, verify, run, retain | done in the harness | `reproduce.py`; exercised by R0-SELF |
| P1-07, P1-08 publish commitment before reveal | harness ready; **open** for R1 | `pre-reveal-commitment.json` carries the evidence id, source digest, report digest and declaration |
| P1-09 reveal after verification; field-level diff | done in the reconciler | `reconcile.py`; tests prove no tampered receipt reaches the reveal |
| P1-10, P1-11 adjudication, second auditor | **open** | |

## Layout

```text
assurance-validation/
├── README.md                          this file
├── reproduce.py                       harness: clone, run, seal (stdlib only)
├── reconcile.py                       reveal and adjudicate (stdlib only)
├── protocol/
│   ├── claim-subject-manifest.json    subject, 32-claim inventory (targets withheld, hash-committed), frozen commands
│   ├── discrepancy-rubric.md          counting rules
│   ├── acceptance-criteria.md         outcomes, what each permits, G1 checklist
│   └── preregistration-receipt.json   SHA-256 of the four files above it and both scripts
├── baseline/
│   └── tracked-files-c175fbd.txt      `git ls-tree -r --long` of the subject (P0-01); no target values
└── reproduction/
    └── R0-SELF-dry-run/               author's harness validation; NOT independent
        ├── environment.json           operator declaration, checkout, recomputed source digest, interpreter, pip freeze
        ├── commands.json              argv, exit code, duration and log names for each frozen command
        ├── cmdN-<role>.stdout.log     raw stdout per command
        ├── cmdN-<role>.stderr.log     raw stderr per command
        ├── generated-results.md       full-campaign report
        ├── generated-results-quick.md quick-campaign report
        ├── output-hashes.sha256       seal: SHA-256 of every file above
        ├── pre-reveal-commitment.json SHA-256 of the seal, plus evidence id, subject, report digest, declaration
        ├── timestamp-anchor.json      where and when the commitment was published
        └── reconciliation.json        outcome and field-level diff
```

Equivalences with the roadmap's proposed layout: per-command
`cmdN-<role>.stdout.log` / `.stderr.log` files in place of a single
`stdout.log` / `stderr.log`, so that each command's output stays separate.

Not built yet (roadmap sections P2–P7): `formal/` (needs an approved way to
run SANY/TLC), `adversarial/`, and `evidence/` registers. The
preregistration's timestamp anchor is weak: GitHub's push and CI timestamps,
which are server-side but not an append-only third-party log (P0-06 remains
open).
