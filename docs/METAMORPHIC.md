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
- Behaviors about *order* are **transition witnesses**. The first version of
  "re-admission after X_⊥" was a state predicate, and a calibration test
  showed it accepting a path where the invalidation came *after* the
  re-admission. Each remaining state witness is either order-free or has its
  order forced by the model's guards (for example, an invalidated record can't
  be superseded afterwards, so "superseded and invalidated" implies the
  invalidation came second).
- Calibration: in deliberately over-constrained models (no re-admission
  after invalidation; no grants), the search must report **unreachable**.
- **Caveat on the quick profile:** its domain has a single semantic version,
  so role revision is unreachable there; its claims are not evidence about
  I_5. Witnesses therefore always use the headline domain.

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
| Serialization round-trip | the frozen form is the complete state | `thaw(freeze(r))` rebuilds every behavior-relevant field, so the copy responds identically to every next event (reference: a never-serialized re-execution under the same flags) | `freeze_drops_invalidated` |
| `ac-state-v1` round-trip (core) | versioned canonical encoding | `decanon` inverts `canon`; decoded states enable the same steps | a codec that drops `retracted` (test) |

**Not claimed for the core model:** lineage projection. Core lineages share
global state (authority levels, semantic version), so adding a lineage is
not semantics-preserving there.

**Calibration.** Each mutant counts executions of its deviant branch, so the
relation × mutant matrix in `docs/RESULTS.md` distinguishes **KILLED**
(relation violated), **survived** (the branch ran and the relation held, as
it should when the relation can't see that kind of fault) and **not
exercised** (the relation's inputs never reach the branch). Each negative
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
