# Independent oracle: TLA+/TLC (UNVALIDATED)

> **Status: UNVALIDATED.** `tla/AuthorityCore.tla` has never been parsed by
> SANY or checked by TLC. Running TLC awaits an approved execution path; it
> was not run in the development container, where executing a downloaded
> `tla2tools.jar` was blocked. Nothing here changes the accepted baseline in
> `docs/RESULTS.md`; a passing oracle run would be additional, independent
> validation, and a failing one would be a finding to investigate.

## What the oracle must show (acceptance criteria)

1. **Independent state construction.** The TLA+ spec states the transition
   rules directly (`Admit`, `Supersede`, …). It does not import, translate
   or replay Python's generated states.
2. **Semantic correspondence before numbers.** The mapping below is fixed
   and documented *before* any count is compared.
3. **Evidence beyond counts.** Equal cardinalities can hide different sets,
   so the comparison is **count + state-set digest** (`ac-state-v1`, see
   `assurance_lab/statedigest.py`) over TLC's `-dump` of every distinct
   state, parsed by `assurance_lab/tla.py`. Record the TLC release, the jar's
   SHA-256, each `.cfg`, and the logs.
4. **The inductive domain is specified separately.** TLC's ordinary run
   counts *reachable* states only. `IndInit` makes the *initial* states every
   Inv-state, so TLC's initial-state set must equal Python's Inv-state set.
   `IndInit` prunes the way Python's generator does (record-local
   candidates, forward edges, `current` fixed to the live set), so on the
   small domain `IndInitUnpruned` (the whole typed space, filtered by `Inv`
   alone) must give the **same** set, which checks the pruning independently.

## Already corroborated without TLC

`checker/relational.py` is a second transition relation translated line by
line from `AuthorityCore.tla` (dict state, its own codec, TLA+ action
instances as labels, the spec's `Mutant` constant). It gives the
unvalidated spec its first executable reading:

- action-labeled successors agree with `CoreModel.next` on **every**
  Inv-state (reachable or not) of three small domains, and on a 20k-state
  sample of the core domain;
- it disagrees with all 20 mutated transition relations, including the
  three that reach exactly the clean state set (invisible to state digests);
- the four TLA+ mutant translations agree exactly with their Python twins;
- every enumerated transition must carry exactly one, distinct, label
  (duplicates are reported, not merged by set comparison).

Transition targets for TLC: the labeled-transition and edge digests in
`docs/RESULTS.md`. A TLC `-dump` carries states but not parameterized
action labels, so a TLC run can be compared on states and edges; labeled
transitions need TLC's action-label dot output or a dedicated module.

**Shared-dependency caveat.** The dump parser maps TLC values to the same
canonical encoding as the Python side. Each state is therefore encoded by two
separately written serializers (`canon_parts` and the oracle's `write`),
which must agree byte for byte, so a defect in one cannot produce matching
wrong digests on both sides. Both are still Python and share an author; a
serializer in another language would be stronger.

## State correspondence

| TLA+ (`AuthorityCore.tla`) | Python (`core/model.py`) |
|---|---|
| `recs[x]` record, `x ∈ 0..NRec-1` | `State.recs[x]` (`Rec`) |
| fields `admitted, lineage, owner, approver, sem, invalidated, retracted, adj` | `Rec` fields in the same order (`adj` ↔ `adj_sem`) |
| `NULL` = `-1` | `None` |
| `current ⊆ R` | `State.current` (frozenset) |
| `edges ⊆ R × R`, pairs `<<x, y>>` | `State.edges` (frozenset of `(x, y)`) |
| `levels ∈ [P → 0..MaxLevel]` | `State.levels` (tuple, index = principal) |
| `semNow` | `State.sem_now` |

- No auxiliary variables, and the cfgs use **no `SYMMETRY` and no `VIEW`**:
  either would change what counts as a distinct state.
- `Diagnose` is `UNCHANGED vars` in TLA+ and a self-loop in Python. Neither
  creates states, so distinct-state sets are unaffected. **Transition counts
  are not compared**, because TLC's "states generated" counts stutter steps
  and duplicates on its own terms.
- `Inv` in TLA+ writes `CurrentCoherent ∧ NoOrphanLive` as the single clause
  `current = {x ∈ R : Live(x)}`, which is equivalent. Step properties are
  stated over state changes (TLA+ action formulas cannot see action labels);
  for the clean spec this matches the label-based Python checks.

## Targets

From the Python campaign (full-domain targets: the digest table in
`docs/RESULTS.md`, bound to `903be71`). Each full-domain digest was produced
twice by different code paths: merged from 4 pool workers' partition digests
inside the bound campaign, and recomputed sequentially with no pool in a
separate process. Both agreed.

| cfg | Domain | Python target (`count:digest`) |
|---|---|---|
| `small_reach` | 2 records, 2 principals, 1 lineage, levels 0..1, sem 0..0 | `670:90bd9c18894fdec12916ad1104da9df12c797dbb1fb75752fc6c67bcb3954774` |
| `small_ind` | same | `812:44e4ac49ad9f94ba7045196517bb4c104cf9fbfa38245566e4ad230d0e0fb54d` |
| `small_ind_unpruned` | same (278,784 typed candidates) | same as `small_ind` |
| `full_reach` | 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1 | `1314526:c7b12c6dce74e495e7fe7f7174a44319e510d5d9aa6022a62faad6a30cf3b795` |
| `full_ind` | same (~39.5M candidates; expect a long initial-state phase) | `3587826:f1bd334969eaa3b725436e5155697dfc9e08457c2c002607acfb6e80180a9f1b` |
| `mutant_*` | 3 records, 2 principals, 1 lineage, levels 0..2, sem 0..1 | TLC must report a violation; each Python twin is non-equivalent in this domain |

## Procedure

```bash
# 1. Obtain tla2tools.jar through an approved path; note its release and SHA-256.
# 2. Run (the script never downloads anything and refuses a checksum mismatch):
TLC_JAR=/path/to/tla2tools.jar TLC_JAR_SHA256=<sha256> tla/run_oracle.sh small
TLC_JAR=... TLC_JAR_SHA256=... tla/run_oracle.sh mutants
TLC_JAR=... TLC_JAR_SHA256=... tla/run_oracle.sh full
# 3. Record: TLC version banner, jar SHA-256, cfgs, logs, MATCH/MISMATCH lines.
```

Expect first-run friction: the parser in `assurance_lab/tla.py` is tested
against hand-written samples in TLC's value syntax, not against a real dump,
and the spec itself has never been parsed. A mismatch must be investigated
from both sides; it is not resolved by editing whichever side is easier.

## What a pass would and would not show

- **Would:** two separately written transition systems, checked by
  different engines, explore exactly the same reachable states and agree on
  exactly the same Inv-states; four mutants are caught by TLC as well.
- **Would not:** independent *authorship*. Both encodings were written by
  the same author from the same reading of the handoff, so a shared
  misreading would pass. An independent reviewer writing the spec from the
  handoff alone would close that.
- **Would not:** anything beyond the finite domains above.
