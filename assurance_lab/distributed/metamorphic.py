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
    }[kind]
    return Instance(kind, events, pol, (lineage,))


KINDS = ("chain", "chain+invalidate", "fork", "fork+resolution", "fork+beneficiary-only",
         "fork+dispute", "fork+dispute+escalation", "fork+resolution+invalidate-winner")


def instances() -> list[Instance]:
    return [build(k) for k in KINDS]


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
    hits: int = 0  # mutant deviant-branch executions observed
    witnesses: list = field(default_factory=list)

    @property
    def held(self) -> bool:
        return not self.witnesses


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
        base = list(inst.events)
        want = run_obs(inst, base, flags, ctr)
        perms = list(itertools.permutations(base))
        if len(perms) > max_perms:
            rng = random.Random(seed)
            perms = [tuple(base), tuple(reversed(base))] + [tuple(rng.sample(base, len(base)))
                                                          for _ in range(max_perms - 2)]
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
                       compare: Compare = lambda a, b: a == b) -> RelationResult:
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
        L, M = list(inst.events), list(other.events)
        ids = {e.rid for e in L if isinstance(e, Admit)}
        want = _project(run_obs(inst, L, flags, ctr, ("L",)), "L", ids)
        mixes = [M + L, L + M]
        for _ in range(interleavings):
            slots = sorted(rng.sample(range(len(L) + len(M)), len(M)))
            mix, li, mi = [], iter(L), iter(M)
            for k in range(len(L) + len(M)):
                mix.append(next(mi) if k in slots else next(li))
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


def serialization_round_trip(insts, flags=None, compare: Compare = lambda a, b: a == b) -> RelationResult:
    """R5 serialization round-trip (``Replica.freeze``/``thaw``).

    Precondition: the frozen form is the replica's complete state.
    Justification: thaw(freeze(r)) reconstructs every field the behavior
    depends on, so the restored replica has the same canonical state and
    responds identically to every possible next event.
    """
    flags = flags or {}
    res, ctr = RelationResult("serialization round-trip"), [0]
    for inst in insts:
        base = list(inst.events)
        done = False
        for i in range(len(base) + 1):
            orig = run(inst, base[:i], flags)
            copy = Replica.thaw(inst.policy, orig.freeze(), **flags)
            res.checks += 1
            same = copy.canonical() == orig.canonical() and \
                compare(observe(copy, inst.lineages), observe(orig, inst.lineages))
            ctr[0] += orig.hits + copy.hits
            for nxt in base:  # identical response to every possible next event
                restored = Replica.thaw(inst.policy, run(inst, base[:i], flags).freeze(), **flags)
                reference = run(inst, base[:i], flags)  # never serialized, same flags
                restored.deliver(nxt)
                reference.deliver(nxt)
                res.checks += 1
                same = same and compare(observe(restored, inst.lineages),
                                        observe(reference, inst.lineages))
                ctr[0] += restored.hits + reference.hits
            if not same:
                res.witnesses.append(Witness(res.relation, inst.name,
                                             f"state after {i} events not preserved by freeze/thaw",
                                             [base[:i]]))
                done = True
            if done:
                break
    res.hits = ctr[0]
    return res


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


def mutant_matrix() -> dict:
    """(relation, flag) -> (Cell, deviant-branch hits)."""
    out = {}
    for flag in FLAGS:
        row = {}
        for name, rel in RELATIONS.items():
            try:
                r = rel({flag: True})
                row[name] = (not r.held, r.hits)
            except Exception as e:  # infrastructure failure: never a kill
                row[name] = (Cell.ERROR + f": {type(e).__name__}", 0)
        exercised = any(k is True or h > 0 for k, h in row.values())
        for name, (killed, hits) in row.items():
            if isinstance(killed, str):
                out[(name, flag)] = (killed, hits)
            elif killed:
                out[(name, flag)] = (Cell.KILLED, hits)
            elif hits:
                out[(name, flag)] = (Cell.SURVIVED, hits)
            else:
                out[(name, flag)] = (Cell.NOT_REACHED if exercised else Cell.EQUIVALENT_CANDIDATE, 0)
    return out


def mutation_score(m: dict) -> tuple[int, int]:
    """(mutants killed by some relation, mutants not equivalent-candidates)."""
    killed = {f for (r, f), (c, _h) in m.items() if c == Cell.KILLED}
    equiv = {f for f in FLAGS if all(m[(r, f)][0] == Cell.EQUIVALENT_CANDIDATE for r in RELATIONS)}
    return len(killed), len(FLAGS) - len(equiv)


def matrix_markdown(m: dict) -> str:
    sym = {Cell.KILLED: "**KILLED**", Cell.SURVIVED: "survived", Cell.NOT_REACHED: "not reached",
           Cell.EQUIVALENT_CANDIDATE: "equivalent?"}
    lines = ["| mutant | " + " | ".join(RELATIONS) + " |", "|---|" + "---|" * len(RELATIONS)]
    for flag in FLAGS:
        cells = []
        for r in RELATIONS:
            c, h = m[(r, flag)]
            cells.append(f"{sym.get(c, c)} ({h})")
        lines.append(f"| `{flag}` | " + " | ".join(cells) + " |")
    k, n = mutation_score(m)
    lines += ["", f"Cells show the outcome and the number of deviant-branch executions. "
                  f"Suite mutation score: {k}/{n} (killed / non-equivalent)."]
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
