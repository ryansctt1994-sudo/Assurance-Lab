"""Replica-level explicit-state model checker (§27-28).

Replaces "one replica, permutations of a fixed event list" with an
exhaustive exploration of a small network:

* each replica originates its own scripted events, only once it has
  causally seen what they depend on (you can only supersede a record you
  have seen), and broadcasts them;
* in-flight messages are a bag: any one may be delivered next (reordering),
  and a bounded number may be duplicated;
* *resolvers* are authorities attached to a replica that issue a
  SUCCESSOR_CONFLICT_RESOLUTION when that replica observes a fork it has
  not yet seen resolved (or, for escalation, observes conflicting
  resolutions), so several authorities can resolve concurrently.

Checked at **every** reachable state, against what each replica has
observed, written independently of ``Replica.head``:

* NoSilentChoice: an unresolved fork ⇒ head is CONFLICT.
* ConflictingResolutionsFailClosed: top-epoch resolutions of one fork that
  disagree ⇒ head is CONFLICT.
* LoserNeverHeads: the head is never the loser of the effective (top-epoch,
  agreed) resolution, nor anything after it.
* NoFallback: the head is never invalidated or superseded by an eligible
  successor (X_⊥ is not "use the previous record").

Checked at every **quiescent** state (no message in flight, no action
enabled): all replicas have identical canonical state and head, nothing is
pending, every script ran to completion, and the head is the scenario's
expected outcome.

Bounds: the scenarios below, 2 replicas, a duplication budget. Not modeled:
message loss without retransmission, crashes, more replicas, Byzantine
replicas.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .replica import (
    CONFLICT, Admit, Edge, Event, Invalidate, Replica, ResolutionPolicy, Resolve, make_resolution,
)

POLICY = ResolutionPolicy(frozenset({"alice", "bob", "carol"}), 2)


@dataclass(frozen=True)
class Resolver:
    replica: int  # index of the replica the authority observes
    source: str
    winner: str
    loser: str
    approvals: frozenset
    epoch: int = 1
    trigger: str = "fork"  # "fork": sees an unresolved fork; "dispute": sees conflicting resolutions


@dataclass(frozen=True)
class Scenario:
    name: str
    scripts: tuple[tuple[Event, ...], ...]  # per replica, in origination order
    resolvers: tuple[Resolver, ...] = ()
    # Expected head at quiescence, keyed by the set of resolver indices that
    # actually issued (which ones do depends on the schedule). A quiescent
    # state whose issued set is not a key is itself a violation.
    expect: tuple[tuple[frozenset, Optional[str]], ...] = ()
    max_dups: int = 3
    lineage: str = "L"
    policy: ResolutionPolicy = POLICY
    first_applied_wins: bool = False  # replica mutant (retained counterexample)
    naive_stale_guard: bool = False  # replica mutant (handoff failure #3)


# ------------------------------------------------------------ observations


def _resolutions(rep: Replica, source: str) -> set[Resolve]:
    v = rep.resolutions.get(source)
    if v is None:
        return set()
    return set(v) if isinstance(v, (set, frozenset)) else {v}


def _effective(rep: Replica, source: str):
    """('none'|'agreed'|'disputed', winner, loser) from top-epoch resolutions."""
    rs = _resolutions(rep, source)
    if not rs:
        return "none", None, None
    top = max(r.epoch for r in rs)
    decisions = {(r.winner, r.loser) for r in rs if r.epoch == top}
    if len(decisions) > 1:
        return "disputed", None, None
    (w, lo), = decisions
    return "agreed", w, lo


def _after(rep: Replica, roots: set[str]) -> set[str]:
    out, frontier = set(), list(roots)
    while frontier:
        n = frontier.pop()
        if n not in out:
            out.add(n)
            frontier.extend(b for a, b in rep.edges if a == n)
    return out


def safety_violations(rep: Replica, lineage: str) -> list[str]:
    head = rep.head(lineage)
    out = []
    sources = {a for a, _ in rep.edges}
    losers: set[str] = set()
    for x in sources:
        if rep.admitted.get(x) is None or rep.admitted[x].lineage != lineage:
            continue
        status, _w, lo = _effective(rep, x)
        if status == "agreed":
            losers |= _after(rep, {lo})
    for x in sources:
        if rep.admitted[x].lineage != lineage:
            continue
        succ = {b for a, b in rep.edges if a == x and b not in losers}
        status, _w, _lo = _effective(rep, x)
        if len(succ) > 1 and status == "none" and head != CONFLICT:
            out.append(f"NoSilentChoice: fork at {x} unresolved but head={head}")
        if status == "disputed" and head != CONFLICT:
            out.append(f"ConflictingResolutionsFailClosed: disputed {x} but head={head}")
    if head not in (None, CONFLICT):
        if head in losers:
            out.append(f"LoserNeverHeads: head={head} is (after) an effective loser")
        if head in rep.invalidated:
            out.append(f"NoFallback: head={head} is invalidated")
        if any(a == head and b not in losers for a, b in rep.edges):
            out.append(f"NoFallback: head={head} is superseded")
    return out


# ------------------------------------------------------------ exploration


@dataclass
class MCReport:
    scenario: str
    states: int
    transitions: int
    quiescent: int
    violation: Optional[str] = None
    trace: list = field(default_factory=list)
    quiescent_heads: set = field(default_factory=set)
    issued_sets: set = field(default_factory=set)


def _label(ev) -> str:
    if isinstance(ev, Admit):
        return f"Admit({ev.rid}{', pred=' + ev.pred if ev.pred else ''})"
    if isinstance(ev, Edge):
        return f"Edge({ev.old}->{ev.new})"
    if isinstance(ev, Invalidate):
        return f"Invalidate({ev.rid})"
    if isinstance(ev, Resolve):
        return f"Resolve(winner={ev.winner}, epoch={ev.epoch}, approvals={sorted(ev.approvals)})"
    return repr(ev)


def _ready_to_originate(rep: Replica, ev: Event) -> bool:
    if isinstance(ev, Admit):
        return ev.pred is None or ev.pred in rep.admitted
    if isinstance(ev, Edge):
        return ev.old in rep.admitted and ev.new in rep.admitted
    if isinstance(ev, Invalidate):
        return ev.rid in rep.admitted
    return True


def _resolver_enabled(rep: Replica, rv: Resolver) -> bool:
    if not ({(rv.source, rv.winner), (rv.source, rv.loser)} <= rep.edges):
        return False
    status, _w, _lo = _effective(rep, rv.source)
    if rv.trigger == "fork":
        return status == "none"
    return status == "disputed"


def check_scenario(sc: Scenario, max_states: int = 2_000_000) -> MCReport:
    n = len(sc.scripts)
    flags = {"first_applied_wins": sc.first_applied_wins, "naive_stale_guard": sc.naive_stale_guard}
    thaw = lambda fr: Replica.thaw(sc.policy, fr, **flags)  # noqa: E731
    s0 = (tuple(Replica(sc.policy, **flags).freeze() for _ in range(n)), (0,) * n, frozenset(), (),
          sc.max_dups)
    parent = {s0: (None, None)}
    q = deque([s0])
    transitions = quiescent = 0
    heads_at_quiescence: set = set()
    issued_seen: set = set()

    def trace(st, extra=None):
        out, cur = [], st
        while cur is not None:
            p, lbl = parent[cur]
            if lbl:
                out.append(lbl)
            cur = p
        out.reverse()
        return out + ([extra] if extra else [])

    def broadcast(inflight, origin, ev):
        return tuple(sorted(inflight + tuple((j, ev) for j in range(n) if j != origin), key=repr))

    def fail(st, msg):
        return MCReport(sc.name, len(parent), transitions, quiescent, msg, trace(st),
                        heads_at_quiescence, issued_seen)

    while q:
        s = q.popleft()
        reps, prog, issued, inflight, dups = s
        for i, fr in enumerate(reps):
            v = safety_violations(thaw(fr), sc.lineage)
            if v:
                return fail(s, f"replica {i}: {v[0]}")
        succs = []
        for i in range(n):  # originate scripted events
            if prog[i] < len(sc.scripts[i]):
                ev = sc.scripts[i][prog[i]]
                rep = thaw(reps[i])
                if _ready_to_originate(rep, ev):
                    rep.deliver(ev)
                    succs.append((f"R{i} originates {_label(ev)}",
                                  (reps[:i] + (rep.freeze(),) + reps[i + 1:],
                                   prog[:i] + (prog[i] + 1,) + prog[i + 1:], issued,
                                   broadcast(inflight, i, ev), dups)))
        for k, rv in enumerate(sc.resolvers):  # authorities resolve
            if k in issued:
                continue
            rep = thaw(reps[rv.replica])
            if _resolver_enabled(rep, rv):
                ev = make_resolution(rep, rv.source, rv.winner, rv.loser, rv.approvals, epoch=rv.epoch)
                rep.deliver(ev)
                i = rv.replica
                succs.append((f"R{i} authority issues {_label(ev)}",
                              (reps[:i] + (rep.freeze(),) + reps[i + 1:], prog, issued | {k},
                               broadcast(inflight, i, ev), dups)))
        for idx, (j, ev) in enumerate(inflight):  # deliver / duplicate one message
            if idx and inflight[idx - 1] == (j, ev):
                continue  # identical copies give identical successors
            rest = inflight[:idx] + inflight[idx + 1:]
            rep = thaw(reps[j])
            rep.deliver(ev)
            succs.append((f"deliver {_label(ev)} to R{j}",
                          (reps[:j] + (rep.freeze(),) + reps[j + 1:], prog, issued, rest, dups)))
            if dups > 0:
                succs.append((f"duplicate {_label(ev)} to R{j}",
                              (reps, prog, issued, tuple(sorted(inflight + ((j, ev),), key=repr)),
                               dups - 1)))
        if not succs:
            quiescent += 1
            live = [thaw(fr) for fr in reps]
            canon = {r.canonical() for r in live}
            heads = {r.head(sc.lineage) for r in live}
            heads_at_quiescence |= heads
            issued_seen.add(issued)
            if len(canon) > 1 or len(heads) > 1:
                return fail(s, f"divergence at quiescence: heads={sorted(map(str, heads))}")
            if any(r.pending for r in live):
                return fail(s, "events still pending at quiescence")
            if any(prog[i] < len(sc.scripts[i]) for i in range(n)):
                return fail(s, "a replica's script never completed")
            table = dict(sc.expect)
            if issued not in table:
                return fail(s, f"unexpected set of issued resolutions at quiescence: {sorted(issued)}")
            if heads != {table[issued]}:
                return fail(s, f"quiescent head {heads.pop()} != expected {table[issued]} "
                               f"(issued: {sorted(issued)})")
        for lbl, t in succs:
            transitions += 1
            if t not in parent:
                if len(parent) >= max_states:
                    return MCReport(sc.name, len(parent), transitions, quiescent,
                                    "UNKNOWN: state budget exhausted", [], heads_at_quiescence)
                parent[t] = (s, lbl)
                q.append(t)
    return MCReport(sc.name, len(parent), transitions, quiescent, None, [], heads_at_quiescence,
                    issued_seen)


# ------------------------------------------------------------ scenarios

_r0 = Admit("r0", "L", 0, "alice")
_r1 = Admit("r1", "L", 1, "bob", pred="r0")
_r2_fork = Admit("r2", "L", 2, "carol", pred="r0")
_r2_chain = Admit("r2", "L", 2, "carol", pred="r1")
_A_FORK = (_r0, _r1, Edge("r0", "r1"))
_B_FORK = (_r2_fork, Edge("r0", "r2"))

E = frozenset

SCENARIOS = (
    Scenario("chain across replicas", ((_r0, _r1, Edge("r0", "r1")), (_r2_chain, Edge("r1", "r2"))),
             expect=((E(), "r2"),)),
    Scenario("invalidated head is X_⊥, not fallback",
             ((_r0, _r1, Edge("r0", "r1")), (_r2_chain, Edge("r1", "r2"), Invalidate("r2"))),
             expect=((E(), None),)),
    Scenario("fork, one independent resolver", (_A_FORK, _B_FORK),
             (Resolver(0, "r0", "r2", "r1", frozenset({"alice", "bob"})),),
             expect=((E({0}), "r2"),)),
    Scenario("fork, beneficiary-only resolution", (_A_FORK, _B_FORK),
             (Resolver(1, "r0", "r2", "r1", frozenset({"carol"})),),
             expect=((E({0}), CONFLICT),)),
    Scenario("fork, two authorities may resolve concurrently and disagree", (_A_FORK, _B_FORK),
             (Resolver(0, "r0", "r1", "r2", frozenset({"alice", "carol"})),
              Resolver(1, "r0", "r2", "r1", frozenset({"alice", "bob"}))),
             expect=((E({0}), "r1"), (E({1}), "r2"), (E({0, 1}), CONFLICT))),
    Scenario("fork, disagreement escalated at epoch 2", (_A_FORK, _B_FORK),
             (Resolver(0, "r0", "r1", "r2", frozenset({"alice", "carol"})),
              Resolver(1, "r0", "r2", "r1", frozenset({"alice", "bob"})),
              Resolver(0, "r0", "r2", "r1", frozenset({"alice", "bob", "carol"}), epoch=2,
                       trigger="dispute")),
             expect=((E({0}), "r1"), (E({1}), "r2"), (E({0, 1, 2}), "r2"))),
)


def mutant_scenarios() -> tuple[Scenario, ...]:
    """Retained counterexamples, each of which the checker must keep finding."""
    from dataclasses import replace
    fork2 = next(x for x in SCENARIOS if x.name.startswith("fork, two authorities"))
    chain = next(x for x in SCENARIOS if x.name.startswith("chain"))
    return (
        replace(fork2, name="split-brain: first-applied-wins resolution", first_applied_wins=True),
        replace(chain, name="handoff failure #3: naive stale-successor guard", naive_stale_guard=True),
    )


def with_observer(sc: Scenario, max_dups: int) -> Scenario:
    """The same scenario with a third, passive replica."""
    from dataclasses import replace
    return replace(sc, name=sc.name + " (+ passive 3rd replica)", scripts=sc.scripts + ((),),
                   max_dups=max_dups)
