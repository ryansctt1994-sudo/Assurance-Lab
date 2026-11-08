"""METAMORPHIC_CONSISTENCY: semantics-preserving transformations of replica inputs.

A metamorphic relation is itself a claim about the semantics, so each one
below states its **precondition**, a **justification** for why the
transformation commutes with the semantics, and the **comparison** used.
Each is calibrated by negative-control mutants (``Replica`` flags), whose
deviant branches count their executions, so the relation × mutant matrix can
tell KILLED (relation violated) from SURVIVED (deviant branch ran, relation
held) from NOT_EXERCISED (deviant branch never ran on this relation's
inputs).

Shared preconditions: each record id is issued once (no Byzantine
equivocation; see ``EQUIVOCATION_PROBE``), the resolution policy is fixed,
and every source instance is a fixed set of issued events delivered
completely (no issuance during delivery).

Observation (``observe``): per-lineage head, admitted records, applied
edges, invalidations, and per-fork decision. Internal bookkeeping (rejection
log, pending queue, arrival order) is deliberately not observed.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
import random
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .replica import (
    CorruptSnapshot,
    Admit, Edge, Invalidate, Replica, ResolutionPolicy, Resolve, make_resolution,
)

PRINCIPALS = ("alice", "bob", "carol")
FLAGS = ("first_applied_wins", "naive_stale_guard", "replay_readmits", "global_dispute",
         "rid_tiebreak", "principal_order_quorum", "freeze_drops_invalidated")

# ------------------------------------------------------------ instances


@dataclass(frozen=True)
class Instance:
    name: str
    events: tuple
    policy: ResolutionPolicy
    lineages: tuple = ("L",)


def build(kind: str, rid=lambda r: r, who=lambda p: p, lineage: str = "L") -> Instance:
    """A scenario under a naming (record ids ``rid``, principals ``who``).
    Resolutions are rebuilt for the naming, since their roots hash the ids."""
    pol = ResolutionPolicy(frozenset(map(who, PRINCIPALS)), 2)
    r0, r1, r2 = rid("r0"), rid("r1"), rid("r2")
    chain = (Admit(r0, lineage, 0, who("alice")), Admit(r1, lineage, 1, who("bob"), pred=r0),
             Admit(r2, lineage, 2, who("carol"), pred=r1), Edge(r0, r1), Edge(r1, r2))
    fork = (Admit(r0, lineage, 0, who("alice")), Admit(r1, lineage, 1, who("bob"), pred=r0),
            Admit(r2, lineage, 2, who("carol"), pred=r0), Edge(r0, r1), Edge(r0, r2))
    scratch = Replica(pol)
    for e in fork:
        scratch.deliver(e)

    def res(winner, loser, approvers, epoch=1):
        return make_resolution(scratch, r0, rid(winner), rid(loser), set(map(who, approvers)),
                               epoch=epoch)

    a = res("r1", "r2", ("alice", "carol"))
    b = res("r2", "r1", ("alice", "bob"))
    events = {
        "chain": chain,
        "chain+invalidate": chain + (Invalidate(r2),),
        "fork": fork,
        "fork+resolution": fork + (b,),
        "fork+beneficiary-only": fork + (res("r2", "r1", ("carol",)),),
        "fork+dispute": fork + (a, b),
        "fork+dispute+escalation": fork + (a, b, res("r2", "r1", ("alice", "bob", "carol"), epoch=2)),
        # the resolved winner is later invalidated: X_⊥, never a fallback to the loser
        "fork+resolution+invalidate-winner": fork + (b, Invalidate(r2)),
        # targeted (audit A3): more inputs that exercise invalidation and
        # out-of-order chain edges, the two thinly killed negative controls
        "chain+invalidate-middle": chain + (Invalidate(r1),),
        "chain+invalidate-root": chain + (Invalidate(r0),),
        "chain+invalidate-head-and-middle": chain + (Invalidate(r2), Invalidate(r1)),
        "fork+resolution+invalidate-loser": fork + (b, Invalidate(r1)),
    }[kind]
    return Instance(kind, events, pol, (lineage,))


KINDS = ("chain", "chain+invalidate", "fork", "fork+resolution", "fork+beneficiary-only",
         "fork+dispute", "fork+dispute+escalation", "fork+resolution+invalidate-winner",
         "chain+invalidate-middle", "chain+invalidate-root", "chain+invalidate-head-and-middle",
         "fork+resolution+invalidate-loser")


#: Frozen baseline fixture set (matrix v1, as published at b535b1a). The
#: expanded set (v2) adds four targeted instances; both are reported, so a
#: new fixture cannot silently improve the baseline's numbers.
KINDS_V1 = KINDS[:8]


def instances(kinds=KINDS) -> list[Instance]:
    return [build(k) for k in kinds]


# ------------------------------------------------------------ execution


def run(inst: Instance, order, flags: dict) -> Replica:
    rep = Replica(inst.policy, **flags)
    for e in order:
        rep.deliver(e)
    return rep


def run_obs(inst: Instance, order, flags: dict, counter: list, lineages=None) -> tuple:
    """Deliver, observe, and only then harvest deviant-branch hits: some
    mutants deviate inside ``head()``, which observation calls."""
    rep = run(inst, order, flags)
    obs = observe(rep, lineages or inst.lineages)
    counter[0] += rep.hits
    return obs


def observe(rep: Replica, lineages) -> tuple:
    return (
        tuple((lin, rep.head(lin)) for lin in lineages),
        frozenset(rep.admitted.values()),
        frozenset(rep.edges),
        frozenset(rep.invalidated),
        tuple(sorted((src, rep.decision(src)) for src in rep.resolutions)),
    )


def label(e) -> str:
    if isinstance(e, Admit):
        return f"Admit({e.rid}, {e.lineage}, seq={e.seq}, owner={e.owner}{', pred=' + e.pred if e.pred else ''})"
    if isinstance(e, Edge):
        return f"Edge({e.old}->{e.new})"
    if isinstance(e, Invalidate):
        return f"Invalidate({e.rid})"
    return f"Resolve(winner={e.winner}, epoch={e.epoch}, approvals={sorted(e.approvals)})"


@dataclass
class Witness:
    relation: str
    instance: str
    detail: str
    executions: list  # the event sequences compared (replay them with ``run``)

    @property
    def orders(self) -> list:
        return [[label(e) for e in ex] for ex in self.executions]

    def pretty(self) -> str:
        lines = [f"{self.relation} violated on {self.instance!r}: {self.detail}"]
        for i, o in enumerate(self.orders):
            lines.append(f"  execution {i}: " + ", ".join(o))
        return "\n".join(lines)


@dataclass
class RelationResult:
    relation: str
    checks: int = 0  # comparisons made
    # Deviant-branch executions observed. Relations stop at the first witness
    # per instance, so for a KILLED mutant this counts exercise *until
    # detection*, not exposure; kill breadth is ``killed_instances``.
    hits: int = 0
    witnesses: list = field(default_factory=list)
    instances: int = 0
    coverage: list = field(default_factory=list)  # (instance, checked, total) where not exhaustive
    infected: int = 0  # instances where an injected corruption actually changed the state
    mechanisms: dict = field(default_factory=dict)  # detection mechanism -> instances

    @property
    def held(self) -> bool:
        return not self.witnesses

    @property
    def killed_instances(self) -> int:
        return len({w.instance for w in self.witnesses})

    def coverage_note(self) -> str:
        if not self.coverage:
            return f"all {self.instances} instances exhaustive"
        sampled = "; ".join(f"{name}: {done:,} of {total:,} orders sampled" for name, done, total in self.coverage)
        return f"{self.instances - len(self.coverage)}/{self.instances} instances exhaustive ({sampled})"


def _shrink(events: list, still_fails: Callable[[list], bool]) -> list:
    """Greedy one-at-a-time removal while the failure persists (ddmin-lite).
    Removing events keeps every relation's precondition: any fixed set of
    issued events is a valid instance."""
    cur = list(events)
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            cand = cur[:i] + cur[i + 1:]
            if cand and still_fails(cand):
                cur, changed = cand, True
                break
    return cur


# ------------------------------------------------------------ relations

Compare = Callable[[tuple, tuple], bool]


#: Execution configuration of the relations, read at call time (so it is
#: part of every matrix cache key, and one place states it).
EXECUTION_CONFIG = {"order_max_perms": 5040, "order_seed": 0, "projection_interleavings": 40,
                    "projection_seed": 0, "projection_max_exhaustive": 8000}


def order_executions(inst: Instance, max_perms: Optional[int] = None, seed: Optional[int] = None):
    """Delivery orders the order relation compares for one instance:
    every permutation, or a deterministic sample (both orders of the causal
    sequence plus seeded shuffles) reported as (distinct, total)."""
    max_perms = EXECUTION_CONFIG["order_max_perms"] if max_perms is None else max_perms
    seed = EXECUTION_CONFIG["order_seed"] if seed is None else seed
    base = list(inst.events)
    total = __import__("math").factorial(len(base))
    if total <= max_perms:
        return list(itertools.permutations(base)), None
    rng = random.Random(seed)
    perms = [tuple(base), tuple(reversed(base))] + [tuple(rng.sample(base, len(base)))
                                                  for _ in range(max_perms - 2)]
    return perms, (len(set(perms)), total)


def duplicate_executions(inst: Instance) -> list:
    """(i, duplicated event, delivery sequence) for every redelivery position."""
    base = list(inst.events)
    return [(i, dup, base[:i] + [dup] + base[i:]) for i in range(1, len(base) + 1) for dup in base[:i]]


def order_invariance(insts, flags=None, max_perms: Optional[int] = None, seed: Optional[int] = None,
                     compare: Compare = lambda a, b: a == b) -> RelationResult:
    """R1 complete-delivery order invariance.

    Precondition: a fixed set of issued events, delivered completely.
    Justification: every applied effect is a set insertion guarded by
    conditions on the set of applied events; the decision for a fork is a
    function of the *set* of valid resolutions. So the final observation is
    a function of the delivered set. Intermediate observations may differ
    and are not compared.
    """
    flags = flags or {}
    res, ctr = RelationResult("complete-delivery order invariance"), [0]
    for inst in insts:
        res.instances += 1
        base = list(inst.events)
        want = run_obs(inst, base, flags, ctr)
        perms, sampled = order_executions(inst, max_perms, seed)
        if sampled:
            res.coverage.append((inst.name, *sampled))
        for p in perms:
            res.checks += 1
            got = run_obs(inst, p, flags, ctr)
            if not compare(got, want):
                def fails(evs, _p=p):
                    return run_obs(inst, [e for e in base if e in evs], flags, [0]) != \
                        run_obs(inst, [e for e in _p if e in evs], flags, [0])
                kept = _shrink(base, fails)
                res.witnesses.append(Witness(res.relation, inst.name, "final observation depends on order",
                                             [[e for e in base if e in kept], [e for e in p if e in kept]]))
                break
    res.hits = ctr[0]
    return res


def duplicate_idempotence(insts, flags=None, compare: Compare = lambda a, b: a == b) -> RelationResult:
    """R2 duplicate-message idempotence.

    Precondition: the duplicate is byte-identical to an event already
    delivered (same valid message, unchanged context).
    Justification: delivery of an event already seen is a no-op, and every
    effect is idempotent set insertion; so redelivery changes neither the
    observation at that point nor the final one.
    """
    flags = flags or {}
    res, ctr = RelationResult("duplicate-message idempotence"), [0]
    for inst in insts:
        res.instances += 1
        base = list(inst.events)
        final = run_obs(inst, base, flags, ctr)
        for i, dup, order in duplicate_executions(inst):
            res.checks += 1
            before = run_obs(inst, base[:i], flags, ctr)
            at = run_obs(inst, base[:i] + [dup], flags, ctr)
            end = run_obs(inst, order, flags, ctr)
            if not (compare(at, before) and compare(end, final)):
                res.witnesses.append(Witness(res.relation, inst.name,
                                             f"redelivering {label(dup)} changed the observation",
                                             [base, order]))
                break
    res.hits = ctr[0]
    return res


def _project(obs: tuple, lineage: str, ids: set) -> tuple:
    heads, admitted, edges, invalidated, decisions = obs
    return (tuple(h for h in heads if h[0] == lineage),
            frozenset(a for a in admitted if a.lineage == lineage),
            frozenset(e for e in edges if e[0] in ids),
            frozenset(x for x in invalidated if x in ids),
            tuple(d for d in decisions if d[0] in ids))


def unrelated_lineage() -> Instance:
    """The added lineage M of the projection relation (also part of the
    serialization alphabet)."""
    return build("fork+dispute", rid=lambda r: "m" + r[1:], lineage="M")


def projection_executions(inst: Instance, rng=None, interleavings: Optional[int] = None,
                          max_exhaustive: Optional[int] = None):
    """(merged instance, interleavings of L and M that keep each lineage's own
    order, (distinct, total) if sampled)."""
    interleavings = EXECUTION_CONFIG["projection_interleavings"] if interleavings is None else interleavings
    max_exhaustive = EXECUTION_CONFIG["projection_max_exhaustive"] if max_exhaustive is None else max_exhaustive
    rng = rng or random.Random(EXECUTION_CONFIG["projection_seed"])
    L, M = list(inst.events), list(unrelated_lineage().events)
    total = __import__("math").comb(len(L) + len(M), len(M))
    sampled = None
    if total <= max_exhaustive:  # every interleaving preserving each lineage's own order
        slot_sets = list(itertools.combinations(range(len(L) + len(M)), len(M)))
    else:
        slot_sets = [tuple(range(len(M))), tuple(range(len(L), len(L) + len(M)))] + \
            [tuple(sorted(rng.sample(range(len(L) + len(M)), len(M)))) for _ in range(interleavings)]
        sampled = (len(set(slot_sets)), total)
    mixes = []
    for slots in slot_sets:
        chosen, mix, li, mi = set(slots), [], iter(L), iter(M)
        for k in range(len(L) + len(M)):
            mix.append(next(mi) if k in chosen else next(li))
        mixes.append(mix)
    merged = Instance(inst.name + " + unrelated M", tuple(L + M), inst.policy, ("L", "M"))
    return merged, mixes, sampled


def lineage_projection(insts, flags=None, interleavings: Optional[int] = None, seed: Optional[int] = None,
                       compare: Compare = lambda a, b: a == b, max_exhaustive: Optional[int] = None) -> RelationResult:
    """R3 unrelated-lineage projection.

    Precondition: the added lineage M shares no record ids with L and no
    event references across lineages.
    Justification: every replica rule is per lineage (edges must stay within
    a lineage, forks and decisions are keyed by a source record, heads are
    computed per lineage), so events of M cannot change what L observes.
    Not claimed for the *core* model, whose lineages share global state
    (authority levels, semantic version).
    """
    flags = flags or {}
    res, ctr = RelationResult("unrelated-lineage projection"), [0]
    rng = random.Random(EXECUTION_CONFIG["projection_seed"] if seed is None else seed)
    for inst in insts:
        res.instances += 1
        L = list(inst.events)
        ids = {e.rid for e in L if isinstance(e, Admit)}
        want = _project(run_obs(inst, L, flags, ctr, ("L",)), "L", ids)
        merged, mixes, sampled = projection_executions(inst, rng, interleavings, max_exhaustive)
        if sampled:
            res.coverage.append((inst.name, *sampled))
        for mix in mixes:
            res.checks += 1
            got = _project(run_obs(merged, mix, flags, ctr, ("L", "M")), "L", ids)
            if not compare(got, want):
                res.witnesses.append(Witness(res.relation, inst.name,
                                             "events of an unrelated lineage changed what L observes",
                                             [L, mix]))
                break
    res.hits = ctr[0]
    return res


def _rename_obs(obs: tuple, rmap: dict, pmap: dict) -> tuple:
    heads, admitted, edges, invalidated, decisions = obs
    r = lambda x: rmap.get(x, x)  # noqa: E731 (None and CONFLICT map to themselves)
    return (tuple((lin, r(h)) for lin, h in heads),
            frozenset(replace(a, rid=r(a.rid), owner=pmap[a.owner], pred=r(a.pred) if a.pred else None)
                      for a in admitted),
            frozenset((r(x), r(y)) for x, y in edges),
            frozenset(r(x) for x in invalidated),
            tuple(sorted((r(src), (st, r(w) if w else w, r(lo) if lo else lo))
                         for src, (st, w, lo) in decisions)))


def renaming_executions(kind: str) -> list:
    """(record-id map, principal map, renamed instance) for every pair of
    bijections the renaming relation applies."""
    out = []
    for rp in itertools.permutations(("r0", "r1", "r2")):
        rmap = dict(zip(("r0", "r1", "r2"), rp))
        for wp in itertools.permutations(PRINCIPALS):
            pmap = dict(zip(PRINCIPALS, wp))
            out.append((rmap, pmap, build(kind, rid=lambda x, m=rmap: m[x], who=lambda q, m=pmap: m[q])))
    return out


def renaming_equivariance(insts_kinds=KINDS, flags=None,
                          expected_map: Optional[Callable] = None) -> RelationResult:
    """R4 identifier-renaming equivariance.

    Precondition: renamings are bijections on record ids and on principal
    names; ``seq`` (the forward ordering) and lineage names are unchanged.
    Justification: the replica uses record ids and principal names only for
    equality and set membership (forward order comes from ``seq``, roots are
    recomputed for the renamed ids), so renaming the inputs renames the
    outputs. ``expected_map`` lets a test inject a wrong expectation.
    """
    flags = flags or {}
    res, ctr = RelationResult("identifier-renaming equivariance"), [0]
    for kind in insts_kinds:
        res.instances += 1
        src = build(kind)
        base = run_obs(src, src.events, flags, ctr)
        for rmap, pmap, inst in renaming_executions(kind):
            res.checks += 1
            got = run_obs(inst, inst.events, flags, ctr)
            want = (expected_map or _rename_obs)(base, rmap, pmap)
            if got != want:
                res.witnesses.append(Witness(res.relation, kind,
                                             f"renaming {rmap} / {pmap} did not rename the outcome",
                                             [list(src.events), list(inst.events)]))
                break
    res.hits = ctr[0]
    return res


# ------------------------------------------------------------ exact-state contract

#: Fields of a replica that affect its future behavior, compared by
#: ``exact_state``. Excluded: ``rejected`` (an append-only diagnostic log no
#: rule reads), ``hits`` (test instrumentation) and the policy (fixed by the
#: precondition). ``pending`` is compared *in order*, since deferred events
#: are retried in that order.
EXACT_STATE_CONTRACT = ("admitted (rid -> full Admit event)", "edges", "invalidated",
                        "resolutions (source -> every valid Resolve)", "pending (in order)",
                        "seen (duplicate filter)", "mutant flags")


def exact_state(rep: Replica) -> tuple:
    """Independent extraction of the exact-state contract, reading the
    replica's fields directly. Deliberately does **not** call ``freeze``:
    comparing two outputs of the serializer under test would hide any field
    the serializer drops (both sides would drop it identically)."""
    return (
        tuple(sorted((rid, repr(ev)) for rid, ev in rep.admitted.items())),
        tuple(sorted(rep.edges)),
        tuple(sorted(rep.invalidated)),
        tuple(sorted((src, tuple(sorted(repr(r) for r in rs))) for src, rs in rep.resolutions.items())),
        tuple(repr(e) for e in rep.pending),
        tuple(sorted(repr(e) for e in rep.seen)),
        tuple(getattr(rep, f) for f in FLAGS),
    )


def named_boundary_events() -> list:
    """(name, event): invalid and boundary-case events of the declared
    alphabet. Each must be handled identically by a restored replica and its
    reference; ``BOUNDARY_EXPECT`` states how the replica's rules may handle
    each one."""
    from dataclasses import replace as _replace
    fork = build("fork+resolution")
    valid_res = next(e for e in fork.events if isinstance(e, Resolve))  # r2 wins over r1 (alice, bob)
    scratch = Replica(fork.policy)
    for e in fork.events:
        if not isinstance(e, Resolve):
            scratch.deliver(e)
    under_quorum = make_resolution(scratch, "r0", "r2", "r1", {"alice"}, rule="under-quorum")
    return [
        ("Edge(r2->r0)", Edge("r2", "r0")),  # backward (non-forward)
        ("Edge(r0->m0)", Edge("r0", "m0")),  # cross-lineage
        ("Edge(rX->r0)", Edge("rX", "r0")),  # unknown record: deferred forever
        ("Edge(r0->r2)", Edge("r0", "r2")),  # undeclared in a chain (r2's declared pred is r1)
        ("Invalidate(rX)", Invalidate("rX")),  # unknown record: deferred forever
        ("Admit(r3, L, seq=3, owner=alice, pred=rX)", Admit("r3", "L", 3, "alice", pred="rX")),  # unknown predecessor
        ("Admit(r1, L, seq=1, owner=carol, pred=r0)", Admit("r1", "L", 1, "carol", pred="r0")),  # conflicting re-admission
        ("tampered resolution (pre-state root)", _replace(valid_res, pre_state_root="0" * 16)),
        # added in v2 so every resolution rejection rule is reachable from the alphabet
        ("tampered resolution (post-state root)", _replace(valid_res, post_state_root="0" * 16)),
        ("malformed resolution (winner = loser)", _replace(valid_res, winner="r1", loser="r1")),
        ("under-quorum resolution (one independent approver)", under_quorum),
    ]


def boundary_events() -> list:
    return [e for _name, e in named_boundary_events()]


def boundary_sequences() -> list:
    """(name, events): short declared sequences delivered after a serialized
    prefix, for rules no single next event can reach. A cross-lineage edge
    needs both ends admitted, and the prefix only holds L's records, so its M
    end is admitted first."""
    m0 = unrelated_lineage().events[0]  # Admit(m0, M, seq=0)
    return [("cross-lineage edge, after admitting its M end", (m0, Edge("r0", "m0")))]


def _suffixes(universe) -> list:
    """What the serialization relation delivers after restoring: each
    alphabet event on its own, then each declared boundary sequence."""
    return [(e,) for e in universe] + [seq for _name, seq in boundary_sequences()]


def event_universe(insts) -> list:
    """The declared finite event alphabet for the serialization relation:
    every event of every source instance, plus the unrelated lineage's
    events. Not every event the replica API could accept (that is
    unbounded); claims are scoped to this alphabet."""
    other = unrelated_lineage()
    seen, out = set(), []
    for e in [e for inst in insts for e in inst.events] + list(other.events) + boundary_events():
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def serialization_prefixes(inst: Instance) -> list:
    """Delivered prefixes the serialization relation freezes: of the causal
    order and of the reversed order (in which events get deferred), so
    states with pending events are serialized too. Deterministic order."""
    orders = [list(inst.events), list(reversed(inst.events))]
    prefixes = {tuple(o[:i]) for o in orders for i in range(len(o) + 1)}
    return sorted(prefixes, key=lambda t: (len(t), [label(e) for e in t]))


def serialization_round_trip(insts, flags=None, compare: Compare = lambda a, b: a == b,
                             universe=None, corrupt=None, exact: bool = True,
                             validate: bool = True) -> RelationResult:
    """R5 serialization round-trip (``Replica.freeze``/``thaw``).

    Precondition: the frozen form is the replica's complete state.
    Justification: thaw(freeze(r)) reconstructs every field the behavior
    depends on, so the restored replica has the same canonical state and
    responds identically to every event of the declared event universe
    (``event_universe``: all source instances' events, both lineages); not
    to every event the API could accept. The reference is a never-
    serialized re-execution under the same flags. Prefixes are taken from the
    causal *and* the reversed delivery order, so states with deferred
    (pending) events are serialized too.

    Two criteria: *observational* (same observation and canonical state,
    same response to every universe event) and, with ``exact``, *exact
    state* (the restored replica's complete internal state equals the
    reference's, before and after each event). A correct thaw is exact, so
    the stricter criterion is valid here, unlike for the order relations; it
    catches lossy snapshots whose loss is not observable (a dropped duplicate
    filter). ``corrupt`` damages the frozen state before thawing.
    """
    flags = flags or {}
    universe = event_universe(insts) if universe is None else universe
    res, ctr = RelationResult("serialization round-trip"), [0]
    res.infected = 0
    res.mechanisms = {"thaw-validation": 0, "projection": 0, "next-event": 0, "exact-state": 0}
    for inst in insts:
        res.instances += 1
        infected_here = False
        caught: set = set()
        witness = None
        for pre in serialization_prefixes(inst):
            base = list(pre)
            source = run(inst, base, flags)
            frozen = source.freeze()
            ctr[0] += source.hits  # a lossy freeze is counted on the replica that froze
            if corrupt:
                damaged = corrupt(frozen)
                infected_here |= damaged != frozen
                frozen = damaged
            # The reference is built straight from the event history: never
            # serialized, never thawed.
            reference = run(inst, base, flags)
            res.checks += 1
            here = set()
            try:
                copy = Replica.thaw(inst.policy, frozen, validate=validate, **flags)
            except CorruptSnapshot:  # refused to restore: fails closed
                here.add("thaw-validation")
                caught |= here
                if witness is None:
                    witness = Witness(res.relation, inst.name, f"snapshot after {len(base)} events "
                                      "refused at thaw (fail closed)", [base])
                if not corrupt:
                    break
                continue
            if copy.canonical() != reference.canonical() or \
                    not compare(observe(copy, inst.lineages), observe(reference, inst.lineages)):
                here.add("projection")
            if exact and exact_state(copy) != exact_state(reference):
                here.add("exact-state")
            ctr[0] += copy.hits + reference.hits
            for suffix in _suffixes(universe):  # every alphabet event, then each declared sequence
                restored = Replica.thaw(inst.policy, frozen, validate=validate, **flags)
                ref = run(inst, base, flags)
                for nxt in suffix:
                    restored.deliver(nxt)
                    ref.deliver(nxt)
                res.checks += 1
                lins = tuple(sorted(set(inst.lineages) | {e.lineage for e in suffix if isinstance(e, Admit)}))
                if not compare(observe(restored, lins), observe(ref, lins)):
                    here.add("next-event")
                if exact and exact_state(restored) != exact_state(ref):
                    here.add("exact-state")
                ctr[0] += restored.hits + ref.hits
            if here and witness is None:
                witness = Witness(res.relation, inst.name,
                                  f"state after {len(base)} events not preserved by freeze/thaw "
                                  f"(detected by {', '.join(sorted(here))})", [base])
            caught |= here
            if here and not corrupt:
                break  # one witness per instance is enough outside corruption accounting
        if witness:
            res.witnesses.append(witness)
        for mech in caught:
            res.mechanisms[mech] += 1
        res.infected += infected_here
    res.hits = ctr[0]
    return res


def _inject_backward_edge(f: tuple) -> tuple:
    """Consistently record (seen *and* applied) an edge the edge rule forbids."""
    adm = dict(f[0])
    back = sorted((x, y) for x in adm for y in adm
                  if adm[x].lineage == adm[y].lineage and adm[x].seq > adm[y].seq)
    if not back:
        return f
    x, y = back[0]
    return (f[0], f[1] | {(x, y)}, *f[2:6], f[6] | {Edge(x, y)})


def _inject_self_authorized_resolution(f: tuple) -> tuple:
    """Consistently record a resolution approved only by its beneficiary."""
    rep = Replica.thaw(ResolutionPolicy(frozenset(PRINCIPALS), 2), f)
    forks = sorted((a, sorted(b for x, b in rep.edges if x == a)) for a in {x for x, _ in rep.edges})
    forks = [(a, bs) for a, bs in forks if len(bs) == 2]
    if not forks:
        return f
    src, (w, lo) = forks[0]
    r = make_resolution(rep, src, w, lo, {rep.admitted[w].owner}, rule="injected")
    res = dict(f[3])
    res[src] = frozenset(res.get(src, frozenset()) | {r})
    return (*f[:3], tuple(sorted(res.items())), f[4], f[5], f[6] | {r})


def _mistype_seq(f: tuple) -> tuple:
    if not f[0]:
        return f
    (rid, a), *rest = f[0]
    bad = replace(a, seq=str(a.seq))
    return ((rid, bad), *rest), *f[1:6], (f[6] - {a}) | {bad}


def _first_event(evs, kind):
    return min((e for e in evs if isinstance(e, kind)), key=label, default=None)


def _roll_back_invalidation(f: tuple) -> tuple:
    """Consistent omission: the state of a replica that never received one
    of the invalidations (a rolled-back snapshot)."""
    if not f[2]:
        return f
    rid = min(f[2])
    return (f[0], f[1], f[2] - {rid}, *f[3:6], f[6] - {Invalidate(rid)})


def _omit_pending(f: tuple) -> tuple:
    if not f[5]:
        return f
    e = f[5][0]
    return (*f[:5], f[5][1:], f[6] - {e})


def _omit_rejected(f: tuple) -> tuple:
    if not f[4]:
        return f
    e = f[4][0][0]
    if any(x == e for x, _why in f[4][1:]):
        return f
    return (*f[:4], f[4][1:], f[5], f[6] - {e})


#: Deliberate damage to a frozen replica state, by class.
#:
#: * ``inconsistent``: effects without evidence, evidence without effects, or
#:   effects the application rules forbid. Must be refused at thaw wherever it
#:   changes the snapshot.
#: * ``malformed``: not the shape or the types ``freeze`` emits. Must be
#:   refused at thaw.
#: * ``omission``: the damaged snapshot is *exactly* the state of a replica
#:   that never received some events. No snapshot-local check can refuse it
#:   (integrity is not authenticity); listed to bound the claim. A rolled-back
#:   invalidation restores a stale head: only authenticated snapshots could
#:   catch it, and none are modeled.
CORRUPTIONS = {
    "drop invalidations": ("inconsistent", lambda f: (f[0], f[1], frozenset(), *f[3:])),
    "drop one applied edge": ("inconsistent", lambda f: (f[0], frozenset(sorted(f[1])[1:]), *f[2:])),
    "drop one admitted record": ("inconsistent", lambda f: (f[0][1:], *f[1:])),
    "drop resolutions": ("inconsistent", lambda f: (f[0], f[1], f[2], (), *f[4:])),
    "drop pending queue": ("inconsistent", lambda f: (*f[:5], (), f[6])),
    "drop seen-set (duplicate filter)": ("inconsistent", lambda f: (*f[:6], frozenset())),
    "inject a backward edge (seen and applied)": ("inconsistent", _inject_backward_edge),
    "inject a self-authorized resolution (seen and applied)": ("inconsistent", _inject_self_authorized_resolution),
    "truncate the snapshot": ("malformed", lambda f: f[:6]),
    "mistype a field (seq as text)": ("malformed", _mistype_seq),
    "roll back an applied invalidation": ("omission", _roll_back_invalidation),
    "omit a pending event": ("omission", _omit_pending),
    "omit a rejected event": ("omission", _omit_rejected),
}


def corruption_snapshots(fn, insts=None):
    """(instance, prefix, genuine snapshot, damaged snapshot) wherever ``fn``
    changes the snapshot, over every serialization prefix of every instance."""
    for inst in insts or instances():
        for pre in serialization_prefixes(inst):
            frozen = run(inst, list(pre), {}).freeze()
            damaged = fn(frozen)
            if damaged != frozen:
                yield inst, pre, frozen, damaged


def _fields(rep: Replica) -> tuple:
    return (dict(rep.admitted), set(rep.edges), set(rep.invalidated),
            {k: set(v) for k, v in rep.resolutions.items()}, list(rep.rejected), list(rep.pending), set(rep.seen))


def corruption_table(insts=None) -> list[dict]:
    """One row per corruption, with each control reported separately.

    * ``validator`` (production boundary, ``thaw`` validating): snapshots
      refused, exceptions other than CorruptSnapshot (must be none), and
      whether two refusals of one snapshot give identical reasons.
    * ``bypass`` (diagnostic only: ``thaw(validate=False)``; not evidence
      that the boundary works): what ``explain_head`` answers on the damaged
      state, whether it ever answers with authority (a record, X_⊥ or
      CONFLICT) from a damaged state, and whether it changed the state.
    * ``relation`` (omissions only, with the boundary on, since they pass
      it): which layer of the serialization relation notices the damage
      against a never-serialized reference.
    """
    insts = insts or instances()
    rows = []
    for name, (cls, fn) in CORRUPTIONS.items():
        row = {"corruption": name, "class": cls, "infected_instances": 0, "infected_snapshots": 0,
               "validator": {"refused": 0, "accepted": 0, "other_exceptions": [], "deterministic": True},
               "bypass": {"statuses": {}, "authority_from_damaged_state": 0, "refused_even_unvalidated": 0,
                          "state_changed_by_head": 0, "exceptions": []},
               "relation": None}
        infected = set()
        for inst, pre, _genuine, damaged in corruption_snapshots(fn, insts):
            infected.add(inst.name)
            row["infected_snapshots"] += 1
            try:
                Replica.thaw(inst.policy, damaged)
                row["validator"]["accepted"] += 1
            except CorruptSnapshot as first:
                row["validator"]["refused"] += 1
                try:
                    Replica.thaw(inst.policy, damaged)
                except CorruptSnapshot as second:
                    row["validator"]["deterministic"] &= first.problems == second.problems
            except Exception as e:  # noqa: BLE001 (recorded: must never happen)
                row["validator"]["other_exceptions"].append(f"{inst.name}: {type(e).__name__}: {e}")
            try:
                rep = Replica.thaw(inst.policy, damaged, validate=False)
            except CorruptSnapshot:
                row["bypass"]["refused_even_unvalidated"] += 1
                continue
            except Exception as e:  # noqa: BLE001
                row["bypass"]["exceptions"].append(f"{inst.name}: thaw: {type(e).__name__}")
                continue
            before = _fields(rep)
            for lin in inst.lineages:
                try:
                    v = rep.explain_head(lin)
                except Exception as e:  # noqa: BLE001
                    row["bypass"]["exceptions"].append(f"{inst.name}: head: {type(e).__name__}")
                    continue
                st = row["bypass"]["statuses"]
                st[v.status] = st.get(v.status, 0) + 1
                row["bypass"]["authority_from_damaged_state"] += v.status != "UNKNOWN"
            row["bypass"]["state_changed_by_head"] += _fields(rep) != before
        row["infected_instances"] = len(infected)
        if cls == "omission":
            r = serialization_round_trip(insts, corrupt=fn)
            row["relation"] = {k: v for k, v in r.mechanisms.items() if k != "thaw-validation"}
        rows.append(row)
    return rows


RELATIONS = {
    "order": lambda flags=None: order_invariance(instances(), flags),
    "duplicate": lambda flags=None: duplicate_idempotence(instances(), flags),
    "projection": lambda flags=None: lineage_projection(instances(), flags),
    "renaming": lambda flags=None: renaming_equivariance(KINDS, flags),
    "serialization": lambda flags=None: serialization_round_trip(instances(), flags),
}

# Outside the shared precondition: a Byzantine origin issues two different
# admissions for one record id. Recorded as a known limitation, not a relation.
EQUIVOCATION_PROBE = Instance(
    "equivocation probe",
    (Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0"),
     Admit("r1", "L", 1, "carol", pred="r0"), Edge("r0", "r1")),
    ResolutionPolicy(frozenset(PRINCIPALS), 2),
)


# ------------------------------------------------------------ mutant matrix


class Cell:
    KILLED = "KILLED"  # the relation detected the mutant on a deviating instance
    SURVIVED = "SURVIVED"  # the mutant deviated on the relation's inputs; the relation held
    # Eligible instances exist, but the relation's inputs never make the
    # mutant deviate (often by design: its precondition excludes the
    # triggering input), while some other relation's inputs do.
    NOT_REACHED = "NOT_REACHED"
    # No eligible instance: the relation's alphabet or operations cannot reach
    # the mutated code region at all.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    # No relation's inputs ever make the mutant deviate: candidate equivalent
    # or unreachable mutant, excluded from the mutation-score denominator.
    EQUIVALENT_CANDIDATE = "EQUIVALENT_CANDIDATE"
    ERROR = "ERROR"


# ------------------------------------------------------------ eligibility (frozen, matrix v2)

#: What each relation does to replicas.
OPERATIONS = {
    "order": frozenset({"deliver", "head"}),
    "duplicate": frozenset({"deliver", "head"}),
    "projection": frozenset({"deliver", "head"}),
    "renaming": frozenset({"deliver", "head"}),
    "serialization": frozenset({"deliver", "head", "freeze", "thaw"}),
}

#: Mutants whose deviation is in ``head()`` alone (they never change replica
#: state). For every other mutant, equal states imply equal heads, because
#: ``head()`` reads no other flag; ``test_head_reads_only_the_head_time_flags``
#: checks that premise against the source.
HEAD_TIME_FLAGS = ("global_dispute", "rid_tiebreak")


def alphabet(relation: str, inst: Instance) -> list:
    """The events a relation can deliver for one source instance."""
    if relation in ("order", "duplicate", "renaming"):
        return list(inst.events)
    if relation == "projection":
        return list(inst.events) + list(unrelated_lineage().events)
    if relation == "serialization":
        return event_universe([inst])
    raise KeyError(relation)


def _admits(evs):
    return [e for e in dict.fromkeys(evs) if isinstance(e, Admit)]


def _resolutions(evs):
    return [e for e in dict.fromkeys(evs) if isinstance(e, Resolve)]


def _edges(evs):
    return {(e.old, e.new) for e in evs if isinstance(e, Edge)}


def _disputed(evs) -> bool:
    by_fork = {}
    for r in _resolutions(evs):
        by_fork.setdefault((r.source, r.epoch), set()).add(r.winner)
    return any(len(w) > 1 for w in by_fork.values())


def _independent_approver(evs, policy) -> bool:
    owner = {a.rid: a.owner for a in _admits(evs)}
    return any(q in policy.authorities and q != owner.get(r.winner) for r in _resolutions(evs) for q in r.approvals)


def _revivable(evs) -> bool:
    admitted = [a.rid for a in _admits(evs)]
    invalidated = {e.rid for e in evs if isinstance(e, Invalidate)}
    return bool(invalidated & set(admitted)) or len(admitted) != len(set(admitted))


#: Frozen eligibility predicates (matrix v2): (operation the mutated code is
#: in, what must be present, predicate over the relation's alphabet and the
#: policy). Defined on the inputs a relation *can* deliver, never on any
#: execution's outcome. A predicate may over-approximate (eligible but never
#: deviating lowers the exposure rate, visibly); it must never
#: under-approximate, and ``mutant_matrix`` checks that: a deviation on an
#: ineligible instance is reported as an anomaly.
ELIGIBILITY = {
    "first_applied_wins": ("deliver", "two distinct resolutions of one fork",
                           lambda evs, pol: any(n > 1 for n in Counter(r.source for r in _resolutions(evs)).values())),
    "naive_stale_guard": ("deliver", "a supersession chain: edges a->b and b->c",
                          lambda evs, pol: any((b, c) in _edges(evs) for a, b in _edges(evs)
                                               for c in {y for x, y in _edges(evs) if x == b})),
    "replay_readmits": ("deliver", "a record a replayed admission could revive or replace: an admitted "
                        "record that is invalidated, or two different admissions of one id",
                        lambda evs, pol: _revivable(evs)),
    "global_dispute": ("head", "a dispute (two same-epoch resolutions of one fork with different winners) "
                       "and records of a second lineage",
                       lambda evs, pol: _disputed(evs) and len({a.lineage for a in _admits(evs)}) > 1),
    "rid_tiebreak": ("head", "a fork: two edges from one record",
                     lambda evs, pol: any(n > 1 for n in Counter(x for x, _ in _edges(evs)).values())),
    "principal_order_quorum": ("deliver", "a resolution approved by an authority other than the winner's owner",
                               _independent_approver),
    "freeze_drops_invalidated": ("freeze", "an invalidation of an admitted record",
                                 lambda evs, pol: bool({e.rid for e in evs if isinstance(e, Invalidate)}
                                                       & {a.rid for a in _admits(evs)})),
}


def eligible(relation: str, flag: str, inst: Instance) -> bool:
    op, _what, pred = ELIGIBILITY[flag]
    return op in OPERATIONS[relation] and pred(alphabet(relation, inst), inst.policy)


# ------------------------------------------------------------ deviation (differential, lockstep)


@dataclass
class Deviation:
    """First point at which the mutant and the correct replica differ on an
    execution the relation performs. Replayable: ``steps`` are the operations."""
    relation: str
    instance: str
    execution: int  # index in the relation's execution list
    step: int  # index of the operation after which they differ
    what: str  # "state", "head(L)", or "mutant snapshot refused at thaw"
    steps: list  # [("deliver", label) | ("roundtrip", "")]


def _programs(relation: str, inst: Instance):
    """The relation's executions as operation programs: (policy, steps,
    lineages observed). Same sets, same order as the relation itself."""
    dl = lambda evs: [("deliver", e) for e in evs]  # noqa: E731
    if relation == "order":
        for p in order_executions(inst)[0]:
            yield inst.policy, dl(p), inst.lineages
    elif relation == "duplicate":
        yield inst.policy, dl(inst.events), inst.lineages
        for _i, _dup, order in duplicate_executions(inst):
            yield inst.policy, dl(order), inst.lineages
    elif relation == "projection":
        yield inst.policy, dl(inst.events), inst.lineages
        merged, mixes, _ = projection_executions(inst)
        for mix in mixes:
            yield merged.policy, dl(mix), ("L", "M")
    elif relation == "renaming":
        src = build(inst.name)
        yield src.policy, dl(src.events), src.lineages
        for _rmap, _pmap, ren in renaming_executions(inst.name):
            yield ren.policy, dl(ren.events), ren.lineages
    elif relation == "serialization":
        universe = event_universe([inst])
        for pre in serialization_prefixes(inst):
            yield inst.policy, dl(pre) + [("roundtrip", None)], inst.lineages
            for suffix in _suffixes(universe):
                lins = tuple(sorted(set(inst.lineages) | {e.lineage for e in suffix if isinstance(e, Admit)}))
                yield inst.policy, dl(pre) + [("roundtrip", None)] + dl(suffix), lins
    else:
        raise KeyError(relation)


def _state(rep: Replica) -> tuple:
    # the exact-state contract minus the flags (which differ by construction)
    return rep.admitted, rep.edges, rep.invalidated, rep.resolutions, rep.pending, rep.seen


def _lockstep(policy, steps, flag: str, lineages):
    m, c = Replica(policy, **{flag: True}), Replica(policy)
    for i, (op, arg) in enumerate(steps):
        if op == "deliver":
            m.deliver(arg)
            c.deliver(arg)
        else:  # roundtrip: freeze and restore through the validating boundary
            c = Replica.thaw(policy, c.freeze())
            try:
                m = Replica.thaw(policy, m.freeze(), **{flag: True})
            except CorruptSnapshot:
                return i, "mutant snapshot refused at thaw"
        if _state(m) != _state(c):
            return i, "state"
        if flag in HEAD_TIME_FLAGS:
            for lin in lineages:
                if m.head(lin) != c.head(lin):
                    return i, f"head({lin})"
    return None


def deviation(relation: str, inst: Instance, flag: str) -> Optional[Deviation]:
    """Does the mutant behave differently from the correct replica on any
    execution this relation performs on ``inst``? Stops at the first."""
    for k, (policy, steps, lineages) in enumerate(_programs(relation, inst)):
        hit = _lockstep(policy, steps, flag, lineages)
        if hit:
            return Deviation(relation, inst.name, k, hit[0], hit[1],
                             [(op, label(a) if a is not None else "") for op, a in steps[:hit[0] + 1]])
    return None


def _relation_on(name: str, inst_or_kind, flags):
    if name == "renaming":
        return renaming_equivariance([inst_or_kind.name], flags)
    return {"order": order_invariance, "duplicate": duplicate_idempotence,
            "projection": lineage_projection, "serialization": serialization_round_trip}[name]([inst_or_kind], flags)


@dataclass
class MatrixCell:
    """One (relation, mutant) cell, with all three denominators over the
    ``evaluated`` source instances. ``eligible``, ``deviating`` and
    ``detecting`` list instance names; the counts are their lengths."""
    category: str
    eligible: list
    deviating: list  # eligible instances where the mutant deviates from the correct replica
    detecting: list  # deviating instances where the relation detects the mutant
    evaluated: int
    errors: int
    deviating_ineligible: list  # must be empty: the predicate missed a deviation
    detecting_nondeviating: list  # must be empty: a detection with no deviation behind it
    hits: int  # deviant-branch executions, by the mutant's own counter (cross-check only)
    deviations: dict = field(default_factory=dict)  # instance -> Deviation (replayable)

    @property
    def counts(self) -> tuple[int, int, int]:
        return len(self.eligible), len(self.deviating), len(self.detecting)

    @property
    def exposure(self) -> tuple[int, int]:
        """(deviating, eligible)."""
        return len(self.deviating), len(self.eligible)

    @property
    def detection_breadth(self) -> tuple[int, int]:
        """(detecting, deviating)."""
        return len(self.detecting), len(self.deviating)


def _canon_event(e) -> str:
    from .snapshot_json import _event
    return json.dumps(_event(e), sort_keys=True)


def code_fingerprint() -> str:
    """SHA-256 over the live code the matrix executes: every function and
    method defined in the replica and metamorphic modules (nested functions
    ride along as constants), marshalled from the live objects, plus each
    eligibility predicate. A monkeypatch or an edit changes it."""
    import marshal
    import sys
    import types
    from . import replica as rmod
    h = hashlib.sha256()

    def add(name, fn):
        code = getattr(fn, "__code__", None)
        if code is not None:
            h.update(name.encode() + b"\0" + marshal.dumps(code))

    for modname, mod in (("replica", rmod), ("metamorphic", sys.modules[__name__])):
        for name, obj in sorted(vars(mod).items()):
            if isinstance(obj, types.FunctionType):
                add(f"{modname}.{name}", obj)
            elif isinstance(obj, type) and obj.__module__ == mod.__name__:
                for an, a in sorted(vars(obj).items()):
                    add(f"{modname}.{name}.{an}", a.__func__ if isinstance(a, (classmethod, staticmethod)) else a)
    for flag, (op, what, pred) in sorted(ELIGIBILITY.items()):
        h.update(f"{flag}|{op}|{what}".encode())
        add(f"eligibility.{flag}", pred)
    return h.hexdigest()


def matrix_inputs_fingerprint(kinds) -> str:
    """The matrix cache key: binds every input that can change a cell. The
    fixture set and each instance's events, policy and lineages; for every
    relation, its alphabet, every eligibility value and its complete
    execution set; the execution configuration; the mutant and relation
    lists; and the live code. Stable across processes and hash seeds."""
    h = hashlib.sha256()
    h.update(code_fingerprint().encode())
    h.update(json.dumps(EXECUTION_CONFIG, sort_keys=True).encode())
    h.update(json.dumps([list(FLAGS), list(RELATIONS), list(kinds)]).encode())
    for inst in instances(kinds):
        index: dict = {}
        canon = lambda e: index.setdefault(e, len(index))  # noqa: E731
        h.update(json.dumps([inst.name, [_canon_event(e) for e in inst.events], sorted(inst.policy.authorities),
                             inst.policy.min_quorum, list(inst.lineages)]).encode())
        for rel in RELATIONS:
            h.update(json.dumps([rel, [_canon_event(e) for e in alphabet(rel, inst)],
                                 [eligible(rel, flag, inst) for flag in FLAGS]]).encode())
            for policy, steps, lineages in _programs(rel, inst):
                h.update(json.dumps([sorted(policy.authorities), policy.min_quorum, list(lineages),
                                     [-1 if a is None else canon(a) for _op, a in steps]]).encode())
            h.update(json.dumps(sorted((_canon_event(e), i) for e, i in index.items())).encode())
    return h.hexdigest()


_MATRIX_CACHE: dict = {}


def mutant_matrix(kinds=KINDS, cache: bool = True) -> dict:
    """(relation, flag) -> MatrixCell, every quantity measured per source
    instance: eligibility from the frozen predicate, deviation by lockstep
    differential execution over the relation's own executions, detection by
    running the relation on that instance alone. Cached under
    ``matrix_inputs_fingerprint``, so a changed input always recomputes;
    ``cache=False`` always recomputes. Callers get their own dict."""
    kinds = tuple(kinds)
    if not cache:
        return _compute_matrix(kinds)
    key = matrix_inputs_fingerprint(kinds)
    if key not in _MATRIX_CACHE:
        _MATRIX_CACHE[key] = _compute_matrix(kinds)
    return dict(_MATRIX_CACHE[key])


def matrix_canonical(m: dict) -> dict:
    """Every cell, every denominator, every recorded deviation, as plain data."""
    return {f"{rel}/{flag}": {
        "category": c.category, "eligible": sorted(c.eligible), "deviating": sorted(c.deviating),
        "detecting": sorted(c.detecting), "evaluated": c.evaluated, "errors": c.errors,
        "deviating_ineligible": sorted(c.deviating_ineligible),
        "detecting_nondeviating": sorted(c.detecting_nondeviating), "hits": c.hits,
        "deviations": {n: [d.execution, d.step, d.what, [list(s) for s in d.steps]]
                       for n, d in sorted(c.deviations.items())},
    } for (rel, flag), c in sorted(m.items())}


def _compute_matrix(kinds: tuple) -> dict:
    insts = instances(kinds)
    out = {}
    for flag in FLAGS:
        cells = {}
        for name in RELATIONS:
            elig, dev, det, dev_out, det_out, devs = [], [], [], [], [], {}
            errors = hits = 0
            for inst in insts:
                is_elig = eligible(name, flag, inst)
                try:
                    d = deviation(name, inst, flag)
                    r = _relation_on(name, inst, {flag: True})
                except Exception:  # infrastructure failure: never a kill
                    errors += 1
                    continue
                hits += r.hits
                if d:
                    devs[inst.name] = d
                if is_elig:
                    elig.append(inst.name)
                    if d:
                        dev.append(inst.name)
                        if not r.held:
                            det.append(inst.name)
                elif d:
                    dev_out.append(inst.name)
                if not r.held and not d:
                    det_out.append(inst.name)
            cells[name] = (elig, dev, det, dev_out, det_out, errors, hits, devs)
        any_deviation = any(c[1] or c[3] for c in cells.values())
        for name, (elig, dev, det, dev_out, det_out, errors, hits, devs) in cells.items():
            if det:
                cat = Cell.KILLED
            elif dev:
                cat = Cell.SURVIVED
            elif errors:
                cat = Cell.ERROR
            elif not any_deviation:
                cat = Cell.EQUIVALENT_CANDIDATE
            else:
                cat = Cell.NOT_REACHED if elig else Cell.NOT_APPLICABLE
            out[(name, flag)] = MatrixCell(cat, elig, dev, det, len(insts), errors, dev_out, det_out, hits, devs)
    return out


def matrix_anomalies(m: dict) -> list[str]:
    """Must be empty: deviations the frozen predicate did not anticipate, and
    detections without a deviation behind them."""
    out = []
    for (rel, flag), c in m.items():
        out += [f"{rel}/{flag}: deviation on ineligible {i}" for i in c.deviating_ineligible]
        out += [f"{rel}/{flag}: detection without deviation on {i}" for i in c.detecting_nondeviating]
        out += [f"{rel}/{flag}: {c.errors} errored instances"] if c.errors else []
    return out


def mutation_score(m: dict) -> tuple[int, int]:
    """(mutants killed by some relation, mutants not equivalent-candidates)."""
    killed = {f for (r, f), v in m.items() if v.category == Cell.KILLED}
    equiv = {f for f in FLAGS if all(m[(r, f)].category == Cell.EQUIVALENT_CANDIDATE for r in RELATIONS)}
    return len(killed), len(FLAGS) - len(equiv)


#: Category labels, shared by the published v1 table and v2.
CATEGORY_LABEL = {Cell.KILLED: "KILLED", Cell.SURVIVED: "survived", Cell.NOT_REACHED: "not reached",
                  Cell.NOT_APPLICABLE: "n/a", Cell.EQUIVALENT_CANDIDATE: "equivalent?", Cell.ERROR: "ERROR"}

_NEVER_REACHES_TWO_LINEAGES = ("this relation's alphabet has a single lineage, so the mutated cross-lineage "
                               "branch is unreachable and no instance is eligible (v1 had no eligibility "
                               "predicate, so it said 'not reached')")
_NEVER_SERIALIZES = ("this relation never serializes, so the mutated freeze() never runs and no instance is "
                     "eligible (v1 had no eligibility predicate, so it said 'not reached')")

#: Every cell whose category differs between the v1 matrix as published at
#: b535b1a and v2, with the reason. ``test_every_v1_to_v2_change_is_explained``
#: requires an exact match, so an unexplained change fails the suite.
V1_TO_V2_CHANGES = {
    ("serialization", "naive_stale_guard"): (
        "not reached", "survived", "prefixes of the reversed order make edges arrive out of order, which reaches "
        "the naive guard; restored and reference replicas share the mutant, so the relation correctly holds"),
    ("serialization", "global_dispute"): (
        "not reached", "survived", "the declared alphabet includes the unrelated lineage's admissions, so a "
        "dispute in L meets a head query for M; restored and reference share the mutant"),
    ("order", "global_dispute"): ("not reached", "n/a", _NEVER_REACHES_TWO_LINEAGES),
    ("duplicate", "global_dispute"): ("not reached", "n/a", _NEVER_REACHES_TWO_LINEAGES),
    ("renaming", "global_dispute"): ("not reached", "n/a", _NEVER_REACHES_TWO_LINEAGES),
    ("order", "freeze_drops_invalidated"): ("not reached", "n/a", _NEVER_SERIALIZES),
    ("duplicate", "freeze_drops_invalidated"): ("not reached", "n/a", _NEVER_SERIALIZES),
    ("projection", "freeze_drops_invalidated"): ("not reached", "n/a", _NEVER_SERIALIZES),
    ("renaming", "freeze_drops_invalidated"): ("not reached", "n/a", _NEVER_SERIALIZES),
}


#: The v1 matrix rows exactly as published in docs/RESULTS.md at b535b1a
#: (``test_documented_v1_table_is_the_published_one`` checks them against git).
PUBLISHED_V1_TABLE = """\
| `first_applied_wins` | **KILLED** (9) | survived (163) | survived (465) | survived (111) | survived (43) |
| `naive_stale_guard` | **KILLED** (2) | not reached (0) | not reached (0) | not reached (0) | not reached (0) |
| `replay_readmits` | not reached (0) | **KILLED** (252) | not reached (0) | not reached (0) | survived (252) |
| `global_dispute` | not reached (0) | not reached (0) | **KILLED** (89) | not reached (0) | not reached (0) |
| `rid_tiebreak` | survived (842) | survived (110) | survived (86) | **KILLED** (16) | survived (102) |
| `principal_order_quorum` | survived (30967) | survived (386) | survived (973) | **KILLED** (26) | survived (123) |
| `freeze_drops_invalidated` | not reached (0) | not reached (0) | not reached (0) | not reached (0) | **KILLED** (2) |
"""


def published_v1_categories(table: str) -> dict:
    """{(relation, flag): label} from the v1 table as published at b535b1a."""
    rels = list(RELATIONS)
    out = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        flag = cells[0].strip("`")
        for rel, c in zip(rels, cells[1:]):
            out[(rel, flag)] = "KILLED" if "KILLED" in c else c.split(" (")[0]
    return out


def v1_to_v2_changes(v1: dict, m: dict) -> dict:
    """{cell: (v1 label, v2 label)} wherever the category differs."""
    return {k: (v1[k], CATEGORY_LABEL[m[k].category]) for k in v1 if v1[k] != CATEGORY_LABEL[m[k].category]}


# ------------------------------------------------------------ retained counterexamples

#: Corrupted snapshots that made ``head()`` raise KeyError before the repair:
#: at b535b1a (development lineage) and at c175fbd (published main baseline,
#: not repaired there). Each is fully determined by this recipe. The data and
#: the recorded historical failures: tests/fixtures/snapshot_counterexamples.json
#: (``scripts/make_snapshot_counterexamples.py`` regenerates it).
RETAINED_COUNTEREXAMPLES = (
    {"id": "CE-1", "site": "_chain_linked follows a predecessor that is no longer admitted",
     "instance": "chain", "order": "reversed", "prefix": 4, "corruption": "drop one admitted record"},
    {"id": "CE-2", "site": "head() looks up the source of a resolution that is no longer admitted",
     "instance": "fork+dispute", "order": "causal", "prefix": 7, "corruption": "drop one admitted record"},
)


def counterexample_snapshot(spec: dict) -> tuple:
    """(instance, delivered prefix, damaged snapshot) for a retained recipe."""
    inst = build(spec["instance"])
    evs = list(inst.events) if spec["order"] == "causal" else list(reversed(inst.events))
    prefix = evs[:spec["prefix"]]
    genuine = run(inst, prefix, {}).freeze()
    damaged = CORRUPTIONS[spec["corruption"]][1](genuine)
    assert damaged != genuine
    return inst, prefix, damaged


# ------------------------------------------------------------ declared alphabet, exercised

#: The handling each boundary event may receive under the replica's own
#: rules, wherever the serialization relation delivers it. Frozen with v2.
BOUNDARY_EXPECT = {
    "Edge(r2->r0)": {"deferred", "rejected: non-forward edge"},
    "Edge(r0->m0)": {"deferred", "rejected: cross-lineage edge"},
    "Edge(rX->r0)": {"deferred"},
    # in a fork r2's declared predecessor *is* r0, so there it is a valid edge
    "Edge(r0->r2)": {"deferred", "applied", "duplicate", "rejected: edge not declared by successor admission"},
    "Invalidate(rX)": {"deferred"},
    "Admit(r3, L, seq=3, owner=alice, pred=rX)": {"applied"},  # admitted, never eligible (its edge never comes)
    "Admit(r1, L, seq=1, owner=carol, pred=r0)": {"applied", "rejected: re-admission of existing id"},
    "tampered resolution (pre-state root)": {"deferred", "rejected: pre-state root mismatch"},
    "tampered resolution (post-state root)": {"deferred", "rejected: post-state root mismatch"},
    "malformed resolution (winner = loser)": {"deferred", "rejected: winner/loser must be the two conflicting candidates"},
    "under-quorum resolution (one independent approver)": {"deferred", "rejected: quorum not met"},
    # declared sequence: the edge is the event classified
    "cross-lineage edge, after admitting its M end": {"deferred", "rejected: cross-lineage edge"},
}

#: Every rejection reason the correct replica can give (mutant-only reasons excluded).
REJECTION_RULES = ("re-admission of existing id", "cross-lineage edge", "non-forward edge",
                   "edge not declared by successor admission",
                   "winner/loser must be the two conflicting candidates", "pre-state root mismatch",
                   "post-state root mismatch", "self-authorized: no independent approver", "quorum not met")


def _boundary_label(e) -> str:
    return next((name for name, b in named_boundary_events() if b == e), label(e))


def alphabet_exercise(insts=None) -> dict:
    """Deliver every event of the declared alphabet after every serialized
    prefix of every instance (as the serialization relation does) to a
    correct replica, and record how the replica handled *that* event:
    applied, deferred, rejected (with the rule) or ignored as a duplicate."""
    insts = insts or instances()
    boundary = {name for name, _e in named_boundary_events()} | {name for name, _s in boundary_sequences()}
    seq_name = {seq: name for name, seq in boundary_sequences()}
    outcomes: dict = {}
    for inst in insts:
        universe = event_universe([inst])
        for pre in serialization_prefixes(inst):
            for suffix in _suffixes(universe):
                rep = run(inst, list(pre), {})
                for e in suffix[:-1]:  # a sequence's leading events set up the one classified
                    rep.deliver(e)
                nxt = suffix[-1]
                dup = nxt in rep.seen
                n = len(rep.rejected)
                rep.deliver(nxt)
                if dup:
                    o = "duplicate"
                elif any(e == nxt for e, _ in rep.rejected[n:]):
                    o = "rejected: " + next(w for e, w in rep.rejected[n:] if e == nxt).split(" (")[0]
                elif nxt in rep.pending:
                    o = "deferred"
                else:
                    o = "applied"
                key = seq_name.get(suffix) or _boundary_label(nxt)
                outcomes.setdefault(key, Counter())[o] += 1
    rules = Counter()
    for c in outcomes.values():
        for o, k in c.items():
            if o.startswith("rejected: "):
                rules[o[len("rejected: "):]] += k
    return {
        "boundary": {b: dict(outcomes.get(b, {})) for b in sorted(boundary)},
        "unexpected": {b: sorted(set(outcomes.get(b, {})) - BOUNDARY_EXPECT[b]) for b in sorted(boundary)
                       if set(outcomes.get(b, {})) - BOUNDARY_EXPECT[b]},
        "never_delivered": sorted(b for b in boundary if not outcomes.get(b)),
        "rejection_rules_exercised": dict(sorted(rules.items())),
        "rejection_rules_not_exercised": [r for r in REJECTION_RULES if r not in rules],
        "events": len(outcomes),
        "deliveries": sum(sum(c.values()) for c in outcomes.values()),
    }


def matrix_markdown(m: dict) -> str:
    sym = {Cell.KILLED: "**KILLED**", Cell.SURVIVED: "survived", Cell.NOT_REACHED: "not reached",
           Cell.NOT_APPLICABLE: "n/a", Cell.EQUIVALENT_CANDIDATE: "equivalent?", Cell.ERROR: "ERROR"}
    lines = ["| mutant | " + " | ".join(RELATIONS) + " |", "|---|" + "---|" * len(RELATIONS)]
    for flag in FLAGS:
        cells = []
        for r in RELATIONS:
            c = m[(r, flag)]
            e, d, k = c.counts
            cells.append(f"{sym.get(c.category, c.category)} E{e} D{d} K{k}" if e or d else sym[c.category])
        lines.append(f"| `{flag}` | " + " | ".join(cells) + " |")
    k, n = mutation_score(m)
    some = next(iter(m.values()))
    lines += ["", f"Per cell, over {some.evaluated} source instances: **E** eligible (frozen predicate over the "
                  "relation's alphabet), **D** deviating (eligible, and the mutant behaves differently from the "
                  "correct replica on some execution the relation performs: state, or head for head-time "
                  "mutants), **K** detecting (deviating, and the relation is violated). Exposure rate = D/E; "
                  "detection breadth = K/D. Anomalies (deviation on an ineligible instance, detection without "
                  f"deviation, errors): {len(matrix_anomalies(m))}. Suite mutation score: {k}/{n} "
                  "(mutants killed by some relation / mutants that deviate under some relation)."]
    return "\n".join(lines)


# ------------------------------------------------------------ preconditions


def shared_precondition(inst: Instance) -> list[str]:
    """Each record id is issued once; edges, predecessors and resolution
    sources only reference records of their own lineage."""
    out, admits = [], {}
    for e in inst.events:
        if isinstance(e, Admit):
            admits.setdefault(e.rid, set()).add(e)
    out += [f"{inst.name}: id {rid!r} issued {len(evs)} times" for rid, evs in admits.items() if len(evs) > 1]
    lin = {rid: next(iter(evs)).lineage for rid, evs in admits.items()}
    for e in inst.events:
        refs = {Edge: lambda x: (x.old, x.new), Admit: lambda x: (x.rid, x.pred) if x.pred else (x.rid,),
                Resolve: lambda x: (x.source, *x.candidates), Invalidate: lambda x: (x.rid,)}[type(e)](e)
        if len({lin.get(r) for r in refs}) > 1:
            out.append(f"{inst.name}: {label(e)} references more than one lineage")
    return out


def precondition_violations() -> list[str]:
    """Machine-checked preconditions of every relation on its own inputs."""
    out = []
    for inst in instances():
        out += shared_precondition(inst)
    other = build("fork+dispute", rid=lambda r: "m" + r[1:], lineage="M")  # R3
    out += shared_precondition(other)
    for inst in instances():
        l_ids = {e.rid for e in inst.events if isinstance(e, Admit)}
        m_ids = {e.rid for e in other.events if isinstance(e, Admit)}
        if l_ids & m_ids:
            out.append(f"projection: {inst.name} shares ids with the added lineage")
    for kind in KINDS:  # R4: bijections that keep seq and lineage
        src = build(kind)
        for rp in itertools.permutations(("r0", "r1", "r2")):
            rmap = dict(zip(("r0", "r1", "r2"), rp))
            for wp in itertools.permutations(PRINCIPALS):
                pmap = dict(zip(PRINCIPALS, wp))
                if len(set(rmap.values())) != 3 or len(set(pmap.values())) != 3:
                    out.append(f"renaming: {rmap}/{pmap} is not a bijection")
                inst = build(kind, rid=lambda x, m=rmap: m[x], who=lambda q, m=pmap: m[q])
                key = lambda ev: sorted((a.seq, a.lineage) for a in ev if isinstance(a, Admit))  # noqa: E731
                if key(inst.events) != key(src.events):
                    out.append(f"renaming changed seq/lineage in {kind}")
                out += shared_precondition(inst)
    return out
