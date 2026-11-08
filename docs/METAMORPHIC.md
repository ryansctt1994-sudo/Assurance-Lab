# Model validity: metamorphic relations, property coverage, witnesses

This file covers three questions: does each check actually detect something,
is the model able to do what it should, and do semantics-preserving input
transformations preserve outputs? The generated matrices live in
`docs/RESULTS.md` (snapshot-bound); this file states what they mean.
Everything here is Python-side evidence. It is independent of the
TLA+/TLC oracle, which remains **UNVALIDATED** (`docs/ORACLE.md`).

## 1. Property coverage (`checker/coverage.py`)

Each of the 20 core mutants is explored **once, completely, in its own
graph** (never the clean model's: a mutation can change what is
reachable). Every property is evaluated at every state and step, and each
property's first violation is recorded, so all properties are judged on the
identical graph. Selecting properties is observational only: tested that the
state count, transition count and state-set digest are identical with all,
one, or no properties enabled, for the clean model and for a mutant.

Outcomes: **KILLED**, **SURVIVED**, **INVALID** (the mutant's successor
relation never differs from the clean model's on its own reachable states:
an equivalent mutant), **ERROR** (exception or exhausted budget; never a
kill).

- The first measurement found four properties with **no demonstrated
  sensitivity**: TypeOK, ApproverAuthorized, SemBound, NoOrphanLive. Seven
  negative controls were added. Now every property kills at least one
  mutant, and no mutant survives all properties.
- Sole detectors: EdgesWF, NoSilentAuthorityIncrease, HistoryMonotone,
  NoOrphanLive, DiagnosisInert. Tests disable each one and check that its
  mutant then survives.
- **NoResurrection can never be a sole detector.** It is implied by
  NoSilentAuthorityIncrease (a newly current record must be freshly
  admitted). That is a structural redundancy, recorded rather than "fixed".
- `compaction_drops_edge`, `diagnosis_revises_roles` and `self_grant` reach
  **exactly** the clean model's state set (5,034 states) and differ only in
  transitions. Equal state-set digests therefore do **not** imply equal
  transition relations; only the step properties catch these.

A property with no kill is not shown to be useless (it may need a better
negative control), and a kill does not show a property is complete.

## 2. Reachability witnesses (`checker/witnesses.py`)

Safety holds vacuously in a model that can't do anything. Each witness is a
behavior the handoff intends to be **possible**, found as a **shortest**
path from Init (BFS with recorded predecessors) in the headline domain, and
**replayed** independently.

- These are **existential** claims ("this can happen"), **not liveness**
  ("this eventually happens"). Nothing here checks progress or fairness.
- Behaviors about *order* are **transition witnesses**. A state predicate can
  only assert that a configuration *occurs*; it cannot assert ordering or
  causation. The first version of "re-admission after X_⊥" was a state
  predicate, and a calibration test showed it accepting a path where the
  invalidation came *after* the re-admission. That wasn't the model
  misbehaving: it was the model correctly answering the wrong question. The
  fix moves the property from the state space to the edge structure (an
  `Admit` step whose pre-state's X_⊥ was *caused* by invalidation). Each remaining state witness is either order-free or has its
  order forced by the model's guards (for example, an invalidated record can't
  be superseded afterwards, so "superseded and invalidated" implies the
  invalidation came second).
- Calibration: in deliberately over-constrained models (no re-admission
  after invalidation; no grants), the search must report **unreachable**.
- **Domain adequacy is reported, not assumed.** The quick profile's domain
  has a single semantic version, so role revision is unreachable there and
  its claims are not evidence about I_5. Every report has a *Domain adequacy*
  table for its own core domain, which shows such a behavior as **NOT
  EXERCISED** instead of omitting it. The witness obligations themselves always
  run on the headline domain.

## 3. Metamorphic relations (`distributed/metamorphic.py`)

A relation is itself a claim about the semantics, so each one has a
precondition, a justification, and a comparison, and is calibrated by a
negative control.

