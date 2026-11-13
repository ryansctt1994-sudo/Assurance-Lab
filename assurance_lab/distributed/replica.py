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
#: head() derived no authority because the replica state fails its integrity
#: check. Only reachable when a snapshot was restored without validation (or
#: a field was damaged in memory). Never a record id and never CONFLICT: a
#: damaged state must not look like a legitimate authority dispute.
UNKNOWN = "UNKNOWN"
#: Outcome of a refused restore (``CorruptSnapshot.status``).
INVALID_SNAPSHOT = "INVALID_SNAPSHOT"


class CorruptSnapshot(ValueError):
    """Restore refused at the thaw boundary: the INVALID_SNAPSHOT outcome.

    ``problems`` is sorted and de-duplicated, so a given snapshot always
    yields the same reasons, in any process and under any hash seed."""

    status = INVALID_SNAPSHOT

    def __init__(self, problems):
        self.problems = tuple(sorted(set(problems)))
        more = len(self.problems) - 3
        super().__init__("; ".join(self.problems[:3]) + (f"; and {more} more" if more > 0 else ""))


@dataclass(frozen=True)
class HeadResult:
    """``head()`` with its reason.

    ``status`` is ``HEAD`` (``value`` is a record id), ``NONE`` (X_⊥: nothing
    eligible), ``CONFLICT`` (a state that passes the integrity check and has
    an unresolved fork, disputed resolutions or several eligible records), or
    ``UNKNOWN`` (the state fails the integrity check, so no authority is
    derived from it; ``reason`` starts with ``integrity:`` and lists why)."""

    status: str
    value: Optional[str]
    reason: str


_OPT_STR = (str, type(None))
_EVENT_FIELDS = {
    Admit: (("rid", str), ("lineage", str), ("seq", int), ("owner", str), ("pred", _OPT_STR)),
    Edge: (("old", str), ("new", str)),
    Invalidate: (("rid", str),),
    Resolve: (("conflict_id", str), ("source", str), ("candidates", tuple), ("pre_state_root", str),
              ("rule", str), ("winner", str), ("loser", str), ("approvals", frozenset),
              ("quorum", int), ("evidence_basis", str), ("epoch", int), ("post_state_root", str)),
}


