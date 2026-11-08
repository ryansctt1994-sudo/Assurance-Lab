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

## v2: this branch (report bound to the commit in `docs/RESULTS.md`)

What changed, in order:

1. **Metric.** Raw branch counts measured exercise *until detection*, not
   exposure. Cells are now evaluated **one source instance at a time**:
   kill breadth = detecting eligible instances / eligible instances, where an
   instance is *eligible* when the mutant makes an **effective deviation**
   in it (changes state or result relative to the correct rule; executing the
   branch harmlessly does not count). Ineligible and errored instances are
   counted separately.
2. **Serialization relation.** Prefixes from the causal *and* the reversed
   order (so deferred events exist). Responses compared on a **declared
   finite event alphabet**: all instances' events, both lineages, plus eight
   invalid and boundary events. Exact-state comparison uses an independent
   extractor (`exact_state`), not the serializer under test. `thaw` validates
   snapshot integrity and refuses inconsistent ones (fail closed).
3. **Fixtures.** Four targeted instances were added for the two thin kills
   (`KINDS` = 12). The v1 fixture set is kept frozen and re-run under the v2
   code, so the expanded fixtures cannot silently improve the baseline's
   numbers (both appear in the report).

**Cells whose category changed from v1 to v2.** Both changed because of the
relation upgrade (change 2), not the fixtures; they read the same on the
frozen v1 fixtures under v2 code:

| Cell | v1 | v2 | Reason |
|---|---|---|---|
| `naive_stale_guard` × serialization | not reached | survived | reversed-order prefixes create out-of-order edge arrivals, which reach the naive guard's branch; the relation correctly doesn't flag it (restored and reference share the mutant) |
| `global_dispute` × serialization | not reached | survived | the event alphabet now includes the unrelated lineage's events, which create the cross-lineage dispute the mutant reacts to; same reason it survives |

**Kill breadth in v2** (every KILLED cell is full breadth, detecting every
eligible instance): `first_applied_wins` 2/2, `naive_stale_guard` 5/5 (v1
fixtures 2/2), `replay_readmits` 6/6, `global_dispute` 9/9,
`rid_tiebreak` 2/2, `principal_order_quorum` 5/5,
`freeze_drops_invalidated` 6/6 (v1 fixtures 2/2). The authoritative v2
tables, both fixture sets, are generated into `docs/RESULTS.md`.