**Shared preconditions:** each record id is issued once (no Byzantine
equivocation), the resolution policy is fixed, and each source instance is
a fixed set of issued events delivered completely. **Observation:**
per-lineage head, admitted records, applied edges, invalidations, per-fork
decision. Internal bookkeeping (rejection log, pending queue, arrival order)
is not observed.

| Relation | Precondition | Why it should hold | Negative control |
|---|---|---|---|
| Complete-delivery order invariance | fixed event set, complete delivery | every effect is a guarded set insertion; a fork's decision is a function of the *set* of valid resolutions. Intermediate observations may differ and are not compared | `first_applied_wins` (the split-brain rule), `naive_stale_guard` |
| Duplicate-message idempotence | the duplicate is identical to an event already delivered | redelivery of a seen event is a no-op; effects are idempotent | `replay_readmits` (a replayed admission revives an invalidated record) |
| Unrelated-lineage projection | the added lineage shares no ids and has no cross-lineage references | every replica rule is per lineage | `global_dispute` (a dispute anywhere blocks every lineage) |
| Identifier-renaming equivariance | bijections on record ids and principal names; `seq` and lineage names fixed | ids and names are used only for equality and membership; forward order comes from `seq`; roots are recomputed for renamed ids | `rid_tiebreak` (silent arbitration by smallest id), `principal_order_quorum` (identity compared by name order) |
| Serialization round-trip: finite-domain exhaustive projection testing **plus exact-state restoration verification over a declared event alphabet** | the frozen form is the complete state | `thaw(freeze(r))` rebuilds every field of the exact-state contract, so the copy has the same projection, the same exact state, and the same response to **every event of the declared finite alphabet** (all instances' events, both lineages, eight invalid and boundary events), but **not** to every event the API could accept. Reference: a never-serialized re-execution under the same flags | `freeze_drops_invalidated`, plus six snapshot corruptions |
| `ac-state-v1` round-trip (core) | versioned canonical encoding | `decanon` inverts `canon`; decoded states enable the same steps | a codec that drops `retracted` (test) |

**Not claimed for the core model:** lineage projection. Core lineages share
global state (authority levels, semantic version), so adding a lineage is
not semantics-preserving there.

**Calibration.** Each mutant counts executions of its deviant branch, so the
relation × mutant matrix in `docs/RESULTS.md` distinguishes:

- **KILLED**: the relation was violated;
- **survived**: the branch ran and the relation held (correct when the
  relation can't see that kind of fault);
- **not reached**: this relation's inputs never reach a branch that some
  other relation does exercise. That's a source-input adequacy observation
  about this relation, often by design, because its precondition excludes the
  triggering input (order invariance has no duplicates, so it can't reach
  `replay_readmits`);
- **equivalent?**: no relation ever exercises the mutant. It's a candidate
  equivalent or unreachable mutant, excluded from the mutation score's
  denominator (currently none: the score is 7/7).

**Measuring kills.** Relations stop at the first witness per source
instance, so a deviant-branch count on a KILLED cell only measures
exercise *until detection*. KILLED cells therefore report **kill breadth**:
in how many source instances the mutant was caught. An external audit
(at `e63a326`) correctly flagged `freeze_drops_invalidated` and
`naive_stale_guard` as thinly killed (2 executions each). Four targeted
instances (invalidation and out-of-order chains) were added; they are now
killed in 6/12 and 5/12 instances.

**Sampled versus exhaustive** is reported per relation. Order invariance is
exhaustive on 11/12 instances and samples one (`fork+dispute+escalation`:
4,713 distinct of 40,320 orders). Lineage projection is exhaustive on all 12
(every interleaving that preserves each lineage's own order).

**Serialization scope and corruption.** Restored replicas are compared
against a never-serialized reference on every event of a **declared finite
event universe**: all source instances' events, both lineages. That is not
every event the API could accept. Prefixes come from the causal and the
reversed delivery order, so states with deferred events are serialized too;
the first version used causal prefixes only, so a dropped pending queue was
never even infected. Two criteria apply: observational, and exact state (a
correct thaw is exact). Six deliberate corruptions of the frozen state are
each rejected in every instance they infect. Losing the duplicate filter is
unobservable, because effects are idempotent, so only the exact-state
criterion rejects it.

**Preconditions are machine-checked**, not just stated:
`precondition_violations()` verifies on every source instance that ids are
issued once, that no event references two lineages, that the added lineage is
disjoint, and that renamings are bijections keeping `seq` and lineage. The
equivocation probe is excluded *because* this checker flags it.

**Strict decoding.** `decanon` accepts only the canonical encoding: every
field a well-formed integer in range for the domain, and re-encoding
reproduces the input byte for byte. The first decoder accepted wrong record
counts, out-of-range values, non-boolean flags (silently coerced) and unsorted
sets. A lax decoder would let the round-trip relation miss an encoder emitting
non-canonical text, which would still change the digest. A corpus of 13
malformed lines, one per defect class, must be rejected 100%. The
dropped-field codec is **not** a partially passing control: it fails on
exactly the 9,888 states it corrupts (those with a retracted record). The
other 3,106 are not infected, since dropping a field that is false loses
nothing. Each negative
control is killed by exactly its intended relation. The tests pin the whole
matrix, so any change to what kills what has to be reviewed. A meta-test
injects deliberately wrong expected observations (an identity map instead
of the renaming, an impossible head, an always-false comparison) and
requires the clean implementation to *fail*: the checker does not accept
everything.

**Witnesses** are replayable (the event sequences themselves, not just
labels) and shrunk to 1-minimal: dropping any event makes the difference
disappear.

**Known limitation (outside the precondition):** a Byzantine origin issuing
two different admissions for one record id makes the outcome depend on
delivery order (the first admission wins). Minimal witness: two `Admit(r1)`
that differ only in owner. Recorded in the report, pinned by a test, and
excluded by the documented threat model (`distributed/mc.py`: no Byzantine
replicas). Fixing it would mean deriving replica state from the set of
events rather than applying them incrementally.

## Out of scope here: crash recovery

Crash-and-recovery testing is a separate follow-up. The replica is
in-memory today, so its first deliverable is a **durability contract**
(what survives a restart, when persistence takes effect, which operations
can be interrupted, what restart reconstructs), before any modeled crash
transitions. Recovery-specific mutants (e.g. a restart that discards a
competing resolution) can only be caught by recovery-aware checks, so
they are not used to calibrate this suite. Convergence under fairness is a
liveness property and would be a new obligation type.

## Exact-state contract and fail-closed restoration

**Exact-state contract** (`EXACT_STATE_CONTRACT`): admitted records (full
admission events), applied edges, invalidations, every valid resolution per
source, the pending queue *in order*, the duplicate filter (seen-set), and
the mutant flags. Excluded: the rejection log (append-only diagnostics no
rule reads), instrumentation counters, and the policy (fixed by
precondition). `exact_state` extracts these fields directly. It deliberately
does **not** call `freeze`: comparing two outputs of the serializer under
test would hide any field the serializer drops on both sides.

**Explicit invariant:** projection equivalence ⇏ behavioral equivalence ⇏
exact-state equivalence. Measured, not assumed: a dropped pending queue is
projection-equal but behaves differently later, and a dropped duplicate
filter is behaviorally equal but not exact-state equal.

**Fail-closed restoration.** `Replica.thaw` validates integrity (every
effect is explained by a seen event; every seen event is applied, pending,
or recorded as rejected) and refuses an inconsistent snapshot with
`CorruptSnapshot`. Building this exposed that a damaged snapshot could make
`head()` raise `KeyError` in two places: an earlier check had short-circuited
before `head()` ran. `head()` now also fails closed to CONFLICT on
inconsistent state (defense in depth). The validator accepts every genuine
state: the replica model checker thaws all of its roughly 31k explored states
through it.

Matrix versions and the reasons for changed cells:
`docs/METAMORPHIC_MATRIX_HISTORY.md`.
