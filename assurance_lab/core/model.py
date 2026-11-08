"""Finite-state core lifecycle model (handoff §2-12, §18).

State is deliberately plain tuples so the whole state space of a small
configuration can be enumerated exhaustively.

Action grammar: Admit, Supersede, Adjudicate, Diagnose, Invalidate, Retract,
RoleRevise, Grant.

Design choices that are *modeling decisions* (not derived from the handoff)
are marked "MODEL CHOICE" so they can be challenged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, NamedTuple, Optional


@dataclass(frozen=True)
class Config:
    n_records: int = 3
    n_principals: int = 2
    n_lineages: int = 2
    max_level: int = 2  # authority levels are 0..max_level
    n_sem: int = 2  # semantic versions are 0..n_sem-1
    # MODEL CHOICE: principal 0 starts fully authorized, others at 0. Authority
    # for anyone else must be granted by an independent principal.
    init_levels: Optional[tuple[int, ...]] = None

    def levels0(self) -> tuple[int, ...]:
        if self.init_levels is not None:
            assert len(self.init_levels) == self.n_principals
            return self.init_levels
        return (self.max_level,) + (0,) * (self.n_principals - 1)

    def describe(self) -> str:
        return (
            f"{self.n_records} records, {self.n_principals} principals, "
            f"{self.n_lineages} lineages, levels 0..{self.max_level}, "
            f"sem 0..{self.n_sem - 1}"
        )


class Rec(NamedTuple):
    admitted: bool
    lineage: Optional[int]
    owner: Optional[int]  # beneficiary principal
    approver: Optional[int]  # independent admitting authority
    sem: Optional[int]  # semantic version bound at admission (I_5)
    invalidated: bool
    retracted: bool
    adj_sem: Optional[int]  # None = not adjudicated; else bound sem (I_3)


UNADMITTED = Rec(False, None, None, None, None, False, False, None)


class State(NamedTuple):
    recs: tuple[Rec, ...]
    current: frozenset  # stored head flags (a cache that Inv must keep honest)
    edges: frozenset  # supersession edges (x, y): y supersedes x
    levels: tuple[int, ...]  # authority level per principal
    sem_now: int  # current semantic version (bumped only by RoleRevise)
    # MODEL CHOICE: the diagnostic log is append-only and authority-irrelevant,
    # so it lives outside the checked state; Diagnose is an explicit stutter on
    # every authority-relevant variable (the DiagnosisInert property).


def init_state(cfg: Config) -> State:
    return State(
        recs=(UNADMITTED,) * cfg.n_records,
        current=frozenset(),
        edges=frozenset(),
        levels=cfg.levels0(),
        sem_now=0,
    )


# ---------------------------------------------------------------------------
# Derived predicates (computed from history, never latched)
# ---------------------------------------------------------------------------


def superseded(s: State, x: int) -> bool:
    return any(a == x for (a, _b) in s.edges)


def live(s: State, x: int) -> bool:
    """Live(x,s) = Admitted ∧ ¬Invalidated ∧ ¬Retracted ∧ ¬Superseded (§7)."""
    r = s.recs[x]
    return r.admitted and not r.invalidated and not r.retracted and not superseded(s, x)


def lineage_members(s: State, lin: int) -> list[int]:
    return [i for i, r in enumerate(s.recs) if r.admitted and r.lineage == lin]


def no_current(s: State, lin: int) -> bool:
    """X_⊥ for a lineage: no current authoritative answer exists (§3)."""
    return not any(s.recs[x].lineage == lin for x in s.current)


def _set(t: tuple, i: int, v) -> tuple:
    return t[:i] + (v,) + t[i + 1 :]


# ---------------------------------------------------------------------------
# Transition relation
# ---------------------------------------------------------------------------


class CoreModel:
    """Clean Next relation. Mutants subclass and override a single action."""

    name = "clean"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def init(self) -> State:
        return init_state(self.cfg)

    def next(self, s: State) -> Iterator[tuple[tuple, State]]:
        yield from self.admit(s)
        yield from self.supersede(s)
        yield from self.adjudicate(s)
        yield from self.diagnose(s)
        yield from self.invalidate(s)
        yield from self.retract(s)
        yield from self.role_revise(s)
        yield from self.grant(s)
        yield from self.extra(s)

    # -- guards shared by authority-increasing actions --------------------

    def approval_ok(self, s: State, owner: int, approver: int) -> bool:
        # I_6: no self-authorizing transition; approver must hold authority.
        return approver != owner and s.levels[approver] >= 1

    def fresh_admission_ok(self, s: State, y: int) -> bool:
        # Fresh admission: y has never been admitted before.
        return not s.recs[y].admitted

    # -- actions ----------------------------------------------------------

    def admit(self, s: State):
        """Admit a fresh record into a lineage that is in X_⊥ (or empty)."""
        cfg = self.cfg
        for x in range(cfg.n_records):
            if not self.fresh_admission_ok(s, x):
                continue
            for lin in range(cfg.n_lineages):
                if not self.admit_lineage_ok(s, lin):
                    continue
                for o in range(cfg.n_principals):
                    for a in range(cfg.n_principals):
                        if not self.approval_ok(s, o, a):
                            continue
                        rec = Rec(True, lin, o, a, s.sem_now, False, False, None)
                        yield (
                            ("Admit", x, lin, o, a),
                            s._replace(recs=_set(s.recs, x, rec), current=s.current | {x}),
                        )

    def admit_lineage_ok(self, s: State, lin: int) -> bool:
        return no_current(s, lin)

    def supersede(self, s: State):
        """x -> y: y is freshly admitted into x's lineage and becomes head."""
        cfg = self.cfg
        for x in range(cfg.n_records):
            if not self.supersede_source_ok(s, x):
                continue
            for y in range(cfg.n_records):
                if y <= x:  # forward-only ordering (§9)
                    continue
                if not self.fresh_admission_ok(s, y):
                    continue
                lin_y = self.successor_lineage(s, x)
                for lin in lin_y:
                    for o in range(cfg.n_principals):
                        for a in range(cfg.n_principals):
                            if not self.approval_ok(s, o, a):
                                continue
                            rec = Rec(True, lin, o, a, s.sem_now, False, False, None)
                            yield (
                                ("Supersede", x, y, o, a),
                                s._replace(
                                    recs=_set(s.recs, y, rec),
                                    current=(s.current - {x}) | {y},
                                    edges=s.edges | {(x, y)},
                                ),
                            )

    def supersede_source_ok(self, s: State, x: int) -> bool:
        return x in s.current

    def successor_lineage(self, s: State, x: int) -> list[int]:
        return [s.recs[x].lineage]

    def adjudicate(self, s: State):
        for x, r in enumerate(s.recs):
            if r.admitted and r.adj_sem is None:
                yield (("Adjudicate", x), s._replace(recs=_set(s.recs, x, r._replace(adj_sem=r.sem))))

    def diagnose(self, s: State):
        # Diagnosis appends explanatory information; it never touches
        # adjudications, lifecycle flags, or authority.
        for x, r in enumerate(s.recs):
            if r.admitted:
                yield (("Diagnose", x), s)

    def invalidate(self, s: State):
        for x, r in enumerate(s.recs):
            if r.admitted and not r.invalidated:
                yield (
                    ("Invalidate", x),
                    s._replace(recs=_set(s.recs, x, r._replace(invalidated=True)), current=s.current - {x}),
                )

    def retract(self, s: State):
        for x, r in enumerate(s.recs):
            if r.admitted and not r.retracted:
                yield (
                    ("Retract", x),
                    s._replace(recs=_set(s.recs, x, r._replace(retracted=True)), current=s.current - {x}),
                )

    def role_revise(self, s: State):
        # Explicit ROLE_REVISION: new semantic version for *future* admissions;
        # historical records stay bound to the version they were admitted under.
        if s.sem_now + 1 < self.cfg.n_sem:
            yield (("RoleRevise", s.sem_now + 1), s._replace(sem_now=s.sem_now + 1))

    def grant(self, s: State):
        # Authority increase for p requires an independent, strictly more
        # authoritative principal q.
        cfg = self.cfg
        for p in range(cfg.n_principals):
            if s.levels[p] >= cfg.max_level:
                continue
            for q in range(cfg.n_principals):
                if q != p and s.levels[q] > s.levels[p]:
                    yield (("Grant", p, q), s._replace(levels=_set(s.levels, p, s.levels[p] + 1)))

    def extra(self, s: State):
        return iter(())


ACTION_KINDS = (
    "Admit",
    "Supersede",
    "Adjudicate",
    "Diagnose",
    "Invalidate",
    "Retract",
    "RoleRevise",
    "Grant",
)
