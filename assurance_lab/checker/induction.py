"""Finite-domain inductive verification by exhaustive enumeration (§19).

Obligations, over *every* state of the finite universe satisfying Inv —
including states that BFS never reaches from Init:

    (1) Init ⇒ Inv
    (2) Inv ∧ Next ⇒ Inv'          (and every transition satisfies the
                                     transition properties)
    (3) Inv ⇒ Safety

``inv_states`` generates exactly {s ∈ TypeOK | Inv(s)}. It prunes during
generation, but every prune is the direct application of an Inv clause, and
each yielded state is re-checked against the full Inv. The tests cross-check
the generator against brute-force filtering on small domains.
"""

from __future__ import annotations

import itertools
import multiprocessing
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

from ..core.model import Config, CoreModel, Rec, State, live
from ..core.properties import inv, inv_failures, safety_failures, transition_failures
from ..provenance import loaded_origins
from ..statedigest import SetDigest, action_label, add_transitions, canon
from .partitions import Evidence, Manifest, build_manifest, manifest_problems, reconcile, sign


def record_domain(cfg: Config) -> list[Rec]:
    """All type-correct values of a single record (no Inv filtering)."""
    opt = lambda n: [None, *range(n)]  # noqa: E731
    out = []
    for adm, lin, own, apr, sem, inv_, ret, adj in itertools.product(
        (False, True), opt(cfg.n_lineages), opt(cfg.n_principals), opt(cfg.n_principals),
        opt(cfg.n_sem), (False, True), (False, True), opt(cfg.n_sem),
    ):
        out.append(Rec(adm, lin, own, apr, sem, inv_, ret, adj))
    return out


def _record_local_ok(r: Rec) -> bool:
    # RecordWF ∧ AdjudicationBound ∧ IndependentApproval, restricted to one record.
    if not r.admitted:
        return r == (False, None, None, None, None, False, False, None)
    if None in (r.lineage, r.owner, r.approver, r.sem):
        return False
    if r.adj_sem is not None and r.adj_sem != r.sem:
        return False
    return r.approver != r.owner


def record_candidates(cfg: Config) -> list[Rec]:
    return [r for r in record_domain(cfg) if _record_local_ok(r)]


def partitions(cfg: Config) -> list[tuple[tuple[int, ...], int]]:
    """Disjoint cover of the Inv-state space by the (levels, sem_now) globals."""
    level_space = itertools.product(range(cfg.max_level + 1), repeat=cfg.n_principals)
    return [(lv, sn) for lv in level_space for sn in range(cfg.n_sem)]


def inv_states(cfg: Config, parts=None) -> Iterator[State]:
    cands = record_candidates(cfg)
    n = cfg.n_records
    pairs = [(a, b) for a in range(n) for b in range(n)]
    for levels, sem_now in (partitions(cfg) if parts is None else parts):
        # ApproverAuthorized ∧ SemBound are per-record given the globals.
        ok = [r for r in cands
              if not r.admitted or (levels[r.approver] >= 1 and r.sem <= sem_now)]
        for recs in itertools.product(ok, repeat=n):
            # EdgesWF: candidate edges only between admitted, same lineage, a<b.
            epairs = [(a, b) for (a, b) in pairs
                      if a < b and recs[a].admitted and recs[b].admitted
                      and recs[a].lineage == recs[b].lineage]
            for k in range(len(epairs) + 1):
                for es in itertools.combinations(epairs, k):
                    # NoFork
                    srcs = [a for a, _ in es]
                    dsts = [b for _, b in es]
                    if len(set(srcs)) != len(srcs) or len(set(dsts)) != len(dsts):
                        continue
                    edges = frozenset(es)
                    probe = State(recs, frozenset(), edges, levels, sem_now)
                    # CurrentCoherent ∧ NoOrphanLive ⇒ current = live set.
                    current = frozenset(x for x in range(n) if live(probe, x))
                    # HeadUnique
                    lins = [recs[x].lineage for x in current]
                    if len(lins) != len(set(lins)):
                        continue
                    s = State(recs, current, edges, levels, sem_now)
                    assert inv(cfg, s), inv_failures(cfg, s)
                    yield s


@dataclass
class InductionReport:
    model: str
    config: Config
    inv_state_count: int
    transitions_checked: int
    init_ok: bool
    step_ok: bool
    safety_ok: bool
    complete: bool
    seconds: float
    first_failure: Optional[tuple] = None  # (obligation, state, label, next, messages)
    start_method: Optional[str] = None  # None: no pool (sequential loop in this process)
    # pid -> files that process imported assurance_lab from (parent included)
    origins: dict = field(default_factory=dict)
    digest: Optional[SetDigest] = None  # over every Inv-state checked
    transition_digest: Optional[SetDigest] = None  # (source, action, destination) triples
    edge_digest: Optional[SetDigest] = None  # (source, destination) pairs
    manifest: Optional[Manifest] = None  # frozen before any partition ran
    evidence: list = field(default_factory=list)  # signed per-partition records
    reconciliation_problems: list = field(default_factory=list)


