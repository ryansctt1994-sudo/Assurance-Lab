"""Crash recovery (§26): graph = source of truth, head = derived cache.

Two layers of protection:

* ``recover`` recomputes the head from the persisted graph, repairing any
  torn edge/cache combination.
* ``verify_checkpoint`` binds the graph to a monotone epoch,
  C_t = H(t, canon(Graph_t)), against an external anchor. Internal consistency
  alone cannot detect rollback to an older but self-consistent generation.

ASSUMPTION: the anchor (``MonotoneAnchor``) is itself rollback-resistant
(e.g. a hardware monotonic counter or an external transparency log). That
assumption is not verified here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional


def canon_graph(edges: set[tuple[str, str]], nodes: set[str]) -> str:
    return json.dumps({"nodes": sorted(nodes), "edges": sorted(map(list, edges))}, separators=(",", ":"))


def checkpoint(epoch: int, edges, nodes) -> str:
    return hashlib.sha256(f"{epoch}|{canon_graph(set(edges), set(nodes))}".encode()).hexdigest()


def derive_heads(nodes: set[str], edges: set[tuple[str, str]], lineage: dict[str, str]) -> dict[str, Optional[str]]:
    """Per-lineage head: unique terminal node, None if none, raises on fork."""
    heads: dict[str, Optional[str]] = {lineage[n]: None for n in nodes}
    for n in sorted(nodes):
        if not any(a == n for a, _ in edges):
            lin = lineage[n]
            if heads[lin] is not None:
                raise ValueError(f"multiple terminals in lineage {lin}: {heads[lin]}, {n}")
            heads[lin] = n
    return heads


@dataclass
class Disk:
    nodes: set[str] = field(default_factory=set)
    edges: set[tuple[str, str]] = field(default_factory=set)
    lineage: dict[str, str] = field(default_factory=dict)
    head_cache: dict[str, Optional[str]] = field(default_factory=dict)
    epoch: int = 0

    def snapshot(self) -> "Disk":
        return Disk(set(self.nodes), set(self.edges), dict(self.lineage), dict(self.head_cache), self.epoch)


@dataclass
class MonotoneAnchor:
    """External, append-only record of the latest committed checkpoint."""

    epoch: int = 0
    digest: Optional[str] = None

    def commit(self, epoch: int, digest: str) -> None:
        if epoch <= self.epoch and self.digest is not None:
            raise ValueError("anchor is monotone")
        self.epoch, self.digest = epoch, digest


def supersede_with_crash(disk: Disk, old: str, new: str, persist_edge: bool, persist_head: bool) -> Disk:
    """Simulate a crash in the middle of a two-write supersession.

    DESIGN CHOICE (§8, atomic at the invariant's abstraction level): the
    successor's admission and its edge are a single log entry, so a durable
    successor never exists without its edge. The crash leaves an arbitrary
    subset of {successor+edge entry, head-cache write} on disk.
    """
    d = disk.snapshot()
    if persist_edge:
        d.nodes.add(new)
        d.edges.add((old, new))
    if persist_head:
        d.head_cache[d.lineage[new]] = new
    return d


def is_torn(d: Disk) -> bool:
    if any(h is not None and h not in d.nodes for h in d.head_cache.values()):
        return True  # cache points at a node whose admission never became durable
    return derive_heads(d.nodes, d.edges, d.lineage) != d.head_cache


def recover(d: Disk) -> Disk:
    r = d.snapshot()
    r.head_cache = derive_heads(r.nodes, r.edges, r.lineage)
    return r


def verify_checkpoint(d: Disk, anchor: MonotoneAnchor) -> bool:
    return d.epoch == anchor.epoch and checkpoint(d.epoch, d.edges, d.nodes) == anchor.digest


def commit(d: Disk, anchor: MonotoneAnchor) -> Disk:
    r = recover(d)
    r.epoch = anchor.epoch + 1
    anchor.commit(r.epoch, checkpoint(r.epoch, r.edges, r.nodes))
    return r
