"""Distributed surrogate (§27-28): eventual convergence, not consensus.

Events
------
* ``Admit(rid, lineage, seq, owner, pred)``: admission. ``pred`` declares the
  intended predecessor; a successor is *not eligible* until its edge is
  applied (fail-closed: an unlinked successor never carries authority).
* ``Edge(old, new)``: historical supersession edge. Edges append
  monotonically; they are never rejected merely because ``new`` has itself
  already been superseded (that rejection was handoff failure #3).
* ``Invalidate(rid)``.
* ``Resolve(...)``: an explicit SUCCESSOR_CONFLICT_RESOLUTION event.

Delivery may be reordered and duplicated. Events whose causal dependencies
are missing are deferred, not dropped.

What is *not* claimed: linearizability, atomic consistency, consensus, or any
bound on how long a replica serves a stale (but once-legitimate) head.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass(frozen=True)
class Admit:
    rid: str
    lineage: str
    seq: int
    owner: str
    pred: Optional[str] = None


@dataclass(frozen=True)
class Edge:
    old: str
    new: str


@dataclass(frozen=True)
class Invalidate:
    rid: str


@dataclass(frozen=True)
class Resolve:
    conflict_id: str
    source: str
    candidates: tuple[str, str]
    pre_state_root: str
    rule: str
    winner: str
    loser: str
    approvals: frozenset
    quorum: int
    evidence_basis: str
    epoch: int
    post_state_root: str


Event = Union[Admit, Edge, Invalidate, Resolve]


def _h(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ResolutionPolicy:
    """Who may resolve successor conflicts, and how many must agree."""

    authorities: frozenset  # principals allowed to approve resolutions
    min_quorum: int = 2


CONFLICT = "CONFLICT"


class CorruptSnapshot(ValueError):
    """A frozen replica state that is not internally consistent."""


@dataclass
class Replica:
    policy: ResolutionPolicy
    naive_stale_guard: bool = False  # mutant: reproduce handoff failure #3
    first_applied_wins: bool = False  # mutant: reproduce the split-brain counterexample
    # Negative controls for the metamorphic suite (see metamorphic.py). Each
    # counts executions of its deviant branch in ``hits``, so "never
    # exercised" can be told apart from "exercised but not detected".
    replay_readmits: bool = False  # no duplicate filter; re-admission revives a record
    global_dispute: bool = False  # a dispute in any lineage blocks every lineage
    rid_tiebreak: bool = False  # unresolved fork resolved silently by smallest id
    principal_order_quorum: bool = False  # "independent" means sorting after the beneficiary
    freeze_drops_invalidated: bool = False  # serialization loses invalidations
    admitted: dict[str, Admit] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    invalidated: set[str] = field(default_factory=set)
    resolutions: dict[str, set] = field(default_factory=dict)  # source -> all valid resolutions
    rejected: list[tuple[Event, str]] = field(default_factory=list)
    pending: list[Event] = field(default_factory=list)
    seen: set[Event] = field(default_factory=set)
    hits: int = field(default=0, compare=False)  # deviant-branch executions (mutants only)

    # ---------------------------------------------------------------- deliver
    def deliver(self, ev: Event) -> None:
        if ev in self.seen and not self.replay_readmits:
            return  # idempotent duplicate delivery
        self.seen.add(ev)
        self.pending.append(ev)
        progress = True
        while progress:
            progress = False
            for e in list(self.pending):
                outcome = self._try_apply(e)
                if outcome != "defer":
                    self.pending.remove(e)
                    progress = True

    def _reject(self, ev: Event, why: str) -> str:
        self.rejected.append((ev, why))
        return "rejected"

    def _try_apply(self, ev: Event) -> str:
        if isinstance(ev, Admit):
            if ev.rid in self.admitted and self.replay_readmits:
                # MUTANT: replayed admission re-admits (and revives) the record.
                # Counted only when it changes state (an *effective* deviation).
                self.hits += ev.rid in self.invalidated or self.admitted[ev.rid] != ev
                self.admitted[ev.rid] = ev
                self.invalidated.discard(ev.rid)
                return "applied"
            if ev.rid in self.admitted:
                return self._reject(ev, "re-admission of existing id")
            self.admitted[ev.rid] = ev
            return "applied"
        if isinstance(ev, Edge):
            a, b = self.admitted.get(ev.old), self.admitted.get(ev.new)
            if a is None or b is None:
                return "defer"
            if a.lineage != b.lineage:
                return self._reject(ev, "cross-lineage edge")
            if not a.seq < b.seq:
                return self._reject(ev, "non-forward edge")
            if b.pred != ev.old:
                return self._reject(ev, "edge not declared by successor admission")
            if self.naive_stale_guard and self._superseded(ev.new):
                self.hits += 1
                return self._reject(ev, "naive guard: successor already superseded")
            self.edges.add((ev.old, ev.new))
            return "applied"
        if isinstance(ev, Invalidate):
            if ev.rid not in self.admitted:
                return "defer"
            self.invalidated.add(ev.rid)
            return "applied"
        if isinstance(ev, Resolve):
            return self._apply_resolution(ev)
        raise TypeError(ev)

    # ------------------------------------------------------------- resolution
    def conflict_root(self, source: str, candidates) -> str:
        a, b = sorted(candidates)
        return _h("pre", source, a, self.admitted[a].owner, b, self.admitted[b].owner)

    def validate_resolution(self, r: Resolve) -> Optional[str]:
        """Return None if valid, else the reason. Structural checks only;
        causal readiness is handled by the caller (defer)."""
        if sorted(r.candidates) != sorted({r.winner, r.loser}) or r.winner == r.loser:
            return "winner/loser must be the two conflicting candidates"
        if r.pre_state_root != self.conflict_root(r.source, r.candidates):
            return "pre-state root mismatch"
        if r.post_state_root != _h("post", r.pre_state_root, r.winner, r.loser, r.epoch):
            return "post-state root mismatch"
        beneficiary = self.admitted[r.winner].owner
        independent = {p for p in r.approvals if p in self.policy.authorities and p != beneficiary}
        if self.principal_order_quorum:  # MUTANT: identity compared by name order
            ordered = {p for p in r.approvals if p in self.policy.authorities and p > beneficiary}
            self.hits += ordered != independent
            independent = ordered
        if not independent:
            return "self-authorized: no independent approver"
        need = max(r.quorum, self.policy.min_quorum)
        if len(independent) < need:
            return f"quorum not met ({len(independent)}/{need} independent approvals)"
        if self.first_applied_wins and self.resolutions.get(r.source) and r not in self.resolutions[r.source]:
            self.hits += 1
            # MUTANT. Rejecting a valid competing resolution because another
            # arrived first lets delivery order pick the authority: the
            # replica-level model checker found replicas converging on
            # different heads (split-brain) and the dispute becoming invisible.
            return "conflict already resolved"
        return None

    def _apply_resolution(self, r: Resolve) -> str:
        if not all(c in self.admitted for c in r.candidates) or r.source not in self.admitted:
            return "defer"
        if not all((r.source, c) in self.edges for c in r.candidates):
            # Edges may be causally pending (deferred) or may never come; we
            # defer. A resolution for a conflict this replica never sees stays
            # pending, which is safe: it can only *remove* eligibility.
            return "defer"
        why = self.validate_resolution(r)
        if why:
            return self._reject(r, why)
        self.resolutions.setdefault(r.source, set()).add(r)
        return "applied"

    def decision(self, source: str) -> tuple[str, Optional[str], Optional[str]]:
        """Effective resolution of a fork, as a function of the *set* of valid
        resolutions held (never of their arrival order).

        ("none", -, -) no resolution; ("agreed", winner, loser) all top-epoch
        resolutions agree; ("disputed", -, -) top-epoch resolutions disagree,
        which fails closed until a higher-epoch resolution settles it.
        """
        rs = self.resolutions.get(source)
        if not rs:
            return "none", None, None
        top = max(r.epoch for r in rs)
        decisions = {(r.winner, r.loser) for r in rs if r.epoch == top}
        if len(decisions) > 1:
            return "disputed", None, None
        (winner, loser), = decisions
        return "agreed", winner, loser

    # ------------------------------------------------------------- derivation
    def _superseded(self, rid: str) -> bool:
        return any(a == rid for a, _ in self.edges)

    def _chain_linked(self, rid: str) -> bool:
        """Every declared predecessor edge back to a root has been applied.

        Refinement from a counterexample found here: checking only the
        record's *own* edge let r2 (linked to r1) be eligible while r1 was
        still unlinked from r0, so r0 and r2 both looked like heads.
        """
        seen = set()
        cur = self.admitted[rid]
        while cur.pred is not None:
            if cur.rid in seen or (cur.pred, cur.rid) not in self.edges:
                return False
            seen.add(cur.rid)
            nxt = self.admitted.get(cur.pred)
            if nxt is None:  # inconsistent state: fail closed, never crash
                return False
            cur = nxt
        return True

    def _losers(self) -> set[str]:
        """Rejected branch roots and everything reachable after them."""
        out: set[str] = set()
        frontier = [lo for src in self.resolutions
                    for status, _w, lo in [self.decision(src)] if status == "agreed"]
        while frontier:
            n = frontier.pop()
            if n in out:
                continue
            out.add(n)
            frontier.extend(b for a, b in self.edges if a == n)
        return out

    def head(self, lineage: str) -> Optional[str]:
        """Derived current authority for a lineage.

        Returns a record id, None (X_⊥ / nothing eligible), or CONFLICT
        (unresolved fork, fail closed). Delivery order is never authority
        order: the answer is a function of the delivered *set*.
        """
        members = [a for a in self.admitted.values() if a.lineage == lineage]
        disputed = [src for src in self.resolutions if self.decision(src)[0] == "disputed"]
        for src in disputed:
            a = self.admitted.get(src)
            if a is None or a.lineage == lineage:
                # authorities disagree (or the state is inconsistent): fail closed
                return CONFLICT
        if self.global_dispute and disputed:  # MUTANT: couples unrelated lineages
            # counted only when the correct rule would not also answer CONFLICT
            self.global_dispute = False
            try:
                self.hits += self.head(lineage) != CONFLICT
            finally:
                self.global_dispute = True
            return CONFLICT
        if any(src not in self.admitted for src in self.resolutions):
            return CONFLICT  # resolution for an unknown record: inconsistent state, fail closed
        losers = self._losers()
        for a in members:
            succ = [b for x, b in self.edges if x == a.rid and b not in losers]
            if len(succ) > 1:
                if self.rid_tiebreak:  # MUTANT: silent arbitration by identifier
                    self.hits += 1
                    keep = min(succ)
                    frontier = [b for b in succ if b != keep]
                    while frontier:
                        n = frontier.pop()
                        if n not in losers:
                            losers.add(n)
                            frontier.extend(y for x, y in self.edges if x == n)
                    continue
                return CONFLICT
        cands = []
        for a in members:
            rid = a.rid
            if rid in self.invalidated or rid in losers:
                continue
            if not self._chain_linked(rid):
                continue  # successor chain not yet linked to a root: not eligible
            if any(x == rid and b not in losers for x, b in self.edges):
                continue  # superseded (by a non-rejected branch)
            cands.append(rid)
        if len(cands) > 1:
            return CONFLICT
        return cands[0] if cands else None

    def freeze(self) -> tuple:
        """Exact, hashable internal state (order-sensitive parts kept in order),
        so a model checker can explore replica states without assuming the
        order-independence it is trying to establish."""
        if self.freeze_drops_invalidated and self.invalidated:
            self.hits += 1  # MUTANT: lossy serialization
        return (
            tuple(sorted(self.admitted.items())),
            frozenset(self.edges),
            frozenset() if self.freeze_drops_invalidated else frozenset(self.invalidated),
            tuple(sorted((k, frozenset(v)) for k, v in self.resolutions.items())),
            tuple(self.rejected),
            tuple(self.pending),
            frozenset(self.seen),
        )

    @classmethod
    def thaw(cls, policy: ResolutionPolicy, frozen: tuple, validate: bool = True, **flags) -> "Replica":
        """Restore a frozen replica. With ``validate`` (default) a structurally
        inconsistent snapshot is refused (``CorruptSnapshot``) rather than
        restored into a replica that misbehaves or crashes later."""
        adm, edges, inv, res, rej, pend, seen = frozen
        rep = cls(policy, admitted=dict(adm), edges=set(edges), invalidated=set(inv),
                  resolutions={k: set(v) for k, v in res}, rejected=list(rej),
                  pending=list(pend), seen=set(seen), **flags)
        if validate:
            problems = rep.integrity_problems()
            if problems:
                raise CorruptSnapshot("; ".join(problems[:3]))
        return rep

    def integrity_problems(self) -> list[str]:
        """Structural integrity of a replica state, independent of history.

        Every effect must be explained by an event in ``seen``, and every
        event in ``seen`` must be accounted for exactly: applied (its effect is
        present), still pending, or recorded as rejected.
        """
        out = []
        rejected = {e for e, _why in self.rejected}
        for rid, ev in self.admitted.items():
            if not isinstance(ev, Admit) or ev.rid != rid:
                out.append(f"admitted[{rid!r}] is not its own admission")
            elif ev not in self.seen:
                out.append(f"admission of {rid} was never seen")
        for a, b in self.edges:
            if a not in self.admitted or b not in self.admitted:
                out.append(f"edge {a}->{b} references a record that is not admitted")
            elif Edge(a, b) not in self.seen:
                out.append(f"edge {a}->{b} was never seen")
        for rid in self.invalidated:
            if rid not in self.admitted:
                out.append(f"invalidation of unadmitted {rid}")
            elif Invalidate(rid) not in self.seen:
                out.append(f"invalidation of {rid} was never seen")
        for src, rs in self.resolutions.items():
            for r in rs:
                if r not in self.seen:
                    out.append(f"resolution of {src} was never seen")
                if not all((r.source, c) in self.edges for c in r.candidates):
                    out.append(f"resolution of {src} without both candidate edges")
        pending = set(self.pending)
        for e in self.seen:
            if e in pending or e in rejected:
                continue
            applied = (
                (isinstance(e, Admit) and self.admitted.get(e.rid) == e)
                or (isinstance(e, Edge) and (e.old, e.new) in self.edges)
                or (isinstance(e, Invalidate) and e.rid in self.invalidated)
                or (isinstance(e, Resolve) and e in self.resolutions.get(e.source, ()))
            )
            if not applied:
                out.append(f"seen event neither applied, pending nor rejected: {type(e).__name__}")
        if not pending <= self.seen:
            out.append("pending event that was never seen")
        return out

    def canonical(self) -> tuple:
        return (
            tuple(sorted(self.admitted)),
            tuple(sorted(self.edges)),
            tuple(sorted(self.invalidated)),
            tuple(sorted(
                (k, tuple(sorted((r.epoch, r.winner, r.loser, tuple(sorted(r.approvals))) for r in v)))
                for k, v in self.resolutions.items())),
        )


def make_resolution(rep: Replica, source: str, winner: str, loser: str, approvals, *,
                    quorum: int = 2, epoch: int = 1, rule: str = "policy:independent-quorum",
                    evidence: str = "evidence-bundle") -> Resolve:
    """Build a well-formed resolution against a replica that sees the conflict."""
    cands = tuple(sorted((winner, loser)))
    pre = rep.conflict_root(source, cands)
    return Resolve(
        conflict_id=_h("cid", source, *cands), source=source, candidates=cands,
        pre_state_root=pre, rule=rule, winner=winner, loser=loser,
        approvals=frozenset(approvals), quorum=quorum, evidence_basis=_h(evidence),
        epoch=epoch, post_state_root=_h("post", pre, winner, loser, epoch),
    )
