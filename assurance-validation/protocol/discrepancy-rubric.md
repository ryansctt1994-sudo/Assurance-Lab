# Discrepancy-counting rubric (frozen before any audit)

Applies to reconciliation and to any human or agent audit of the same
evidence. The denominator is the number of eligible assertions enumerated
under this contract (for reconciliation: the subject's 32 claims), never the
number of findings someone happened to report.

| Rule | Definition |
|---|---|
| Unit | one independently falsifiable assertion at a specific artifact and evidence scope (a claim row: obligation + verdict label) |
| Duplicate reporting | the same defect in several places is one root finding, with each affected location listed |
| Wording variation | excluded when scope, meaning and evidence entitlement are unchanged |
| Evidence-class conflict | counted when author-reported evidence is presented as independent |
| Revision mismatch | counted when results are attributed to a revision other than the bound subject |
| Numerical inconsistency | counted once per distinct underlying derivation defect, not per occurrence |
| Incomplete evidence | recorded separately from confirmed contradiction (INCONCLUSIVE, not MISMATCH) |
| Unresolved disagreement | retained as is; never forced into agreement |

Run-dependent metadata (timestamps, durations, interpreter version, run id,
receipt id) is outside the claims and never counts.
