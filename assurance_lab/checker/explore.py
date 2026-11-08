"""Explicit-state BFS model checking with counterexample traces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..core.model import CoreModel, State
from ..core.properties import inv_failures, safety_failures, transition_failures


@dataclass
class Violation:
    kind: str  # "inv" | "safety" | "transition"
    messages: list[str]
    trace: list[tuple[Optional[tuple], State]]  # [(label-into-state, state), ...]

    def pretty(self) -> str:
        lines = [f"{self.kind} violation: {', '.join(self.messages)}"]
        for i, (lbl, st) in enumerate(self.trace):
            lines.append(f"  {i}: {lbl or 'Init'}")
        return "\n".join(lines)


@dataclass
class ExploreReport:
    model: str
    states: int
    transitions: int
    complete: bool  # False if the state budget was hit
    violation: Optional[Violation]
    actions_seen: dict[str, int] = field(default_factory=dict)
    max_depth: int = 0
    state_set: Optional[frozenset] = None  # only with keep_states=True


def explore(model: CoreModel, max_states: int = 2_000_000, check_inv: bool = True,
            stop_on_violation: bool = True, keep_states: bool = False) -> ExploreReport:
    cfg = model.cfg
    s0 = model.init()
    parent: dict[State, tuple[Optional[State], Optional[tuple]]] = {s0: (None, None)}
    depth = {s0: 0}
    q = deque([s0])
    transitions = 0
    actions_seen: dict[str, int] = {}
    violation: Optional[Violation] = None

    def trace_to(st: State, extra=None):
        out = []
        cur = st
        while cur is not None:
            p, lbl = parent[cur]
            out.append((lbl, cur))
            cur = p
        out.reverse()
        if extra:
            out.append(extra)
        return out

    def check_state(st: State) -> Optional[Violation]:
        msgs = safety_failures(cfg, st)
        if msgs:
            return Violation("safety", msgs, trace_to(st))
        if check_inv:
            msgs = inv_failures(cfg, st)
            if msgs:
                return Violation("inv", msgs, trace_to(st))
        return None

    violation = check_state(s0)
    complete = True
    while q and not (violation and stop_on_violation):
        s = q.popleft()
        for lbl, t in model.next(s):
            transitions += 1
            actions_seen[lbl[0]] = actions_seen.get(lbl[0], 0) + 1
            tf = transition_failures(cfg, s, lbl, t)
            if tf and violation is None:
                violation = Violation("transition", tf, trace_to(s, (lbl, t)))
                if stop_on_violation:
                    break
            if t not in parent:
                if len(parent) >= max_states:
                    complete = False
                    continue
                parent[t] = (s, lbl)
                depth[t] = depth[s] + 1
                v = check_state(t)
                if v and violation is None:
                    violation = v
                    if stop_on_violation:
                        break
                q.append(t)
    return ExploreReport(
        model=model.name,
        states=len(parent),
        transitions=transitions,
        complete=complete and not q,  # budget never hit and frontier drained
        violation=violation,
        actions_seen=actions_seen,
        max_depth=max(depth.values()),
        state_set=frozenset(parent) if keep_states else None,
    )
