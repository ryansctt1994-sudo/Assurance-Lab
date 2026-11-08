# Assurance-Lab

Executable finite-state models for controlling how evidence becomes authority.

> **No mechanism may silently convert uncertainty into authority.**

Status: **research prototype**: finite-domain verified, counterexample-refined.
**Not production-certified. Not unboundedly verified.** No TLA+/TLAPS or
proof-assistant artifact exists yet; every result here is exhaustive
enumeration over a small, explicitly stated domain.

Pure Python ≥3.10, no runtime dependencies.

**Evidence class: author-reported.** No one independent of this repository
has reproduced any result here yet. [`assurance-validation/`](assurance-validation/README.md)
holds a frozen protocol for that: a fresh-clone reproduction (R1) of the
published 32-obligation baseline on `main` (`c175fbd`). The package contains
a claim inventory with the expected values withheld and hash-committed,
outcome definitions and a counting rubric fixed in advance, a stdlib-only
harness that seals its evidence, and a reconciler that reveals the targets
only after the seal verifies. The author's own runs of it (`R0-*`) validate
the harness only.

## Quick start

```bash
pip install pytest
python -m pytest -q                 # ~115 tests, ~1 min
python -m assurance_lab             # quick campaign, in-place, no report
python -m assurance_lab --full --out docs/RESULTS.md   # headline domain from a snapshot, ~6 min on 4 cores
```

| Exit | Meaning | What it guarantees |
|---|---|---|
| `0` | every obligation PASS; with `--out`: snapshot-bound, origins verified, source unchanged | results are bound to the recorded commit |
| `1` | some obligation FAIL or UNKNOWN | non-compensating gate: no PASS elsewhere offsets it |
| `2` | refused before running: dirty `assurance_lab/` tree, or snapshot integrity not established | we won't anchor a report to a tree that isn't a commit (pre-execution check) |
| `3` | provenance invalid or unbound: executed source changed, an import came from outside the executed tree, or an in-place report | the anchor did not hold, or never existed. Deliberately conservative |

## How results are bound to code

Details, limits and the demo transcript are in [`docs/PROVENANCE.md`](docs/PROVENANCE.md).

- **Reports run from a snapshot.** With `--out`, the committed
  `assurance_lab/` tree is exported with `git archive` into a fresh temp
  directory, its bytes are checked against the git objects, and the
  campaign runs there under an isolated interpreter (`-I -S -B`). Working-tree
  edits, including edit-and-revert, cannot reach the run.
- **Import origins are verified, not assumed.** The parent and every pool
  worker that ran a job report the files they imported `assurance_lab`
  from; anything outside the executed tree invalidates the report.
- A **start receipt** (commit, tree id, source SHA-256, domains, workers,
  start method, command) is captured *before* enumeration and never
  recomputed. The executed source is **re-hashed at the end** as a second,
  independent check.
- **In-place reports are rejected** (UNBOUND, exit 3).
  `scripts/demo_edit_and_revert.sh in-place` swaps in a sabotaged checker
  mid-run and reverts it: every per-line provenance check passes while 38% of
  the inductive check is silently skipped. The same demo in `snapshot` mode
  is unaffected.
- **Limit, not an immutability guarantee:** the snapshot closes the
  demonstrated working-tree attack, but a privileged process could modify and
  restore the snapshot itself mid-run, and the end re-hash would not see it.
  Read-only permissions don't bind root; the random path is isolation, not a
  security boundary.
- **Claims carry no run-dependent values.** Timing and environment sit in a
  trailing "Run metadata" section, so two reports of the same code differ
  only there.
- Induction totals are tested to be **worker-count invariant** (plain
  sequential loop vs. a 2-worker process pool) **and start-method invariant**
  (the pool forced to fork, spawn, and forkserver in turn, wherever available;
  CI runs this on Linux, so all three paths execute there).
- **Green CI ≠ full induction.** PR CI runs the tests and the quick
  campaign; the full campaign runs only on `main` or by manual dispatch.
  `docs/RESULTS.md` comes from a local `--full` run whose receipt it records.

## What is here