def _check_parts(model: CoreModel, parts, max_states, stop_on_failure=False):
    cfg = model.cfg
    step_ok = safety_ok = True
    first = None
    count = transitions = 0
    complete = True
    digest, tdig, edig = SetDigest(), SetDigest(), SetDigest()
    keys = set()
    for s in inv_states(cfg, parts):
        if max_states is not None and count >= max_states:
            complete = False
            break
        count += 1
        keys.add((s.levels, s.sem_now))
        digest.add(s)
        succ = list(model.next(s))
        add_transitions(tdig, edig, canon(s), [(action_label(lbl), canon(t)) for lbl, t in succ])
        sf = safety_failures(cfg, s)
        if sf:
            safety_ok = False
            first = first or ("Inv⇒Safety", s, None, None, sf)
        for lbl, t in succ:
            transitions += 1
            msgs = inv_failures(cfg, t) + transition_failures(cfg, s, lbl, t)
            if msgs:
                step_ok = False
                first = first or ("Inv∧Next⇒Inv'", s, lbl, t, msgs)
        if stop_on_failure and first:
            complete = False
            break
    return count, transitions, step_ok, safety_ok, complete, first, digest, tdig, edig, keys


def _evidence(res, pid: str, manifest: Manifest, key: bytes) -> Evidence:
    count, transitions, step_ok, safety_ok, complete, _first, d, t, e, keys = res
    return sign(Evidence(manifest.run_id, manifest.digest(), pid, tuple(sorted(keys)), count, transitions,
                         step_ok, safety_ok, complete, d.label(), t.label(), e.label()), key)


def _worker(args):
    model_cls, cfg, pkeys, pid, manifest, key = args
    res = _check_parts(model_cls(cfg), pkeys, None)
    return _evidence(res, pid, manifest, key), res[5], os.getpid(), loaded_origins()


def check_induction(model: CoreModel, max_states: Optional[int] = None,
                    workers: int = 1, stop_on_failure: bool = False,
                    mp_context=None, manifest: Optional[Manifest] = None,
                    key: Optional[bytes] = None) -> InductionReport:
    """Run the three obligations, one partition at a time.

    The partition set is a ``Manifest`` frozen before any partition runs
    (pass one to bind it to a receipt). Each partition yields a signed
    ``Evidence`` record, sequentially or from a pool worker (``workers>1``;
    needs a module-level model class), and the aggregate is the
    reconciliation of those records against the manifest: any provenance,
    integrity, identity, completeness, disjointness or arithmetic problem
    makes the report incomplete. ``mp_context`` selects the pool's start
    method; every pool worker reports where it imported ``assurance_lab``.
    """
    cfg = model.cfg
    t0 = time.perf_counter()
    manifest = manifest or build_manifest(cfg, secrets.token_hex(8))
    key = key or secrets.token_bytes(32)
    s0 = model.init()
    init_ok = inv(cfg, s0)
    first = None if init_ok else ("Init⇒Inv", s0, None, None, inv_failures(cfg, s0))
    origins = {os.getpid(): loaded_origins()}
    start_method = None
    jobs = [(pid, [(tuple(lv), sn)]) for pid, lv, sn in manifest.entries]
    evidence, firsts = [], []
    if workers > 1 and max_states is None:
        from concurrent.futures import ProcessPoolExecutor
        ctx = mp_context or multiprocessing.get_context()
        start_method = ctx.get_start_method()
        with ProcessPoolExecutor(workers, mp_context=ctx) as ex:
            tagged = list(ex.map(_worker, [(type(model), cfg, pk, pid, manifest, key) for pid, pk in jobs]))
        for ev, fail, wpid, o in tagged:
            evidence.append(ev)
            firsts.append(fail)
            origins[wpid] = o
    else:
        used = 0
        for pid, pk in jobs:
            budget = None if max_states is None else max_states - used
            if budget is not None and budget <= 0:
                break
            res = _check_parts(model, pk, budget, stop_on_failure)
            used += res[0]
            evidence.append(_evidence(res, pid, manifest, key))
            firsts.append(res[5])
            if not res[4]:  # budget hit or stopped on failure
                break
    agg, problems = reconcile(manifest, evidence, key)
    problems = manifest_problems(manifest, cfg) + problems
    for f in firsts:
        first = first or f
    from .partitions import _sd
    return InductionReport(
        model=model.name, config=cfg, inv_state_count=agg.count, transitions_checked=agg.transitions,
        init_ok=init_ok, step_ok=agg.step_ok, safety_ok=agg.safety_ok,
        complete=agg.complete and not problems,
        seconds=time.perf_counter() - t0, first_failure=first,
        start_method=start_method, origins=origins, digest=_sd(agg.digest),
        transition_digest=_sd(agg.transition_digest), edge_digest=_sd(agg.edge_digest),
        manifest=manifest, evidence=evidence, reconciliation_problems=problems,
    )


def unmatched_reachable(cfg: Config, reachable) -> set:
    """Reachable states the Inv-state generator does NOT produce.

    Init ⊨ Inv and Inv inductive imply Reach ⊆ Inv-states, so this must be
    empty. It cross-checks the BFS over Next against the constructive
    generator: two separate pieces of code that must agree.
    """
    remaining = set(reachable)
    for s in inv_states(cfg):
        remaining.discard(s)
        if not remaining:
            break
    return remaining
