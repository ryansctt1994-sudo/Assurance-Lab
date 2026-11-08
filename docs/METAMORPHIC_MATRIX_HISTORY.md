# Relation × mutant matrix: version history

Changes to what the metamorphic suite can detect stay auditable. Each
version records its fixture set, its relation code, its metric, and why any
cell changed. A changed cell is not automatically a regression; an
unexplained one would be.

## v1: published at `b535b1a` (report bound to `748f80a`)

Fixtures: 8 source instances (`KINDS_V1`). Serialization compared each
causal-order prefix against the instance's *own* events only. Metric: raw
deviant-branch executions, counted until the first witness per instance.

| mutant | order | duplicate | projection | renaming | serialization |
|---|---|---|---|---|---|
| `first_applied_wins` | **KILLED** (9) | survived (163) | survived (465) | survived (111) | survived (43) |
| `naive_stale_guard` | **KILLED** (2) | not reached (0) | not reached (0) | not reached (0) | not reached (0) |
| `replay_readmits` | not reached (0) | **KILLED** (252) | not reached (0) | not reached (0) | survived (252) |
| `global_dispute` | not reached (0) | not reached (0) | **KILLED** (89) | not reached (0) | not reached (0) |
| `rid_tiebreak` | survived (842) | survived (110) | survived (86) | **KILLED** (16) | survived (102) |
| `principal_order_quorum` | survived (30967) | survived (386) | survived (973) | **KILLED** (26) | survived (123) |
| `freeze_drops_invalidated` | not reached (0) | not reached (0) | not reached (0) | not reached (0) | **KILLED** (2) |

Suite mutation score 7/7. External audit (at `e63a326`, same matrix
shape) findings A2 and A3 apply to this version: the serialization scope was
overstated, coverage was unlabeled, and two kills were thin.

## v2 draft at `0fe2c2b`: withdrawn before freezing

The draft measured "kill breadth = detecting eligible / eligible", where an
instance was *eligible* when the mutant made an effective deviation at the
point the relation observes. That defines the denominator by the outcome,
and it hid narrow exposure: a mutant that deviates in many instances but is
seen in few read as full breadth. Its "every KILLED cell is full breadth"
claim is withdrawn. Under complete denominators (below):

| Draft claim | Corrected (E / D / K) |
|---|---|
| `rid_tiebreak` × renaming 2/2 | 7 / 7 / 2: deviates transiently in 7 instances, detected in 2 |
| `global_dispute` × projection 9/9 | 12 / 12 / 9: in 3 instances L's final head is CONFLICT either way |
| the other five KILLED cells | full breadth stands: 2/2/2, 5/5/5, 6/6/6, 5/5/5, 6/6/6 |

The draft also reported snapshot corruptions "with validation off" per
layer. With `head()` now failing closed to UNKNOWN, those numbers would
credit the projection comparison for what the integrity guard did, so they
are replaced (see `docs/METAMORPHIC.md`).

## v2: frozen with this change (report bound to the commit in `docs/RESULTS.md`)

**Schema.** Per (relation, mutant), over every source instance, three
quantities, each defined independently of the outcome it is compared with:

| Quantity | Definition |
|---|---|
| Eligible (E) | the instance satisfies the mutant's frozen predicate (`ELIGIBILITY`) over the events the relation can deliver (`alphabet`), and the relation performs the operation the mutated code is in |
| Deviating (D) | eligible, and on some execution the relation performs (`_programs`, the same sets as the relation itself) the mutant and the correct replica differ, run in lockstep: state after a step, or head for the two head-time mutants, or a mutant snapshot refused at thaw |
| Detecting (K) | deviating, and the relation is violated on that instance |

Exposure rate = D/E; detection breadth = K/D. Anomalies fail the campaign:
a deviation on an ineligible instance (the predicate under-approximates), a
detection without a deviation, an errored instance. A new category, **n/a**,
marks cells with no eligible instance. Each deviation is recorded with its
execution and step, and replays (`test_deviation_witnesses_replay`).

**Alphabet.** The serialization alphabet gained three boundary resolutions
(tampered post-state root, winner = loser, under-quorum) and one declared
two-event sequence (admit `m0`, then `Edge(r0->m0)`), so all 9 of the
replica's rejection rules are exercised from it. The cross-lineage rule
cannot be reached by any single event after a single-lineage prefix, which
is why it needs the sequence. The matrix categories did not change.

**Cells whose category differs from v1** (exactly these; the suite fails on
any other, `V1_TO_V2_CHANGES`):

| Cell | v1 | v2 | Reason |
|---|---|---|---|
| `naive_stale_guard` × serialization | not reached | survived | prefixes of the reversed order make edges arrive out of order, which reaches the naive guard; restored and reference replicas share the mutant, so the relation correctly holds |
| `global_dispute` × serialization | not reached | survived | the alphabet includes the unrelated lineage's admissions, so a dispute in L meets a head query for M; restored and reference share the mutant |
| `global_dispute` × order, duplicate, renaming | not reached | n/a | a single-lineage alphabet cannot reach the cross-lineage branch: no instance is eligible (v1 had no eligibility predicate) |
| `freeze_drops_invalidated` × order, duplicate, projection, renaming | not reached | n/a | these relations never serialize, so the mutated `freeze()` never runs (v1 had no eligibility predicate) |

The v1 numbers (raw branch executions) and v2's (E / D / K) are different
metrics and are not compared numerically. The frozen v1 fixtures are re-run
under v2 and give the same categories. Both tables are generated into
`docs/RESULTS.md`; the acceptance receipt for this version is under
`receipts/`.
