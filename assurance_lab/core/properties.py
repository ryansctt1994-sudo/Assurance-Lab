"""Candidate inductive invariant, state safety, and transition properties.

* ``INV_CLAUSES``   – the candidate inductive invariant Inv (conjunction).
* ``safety``        – the encoded state-level NoSilentAuthorityIncrease target,
                       written against *derived* predicates (graph + flags) and
                       not against Inv's clauses, so ``Inv ⇒ Safety`` is a real
                       (if small) obligation rather than a syntactic tautology.
* ``TRANSITION_PROPS`` – properties of a single step (s, label, s').
"""

from __future__ import annotations

from typing import Callable

from .model import Config, State, live, superseded

# ---------------------------------------------------------------------------
# Inv clauses
# ---------------------------------------------------------------------------


def type_ok(cfg: Config, s: State) -> bool:
    if len(s.recs) != cfg.n_records or len(s.levels) != cfg.n_principals:
        return False
    if not (0 <= s.sem_now < cfg.n_sem):
        return False
    if any(not (0 <= lv <= cfg.max_level) for lv in s.levels):
        return False
    rng = range(cfg.n_records)
    if any(x not in rng for x in s.current):
        return False
    if any(a not in rng or b not in rng for (a, b) in s.edges):
        return False
    for r in s.recs:
        for v, n in ((r.lineage, cfg.n_lineages), (r.owner, cfg.n_principals),
                     (r.approver, cfg.n_principals), (r.sem, cfg.n_sem), (r.adj_sem, cfg.n_sem)):
            if v is not None and not (0 <= v < n):
                return False
    return True


def record_wf(cfg: Config, s: State) -> bool:
    for r in s.recs:
        if not r.admitted:
            if r != (False, None, None, None, None, False, False, None):
                return False
        elif None in (r.lineage, r.owner, r.approver, r.sem):
            return False
    return True


def adjudication_bound(cfg: Config, s: State) -> bool:
    """I_3/I_5: an adjudication stays bound to the record's admitted sem."""
    return all(r.adj_sem is None or (r.admitted and r.adj_sem == r.sem) for r in s.recs)


def independent_approval(cfg: Config, s: State) -> bool:
    """I_6: no admitted record was approved by its own beneficiary."""
    return all(not r.admitted or r.approver != r.owner for r in s.recs)


def approver_authorized(cfg: Config, s: State) -> bool:
    return all(not r.admitted or s.levels[r.approver] >= 1 for r in s.recs)


def sem_bound(cfg: Config, s: State) -> bool:
    return all(not r.admitted or r.sem <= s.sem_now for r in s.recs)


def current_coherent(cfg: Config, s: State) -> bool:
    """§8: Current(x) ⇒ Admitted ∧ ¬Invalidated ∧ ¬Retracted ∧ ¬Superseded."""
    return all(live(s, x) for x in s.current)


def no_orphan_live(cfg: Config, s: State) -> bool:
    """Every live record is the current head (no ambiguous live-but-not-current)."""
    return all(x in s.current for x in range(cfg.n_records) if live(s, x))


def head_unique(cfg: Config, s: State) -> bool:
    """§11: at most one current record per lineage."""
    seen = set()
    for x in s.current:
        lin = s.recs[x].lineage
        if lin in seen:
            return False
        seen.add(lin)
    return True


def edges_wf(cfg: Config, s: State) -> bool:
    """§9/§11: edges are forward-only, between admitted records, intra-lineage."""
    for a, b in s.edges:
        ra, rb = s.recs[a], s.recs[b]
        if not (a < b and ra.admitted and rb.admitted and ra.lineage == rb.lineage):
            return False
    return True


def no_fork(cfg: Config, s: State) -> bool:
    """Single-node model: each record has ≤1 successor and ≤1 predecessor."""
    srcs = [a for a, _ in s.edges]
    dsts = [b for _, b in s.edges]
    return len(srcs) == len(set(srcs)) and len(dsts) == len(set(dsts))


INV_CLAUSES: dict[str, Callable[[Config, State], bool]] = {
    "TypeOK": type_ok,
    "RecordWF": record_wf,
    "AdjudicationBound": adjudication_bound,
    "IndependentApproval": independent_approval,
    "ApproverAuthorized": approver_authorized,
    "SemBound": sem_bound,
    "CurrentCoherent": current_coherent,
    "NoOrphanLive": no_orphan_live,
    "HeadUnique": head_unique,
    "EdgesWF": edges_wf,
    "NoFork": no_fork,
}


def inv_failures(cfg: Config, s: State) -> list[str]:
    return [n for n, f in INV_CLAUSES.items() if not f(cfg, s)]


def inv(cfg: Config, s: State) -> bool:
    return all(f(cfg, s) for f in INV_CLAUSES.values())


# ---------------------------------------------------------------------------
# Encoded state safety
# ---------------------------------------------------------------------------