def event_problems(e) -> list[str]:
    """Type-level well-formedness of one event: the types ``deliver`` relies
    on. Never raises."""
    spec = _EVENT_FIELDS.get(type(e))
    if spec is None:
        return [f"{type(e).__name__} is not an event type"]
    out = []
    for name, ty in spec:
        v = getattr(e, name, None)
        if not isinstance(v, ty) or (ty is int and isinstance(v, bool)):
            out.append(f"{type(e).__name__}.{name} is {type(v).__name__}")
    if isinstance(e, Resolve) and not out:
        if len(e.candidates) != 2 or not all(isinstance(c, str) for c in e.candidates):
            out.append("Resolve.candidates is not a pair of record ids")
        if not all(isinstance(q, str) for q in e.approvals):
            out.append("Resolve.approvals holds a non-name")
    return out


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
            why = self._edge_rule(a, b)
            if why:
                return self._reject(ev, why)
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

    @staticmethod
    def _edge_rule(a: Admit, b: Admit) -> Optional[str]:
        """The correct rule for applying Edge(a, b); None if it may apply."""
        if a.lineage != b.lineage:
            return "cross-lineage edge"
        if not a.seq < b.seq:
            return "non-forward edge"
        if b.pred != a.rid:
            return "edge not declared by successor admission"
        return None

    # ------------------------------------------------------------- resolution
    def conflict_root(self, source: str, candidates) -> str:
        a, b = sorted(candidates)
        return _h("pre", source, a, self.admitted[a].owner, b, self.admitted[b].owner)

    def _resolution_rule(self, r: Resolve, independence=None) -> Optional[str]:
        """The correct rule for a resolution (pure); None if valid. The caller
        guarantees that the source and both candidates are admitted.
        ``independence`` lets a mutant replace the independence filter."""
        if sorted(r.candidates) != sorted({r.winner, r.loser}) or r.winner == r.loser:
            return "winner/loser must be the two conflicting candidates"
        if r.pre_state_root != self.conflict_root(r.source, r.candidates):
            return "pre-state root mismatch"
        if r.post_state_root != _h("post", r.pre_state_root, r.winner, r.loser, r.epoch):
            return "post-state root mismatch"
        beneficiary = self.admitted[r.winner].owner
        independent = {p for p in r.approvals if p in self.policy.authorities and p != beneficiary}
        if independence is not None:
            independent = independence(r, beneficiary, independent)
        if not independent:
            return "self-authorized: no independent approver"
        need = max(r.quorum, self.policy.min_quorum)
        if len(independent) < need:
            return f"quorum not met ({len(independent)}/{need} independent approvals)"
        return None

    def validate_resolution(self, r: Resolve) -> Optional[str]:
        """Return None if valid, else the reason. Structural checks only;
        causal readiness is handled by the caller (defer)."""
        def by_name_order(r, beneficiary, independent):  # MUTANT: identity compared by name order
            ordered = {p for p in r.approvals if p in self.policy.authorities and p > beneficiary}
            self.hits += ordered != independent
            return ordered
        why = self._resolution_rule(r, by_name_order if self.principal_order_quorum else None)
        if why:
            return why
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
            # Safe: only called after the integrity check, which guarantees
            # that both ends of every applied edge are admitted.
            cur = self.admitted[cur.pred]
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

    def explain_head(self, lineage: str) -> HeadResult:
        """Derived current authority for a lineage, with its reason.

        Read-only: it never repairs, discards or normalizes the state (only
        the mutants' instrumentation counter changes). A state that fails
        the integrity check yields UNKNOWN, not CONFLICT and not a record:
        damaged evidence must stay distinguishable from a genuine dispute,
        and must never become authority. Delivery order is never authority
        order: on a valid state the answer is a function of the delivered
        *set*.
        """
        problems = self.integrity_problems()
        if problems:
            return HeadResult("UNKNOWN", UNKNOWN, "integrity: " + "; ".join(problems))
        try:
            return self._derive_head(lineage)
        except Exception as e:  # would mean the integrity check is incomplete: fail closed, say so
            return HeadResult("UNKNOWN", UNKNOWN, "derivation failed on a state that passed the "
                              f"integrity check: {type(e).__name__}: {e}")

    def head(self, lineage: str) -> Optional[str]:
        """A record id, None (X_⊥), CONFLICT or UNKNOWN; see ``explain_head``."""
        return self.explain_head(lineage).value

    def _derive_head(self, lineage: str) -> HeadResult:
        members = [a for a in self.admitted.values() if a.lineage == lineage]
        disputed = [src for src in self.resolutions if self.decision(src)[0] == "disputed"]
        for src in disputed:
            if self.admitted[src].lineage == lineage:
                return HeadResult("CONFLICT", CONFLICT, f"authorities disagree on the fork at {src}")
        if self.global_dispute and disputed:  # MUTANT: couples unrelated lineages
            # counted only when the correct rule would not also answer CONFLICT
            self.global_dispute = False
            try:
                self.hits += self.head(lineage) != CONFLICT
            finally:
                self.global_dispute = True
            return HeadResult("CONFLICT", CONFLICT, "mutant global_dispute: a dispute in some lineage")
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
                return HeadResult("CONFLICT", CONFLICT, f"unresolved fork at {a.rid}")
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
            return HeadResult("CONFLICT", CONFLICT, f"{len(cands)} eligible records: {sorted(cands)}")
        if cands:
            return HeadResult("HEAD", cands[0], "the only eligible record")
        return HeadResult("NONE", None, "no eligible record (X_⊥)")

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
        """Restore a frozen replica: the restore boundary.

        A value that is not a snapshot of the shape ``freeze`` emits is always
        refused. With ``validate`` (the default, and the only production use)
        a snapshot that fails ``integrity_problems`` is refused too. Refusal
        is ``CorruptSnapshot`` (status INVALID_SNAPSHOT) with sorted reasons,
        never a replica that misbehaves or crashes later. ``validate=False``
        exists only so tests can measure what later layers would do without
        this boundary; such measurements are diagnostics, not evidence that
        the boundary works."""
        shape = cls.snapshot_problems(frozen)
        if shape:
            raise CorruptSnapshot(shape)
        adm, edges, inv, res, rej, pend, seen = frozen
        rep = cls(policy, admitted=dict(adm), edges=set(edges), invalidated=set(inv),
                  resolutions={k: set(v) for k, v in res}, rejected=list(rej),
                  pending=list(pend), seen=set(seen), **flags)
        if validate:
            problems = rep.integrity_problems()
            if problems:
                raise CorruptSnapshot(problems)
        return rep

    @staticmethod
    def snapshot_problems(frozen) -> list[str]:
        """Can this value be rebuilt into a replica without losing or
        inventing anything? A 7-tuple of the containers ``freeze`` emits, with
        unique string ids. Entry types are checked afterwards, by
        ``integrity_problems``. Never raises."""
        if not isinstance(frozen, tuple) or len(frozen) != 7:
            return ["not a 7-field replica snapshot"]
        adm, edges, inv, res, rej, pend, seen = frozen
        out = []

        def keyed(name, v, what):
            if not (isinstance(v, tuple) and all(isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str)
                                                 for x in v)):
                out.append(f"{name} is not a tuple of (id, {what}) pairs")
            elif len({k for k, _ in v}) != len(v):
                out.append(f"{name} lists an id twice")

        keyed("admitted", adm, "Admit")
        keyed("resolutions", res, "set of Resolve")
        if not out and not all(isinstance(v, (set, frozenset)) for _k, v in res):
            out.append("resolutions is not a tuple of (id, set of Resolve) pairs")
        for name, v in (("edges", edges), ("invalidated", inv), ("seen", seen)):
            if not isinstance(v, (set, frozenset)):
                out.append(f"{name} is not a set")
        if not (isinstance(rej, tuple) and all(isinstance(x, tuple) and len(x) == 2 for x in rej)):
            out.append("rejected is not a tuple of (event, reason) pairs")
        if not isinstance(pend, tuple):
            out.append("pending is not a tuple")
        return out

    def integrity_problems(self) -> list[str]:
        """Could the replica's own rules have produced this state from the
        events it has seen? Returns the problems, sorted (deterministic);
        never raises.

        1. Types: every container and entry has the type ``deliver`` builds.
        2. Accounting: every effect is explained by a seen event, and every
           seen event is exactly one of applied, still pending, or rejected.
        3. Rules: every applied edge and resolution satisfies the correct
           rule for applying it.

        Not checkable from a snapshot alone: completeness. A snapshot that
        consistently omits events is exactly the state of a replica that has
        not received them yet (integrity is not authenticity; a rolled-back
        snapshot can only be caught by authenticating snapshots).
        """
        out = self._type_problems()
        if out:
            return sorted(set(out))
        rejected = {e for e, _why in self.rejected}
        pending = set(self.pending)

        def applied(e) -> bool:
            return ((isinstance(e, Admit) and self.admitted.get(e.rid) == e)
                    or (isinstance(e, Edge) and (e.old, e.new) in self.edges)
                    or (isinstance(e, Invalidate) and e.rid in self.invalidated)
                    or (isinstance(e, Resolve) and e in self.resolutions.get(e.source, ())))

        for rid, ev in self.admitted.items():
            if ev.rid != rid:
                out.append(f"admitted[{rid}] is the admission of {ev.rid}")
            elif ev not in self.seen:
                out.append(f"admission of {rid} was never seen")
        for a, b in self.edges:
            if a not in self.admitted or b not in self.admitted:
                out.append(f"edge {a}->{b} references a record that is not admitted")
                continue
            if Edge(a, b) not in self.seen:
                out.append(f"edge {a}->{b} was never seen")
            why = self._edge_rule(self.admitted[a], self.admitted[b])
            if why:
                out.append(f"applied edge {a}->{b} breaks the edge rule: {why}")
        for rid in self.invalidated:
            if rid not in self.admitted:
                out.append(f"invalidation of unadmitted {rid}")
            elif Invalidate(rid) not in self.seen:
                out.append(f"invalidation of {rid} was never seen")
        for src, rs in self.resolutions.items():
            for r in rs:
                if r.source != src:
                    out.append(f"resolutions[{src}] holds a resolution of {r.source}")
                    continue
                if r not in self.seen:
                    out.append(f"resolution of {src} was never seen")
                if not all((r.source, c) in self.edges for c in r.candidates):
                    out.append(f"resolution of {src} without both candidate edges")
                    continue
                why = self._resolution_rule(r)
                if why:
                    out.append(f"applied resolution of {src} breaks the resolution rule: {why}")
        if len(pending) != len(self.pending):
            out.append("an event is pending twice")
        if not pending <= self.seen:
            out.append("pending event that was never seen")
        if not rejected <= self.seen:
            out.append("rejected event that was never seen")
        for e in pending:
            if applied(e):
                out.append(f"pending {type(e).__name__} is already applied")
        for e in rejected:
            if applied(e):
                out.append(f"rejected {type(e).__name__} is applied")
        for e in self.seen:
            if e not in pending and e not in rejected and not applied(e):
                out.append(f"seen {type(e).__name__} neither applied, pending nor rejected")
        return sorted(set(out))

    def _type_problems(self) -> list[str]:
        out = []
        for name, kind in (("admitted", dict), ("edges", set), ("invalidated", set), ("resolutions", dict),
                           ("rejected", list), ("pending", list), ("seen", set)):
            if not isinstance(getattr(self, name), kind):
                out.append(f"{name} is {type(getattr(self, name)).__name__}, not {kind.__name__}")
        if out:
            return out
        for rid, ev in self.admitted.items():
            if not isinstance(rid, str) or type(ev) is not Admit:
                out.append(f"admitted entry {rid!r} is not (id, Admit)")
            else:
                out += [f"admitted[{rid}]: {p}" for p in event_problems(ev)]
        for x in self.edges:
            if not (isinstance(x, tuple) and len(x) == 2 and all(isinstance(y, str) for y in x)):
                out.append(f"edge {x!r} is not a pair of record ids")
        out += [f"invalidated entry {x!r} is not a record id" for x in self.invalidated if not isinstance(x, str)]
        for src, rs in self.resolutions.items():
            if not isinstance(src, str) or not isinstance(rs, (set, frozenset)):
                out.append(f"resolutions entry {src!r} is not (id, set of Resolve)")
                continue
            for r in rs:
                out += ([f"resolutions[{src}]: {p}" for p in event_problems(r)] if type(r) is Resolve
                        else [f"resolutions[{src}] holds a {type(r).__name__}"])
        for x in self.rejected:
            if not (isinstance(x, tuple) and len(x) == 2 and isinstance(x[1], str)):
                out.append("rejected entry is not (event, reason)")
            else:
                out += [f"rejected: {p}" for p in event_problems(x[0])]
        for name in ("pending", "seen"):
            for e in getattr(self, name):
                out += [f"{name}: {p}" for p in event_problems(e)]
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
