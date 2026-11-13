# AL-SNAPSHOT-001: replay and scope (generated from scope.json)

Code under test (`assurance_lab/distributed/replica.py`, read from git):

| Label | Commit | replica.py SHA-256 | Restore boundary |
|---|---|---|---|
| published-main | `ba10a0c3d58e9ea2663e6b73dc763bee4bc29404` | `b0801e2300bd6245…` | no |
| repair | `deabdad605d3536a7b55110c63404abe64eecc9e` | `5b37c94ed203a17c…` | yes |
| dev-lineage | `52db4d74dc03b168e088e63576f0085baf6a2d6d` | `eed6f38a56a36d78…` | yes |

## Retained counterexamples (production restore; then head() with validation bypassed)

| CE | published-main | repair | dev-lineage |
|---|---|---|---|
| CE-1 | restore accepted (this revision has no restore boundary); head(L) raises KeyError('r1') at line 221 | restore refused; head(L) UNKNOWN | restore refused; head(L) UNKNOWN |
| CE-2 | restore accepted (this revision has no restore boundary); head(L) raises KeyError('r0') at line 245 | restore refused; head(L) UNKNOWN | restore refused; head(L) UNKNOWN |

## Genuine snapshots (164: every serialization prefix of every instance)

- published-main: accepted; heads equal the live replica's: 164 (equal to the generator's snapshot: 164/164)
- repair: accepted; heads equal the live replica's: 164 (equal to the generator's snapshot: 164/164)
- dev-lineage: accepted; heads equal the live replica's: 164 (equal to the generator's snapshot: 164/164)

## Damaged snapshots, production path, by damage class

| Damage | Class | n | published-main | repair | dev-lineage |
|---|---|---|---|---|---|
| drop invalidations | inconsistent | 21 | accepted; head differs from the genuine snapshot's: 7<br>accepted; head unchanged now and after further deliveries: 8<br>accepted; head unchanged now, differs after further deliveries: 6 | accepted; head differs from the genuine snapshot's: 7<br>accepted; head unchanged now and after further deliveries: 8<br>accepted; head unchanged now, differs after further deliveries: 6 | refused (INVALID_SNAPSHOT): 21 |
| drop one applied edge | inconsistent | 57 | accepted; head differs from the genuine snapshot's: 39<br>accepted; head unchanged now and after further deliveries: 13<br>accepted; head unchanged now, differs after further deliveries: 5 | accepted; head differs from the genuine snapshot's: 39<br>accepted; head unchanged now and after further deliveries: 13<br>accepted; head unchanged now, differs after further deliveries: 5 | refused (INVALID_SNAPSHOT): 57 |
| drop one admitted record | inconsistent | 112 | accepted; head differs from the genuine snapshot's: 36<br>accepted; head raises KeyError: 53<br>accepted; head unchanged now and after further deliveries: 7<br>accepted; head unchanged now, differs after further deliveries: 16 | accepted; head differs from the genuine snapshot's: 36<br>accepted; head unchanged now and after further deliveries: 3<br>accepted; head unchanged now, differs after further deliveries: 12<br>refused (INVALID_SNAPSHOT): 61 | refused (INVALID_SNAPSHOT): 112 |
| drop resolutions | inconsistent | 15 | accepted; head differs from the genuine snapshot's: 12<br>accepted; head unchanged now and after further deliveries: 3 | accepted; head differs from the genuine snapshot's: 12<br>accepted; head unchanged now and after further deliveries: 3 | refused (INVALID_SNAPSHOT): 15 |
| drop pending queue | inconsistent | 64 | accepted; head unchanged now and after further deliveries: 6<br>accepted; head unchanged now, differs after further deliveries: 58 | accepted; head unchanged now and after further deliveries: 6<br>accepted; head unchanged now, differs after further deliveries: 58 | refused (INVALID_SNAPSHOT): 64 |
| drop seen-set (duplicate filter) | inconsistent | 152 | accepted; head unchanged now and after further deliveries: 152 | accepted; head unchanged now and after further deliveries: 152 | refused (INVALID_SNAPSHOT): 152 |
| inject a backward edge (seen and applied) | inconsistent | 88 | accepted; head differs from the genuine snapshot's: 37<br>accepted; head unchanged now and after further deliveries: 28<br>accepted; head unchanged now, differs after further deliveries: 23 | accepted; head differs from the genuine snapshot's: 37<br>accepted; head unchanged now and after further deliveries: 28<br>accepted; head unchanged now, differs after further deliveries: 23 | refused (INVALID_SNAPSHOT): 88 |
| inject a self-authorized resolution (seen and applied) | inconsistent | 25 | accepted; head differs from the genuine snapshot's: 18<br>accepted; head unchanged now and after further deliveries: 7 | accepted; head differs from the genuine snapshot's: 18<br>accepted; head unchanged now and after further deliveries: 7 | refused (INVALID_SNAPSHOT): 25 |
| truncate the snapshot | malformed | 164 | restore raises ValueError: 164 | restore raises ValueError: 164 | refused (INVALID_SNAPSHOT): 164 |
| mistype a field (seq as text) | malformed | 112 | accepted; head unchanged now and after further deliveries: 45<br>accepted; head unchanged now, differs after further deliveries: 67 | accepted; head unchanged now and after further deliveries: 45<br>accepted; head unchanged now, differs after further deliveries: 67 | refused (INVALID_SNAPSHOT): 112 |
| roll back an applied invalidation | omission | 21 | accepted; head differs from the genuine snapshot's: 5<br>accepted; head unchanged now and after further deliveries: 16 | accepted; head differs from the genuine snapshot's: 5<br>accepted; head unchanged now and after further deliveries: 16 | accepted; head differs from the genuine snapshot's: 5<br>accepted; head unchanged now and after further deliveries: 16 |
| omit a pending event | omission | 64 | accepted; head unchanged now and after further deliveries: 64 | accepted; head unchanged now and after further deliveries: 64 | accepted; head unchanged now and after further deliveries: 64 |
| omit a rejected event | omission | 2 | accepted; head unchanged now and after further deliveries: 2 | accepted; head unchanged now and after further deliveries: 2 | accepted; head unchanged now and after further deliveries: 2 |

Totals over 897 damaged snapshots:

- published-main: accepted; head differs from the genuine snapshot's: 154; accepted; head raises KeyError: 53; accepted; head unchanged now and after further deliveries: 351; accepted; head unchanged now, differs after further deliveries: 175; restore raises ValueError: 164
- repair: accepted; head differs from the genuine snapshot's: 154; accepted; head unchanged now and after further deliveries: 347; accepted; head unchanged now, differs after further deliveries: 171; refused (INVALID_SNAPSHOT): 61; restore raises ValueError: 164
- dev-lineage: accepted; head differs from the genuine snapshot's: 5; accepted; head unchanged now and after further deliveries: 82; refused (INVALID_SNAPSHOT): 810

## head() raise sites with validation bypassed (diagnostic)

- published-main: KeyError at line 221: cur = self.admitted[cur.pred] ×38; KeyError at line 245: if any(self.admitted[src].lineage == lineage and self.decision(src)[0] == "disputed" ×30
- repair: none
- dev-lineage: none
