# Handoff → code traceability

Source: "Authority/Evidence Architecture: Current Handoff" (2026-11-08).
Status key: **modeled** (in the exhaustively checked core), **tested**
(scenario/permutation tests, not exhaustive model checking), **partial**,
**not covered**.

| § | Topic | Status | Where |
|---|---|---|---|
| 2 | Negative info ⇏ positive authority | modeled | `NoSilentAuthorityIncrease`; mutants `invalidate_falls_back`, `resurrect_on_retract` |
| 3 | Stable X_⊥, no fallback | modeled + tested | `no_current`; `test_stable_no_current_state_*`; `test_read_repair_never_falls_back_*` |
| 4 | Pipeline; no invalidation shortcut | modeled | Supersede requires fresh admission; mutant `supersede_without_fresh_admission` |
| 5 I_1 | Completion ⇏ correctness | **not covered** | no execution-witness model yet |
| 5 I_2 | Admission causal closure | tested (in-process only) | `admission.py`; side channels and sandboxing out of scope |
| 5 I_3 | Prefix-determined adjudication | modeled | `AdjudicationBound`, `HistoryMonotone` |
| 5 I_4 | Diagnose ≠ Invalidate ≠ Supersede | modeled | `DiagnosisInert`; separate actions |
| 5 I_5 | Semantic-role invariance | modeled | `sem` bound at admission; mutant `sem_rewrite_on_role_revise` |
| 5 I_6 | No self-authorization | modeled | `IndependentApproval`, Grant guard; mutants `beneficiary_self_approval`, `self_grant` |
| 5 I_6R | Repair not its own oracle | partial | mutants + brute-force cross-check of the Inv generator; no independent reviewer process |
| 7 | Live / Fresh / BasisBound | partial | Live is derived (mutant `latched_live`); Fresh ≈ fresh admission; BasisBound **not covered** |
| 8 | Lifecycle coherence | modeled | `CurrentCoherent`, `NoOrphanLive` |
| 9–10 | Forward-only, append-only, no resurrection | modeled | `EdgesWF`, `HistoryMonotone`, `NoResurrection` |
| 11 | Lineage, per-lineage head uniqueness | modeled | `HeadUnique`; mutants `lineage_rewrite`, `cross_lineage_supersede` |
| 12 | Terminal ≠ current | modeled + tested | `live` needs ¬Invalidated ∧ ¬Retracted; replica `head()` |
| 13 | Reliance receipts | tested | `reliance.py` |
| 14 | Descendant closure, unknown → review | tested | `RelianceLedger.descendants` |
| 15 | BoundaryCompleteness triage | tested | `classify_boundary` (logic only; detecting real-world effects is out of scope) |
| 16–17 | Three-valued verdicts, scoped labels | implemented | `verdict.py` |
| 19–20 | Inductive obligations | modeled | `checker/induction.py`, results above |
| 21 | Retained counterexamples | tested | `test_handoff_failure_1/2/3_*` |
| 22 | Mutation testing | modeled | 13 mutants, BFS + induction |
| 23–25 | Store seam | tested | `store/retrieval.py` |
| 26 | Crash recovery + epoch checkpoint | tested | `store/recovery.py` (anchor trust is an assumption) |
| 27 | Schedule convergence | **modeled** (replica level) + tested | `distributed/mc.py`: 2 replicas (3 in `--full`), concurrent origination, reordering, ≤3 duplicates; convergence at every quiescent state. Plus 120 single-replica schedules |
| 28 | Successor conflict resolution | **modeled** (replica level) + tested | concurrent resolutions by different authorities; disagreement fails closed; higher epoch settles it. Found and fixed a split-brain (see below). Plus 720 schedules; self-auth / quorum / root tampering rejected |
| 29 | Execution-authority boundary | **not covered** | handoff text was truncated at this heading |

## Modeling choices to challenge

* **Diagnostics live outside the checked state.** Diagnose is an explicit
  stutter on all authority-relevant variables. That is the property we want,
  but it also means diagnostic *content* is not modeled.
* **Inv ⇒ Safety is close to trivial.** Safety is written against derived
  predicates and not Inv's clauses, but most of it is still implied by
  single Inv clauses. The substantive obligations are `Inv∧Next⇒Inv'` and the
  transition properties checked on every step from every Inv-state.
* **Authority levels are a toy.** Principal 0 starts at max level, others at
  0; Grant needs a strictly more authoritative, distinct grantor.
* **The distributed model checker covers scripted scenarios**, not arbitrary
  workloads: 2 replicas (3 in `--full`), bounded duplication, no message
  loss, crashes or Byzantine replicas. A replica may still serve a
  once-legitimate head that later loses a conflict (asserted in
  `test_valid_resolution_*`); reliance receipts are the mitigation.
* **Split-brain found by the replica model checker.** The first resolution
  rule kept whichever valid resolution arrived first and rejected the other
  as "already resolved". When two authorities resolved one fork
  concurrently, replicas converged on *different* heads, and the dispute
  was invisible, so escalation could never happen. Now every valid
  resolution is kept and the decision is a function of the set: top-epoch
  agreement decides, disagreement fails closed to CONFLICT, and a
  higher-epoch resolution settles it. The old rule is retained as the
  `first_applied_wins` mutant, which the checker must keep detecting.
* **Atomic successor+edge** in recovery (§8 option 1). A design that writes
  them separately would need its invariants reproved over the microsteps.

## Suggested next steps

1. TLA+ oracle: spec, cfgs, dump parser, digest targets and runner are in
   place but **UNVALIDATED**; running TLC awaits an approved execution path
   (`docs/ORACLE.md`).
2. Model I_1 (execution witness vs correctness) and BasisBound.
3. ~~Replica-level model checker~~: done (`distributed/mc.py`). Next: message
   loss with retransmission, crash/recovery, and Byzantine replicas.
4. Write out §29 and model the execution boundary.
5. ~~Run `--full` from an immutable snapshot~~: done; see `docs/PROVENANCE.md`.
6. ~~Metadata precision~~: done (`.3f`).
