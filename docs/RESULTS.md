# Verification Results

Status: research prototype. **Not production-certified. Not unboundedly verified.**

These results come from a local run of the command below, **not from CI**.
PR CI runs the test suite and the *quick* campaign only; its `full-induction`
job is skipped on PRs by design. Green CI ≠ full induction completed.

## Provenance (start receipt, captured before enumeration)

- Command: `python -m assurance_lab --full --out docs/RESULTS.md`
- Commit: `668050e490455d6bcb38956bba6714fe31cb574d` (tree `f87358710b5586b82493114318e917d315574fda`)
- Execution: **snapshot**: committed tree exported with `git archive` into a fresh temp directory, run by an isolated interpreter (`-I -S -B`)
- Snapshot bytes = git objects: **yes**
- `assurance_lab/` working tree at start: **clean**
- SHA-256 over executed `assurance_lab/**/*.py` at start: `215fae0130597e6b707ab5cf375413d4af83ffa50639c0586d39314ead65f0cc`
- Same digest from git objects at the commit: `215fae0130597e6b707ab5cf375413d4af83ffa50639c0586d39314ead65f0cc`
- Import origins verified in every process that ran campaign code: the parent + 4 pool worker(s) that executed jobs (start method `fork`); outside executed tree: **none**
- Executed source re-hashed at end: **unchanged**
- Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Mutant domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Workers: 4
- Receipt id: `298d1a25f01bc8f9`
- Provenance verdict: **BOUND** to commit `668050e49045`

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
| mutant detected: unbounded_grant | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: admit_unauthorized_approver | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 1]` |
| mutant detected: role_revise_rollback | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: admit_without_head | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 1]` |
| mutant detected: diagnosis_revises_roles | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 2]` |
| mutant detected: unretract | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 3]` |
| mutant detected: compaction_drops_edge | `PASS[bfs-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; counterexample length 4]` |
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
| coverage: every property kills at least one mutant | `PASS[mutant-x-property-matrix; each mutant in its smallest non-equivalent domain; 20 mutants × 16 properties]` |
| coverage: no mutant survives every property | `PASS[mutant-x-property-matrix; each mutant in its smallest non-equivalent domain; 20 mutants, none equivalent]` |
| witness: stable no-current state | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 2]` |
| witness: re-admission after X_⊥ | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 3]` |
| witness: supersession chain | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 3]` |
| witness: independent lineages | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 2]` |
| witness: adjudication bound to an old meaning | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 3]` |
| witness: granted authority exercised | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 2]` |
| witness: retraction without resurrection | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 3]` |
| witness: invalidation of a historical premise | `PASS[bfs-shortest-witness; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; path length 3]` |
| METAMORPHIC: complete-delivery order invariance | `PASS[metamorphic-relation; 7 replica source instances; 12,480 comparisons]` |
| METAMORPHIC: duplicate-message idempotence | `PASS[metamorphic-relation; 7 replica source instances; 157 comparisons]` |
| METAMORPHIC: unrelated-lineage projection | `PASS[metamorphic-relation; 7 replica source instances; 294 comparisons]` |
| METAMORPHIC: identifier-renaming equivariance | `PASS[metamorphic-relation; 7 replica source instances; 252 comparisons]` |
| METAMORPHIC: serialization round-trip | `PASS[metamorphic-relation; 7 replica source instances; 364 comparisons]` |
| METAMORPHIC: ac-state-v1 encoding round-trip | `PASS[metamorphic-relation; 2 records, 2 principals, 1 lineages, levels 0..2, sem 0..1; all 12,994 Inv-states decode to the same state with the same steps]` |
| METAMORPHIC: every relation kills a negative control | `PASS[relation-x-mutant-matrix; 5 relations × 7 mutants]` |
| METAMORPHIC: every negative control is killed | `PASS[relation-x-mutant-matrix; 5 relations × 7 mutants]` |

## State-set digests (deterministic; exact targets for independent oracles)

Encoding `ac-state-v1`, digest = count and Σ SHA-256(state) mod 2^256 (see `assurance_lab/statedigest.py`). Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1.

| Set | States | Digest |
|---|---|---|
| reachable | 1,314,526 | `c7b12c6dce74e495e7fe7f7174a44319e510d5d9aa6022a62faad6a30cf3b795` |
| inv_states | 3,587,826 | `f1bd334969eaa3b725436e5155697dfc9e08457c2c002607acfb6e80180a9f1b` |

## Reachability witnesses (existential, not liveness)

Shortest paths from Init, found by BFS in the core domain. Each shows a behavior the handoff intends to be *possible*; none claims it eventually happens.

| Behavior | Handoff | Shortest path |
|---|---|---|
| a lineage whose record was invalidated has no current record (X_⊥) | §3 | `('Admit', 0, 0, 1, 0) → ('Invalidate', 0)` |
| a fresh admission into a lineage whose authority was removed by invalidation (X → X_⊥ → X') | §4 | `('Admit', 0, 0, 1, 0) → ('Invalidate', 0) → ('Admit', 1, 0, 1, 0)` |
| x → y → z within one lineage | §9 | `('Admit', 0, 0, 1, 0) → ('Supersede', 0, 1, 1, 0) → ('Supersede', 1, 2, 1, 0)` |
| two lineages each have a current head | §11 | `('Admit', 0, 0, 1, 0) → ('Admit', 1, 1, 1, 0)` |
| a record adjudicated under semantic version v while the current version is newer | §5 I_5 | `('Admit', 0, 0, 1, 0) → ('Adjudicate', 0) → ('RoleRevise', 1)` |
| a principal that started without authority, after an independent grant, approves an admission | §5 I_6 | `('Grant', 1, 0) → ('Admit', 0, 0, 0, 1)` |
| a successor is retracted and its superseded predecessor does not regain authority | §10 | `('Admit', 0, 0, 1, 0) → ('Supersede', 0, 1, 1, 0) → ('Retract', 1)` |
| an already superseded record is later invalidated | §4, §14 | `('Admit', 0, 0, 1, 0) → ('Supersede', 0, 1, 1, 0) → ('Invalidate', 0)` |


## Property coverage (mutant × property)

K = killed, **K** = sole detector, · = survived, I = invalid (equivalent mutant), E = error. Each row is one complete exploration of that mutant's own graph.

| mutant | states | TypeOK | RecordWF | AdjBound | IndepAppr | ApprAuth | SemBound | CurCoh | NoOrphanLive | HeadUnique | EdgesWF | NoFork | Safety | NSAI | HistMono | NoResurr | DiagInert |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `supersede_without_fresh_admission` | 5,790 | · | · | · | · | · | · | K | · | · | · | · | K | K | K | K | · |
| `resurrect_on_retract` | 6,042 | · | · | · | · | · | · | K | · | · | · | · | K | K | · | K | · |
| `sem_rewrite_on_role_revise` | 3,833 | · | · | K | · | · | · | · | · | · | · | · | · | · | K | · | · |
| `beneficiary_self_approval` | 19,638 | · | · | · | K | · | · | · | · | · | · | · | K | K | · | · | · |
| `self_grant` | 5,034 | · | · | · | · | · | · | · | · | · | · | · | · | **K** | · | · | · |
| `adjudicate_unadmitted` | 5,467 | · | K | K | · | · | · | · | · | · | · | · | · | · | · | · | · |
| `lineage_rewrite` | 21,222 | · | · | · | · | · | · | · | · | K | K | · | K | · | K | · | · |
| `cross_lineage_supersede` | 20,430 | · | · | · | · | · | · | · | · | · | **K** | · | · | · | · | · | · |
| `stale_successor` | 330,794 | · | · | · | · | · | · | · | · | K | · | K | K | · | · | · | · |
| `admit_into_occupied_lineage` | 5,430 | · | · | · | · | · | · | · | · | K | · | · | K | · | · | · | · |
| `invalidate_falls_back` | 6,042 | · | · | · | · | · | · | K | · | · | · | · | K | K | · | K | · |
| `diagnosis_rewrites_adjudication` | 11,139 | · | · | K | · | · | · | · | · | · | · | · | · | · | K | · | K |
| `latched_live` | 5,034 | · | · | · | · | · | · | K | · | K | · | · | K | · | · | · | · |
| `unbounded_grant` | 11,712 | K | · | · | · | · | · | · | · | · | · | · | · | K | · | · | · |
| `admit_unauthorized_approver` | 6,678 | · | · | · | · | K | · | · | · | · | · | · | K | K | · | · | · |
| `role_revise_rollback` | 9,254 | · | · | · | · | · | K | · | · | · | · | · | · | · | K | · | · |
| `admit_without_head` | 3,126 | · | · | · | · | · | · | · | **K** | · | · | · | · | · | · | · | · |
| `diagnosis_revises_roles` | 5,034 | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | **K** |
| `unretract` | 5,250 | · | · | · | · | · | · | · | · | · | · | · | · | K | K | K | · |
| `compaction_drops_edge` | 5,034 | · | · | · | · | · | · | · | · | · | · | · | · | · | **K** | · | · |

Sole witnesses: `NoOrphanLive`: admit_without_head; `EdgesWF`: cross_lineage_supersede; `NoSilentAuthorityIncrease`: self_grant; `HistoryMonotone`: compaction_drops_edge; `DiagnosisInert`: diagnosis_revises_roles.
`NoResurrection` cannot have a sole witness: it is implied by `NoSilentAuthorityIncrease` (a newly current record must be freshly admitted).


## Metamorphic relations × negative controls

KILLED = relation violated; survived = the mutant's deviant branch ran and the relation held; not exercised = the relation's inputs never reach that branch. Preconditions and justifications: `docs/METAMORPHIC.md`.

| mutant | order | duplicate | projection | renaming | serialization |
|---|---|---|---|---|---|
| `first_applied_wins` | KILLED | survived | survived | survived | survived |
| `naive_stale_guard` | KILLED | not exercised | not exercised | not exercised | not exercised |
| `replay_readmits` | not exercised | KILLED | not exercised | not exercised | survived |
| `global_dispute` | not exercised | not exercised | KILLED | not exercised | not exercised |
| `rid_tiebreak` | survived | survived | survived | KILLED | survived |
| `principal_order_quorum` | survived | survived | survived | KILLED | survived |
| `freeze_drops_invalidated` | not exercised | not exercised | not exercised | not exercised | KILLED |

**Known limitation (outside the precondition):** with Byzantine equivocation (two different admissions for one record id), the outcome depends on delivery order. Minimal witness:

```
complete-delivery order invariance violated on 'equivocation probe': final observation depends on order
  execution 0: Admit(r1, L, seq=1, owner=bob, pred=r0), Admit(r1, L, seq=1, owner=carol, pred=r0)
  execution 1: Admit(r1, L, seq=1, owner=carol, pred=r0), Admit(r1, L, seq=1, owner=bob, pred=r0)
```


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
- a kill shows sensitivity, not completeness
- existential only: says nothing about progress
- each record id is issued once (no Byzantine equivocation)
- fixed resolution policy; complete delivery of a fixed event set

Non-compensating gate over all 57 obligations and provenance: **all PASS**

## Run metadata (varies between runs; not part of any claim)

- started_utc: 2026-11-08 10:28:59
- python: 3.13.18
- interpreter: isolated=1 no_site=1 dont_write_bytecode=1
- reachability_s: 115.446
- induction_s: 203.176
- subset_check_s: 66.817
- digest_s: 12.040
- coverage_witness_metamorphic_s: 53.705
- core_total_s: 398.188
- mutants_s: 0.075
- distributed_s: 47.189
