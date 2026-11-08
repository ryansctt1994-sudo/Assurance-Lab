# Verification Results

Status: research prototype. **Not production-certified. Not unboundedly verified.**

These results come from a local run of the command below, **not from CI**.
PR CI runs the test suite and the *quick* campaign only; its `full-induction`
job is skipped on PRs by design. Green CI ≠ full induction completed.

## Provenance (start receipt, captured before enumeration)

- Command: `python -m assurance_lab --full --out docs/RESULTS.md`
- Commit: `903be71081c0e4df8defa126597ec0658f1cb9e9` (tree `7fa5a8e7e209a39712b389c5ea72e2b44f6bf4b2`)
- Execution: **snapshot**: committed tree exported with `git archive` into a fresh temp directory, run by an isolated interpreter (`-I -S -B`)
- Snapshot bytes = git objects: **yes**
- `assurance_lab/` working tree at start: **clean**
- SHA-256 over executed `assurance_lab/**/*.py` at start: `4eba65f66cf601412c74a8db42d94e3ee54ed5132e6f07cc365bd2cf25ecad23`
- Same digest from git objects at the commit: `4eba65f66cf601412c74a8db42d94e3ee54ed5132e6f07cc365bd2cf25ecad23`
- Import origins verified in every process that ran campaign code: the parent + 4 pool worker(s) that executed jobs (start method `fork`); outside executed tree: **none**
- Executed source re-hashed at end: **unchanged**
- Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Mutant domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Workers: 4
- Receipt id: `47e0476a6478c4a0`
- Provenance verdict: **BOUND** to commit `903be71081c0`

## Claims

| Obligation | Verdict |
|---|---|
| Reachable ⊨ Inv ∧ Safety ∧ TransitionProps | `PASS[bfs-exhaustive-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 1,314,526 reachable states, 10,580,311 transitions, depth 15]` |
| Init ⇒ Inv | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ∧ Next ⇒ Inv' | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ⇒ Safety | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Reach ⊆ Inv-states (BFS vs generator) | `PASS[exhaustive-cross-check; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; all 1,314,526 reachable states produced by the generator]` |
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
| replica MC: fork, one independent resolver (+ passive 3rd replica) | `PASS[bfs-exhaustive-replica-network; 3 replicas, ≤1 duplicated message; 24,454 states, 2 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: fork, beneficiary-only resolution (+ passive 3rd replica) | `PASS[bfs-exhaustive-replica-network; 3 replicas, ≤1 duplicated message; 24,454 states, 2 quiescent, 1 resolution outcome(s) reached]` |
| replica MC: fork, two authorities may resolve concurrently and disagree (+ passive 3rd replica) | `PASS[bfs-exhaustive-replica-network; 3 replicas, ≤1 duplicated message; 64,174 states, 6 quiescent, 3 resolution outcome(s) reached]` |
| replica MC: fork, disagreement escalated at epoch 2 (+ passive 3rd replica) | `PASS[bfs-exhaustive-replica-network; 3 replicas, ≤1 duplicated message; 204,286 states, 6 quiescent, 3 resolution outcome(s) reached]` |
| replica mutant detected: split-brain: first-applied-wins resolution | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; counterexample schedule length 14]` |
| replica mutant detected: handoff failure #3: naive stale-successor guard | `PASS[bfs-exhaustive-replica-network; 2 replicas, ≤3 duplicated messages; counterexample schedule length 10]` |

## State-set digests (deterministic; exact targets for independent oracles)

Encoding `ac-state-v1`, digest = count and Σ SHA-256(state) mod 2^256 (see `assurance_lab/statedigest.py`). Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1.

| Set | States | Digest |
|---|---|---|
| reachable | 1,314,526 | `c7b12c6dce74e495e7fe7f7174a44319e510d5d9aa6022a62faad6a30cf3b795` |
| inv_states | 3,587,826 | `f1bd334969eaa3b725436e5155697dfc9e08457c2c002607acfb6e80180a9f1b` |

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

Non-compensating gate over all 32 obligations and provenance: **all PASS**

## Run metadata (varies between runs; not part of any claim)

- started_utc: 2026-11-08 09:33:09
- python: 3.13.18
- interpreter: isolated=1 no_site=1 dont_write_bytecode=1
- reachability_s: 103.939
- induction_s: 169.032
- subset_check_s: 50.637
- digest_s: 9.368
- core_total_s: 333.573
- mutants_s: 0.012
- distributed_s: 41.614
