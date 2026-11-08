# Verification Results

Status: research prototype. **Not production-certified. Not unboundedly verified.**

These results come from a local run of the command below, **not from CI**.
PR CI runs the test suite and the *quick* campaign only; its `full-induction`
job is skipped on PRs by design. Green CI ≠ full induction completed.

## Provenance (start receipt, captured before enumeration)

- Command: `python -m assurance_lab --full --out docs/RESULTS.md`
- Commit: `748f80a09fbf0eba6906c6c71f0245e067cf2634` (tree `97178168b8f7a7b9675a2d8e36921646d9ea729b`)
- Execution: **snapshot**: committed tree exported with `git archive` into a fresh temp directory, run by an isolated interpreter (`-I -S -B`)
- Snapshot bytes = git objects: **yes**
- `assurance_lab/` working tree at start: **clean**
- SHA-256 over executed `assurance_lab/**/*.py` at start: `b625bcf64245d8e81dab3df97adf87c72430a465bd4a22004d9faa8a72b81461`
- Same digest from git objects at the commit: `b625bcf64245d8e81dab3df97adf87c72430a465bd4a22004d9faa8a72b81461`
- Import origins verified in every process that ran campaign code: the parent + 4 pool worker(s) that executed jobs (start method `fork`); outside executed tree: **none**
- Executed source re-hashed at end: **unchanged**
- Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Mutant domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1
- Workers: 4
- Partition manifest (frozen before execution): 18 partitions, content sha256 bac6c4698fb004bf91a4d9098f71b60ba30e12493532e7322dfc57f09c4e970a
- Receipt id: `09acb84fb4b633fe`
- Provenance verdict: **BOUND** to commit `748f80a09fbf`

## Claims

| Obligation | Verdict |
|---|---|
| Reachable ⊨ Inv ∧ Safety ∧ TransitionProps | `PASS[bfs-exhaustive-reachability; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 1,314,526 reachable states, 10,580,311 transitions, depth 15]` |
| partition reconciliation: evidence matches the frozen manifest exactly | `PASS[manifest-reconciliation; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 18 partitions: complete, disjoint, authenticated, arithmetic recomputed]` |
| Init ⇒ Inv | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ∧ Next ⇒ Inv' | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Inv ⇒ Safety | `PASS[finite-exhaustive-induction; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 3,587,826 Inv-states, 29,053,749 transitions]` |
| Reach ⊆ Inv-states (BFS vs generator) | `PASS[exhaustive-cross-check; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; all 1,314,526 reachable states produced by the generator]` |
| digest counts equal the checkers' own counts | `PASS[self-consistency; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; states and transitions, reachable and Inv]` |
| TRANSITION ORACLE: action-labeled successors agree on every Inv-state | `PASS[exhaustive-relation-comparison; 2 records, 2 principals, 1 lineages, levels 0..2, sem 0..1; 12,994 states, 74,045 labeled transitions]` |
| TRANSITION ORACLE: action-labeled successors agree on every Inv-state | `PASS[exhaustive-relation-comparison; 2 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 38,770 states, 224,229 labeled transitions]` |
| TRANSITION ORACLE: action-labeled successors agree on every Inv-state | `PASS[exhaustive-relation-comparison; 3 records, 2 principals, 1 lineages, levels 0..1, sem 0..0; 26,172 states, 195,562 labeled transitions]` |
| TRANSITION ORACLE: agreement on a sample of the core domain | `PASS[sampled-relation-comparison; 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1; 20,224 of 1,314,526 reachable states (every k-th in canonical order)]` |
| TRANSITION ORACLE: disagrees with every mutated transition relation | `PASS[mutant-calibration; each mutant's own graph; 20/20 mutants seen]` |
| TRANSITION ORACLE: TLA+ mutant translations match their Python twins | `PASS[exhaustive-relation-comparison; each mutant's own graph; 4/4 agree]` |
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
| METAMORPHIC: complete-delivery order invariance | `PASS[metamorphic-relation; 8 replica source instances; 17,520 comparisons]` |
| METAMORPHIC: duplicate-message idempotence | `PASS[metamorphic-relation; 8 replica source instances; 185 comparisons]` |
| METAMORPHIC: unrelated-lineage projection | `PASS[metamorphic-relation; 8 replica source instances; 336 comparisons]` |
| METAMORPHIC: identifier-renaming equivariance | `PASS[metamorphic-relation; 8 replica source instances; 288 comparisons]` |
| METAMORPHIC: serialization round-trip | `PASS[metamorphic-relation; 8 replica source instances; 428 comparisons]` |
| METAMORPHIC: relation preconditions hold on every source instance | `PASS[precondition-check; 8 instances, renamings, added lineage; ids issued once; no cross-lineage references; disjoint added lineage; renamings are bijections keeping seq and lineage]` |
| METAMORPHIC: ac-state-v1 encoding round-trip (strict decoder) | `PASS[metamorphic-relation; 2 records, 2 principals, 1 lineages, levels 0..2, sem 0..1; all 12,994 Inv-states decode to the same state with the same steps]` |
| ac-state-v1 strict decoder rejects every malformed encoding | `PASS[negative-corpus; 2 records, 2 principals, 1 lineages, levels 0..2, sem 0..1; 13/13 defect classes rejected]` |
| METAMORPHIC: every relation kills a negative control | `PASS[relation-x-mutant-matrix; 5 relations × 7 mutants]` |
| METAMORPHIC: every negative control is killed | `PASS[relation-x-mutant-matrix; 5 relations × 7 mutants]` |

