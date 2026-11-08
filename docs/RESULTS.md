# Verification Results

Status: research prototype. **Not production-certified. Not unboundedly verified.**

These results come from a local run of the command below, **not from CI**.
PR CI runs the test suite and the *quick* campaign only; its `full-induction`
job is skipped on PRs by design. Green CI ≠ full induction completed.

## Provenance (start receipt, captured before enumeration)

- Command: `python -m assurance_lab --full --out docs/RESULTS.md`
- Commit: `fb69e01f506652f4b6c82da5ffdcb9300308643e`
- `assurance_lab/` tree at start: **clean**
- SHA-256 over `assurance_lab/**/*.py` at start: `393ce1b973870245aaad586f830d340bd20cd56888105a24ebe7ed75b75ffb94`
- Source re-hashed at end: **unchanged**
- Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Mutant domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Receipt id: `0220330ea5c90ead`

## Claims

| Obligation | Verdict |
|---|---|
| Reachable ⊨ Inv ∧ Safety ∧ TransitionProps | `PASS[bfs-exhaustive-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 1,314,526 reachable states, 10,580,311 transitions, depth 15]` |
| Init ⇒ Inv | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ∧ Next ⇒ Inv' | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ⇒ Safety | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
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

Non-compensating gate over all 19 obligations: **all PASS**

## Run metadata (varies between runs; not part of any claim)

- started_utc: 2026-11-08 08:16:24
- python: 3.13.18
- reachability_s: 105.1
- induction_s: 167.8
- core_total_s: 272.9
- mutants_s: 0.0
- distributed_s: 0.0