def safety_failures(cfg: Config, s: State) -> list[str]:
    """NoSilentAuthorityIncrease (state part), from derived predicates.

    For each record holding current authority:
      * it is admitted, and lifecycle-eligible as recomputed from the graph;
      * its admission was approved by someone other than its beneficiary,
        who held authority;
    and for each lineage, at most one record holds authority.
    """
    out = []
    per_lineage: dict[int, int] = {}
    for x in s.current:
        r = s.recs[x]
        if not r.admitted:
            out.append(f"current-unadmitted(r{x})")
            continue
        if r.invalidated or r.retracted or superseded(s, x):
            out.append(f"current-ineligible(r{x})")
        if r.approver == r.owner:
            out.append(f"current-self-approved(r{x})")
        elif s.levels[r.approver] < 1:
            out.append(f"current-unauthorized-approver(r{x})")
        per_lineage[r.lineage] = per_lineage.get(r.lineage, 0) + 1
    for lin, n in per_lineage.items():
        if n > 1:
            out.append(f"multiple-heads(lineage{lin})")
    return out


# ---------------------------------------------------------------------------
# Transition properties
# ---------------------------------------------------------------------------

AUTHORIZING = {"Admit", "Supersede"}


def no_silent_authority_increase(cfg: Config, s: State, lbl: tuple, t: State) -> list[str]:
    """Every authority increase is a named, independently approved transition.

    * A record may become current only in the step that freshly admits it,
      via Admit/Supersede naming it, with approver ≠ owner and an authorized
      approver.  (So Invalidate / Retract / Diagnose / RoleRevise / Adjudicate
      can never produce current authority — negative information does not
      create positive authority.)
    * A principal's level may rise only via Grant(p, q) naming p, with q ≠ p
      and q strictly more authoritative, and by exactly one step.
    """
    out = []
    newly = t.current - s.current
    kind = lbl[0]
    for x in newly:
        target = lbl[1] if kind == "Admit" else (lbl[2] if kind == "Supersede" else None)
        if kind not in AUTHORIZING or target != x:
            out.append(f"{kind}-silently-promoted(r{x})")
            continue
        if s.recs[x].admitted:
            out.append(f"{kind}-promoted-non-fresh(r{x})")
        o, a = lbl[-2], lbl[-1]
        if a == o:
            out.append(f"{kind}-self-authorized(r{x})")
        elif s.levels[a] < 1:
            out.append(f"{kind}-unauthorized-approver(r{x})")
    for p, (before, after) in enumerate(zip(s.levels, t.levels)):
        if after > before:
            if kind != "Grant" or lbl[1] != p:
                out.append(f"{kind}-silent-level-increase(p{p})")
            elif lbl[2] == p:
                out.append(f"Grant-self-authorized(p{p})")
            elif s.levels[lbl[2]] <= before or after != before + 1:
                out.append(f"Grant-insufficient-grantor(p{p})")
    return out


def history_monotone(cfg: Config, s: State, lbl: tuple, t: State) -> list[str]:
    """Append-only history: no erasure, rewrite, or reinterpretation."""
    out = []
    if not s.edges <= t.edges:
        out.append("edge-removed")
    if t.sem_now < s.sem_now:
        out.append("sem-rollback")
    for x, (r, q) in enumerate(zip(s.recs, t.recs)):
        if not r.admitted:
            continue
        if not q.admitted:
            out.append(f"unadmitted(r{x})")
        if (q.lineage, q.owner, q.approver) != (r.lineage, r.owner, r.approver):
            out.append(f"identity-rewrite(r{x})")
        if q.sem != r.sem:
            out.append(f"sem-rewrite(r{x})")
        if r.invalidated and not q.invalidated:
            out.append(f"uninvalidated(r{x})")
        if r.retracted and not q.retracted:
            out.append(f"unretracted(r{x})")
        if r.adj_sem is not None and q.adj_sem != r.adj_sem:
            out.append(f"adjudication-rewrite(r{x})")
    return out


def no_resurrection(cfg: Config, s: State, lbl: tuple, t: State) -> list[str]:
    """§10: an admitted record that is not current never becomes current again."""
    return [
        f"resurrected(r{x})"
        for x in t.current - s.current
        if s.recs[x].admitted
    ]


def diagnosis_inert(cfg: Config, s: State, lbl: tuple, t: State) -> list[str]:
    """I_4: Diagnose only appends; it changes nothing else."""
    if lbl[0] != "Diagnose":
        return []
    return [] if t == s else ["diagnosis-changed-state"]


TRANSITION_PROPS = {
    "NoSilentAuthorityIncrease": no_silent_authority_increase,
    "HistoryMonotone": history_monotone,
    "NoResurrection": no_resurrection,
    "DiagnosisInert": diagnosis_inert,
}


def transition_failures(cfg: Config, s: State, lbl: tuple, t: State) -> list[str]:
    out = []
    for name, f in TRANSITION_PROPS.items():
        out.extend(f"{name}:{m}" for m in f(cfg, s, lbl, t))
    return out