## State-set and transition digests (deterministic; exact targets for independent oracles)

Encoding `ac-state-v1` (see `assurance_lab/statedigest.py`). Core domain: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1. `ac-state-v1:<count>:<hex>` = count and Σ SHA-256 mod 2^256 (combinable across workers); `sorted-v1:<count>:<hex>` = SHA-256 over the sorted, duplicate-free, length-delimited stream. Transitions are the set of (source, action, destination) triples: distinct actions reaching the same state count separately, stutter steps included. Edges are (source, destination) pairs, each counted once.

| Set | Sum digest | Sorted digest |
|---|---|---|
| reachable states | `ac-state-v1:1314526:c7b12c6dce74e495e7fe7f7174a44319e510d5d9aa6022a62faad6a30cf3b795` | `sorted-v1:1314526:6bab537544cd7cd076a3651ecff9babdba2f1c578057c2950a9ac413c3454fdf` |
| reachable transitions (source, action, destination) | `ac-state-v1:10580311:7289fe77d7938a21661b6a8206dfd4f4feecdb60b950231cc98abb4c887ef7ca` | — |
| reachable edges (source, destination) | `ac-state-v1:7998719:be4562d3cd11598fe74e122cf53b514187d41da937c71d33a83594c247aa69e6` | — |
| Inv-states | `ac-state-v1:3587826:f1bd334969eaa3b725436e5155697dfc9e08457c2c002607acfb6e80180a9f1b` | `sorted-v1:3587826:55e37f979bde587f089bf9e585139a9626898aa78dd6da3e0739536d2e864fd2` |
| Inv-state transitions (source, action, destination) | `ac-state-v1:29053749:a866a8ceee2c50f6f3af4c4e4ba84e47636a8b8d48cc8457c993fa01a5adf13c` | — |
| Inv-state edges (source, destination) | `ac-state-v1:21994389:56ab102eca61af9143cce58b3cc3e44c60f32197a2983c882bf3a0c6841956c5` | — |

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


## Domain adequacy: 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1

Whether this run's core domain can exhibit each intended behavior at all (existential BFS). The witness obligations above are always checked on the headline domain; this table qualifies the core claims of *this* run.

| Behavior | On this domain |
|---|---|
| stable no-current state | exercised (path length 2) |
| re-admission after X_⊥ | exercised (path length 3) |
| supersession chain | exercised (path length 3) |
| independent lineages | exercised (path length 2) |
| adjudication bound to an old meaning | exercised (path length 3) |
| granted authority exercised | exercised (path length 2) |
| retraction without resurrection | exercised (path length 3) |
| invalidation of a historical premise | exercised (path length 3) |


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

KILLED = relation violated; survived = the deviant branch ran and the relation held; not reached = this relation's inputs never reach a branch another relation does exercise (source-input adequacy); equivalent? = no relation exercises the mutant (excluded from the score). Numbers are deviant-branch executions; a kill resting on very few is thin evidence. Preconditions and justifications: `docs/METAMORPHIC.md`.

| mutant | order | duplicate | projection | renaming | serialization |
|---|---|---|---|---|---|
| `first_applied_wins` | **KILLED** (9) | survived (163) | survived (465) | survived (111) | survived (43) |
| `naive_stale_guard` | **KILLED** (2) | not reached (0) | not reached (0) | not reached (0) | not reached (0) |
| `replay_readmits` | not reached (0) | **KILLED** (252) | not reached (0) | not reached (0) | survived (252) |
| `global_dispute` | not reached (0) | not reached (0) | **KILLED** (89) | not reached (0) | not reached (0) |
| `rid_tiebreak` | survived (842) | survived (110) | survived (86) | **KILLED** (16) | survived (102) |
| `principal_order_quorum` | survived (30967) | survived (386) | survived (973) | **KILLED** (26) | survived (123) |
| `freeze_drops_invalidated` | not reached (0) | not reached (0) | not reached (0) | not reached (0) | **KILLED** (2) |

