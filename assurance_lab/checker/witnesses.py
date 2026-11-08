"""Reachability witnesses: the model is not over-constrained.

Safety properties hold vacuously in a model that cannot do anything. Each
witness below names a behavior the handoff *intends* to be possible and
finds a **shortest** path from Init to a state exhibiting it (BFS with
recorded predecessors), returning the concrete action sequence and states.

These are **existential** claims ("this can happen"), not liveness claims
("this eventually happens"). A reachable example says nothing about
progress under fairness; no liveness property is checked in this repo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from ..core.model import Config, CoreModel, State, superseded


@dataclass(frozen=True)
class WitnessSpec:
    """A state witness (``holds`` on a state) or a transition witness
    (``step`` on (s, label, t)). Use a transition witness whenever the
    behavior is about *order* ("X after Y"): a state predicate cannot see
    order, and a calibration test caught exactly that mistake here."""

    name: str
    section: str  # handoff reference
    meaning: str
    holds: Optional[Callable[[Config, State], bool]] = None
    step: Optional[Callable[[Config, State, tuple, State], bool]] = None


def _lineages(cfg: Config):
    return range(cfg.n_lineages)


def _no_current(s: State, lin: int) -> bool:
    return not any(s.recs[x].lineage == lin for x in s.current)


SPECS = (
    WitnessSpec("stable no-current state", "§3",
                "a lineage whose record was invalidated has no current record (X_⊥)",
                lambda c, s: any(r.admitted and r.invalidated and _no_current(s, r.lineage) for r in s.recs)),
    WitnessSpec("re-admission after X_⊥", "§4",
                "a fresh admission into a lineage whose authority was removed by invalidation (X → X_⊥ → X')",
                step=lambda c, s, lbl, t: lbl[0] == "Admit" and _no_current(s, lbl[2]) and any(
                    r.lineage == lbl[2] and r.invalidated and not r.retracted and not superseded(s, i)
                    for i, r in enumerate(s.recs) if r.admitted)),
    WitnessSpec("supersession chain", "§9", "x → y → z within one lineage",
                lambda c, s: any((a, b) in s.edges and (b, d) in s.edges
                                 for a in range(c.n_records) for b in range(c.n_records)
                                 for d in range(c.n_records))),
    WitnessSpec("independent lineages", "§11", "two lineages each have a current head",
                lambda c, s: len({s.recs[x].lineage for x in s.current}) >= 2),
    WitnessSpec("adjudication bound to an old meaning", "§5 I_5",
                "a record adjudicated under semantic version v while the current version is newer",
                lambda c, s: any(r.adj_sem is not None and r.adj_sem < s.sem_now for r in s.recs)),
    WitnessSpec("granted authority exercised", "§5 I_6",
                "a principal that started without authority, after an independent grant, approves an admission",
                lambda c, s: any(r.admitted and c.levels0()[r.approver] == 0 for r in s.recs)),
    WitnessSpec("retraction without resurrection", "§10",
                "a successor is retracted and its superseded predecessor does not regain authority",
                lambda c, s: any((a, b) in s.edges and s.recs[b].retracted and _no_current(s, s.recs[b].lineage)
                                 for a in range(c.n_records) for b in range(c.n_records))),
    WitnessSpec("invalidation of a historical premise", "§4, §14",
                "an already superseded record is later invalidated",
                lambda c, s: any(r.admitted and r.invalidated and superseded(s, i) for i, r in enumerate(s.recs))),
)


@dataclass
class Witness:
    spec: WitnessSpec
    path: Optional[list]  # [(label or None, state), ...] from Init; None if unreachable
    explored: int

    @property
    def found(self) -> bool:
        return self.path is not None

    @property
    def actions(self) -> list:
        return [lbl for lbl, _s in (self.path or []) if lbl]


def find(spec: WitnessSpec, model: CoreModel, max_states: int = 3_000_000) -> Witness:
    cfg = model.cfg
    s0 = model.init()
    parent = {s0: (None, None)}
    q = deque([s0])
    def path_to(st, extra=None):
        path, cur = [], st
        while cur is not None:
            p, lbl = parent[cur]
            path.append((lbl, cur))
            cur = p
        return path[::-1] + ([extra] if extra else [])

    while q:
        s = q.popleft()
        if spec.holds is not None and spec.holds(cfg, s):
            return Witness(spec, path_to(s), len(parent))
        for lbl, t in model.next(s):
            if spec.step is not None and spec.step(cfg, s, lbl, t):
                return Witness(spec, path_to(s, (lbl, t)), len(parent))
            if t not in parent and len(parent) < max_states:
                parent[t] = (s, lbl)
                q.append(t)
    return Witness(spec, None, len(parent))


def replay(model: CoreModel, actions: list) -> State:
    """Re-execute a witness's action sequence from Init, step by step."""
    s = model.init()
    for lbl in actions:
        s = dict(model.next(s))[lbl]
    return s


def confirms(spec: WitnessSpec, model: CoreModel, actions: list) -> bool:
    """Replay the actions and check the witness predicate independently."""
    if spec.holds is not None:
        return spec.holds(model.cfg, replay(model, actions))
    pre = replay(model, actions[:-1])
    lbl = actions[-1]
    return spec.step(model.cfg, pre, lbl, dict(model.next(pre))[lbl])


def all_witnesses(cfg: Config) -> list[Witness]:
    return [find(spec, CoreModel(cfg)) for spec in SPECS]
