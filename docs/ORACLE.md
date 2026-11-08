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

From the Python campaign. The full-domain targets are the digest table in
`docs/RESULTS.md`.

| cfg | Domain | Python target (`count:digest`) |
|---|---|---|
| `small_reach` | 2 records, 2 principals, 1 lineage, levels 0..1, sem 0..0 | `670:90bd9c18894fdec12916ad1104da9df12c797dbb1fb75752fc6c67bcb3954774` |
| `small_ind` | same | `812:44e4ac49ad9f94ba7045196517bb4c104cf9fbfa38245566e4ad230d0e0fb54d` |
| `small_ind_unpruned` | same (278,784 typed candidates) | same as `small_ind` |
| `full_reach` | 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1 | `reachable` row of `docs/RESULTS.md` (1,314,526 states) |
| `full_ind` | same (~39.5M candidates; expect a long initial-state phase) | `inv_states` row of `docs/RESULTS.md` (3,587,826 states) |
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
