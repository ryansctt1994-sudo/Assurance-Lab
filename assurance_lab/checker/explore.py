"""Explicit-state BFS model checking with counterexample traces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..core.model import CoreModel, State
from ..core.properties import INV_CLAUSES, TRANSITION_PROPS, inv_failures, safety_failures, transition_failures

#: Every registered property name ``explore(properties=...)`` accepts.
PROPERTIES = (*INV_CLAUSES, "Safety", *TRANSITION_PROPS)


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
    # property -> first violation of that property met in BFS order (with
    # stop_on_violation=False this covers the whole reachable graph)
    violations: dict = field(default_factory=dict)


def explore(model: CoreModel, max_states: int = 2_000_000, check_inv: bool = True,
            stop_on_violation: bool = True, keep_states: bool = False, *,
            properties: Optional[frozenset] = None) -> ExploreReport:
    """BFS from Init.

    ``properties`` selects which properties are *evaluated*: ``None`` = all
    registered ones (``PROPERTIES``), an empty set = none, unknown names
    raise. Selection is observational only: it never changes successor
    generation, so with ``stop_on_violation=False`` the explored graph is
    identical whatever is selected (tested).
    """
    cfg = model.cfg
    if properties is not None:
        unknown = set(properties) - set(PROPERTIES)
        if unknown:
            raise ValueError(f"unknown properties: {sorted(unknown)}")

    def wanted(name: str) -> bool:
        return properties is None or name in properties

    firsts: dict[str, Violation] = {}

    def note(v: Violation, names) -> None:
        for n in names:
            firsts.setdefault(n, v)
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
        found = None
        if wanted("Safety"):
            msgs = safety_failures(cfg, st)
            if msgs:
                found = Violation("safety", msgs, trace_to(st))
                note(found, ["Safety"])
        if check_inv:
            msgs = [n for n in inv_failures(cfg, st) if wanted(n)]
            if msgs:
                v = Violation("inv", msgs, trace_to(st))
                note(v, msgs)
                found = found or v
        return found

    violation = check_state(s0)
    complete = True
    while q and not (violation and stop_on_violation):
        s = q.popleft()
        for lbl, t in model.next(s):
            transitions += 1
            actions_seen[lbl[0]] = actions_seen.get(lbl[0], 0) + 1
            tf = [m for m in transition_failures(cfg, s, lbl, t) if wanted(m.split(":", 1)[0])]
            if tf:
                v = Violation("transition", tf, trace_to(s, (lbl, t)))
                note(v, {m.split(":", 1)[0] for m in tf})
                if violation is None:
                    violation = v
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
        violations=firsts,
    )
