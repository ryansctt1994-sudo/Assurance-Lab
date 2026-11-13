# Verification Results

Status: research prototype. **Not production-certified. Not unboundedly verified.**

These results come from a local run of the command below, **not from CI**.
PR CI runs the test suite and the *quick* campaign only; its `full-induction`
job is skipped on PRs by design. Green CI ≠ full induction completed.

## Provenance (start receipt, captured before enumeration)

- Command: `python -m assurance_lab --out results-quick.md`
- Commit: `c175fbd7b7eb5efdbd3a7b07bbf9d41bc998139c` (tree `9301006544f9c9b5b288a133fdc4995987d03040`)
- Execution: **snapshot**: committed tree exported with `git archive` into a fresh temp directory, run by an isolated interpreter (`-I -S -B`)
- Snapshot bytes = git objects: **yes**
- `assurance_lab/` working tree at start: **clean**
- SHA-256 over executed `assurance_lab/**/*.py` at start: `bd9443baa64e641217e24d4c80bc787b0254d7a8da82ef1b79423de661414af8`
- Same digest from git objects at the commit: `bd9443baa64e641217e24d4c80bc787b0254d7a8da82ef1b79423de661414af8`
- Import origins verified in every process that ran campaign code: the parent + 3 pool worker(s) that executed jobs (start method `fork`); outside executed tree: **none**
- Executed source re-hashed at end: **unchanged**
- Core domain: 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0
- Mutant domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Workers: 4
- Receipt id: `96c1170cafdc412c`
- Provenance verdict: **BOUND** to commit `c175fbd7b7eb`

## Claims

| Obligation | Verdict |
|---|---|
| Reachable ⊨ Inv ∧ Safety ∧ TransitionProps | `PASS[bfs-exhaustive-reachability; 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0; 102,266 reachable states, 773,447 transitions, depth 13]` |
| Init ⇒ Inv | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0; 114,164 Inv-states, 872,610 transitions]` |
| Inv ∧ Next ⇒ Inv' | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0; 114,164 Inv-states, 872,610 transitions]` |
| Inv ⇒ Safety | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0; 114,164 Inv-states, 872,610 transitions]` |
| Reach ⊆ Inv-states (BFS vs generator) | `PASS[exhaustive-cross-check; 3 records, 2 principals, 2 lineages, levels 0..1, sem 0..0; all 102,266 reachable states produced by the generator]` |
| mutant detected: supersede_without_fresh_admission | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: resurrect_on_retract | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: sem_rewrite_on_role_revise | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: beneficiary_self_approval | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 1]` |
| mutant detected: self_grant | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: adjudicate_unadmitted | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 1]` |
| mutant detected: lineage_rewrite | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: cross_lineage_supersede | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: stale_successor | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: admit_into_occupied_lineage | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: invalidate_falls_back | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: diagnosis_rewrites_adjudication | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: latched_live | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| eventual convergence (chain r0→r1→r2) | `PASS[permutation-exhaustive; 5 events; 120/120 schedules]` |
| successor conflict resolves only via independent quorum | `PASS[permutation-exhaustive; 6 events (fork + resolution); 720/720 schedules]` |
| replica MC: chain across replicas | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 2,730 states, 4 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: invalidated head is X_⊥, not fallback | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 8,562 states, 4 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: fork, one independent resolver | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 3,990 states, 4 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: fork, beneficiary-only resolution | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 3,990 states, 4 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: fork, two authorities may resolve concurrently and disagree | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 5,730 states, 12 quiescent, 3 resolution outcome(s) reached]` |
| replica MC: fork, disagreement escalated at epoch 2 | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; 6,610 states, 12 quiescent, 3 resolution outcome(s) reached]` |
| replica mutant detected: split-brain: first-applied-wins resolution | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; counterexample schedule length 14]` |
| replica mutant detected: handoff failure #3: naive stale-successor guard | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; counterexample schedule length 10]` |

## Assumptions and residual risks

- BoundaryCompleteness: every covered authority relation is represented in the model
- Logical actions are atomic at the invariant's abstraction level
- Python interpreter and this checker are correct (no independent proof checker)
- finite domain only
- finite domain only; no unbounded/parameterized proof
- Safety is encoded; its adequacy vs. the informal principle is a judgment
- causal deferral; reliable eventual delivery
- NOT linearizability, consensus, or arbitrary partitions
- replicas may serve a stale head before delivery
- resolution policy authorities are themselves trustworthy
- a replica may act on the eventual loser before learning of the fork
- reliable eventual delivery (no loss)
- no message loss, crashes, or Byzantine replicas modeled
- outcomes checked only for the scripted scenarios

Non-compensating gate over all 28 obligations and provenance: **all PASS**

## Run metadata (varies between runs; not part of any claim)

- started_utc: 2026-11-08 13:14:29
- python: 3.13.18
- interpreter: isolated=1 no_site=1 dont_write_bytecode=1
- reachability_s: 9.474
- induction_s: 14.236
- subset_check_s: 1.989
- core_total_s: 25.766
- mutants_s: 0.020
- distributed_s: 3.084
