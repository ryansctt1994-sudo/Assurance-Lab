# Discrepancies found reconciling `v2-acceptance-3c6fad143994`

Kept outside the sealed directory, so the seal (`output-hashes.sha256`,
commitment `66a029a5…`) stays exactly as the receipt wrote it. Counted under
`assurance-validation/protocol/discrepancy-rubric.md`.

## D-1: derived test count 253, pytest summary 291 (one derivation defect)

| | Value | Source |
|---|---|---|
| pytest's own summary | `291 passed in 747.08s` | sealed `pytest.stdout.log`, `tests.json` → `summary` |
| lines marked `PASSED` in the raw log | 291 | sealed `pytest.stdout.log` |
| lines marked FAILED, ERROR, SKIPPED, XFAIL or XPASS | 0 | sealed `pytest.stdout.log` |
| receipt's derived index | `{"PASSED": 253}` | `tests.json` → `outcomes`, `pytest-outcomes.json` |

**Cause.** `run_tests` in `scripts/v2_acceptance_receipt.py` (at `3c6fad1`)
splits each pytest line on whitespace and takes the first token as the test
id. The 38 tests whose parametrized ids contain spaces (for example
`tests/test_core.py::test_clean_induction[2 records, 2 principals, 1 lineages,
levels 0..2, sem 0..1]`) are therefore missing from the index.

**Classification.** Numerical inconsistency arising from one derivation
defect in the receipt script. It is not a test failure, and it is not an
evidence gap: the raw log is sealed and complete.

**Effect on the gates: none.** The `tests` gate reads the exit code (0). Each
negative-control test the gates name has an id without spaces, and every one
was found and `PASSED`. A name missing from the index counts as *not
passed*, so this defect can undercount but cannot produce a false pass.

**Disposition.** Retained unaltered in the sealed receipt. Corrected in the
next candidate's receipt script (the index is keyed on the outcome token,
and its total is reconciled against pytest's summary).