| Layer | Module | What it checks |
|---|---|---|
| Verdicts | `assurance_lab/verdict.py` | PASS / FAIL / UNKNOWN, scoped labels, non-compensating `authorize` |
| Core model | `core/model.py` | Admit, Supersede, Adjudicate, Diagnose, Invalidate, Retract, RoleRevise, Grant |
| Properties | `core/properties.py` | candidate inductive Inv (11 clauses), encoded Safety, 4 transition properties |
| Checker | `checker/explore.py`, `checker/induction.py` | BFS with traces; `Init⇒Inv`, `Inv∧Next⇒Inv'`, `Inv⇒Safety` over **all** Inv-states |
| Mutants | `core/mutants.py` | 20 single-guard mutations (negative controls), each shown to be caught by at least one property |
| Store seam | `store/retrieval.py` | filter-before-rank, effect-time read repair, compaction |
| Recovery | `store/recovery.py` | graph = truth / head = cache, 4 torn-write combos, monotone checkpoint vs rollback |
| Distributed | `distributed/replica.py`, `distributed/mc.py` | causal deferral, idempotence, `SUCCESSOR_CONFLICT_RESOLUTION`; replica-level model checker (concurrent origination, reordering, duplication, concurrent resolutions) |
| Model validity | `checker/coverage.py`, `checker/witnesses.py`, `distributed/metamorphic.py` | mutant × property coverage; existential reachability witnesses; metamorphic relations calibrated by negative controls (`docs/METAMORPHIC.md`) |
| Digests | `statedigest.py` | canonical state encoding; order-independent state-set digests (oracle targets) |
| TLA+ oracle | `tla/`, `tla.py` | **UNVALIDATED** spec + TLC dump parser; see `docs/ORACLE.md` |
| Admission | `admission.py` | I_2 projection view with read-set logging and receipt ρ_A |
| Reliance | `reliance.py` | use receipts, descendant closure, BoundaryCompleteness triage |

## Headline result (generated by this repo, not copied from the handoff)

From [`docs/RESULTS.md`](docs/RESULTS.md): snapshot of `748f80a`, bound and
origin-verified, partition evidence reconciled against a manifest frozen
before execution, **67/67 obligations PASS**. Core domain: 3 records, 2
principals, 2 lineages, levels 0..2, sem 0..1.

```
Reachable ⊨ all       PASS[bfs-exhaustive; 1,314,526 reachable states; 10,580,311 transitions; depth 15]
Init ⇒ Inv            PASS[finite-exhaustive-induction; 3,587,826 Inv-states; 29,053,749 transitions]
Inv ∧ Next ⇒ Inv'     PASS[same]
Inv ⇒ Safety          PASS[same]
Reach ⊆ Inv-states    PASS[all 1,314,526 reachable states produced by the generator]
Transition oracle     PASS[independent relation from the TLA+ text: identical action-labeled successors on
                           every Inv-state of 3 small domains + a 20k sample; sees 20/20 mutants]
Partitions            PASS[18 partitions: complete, disjoint, authenticated, arithmetic recomputed]
20/20 core mutants detected; every one of 16 properties kills at least one
8/8 intended behaviors reachable (existential witnesses, not liveness)
Replica model checker: 10 scenarios PASS (2 and 3 replicas), 2 replica mutants detected
Metamorphic: 5 replica relations + strict encoding round-trip hold; suite mutation score 7/7
```

The induction covers ~2.7× more states than are reachable: it also checks
Inv-satisfying states that Init can never reach. The core counts were
identical across ten separate full runs, one of them on GitHub's runner; the state-set
digests were identical across the last four. Transition and edge digests, and sorted
state digests, are recorded as further oracle targets.

The handoff reported 106,938 states for its v3 model. That model is not in
this repo; this is an independent rebuild with a different state encoding,
so the numbers are not comparable. See `docs/SPEC_MAP.md` for exactly what
is and is not modeled.

## Counterexamples found while building this repo

1. **Partial-chain eligibility (distributed).** The first replica marked a
   successor eligible once *its own* edge arrived. With `Edge(r1,r2)`
   delivered before `Edge(r0,r1)`, both r0 and r2 looked like heads. Fixed
   by requiring the full predecessor chain to be linked. Retained as
   `test_retained_partial_chain_counterexample`.
2. **Split-brain on concurrent resolutions (replica model checker).** Two
   authorities validly resolving one fork at the same time left replicas on
   different heads, because each kept the resolution it saw first; the
   dispute was also invisible, so it could never be escalated. Now a
   disagreement fails closed to CONFLICT until a higher-epoch resolution
   settles it. Retained as the `first_applied_wins` mutant.
3. **Equivalent mutants.** `self_grant` (levels 0..1), `stale_successor`
   (2 records), and the lineage mutants (1 lineage) cannot fire in those
   domains. Each mutant is now checked in the smallest domain where it is
   non-equivalent, and the equivalence is asserted, not hidden.

The handoff's three retained counterexamples are reproduced as explicit tests
(`test_handoff_failure_1/2/3_*`).
