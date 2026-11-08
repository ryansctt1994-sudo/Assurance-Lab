"""Store seam: retrieval with supersession metadata (§23-25).

The graph of supersession edges is the source of truth. Records carry
lifecycle flags; payloads may be compacted away, metadata may not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StoredRecord:
    rid: str
    lineage: str
    score: float  # retrieval relevance (unrelated to authority!)
    payload: Optional[str] = "payload"
    admitted: bool = True
    invalidated: bool = False
    retracted: bool = False


class NoCurrent(Exception):
    """X_⊥: no current authoritative answer. Consumers must not fall back."""


class SupersessionConflict(Exception):
    """A record has >1 successor; authority is undetermined until resolved."""


@dataclass
class Store:
    records: dict[str, StoredRecord] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)  # (old, new)

    # -- writes ---------------------------------------------------------
    def put(self, rec: StoredRecord) -> None:
        self.records[rec.rid] = rec

    def supersede(self, old: str, new: str) -> None:
        a, b = self.records[old], self.records[new]
        if a.lineage != b.lineage:
            raise ValueError("cross-lineage supersession rejected")
        self.edges.add((old, new))

    # -- derived lifecycle -----------------------------------------------
    def successors(self, rid: str) -> list[str]:
        return sorted(b for (a, b) in self.edges if a == rid)

    def is_superseded(self, rid: str) -> bool:
        return any(a == rid for (a, _b) in self.edges)

    def is_current(self, rid: str) -> bool:
        r = self.records.get(rid)
        return bool(
            r and r.admitted and not r.invalidated and not r.retracted and not self.is_superseded(rid)
        )

    # -- retrieval -------------------------------------------------------
    def rank_then_filter(self, k: int) -> list[str]:
        """UNSAFE: take top-k by score, then drop stale ones."""
        ranked = sorted(self.records.values(), key=lambda r: -r.score)[:k]
        return [r.rid for r in ranked if self.is_current(r.rid)]

    def filter_then_rank(self, k: int) -> list[str]:
        """SAFE: currency/supersession filtering before ranking (§23)."""
        live = [r for r in self.records.values() if self.is_current(r.rid)]
        return [r.rid for r in sorted(live, key=lambda r: -r.score)[:k]]

    # -- effect-time read repair (§24) -------------------------------------
    def resolve_at_effect(self, rid: str, max_hops: int = 10_000) -> str:
        """Follow the supersession chain from a possibly stale handle.

        Returns the live terminal record, or raises NoCurrent if the chain
        ends in an ineligible record. Never falls back to a predecessor.
        """
        cur = rid
        for _ in range(max_hops):
            succ = self.successors(cur)
            if len(succ) > 1:
                raise SupersessionConflict(cur)
            if not succ:
                if self.is_current(cur):
                    return cur
                raise NoCurrent(cur)
            cur = succ[0]
        raise NoCurrent(f"chain too long from {rid}")

    # -- compaction (§25) ----------------------------------------------
    def compact_safe(self, keep_payload: set[str]) -> None:
        """Drop payloads of non-current records; keep all metadata + edges."""
        for r in self.records.values():
            if r.rid not in keep_payload and not self.is_current(r.rid):
                r.payload = None

    def compact_unsafe(self, drop: set[str]) -> None:
        """Mutant: treat compaction as storage-only and drop records + edges."""
        for rid in drop:
            self.records.pop(rid, None)
            # NB: also drops edges *into* dropped records, the dangerous part.
            self.edges = {(a, b) for (a, b) in self.edges if b not in drop and a not in drop}