Cells show the outcome and the number of deviant-branch executions. Suite mutation score: 7/7 (killed / non-equivalent).

**Known limitation (outside the precondition):** with Byzantine equivocation (two different admissions for one record id), the outcome depends on delivery order. The precondition checker flags the probe (equivocation probe: id 'r1' issued 2 times), which is why it is excluded. Minimal witness:

```
complete-delivery order invariance violated on 'equivocation probe': final observation depends on order
  execution 0: Admit(r1, L, seq=1, owner=bob, pred=r0), Admit(r1, L, seq=1, owner=carol, pred=r0)
  execution 1: Admit(r1, L, seq=1, owner=carol, pred=r0), Admit(r1, L, seq=1, owner=bob, pred=r0)
```


## Partition evidence (reconciled against the frozen manifest)

Each row is one signed record from the worker that enumerated that partition. Reconciliation checked provenance, MAC integrity, identity, completeness, disjointness (observed keys) and arithmetic: **no problems**.

| Partition | Key (levels, sem_now) | States | Transitions | State digest |
|---|---|---|---|---|
| p000 | (0, 0), 0 | 1 | 1 | `5216d1a4e453c705…` |
| p001 | (0, 0), 1 | 1 | 0 | `81d54f9960607fb8…` |
| p002 | (0, 1), 0 | 11,897 | 111,060 | `03e3b91ca13cd6ea…` |
| p003 | (0, 1), 1 | 90,369 | 756,191 | `6a100852583d5397…` |
| p004 | (0, 2), 0 | 11,897 | 111,060 | `2300726c112e486a…` |
| p005 | (0, 2), 1 | 90,369 | 756,191 | `7249e19c43df0580…` |
| p006 | (1, 0), 0 | 11,897 | 111,060 | `e655aa2d93365ccc…` |
| p007 | (1, 0), 1 | 90,369 | 756,191 | `afbe25f6d254b789…` |
| p008 | (1, 1), 0 | 90,369 | 764,653 | `ce325ff1e89c49a1…` |
| p009 | (1, 1), 1 | 704,321 | 5,234,188 | `9cee5f0915974dbb…` |
| p010 | (1, 2), 0 | 90,369 | 855,022 | `0f9fc0c7a7459c0d…` |
| p011 | (1, 2), 1 | 704,321 | 5,938,509 | `ce0cba5ec0ac21b9…` |
| p012 | (2, 0), 0 | 11,897 | 111,060 | `c2ecb806ca97cc9d…` |
| p013 | (2, 0), 1 | 90,369 | 756,191 | `0d6741d7fea19d3a…` |
| p014 | (2, 1), 0 | 90,369 | 855,022 | `ef02e962553c3fb0…` |
| p015 | (2, 1), 1 | 704,321 | 5,938,509 | `095232501643dec3…` |
| p016 | (2, 2), 0 | 90,369 | 764,653 | `7ec68daee2e1a9be…` |
| p017 | (2, 2), 1 | 704,321 | 5,234,188 | `f4424f0df3634909…` |


## Assumptions and residual risks

- BoundaryCompleteness: every covered authority relation is represented in the model
- Logical actions are atomic at the invariant's abstraction level
- Python interpreter and this checker are correct (no independent proof checker)
- finite domain only
- does not defend against a malicious worker
- finite domain only; no unbounded/parameterized proof
- Safety is encoded; its adequacy vs. the informal principle is a judgment
- same author wrote both relations: a shared misreading of the handoff would agree
- sampled, not exhaustive
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

Non-compensating gate over all 67 obligations and provenance: **all PASS**

## Run metadata (varies between runs; not part of any claim)

- started_utc: 2026-11-08 11:33:19
- run_id (partition evidence binding): edc8ddce68a1ff33
- python: 3.13.18
- interpreter: isolated=1 no_site=1 dont_write_bytecode=1
- reachability_s: 119.065
- induction_s: 284.482
- subset_check_s: 53.107
- digest_s: 214.566
- oracle_s: 189.420
- coverage_witness_metamorphic_s: 48.990
- core_total_s: 861.581
- mutants_s: 0.068
- distributed_s: 47.272
