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

import itertools
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


def order_invariance(insts, flags=None, max_perms: int = 5040, seed: int = 0,
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
        total = __import__("math").factorial(len(base))
        if total > max_perms:  # deterministic sample, reported as such
            rng = random.Random(seed)
            perms = [tuple(base), tuple(reversed(base))] + [tuple(rng.sample(base, len(base)))
                                                          for _ in range(max_perms - 2)]
            res.coverage.append((inst.name, len(set(perms)), total))
        else:
            perms = list(itertools.permutations(base))
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
        done = False
        for i in range(1, len(base) + 1):
            for dup in base[:i]:
                res.checks += 1
                order = base[:i] + [dup] + base[i:]
                before = run_obs(inst, base[:i], flags, ctr)
                at = run_obs(inst, base[:i] + [dup], flags, ctr)
                end = run_obs(inst, order, flags, ctr)
                if not (compare(at, before) and compare(end, final)):
                    res.witnesses.append(Witness(res.relation, inst.name,
                                                 f"redelivering {label(dup)} changed the observation",
                                                 [base, order]))
                    done = True
                    break
            if done:
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


def lineage_projection(insts, flags=None, interleavings: int = 40, seed: int = 0,
                       compare: Compare = lambda a, b: a == b, max_exhaustive: int = 8000) -> RelationResult:
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
    other = build("fork+dispute", rid=lambda r: "m" + r[1:], lineage="M")
    rng = random.Random(seed)
    for inst in insts:
        res.instances += 1
        L, M = list(inst.events), list(other.events)
        ids = {e.rid for e in L if isinstance(e, Admit)}
        want = _project(run_obs(inst, L, flags, ctr, ("L",)), "L", ids)
        total = __import__("math").comb(len(L) + len(M), len(M))
        if total <= max_exhaustive:  # every interleaving preserving each lineage's own order
            slot_sets = itertools.combinations(range(len(L) + len(M)), len(M))
        else:
            slot_sets = [tuple(range(len(M))), tuple(range(len(L), len(L) + len(M)))] + \
                [tuple(sorted(rng.sample(range(len(L) + len(M)), len(M)))) for _ in range(interleavings)]
            res.coverage.append((inst.name, len(set(slot_sets)), total))
        mixes = []
        for slots in slot_sets:
            chosen, mix, li, mi = set(slots), [], iter(L), iter(M)
            for k in range(len(L) + len(M)):
                mix.append(next(mi) if k in chosen else next(li))
            mixes.append(mix)
        merged = Instance(inst.name + " + unrelated M", tuple(L + M), inst.policy, ("L", "M"))
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
    rid_perms = list(itertools.permutations(("r0", "r1", "r2")))
    who_perms = list(itertools.permutations(PRINCIPALS))
    for kind in insts_kinds:
        res.instances += 1
        src = build(kind)
        base = run_obs(src, src.events, flags, ctr)
        done = False
        for rp in rid_perms:
            rmap = dict(zip(("r0", "r1", "r2"), rp))
            for wp in who_perms:
                pmap = dict(zip(PRINCIPALS, wp))
                inst = build(kind, rid=lambda x, m=rmap: m[x], who=lambda p, m=pmap: m[p])
                res.checks += 1
                got = run_obs(inst, inst.events, flags, ctr)
                want = (expected_map or _rename_obs)(base, rmap, pmap)
                if got != want:
                    res.witnesses.append(Witness(res.relation, kind,
                                                 f"renaming {rmap} / {pmap} did not rename the outcome",
                                                 [list(src.events), list(inst.events)]))
                    done = True
                    break
            if done:
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


def boundary_events() -> list:
    """Invalid and boundary-case events for the declared alphabet: each must
    be handled identically by a restored replica and its reference."""
    from dataclasses import replace as _replace
    fork = build("fork+resolution")
    valid_res = fork.events[-1]
    return [
        Edge("r2", "r0"),  # backward (non-forward)
        Edge("r0", "m0"),  # cross-lineage
        Edge("rX", "r0"),  # unknown record: deferred forever
        Edge("r0", "r2"),  # undeclared in a chain (r2's declared pred is r1)
        Invalidate("rX"),  # unknown record: deferred forever
        Admit("r3", "L", 3, "alice", pred="rX"),  # unknown predecessor
        Admit("r1", "L", 1, "carol", pred="r0"),  # conflicting re-admission of an existing id
        _replace(valid_res, pre_state_root="0" * 16),  # tampered resolution
    ]


def event_universe(insts) -> list:
    """The declared finite event alphabet for the serialization relation:
    every event of every source instance, plus the unrelated lineage's
    events. Not every event the replica API could accept (that is
    unbounded); claims are scoped to this alphabet."""
    other = build("fork+dispute", rid=lambda r: "m" + r[1:], lineage="M")
    seen, out = set(), []
    for e in [e for inst in insts for e in inst.events] + list(other.events) + boundary_events():
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


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
        orders = [list(inst.events), list(reversed(inst.events))]  # reversed: events get deferred
        prefixes = {tuple(o[:i]) for o in orders for i in range(len(o) + 1)}
        infected_here = False
        caught: set = set()
        witness = None
        for pre in sorted(prefixes, key=lambda t: (len(t), [label(e) for e in t])):
            base = list(pre)
            frozen = run(inst, base, flags).freeze()
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
            for nxt in universe:  # response to every event of the declared universe
                restored = Replica.thaw(inst.policy, frozen, validate=validate, **flags)
                ref = run(inst, base, flags)
                restored.deliver(nxt)
                ref.deliver(nxt)
                res.checks += 1
                lins = tuple(sorted(set(inst.lineages) | ({nxt.lineage} if isinstance(nxt, Admit) else set())))
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


def corruption_table(insts=None) -> list[tuple]:
    """(corruption, infected, rejected at thaw, {mechanism: instances} with
    thaw validation *off*).

    With validation on, a corrupted snapshot is refused at thaw (fail
    closed), so the later detectors never see it. Running again with
    validation off shows what each later layer would catch on its own:
    ``projection`` (observation right after thaw), ``next-event`` (response
    to some event of the declared universe), ``exact-state`` (independent
    extraction of the exact-state contract)."""
    insts = insts or instances()
    rows = []
    for name, fn in CORRUPTIONS.items():
        on = serialization_round_trip(insts, corrupt=fn)
        off = serialization_round_trip(insts, corrupt=fn, validate=False)
        mech = {k: v for k, v in off.mechanisms.items() if k != "thaw-validation"}
        rows.append((name, on.infected, on.mechanisms["thaw-validation"], mech))
    return rows


#: Deliberate damage to a frozen replica state (audit P1). Each must be
#: rejected in every instance it infects.
CORRUPTIONS = {
    "drop invalidations": lambda f: (f[0], f[1], frozenset(), *f[3:]),
    "drop one applied edge": lambda f: (f[0], frozenset(sorted(f[1])[1:]), *f[2:]),
    "drop one admitted record": lambda f: (f[0][1:], *f[1:]),
    "drop resolutions": lambda f: (f[0], f[1], f[2], (), *f[4:]),
    "drop pending queue": lambda f: (*f[:5], (), f[6]),
    "drop seen-set (duplicate filter)": lambda f: (*f[:6], frozenset()),
}


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
    KILLED = "KILLED"  # the relation was violated
    SURVIVED = "SURVIVED"  # deviant branch ran, relation held
    # Deviant branch never ran on this relation's inputs, but some other
    # relation does exercise the mutant: a source-input adequacy observation
    # about this relation (often by design: its precondition excludes the
    # triggering input).
    NOT_REACHED = "NOT_REACHED"
    # No relation ever exercises the mutant: candidate equivalent/unreachable
    # mutant, excluded from the mutation-score denominator.
    EQUIVALENT_CANDIDATE = "EQUIVALENT_CANDIDATE"
    ERROR = "ERROR"


def _relation_on(name: str, inst_or_kind, flags):
    if name == "renaming":
        return renaming_equivariance([inst_or_kind.name], flags)
    return {"order": order_invariance, "duplicate": duplicate_idempotence,
            "projection": lineage_projection, "serialization": serialization_round_trip}[name]([inst_or_kind], flags)


@dataclass
class MatrixCell:
    category: str
    detecting: int  # eligible instances in which the relation caught the mutant
    eligible: int  # instances in which the mutant's deviant branch executed (or it was caught)
    evaluated: int
    errors: int
    hits: int  # total deviant-branch executions (exercise until detection)

    def __iter__(self):  # (category, hits, detecting, eligible) for older callers
        return iter((self.category, self.hits, self.detecting, self.eligible))

    def __getitem__(self, i):
        return tuple(self)[i]


def mutant_matrix(kinds=KINDS) -> dict:
    """(relation, flag) -> MatrixCell, evaluated one source instance at a time,
    so kill breadth = detecting eligible instances / eligible instances."""
    insts = instances(kinds)
    out = {}
    for flag in FLAGS:
        cells = {}
        for name in RELATIONS:
            detecting = eligible = errors = hits = 0
            for inst in insts:
                try:
                    r = _relation_on(name, inst, {flag: True})
                except Exception:  # infrastructure failure: never a kill
                    errors += 1
                    continue
                hits += r.hits
                caught = not r.held
                detecting += caught
                eligible += bool(caught or r.hits)
            cells[name] = (detecting, eligible, errors, hits)
        exercised = any(e for _d, e, _x, _h in cells.values())
        for name, (d, e, x, h) in cells.items():
            if d:
                cat = Cell.KILLED
            elif e:
                cat = Cell.SURVIVED
            elif x:
                cat = Cell.ERROR
            else:
                cat = Cell.NOT_REACHED if exercised else Cell.EQUIVALENT_CANDIDATE
            out[(name, flag)] = MatrixCell(cat, d, e, len(insts), x, h)
    return out


def mutation_score(m: dict) -> tuple[int, int]:
    """(mutants killed by some relation, mutants not equivalent-candidates)."""
    killed = {f for (r, f), v in m.items() if v.category == Cell.KILLED}
    equiv = {f for f in FLAGS if all(m[(r, f)].category == Cell.EQUIVALENT_CANDIDATE for r in RELATIONS)}
    return len(killed), len(FLAGS) - len(equiv)


def matrix_markdown(m: dict) -> str:
    sym = {Cell.KILLED: "**KILLED**", Cell.SURVIVED: "survived", Cell.NOT_REACHED: "not reached",
           Cell.EQUIVALENT_CANDIDATE: "equivalent?"}
    lines = ["| mutant | " + " | ".join(RELATIONS) + " |", "|---|" + "---|" * len(RELATIONS)]
    for flag in FLAGS:
        cells = []
        for r in RELATIONS:
            c = m[(r, flag)]
            if c.category == Cell.KILLED:
                cells.append(f"**KILLED** {c.detecting}/{c.eligible} eligible")
            elif c.category == Cell.SURVIVED:
                cells.append(f"survived 0/{c.eligible} eligible")
            else:
                cells.append(sym.get(c.category, c.category))
        lines.append(f"| `{flag}` | " + " | ".join(cells) + " |")
    k, n = mutation_score(m)
    some = next(iter(m.values()))
    errs = sum(c.errors for c in m.values())
    lines += ["", f"Kill breadth = detecting eligible instances / eligible instances, of {some.evaluated} "
                  "evaluated per cell. An instance is *eligible* when the mutant's deviant branch makes an "
                  "effective deviation in it (changes state or result relative to the correct rule); "
                  f"the rest are ineligible. Errored instances: {errs}. Suite mutation score: "
                  f"{k}/{n} (killed / non-equivalent)."]
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
