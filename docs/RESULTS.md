# Verification Results

Generated 2026-11-08 08:05 UTC by `python -m assurance_lab --full` (Python 3.13.18).

Status: research prototype. **Not production-certified. Not unboundedly verified.**

| Obligation | Verdict |
|---|---|
| Reachable ⊨ Inv ∧ Safety ∧ TransitionProps | `PASS[bfs-exhaustive-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 1,314,526 reachable states, 10,580,311 transitions, depth 15]` |
| Init ⇒ Inv | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions, 161s]` |
| Inv ∧ Next ⇒ Inv' | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions, 161s]` |
| Inv ⇒ Safety | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions, 161s]` |
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
