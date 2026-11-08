---------------------------- MODULE AuthorityCore ----------------------------
(***************************************************************************)
(* Independent TLA+ statement of the core authority lifecycle, written to   *)
(* be checked by TLC as an oracle for the Python checker. Same state space  *)
(* (so distinct-state counts must agree exactly), separately written        *)
(* transition relation, invariant, safety and step properties.            *)
(*                                                                          *)
(* STATUS: UNVALIDATED. Never parsed by SANY or checked by TLC; running    *)
(* TLC awaits an approved execution path. See docs/ORACLE.md.              *)
(***************************************************************************)
EXTENDS Integers, FiniteSets   \* Integers: NULL is -1

CONSTANTS NRec, NPrin, NLin, MaxLevel, NSem, Mutant

R  == 0 .. (NRec - 1)
P  == 0 .. (NPrin - 1)
L  == 0 .. (NLin - 1)
Lv == 0 .. MaxLevel
S  == 0 .. (NSem - 1)
NULL == -1

VARIABLES recs, current, edges, levels, semNow
vars == <<recs, current, edges, levels, semNow>>

Unadmitted == [admitted |-> FALSE, lineage |-> NULL, owner |-> NULL, approver |-> NULL,
               sem |-> NULL, invalidated |-> FALSE, retracted |-> FALSE, adj |-> NULL]

Fresh(l, o, a) == [admitted |-> TRUE, lineage |-> l, owner |-> o, approver |-> a,
                   sem |-> semNow, invalidated |-> FALSE, retracted |-> FALSE, adj |-> NULL]

Init == /\ recs = [x \in R |-> Unadmitted]
        /\ current = {}
        /\ edges = {}
        /\ levels = [p \in P |-> IF p = 0 THEN MaxLevel ELSE 0]
        /\ semNow = 0

