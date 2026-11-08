# Reports, by the commit each is bound to

Every file here is a snapshot-bound campaign report, kept byte-identical to
what the campaign wrote. A report is evidence only for the commit in its
own provenance section, never for a later one. `docs/RESULTS.md` is a copy
of the current development-lineage report.

| File | Bound to | Obligations | Lineage | Status |
|---|---|---|---|---|
| `main-c175fbd.md` | `c175fbd` | 32 | **published main baseline** | historical; author-reported; the subject of the R1 protocol (`assurance-validation/`). Byte-identical to `ba10a0c:docs/RESULTS.md` |
| `dev-748f80a.md` | `748f80a` | 67 | development (PR #2) | historical; byte-identical to `b535b1a:docs/RESULTS.md`; publishes matrix v1 |
| `dev-5a925e5-before-change.md` | `5a925e5` | 70 | development (PR #2) | **before-change receipt**: the `0fe2c2b` code, before the snapshot-integrity correction and matrix v2. Not evidence for v2 |

The main baseline's code has the `head()` defect that the development
lineage repaired (see `docs/METAMORPHIC.md`); none of the development
reports is evidence about `main`.