\* ---------------------------------------------------------------- derived
Superseded(x) == \E e \in edges : e[1] = x
Live(x) == recs[x].admitted /\ ~recs[x].invalidated /\ ~recs[x].retracted /\ ~Superseded(x)
NoCurrentIn(l) == \A x \in current : recs[x].lineage # l
Approval(o, a) == (Mutant = "beneficiary_self_approval" \/ a # o) /\ levels[a] >= 1

\* ---------------------------------------------------------------- actions
Admit(x, l, o, a) ==
    /\ ~recs[x].admitted
    /\ (Mutant = "admit_into_occupied_lineage" \/ NoCurrentIn(l))
    /\ Approval(o, a)
    /\ recs' = [recs EXCEPT ![x] = Fresh(l, o, a)]
    /\ current' = current \cup {x}
    /\ UNCHANGED <<edges, levels, semNow>>

Supersede(x, y, o, a) ==
    /\ IF Mutant = "stale_successor" THEN recs[x].admitted ELSE x \in current
    /\ x < y
    /\ ~recs[y].admitted
    /\ Approval(o, a)
    /\ recs' = [recs EXCEPT ![y] = Fresh(recs[x].lineage, o, a)]
    /\ current' = (current \ {x}) \cup {y}
    /\ edges' = edges \cup {<<x, y>>}
    /\ UNCHANGED <<levels, semNow>>

Adjudicate(x) ==
    /\ recs[x].admitted /\ recs[x].adj = NULL
    /\ recs' = [recs EXCEPT ![x].adj = recs[x].sem]
    /\ UNCHANGED <<current, edges, levels, semNow>>

Diagnose(x) == recs[x].admitted /\ UNCHANGED vars

Invalidate(x) ==
    /\ recs[x].admitted /\ ~recs[x].invalidated
    /\ recs' = [recs EXCEPT ![x].invalidated = TRUE]
    /\ current' = current \ {x}
    /\ UNCHANGED <<edges, levels, semNow>>

Retract(x) ==
    /\ recs[x].admitted /\ ~recs[x].retracted
    /\ recs' = [recs EXCEPT ![x].retracted = TRUE]
    /\ current' = IF Mutant = "resurrect_on_retract" /\ x \in current
                  THEN (current \ {x}) \cup {e[1] : e \in {f \in edges : f[2] = x}}
                  ELSE current \ {x}
    /\ UNCHANGED <<edges, levels, semNow>>

RoleRevise ==
    /\ semNow + 1 < NSem
    /\ semNow' = semNow + 1
    /\ UNCHANGED <<recs, current, edges, levels>>

Grant(p, q) ==
    /\ levels[p] < MaxLevel /\ q # p /\ levels[q] > levels[p]
    /\ levels' = [levels EXCEPT ![p] = levels[p] + 1]
    /\ UNCHANGED <<recs, current, edges, semNow>>

Next ==
    \/ \E x \in R, l \in L, o \in P, a \in P : Admit(x, l, o, a)
    \/ \E x \in R, y \in R, o \in P, a \in P : Supersede(x, y, o, a)
    \/ \E x \in R : Adjudicate(x) \/ Diagnose(x) \/ Invalidate(x) \/ Retract(x)
    \/ RoleRevise
    \/ \E p \in P, q \in P : Grant(p, q)

Spec == Init /\ [][Next]_vars

\* ---------------------------------------------------------------- invariant
RecType == {Unadmitted} \cup
           [admitted: {TRUE}, lineage: L, owner: P, approver: P, sem: S,
            invalidated: BOOLEAN, retracted: BOOLEAN, adj: S \cup {NULL}]

TypeOK == /\ recs \in [R -> RecType]
          /\ current \subseteq R
          /\ edges \subseteq R \X R
          /\ levels \in [P -> Lv]
          /\ semNow \in S

Inv ==
    /\ TypeOK
    /\ \A x \in R : recs[x].adj # NULL => recs[x].admitted /\ recs[x].adj = recs[x].sem
    /\ \A x \in R : recs[x].admitted => recs[x].approver # recs[x].owner
    /\ \A x \in R : recs[x].admitted => levels[recs[x].approver] >= 1
    /\ \A x \in R : recs[x].admitted => recs[x].sem <= semNow
    /\ current = {x \in R : Live(x)}                         \* coherent and complete
    /\ \A x, y \in current : recs[x].lineage = recs[y].lineage => x = y
    /\ \A e \in edges : /\ e[1] < e[2]
                        /\ recs[e[1]].admitted /\ recs[e[2]].admitted
                        /\ recs[e[1]].lineage = recs[e[2]].lineage
    /\ \A e, f \in edges : (e[1] = f[1] \/ e[2] = f[2]) => e = f   \* no fork

\* Encoded safety, from the informal principle (no reuse of Inv's clauses)
Safety ==
    /\ \A x \in current : /\ recs[x].admitted
                          /\ ~recs[x].invalidated /\ ~recs[x].retracted
                          /\ ~Superseded(x)
                          /\ recs[x].approver # recs[x].owner
                          /\ levels[recs[x].approver] >= 1
    /\ \A l \in L : Cardinality({x \in current : recs[x].lineage = l}) <= 1

\* ---------------------------------------------------------------- step properties
NoSilentAuthorityIncrease ==
    /\ \A x \in current' \ current :
          /\ ~recs[x].admitted /\ recs'[x].admitted                 \* fresh admission
          /\ recs'[x].approver # recs'[x].owner
          /\ levels[recs'[x].approver] >= 1
    /\ \A p \in P : levels'[p] > levels[p] =>
          /\ levels'[p] = levels[p] + 1
          /\ \E q \in P : q # p /\ levels[q] > levels[p]

HistoryMonotone ==
    /\ edges \subseteq edges'
    /\ semNow <= semNow'
    /\ \A x \in R : recs[x].admitted =>
          /\ recs'[x].admitted
          /\ recs'[x].lineage = recs[x].lineage /\ recs'[x].owner = recs[x].owner
          /\ recs'[x].approver = recs[x].approver /\ recs'[x].sem = recs[x].sem
          /\ recs[x].invalidated => recs'[x].invalidated
          /\ recs[x].retracted => recs'[x].retracted
          /\ recs[x].adj # NULL => recs'[x].adj = recs[x].adj

NoResurrection == \A x \in current' \ current : ~recs[x].admitted

StepProps == [][NoSilentAuthorityIncrease /\ HistoryMonotone /\ NoResurrection]_vars

\* ---------------------------------------------------------------- induction
\* Initial states = all Inv-states. Pruning, each justified by an Inv clause:
\* records drawn from the record-local candidates, edges forward-only, and
\* current fixed to the live set (the coherent-and-complete clause).
RecCand == {Unadmitted} \cup
           {r \in [admitted: {TRUE}, lineage: L, owner: P, approver: P, sem: S,
                   invalidated: BOOLEAN, retracted: BOOLEAN, adj: S \cup {NULL}] :
              r.approver # r.owner /\ (r.adj = NULL \/ r.adj = r.sem)}

IndInit ==
    /\ recs \in [R -> RecCand]
    /\ edges \in SUBSET {e \in R \X R : e[1] < e[2]}
    /\ levels \in [P -> Lv]
    /\ semNow \in S
    /\ current = {x \in R : Live(x)}
    /\ Inv

IndSpec == IndInit /\ [][Next]_vars

\* Unpruned variant for small domains: the whole typed space filtered by Inv
\* alone. Agreement with IndInit checks the pruning above independently.
IndInitUnpruned ==
    /\ recs \in [R -> RecType]
    /\ current \in SUBSET R
    /\ edges \in SUBSET (R \X R)
    /\ levels \in [P -> Lv]
    /\ semNow \in S
    /\ Inv
=============================================================================
