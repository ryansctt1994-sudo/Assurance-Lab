"""Partition integrity for the parallel induction.

Self-consistency is not enough: an aggregator that drops a partition and
recomputes counts and digests from the rest produces a package whose numbers
all agree with each other, but is incomplete. So the expected partition set
is **frozen before execution** and every result is reconciled against it.

* **Manifest**: run id, domain, partition ids with their key predicates
  (``(levels, sem_now)``), and the coverage obligation that the keys are
  exactly the product ``Lv^P × S``, each once. Its digest is recorded in the
  start receipt.
* **Evidence**: one record per partition, produced by the worker that
  enumerated it: the keys of every state it generated, counts, verdict
  bits and digests, HMAC'd under a per-run key that never leaves the
  process tree or appears in a report.
* **Reconciliation** checks, in order: provenance (run id and manifest
  digest), integrity (MAC), unknown or duplicate partition identity,
  completeness (every expected id present), disjointness (each partition's
  observed keys are exactly its own key; keys are distinct, so states in
  different partitions are distinct), and arithmetic (a claimed aggregate
  must equal the one recomputed from the verified records). Order of
  records is irrelevant: aggregation is by partition id.

Threat model: this detects mis-aggregation, missing/repeated/overlapping
partitions, stale or substituted evidence and corruption after signing. It
does not defend against a malicious worker, which holds the key.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
from dataclasses import asdict, dataclass, replace
from typing import Optional

from ..statedigest import SetDigest


@dataclass(frozen=True)
class Manifest:
    run_id: str
    domain: str
    entries: tuple  # ((pid, levels, sem_now), ...)

    def digest(self) -> str:
        body = json.dumps({"run_id": self.run_id, "domain": self.domain,
                           "entries": [[p, list(lv), sn] for p, lv, sn in self.entries]},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def content_digest(self) -> str:
        """Digest of the partition definitions alone (no run id): identical for
        every run of the same domain, so it can sit in the stable receipt."""
        body = json.dumps({"domain": self.domain,
                           "entries": [[p, list(lv), sn] for p, lv, sn in self.entries]},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def key_of(self, pid: str):
        return next(((tuple(lv), sn) for p, lv, sn in self.entries if p == pid), None)


def build_manifest(cfg, run_id: str) -> Manifest:
    keys = [(lv, sn) for lv in itertools.product(range(cfg.max_level + 1), repeat=cfg.n_principals)
            for sn in range(cfg.n_sem)]
    return Manifest(run_id, cfg.describe(), tuple((f"p{i:03d}", lv, sn) for i, (lv, sn) in enumerate(keys)))


def manifest_problems(m: Manifest, cfg) -> list[str]:
    """Static coverage obligation: ids distinct; keys distinct and exactly the
    product of the globals' ranges."""
    out = []
    pids = [p for p, _lv, _sn in m.entries]
    keys = [(tuple(lv), sn) for _p, lv, sn in m.entries]
    if len(set(pids)) != len(pids):
        out.append("manifest invalid: duplicate partition id")
    if len(set(keys)) != len(keys):
        out.append("manifest invalid: overlapping partition predicates")
    want = {(lv, sn) for lv in itertools.product(range(cfg.max_level + 1), repeat=cfg.n_principals)
            for sn in range(cfg.n_sem)}
    if set(keys) != want:
        out.append("manifest invalid: keys do not cover the domain exactly")
    return out


@dataclass(frozen=True)
class Evidence:
    run_id: str
    manifest_digest: str
    pid: str
    keys_observed: tuple  # sorted ((levels, sem_now), ...) of every state generated
    count: int
    transitions: int
    step_ok: bool
    safety_ok: bool
    complete: bool
    digest: str  # labels of the three sum digests
    transition_digest: str
    edge_digest: str
    mac: str = ""

    def payload(self) -> bytes:
        d = asdict(self)
        d.pop("mac")
        d["keys_observed"] = [[list(lv), sn] for lv, sn in self.keys_observed]
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def sign(ev: Evidence, key: bytes) -> Evidence:
    return replace(ev, mac=hmac.new(key, ev.payload(), hashlib.sha256).hexdigest())


def verify(ev: Evidence, key: bytes) -> bool:
    return hmac.compare_digest(ev.mac, hmac.new(key, ev.payload(), hashlib.sha256).hexdigest())


def _sd(label: str) -> SetDigest:
    _enc, count, hexd = label.split(":")
    return SetDigest(int(count), int(hexd, 16))


@dataclass(frozen=True)
class Aggregate:
    count: int
    transitions: int
    step_ok: bool
    safety_ok: bool
    complete: bool
    digest: str
    transition_digest: str
    edge_digest: str


def aggregate(records) -> Aggregate:
    """Order-independent: records are combined in partition-id order."""
    recs = sorted(records, key=lambda e: e.pid)
    d, t, e = SetDigest(), SetDigest(), SetDigest()
    for r in recs:
        d, t, e = d.merge(_sd(r.digest)), t.merge(_sd(r.transition_digest)), e.merge(_sd(r.edge_digest))
    return Aggregate(sum(r.count for r in recs), sum(r.transitions for r in recs),
                     all(r.step_ok for r in recs), all(r.safety_ok for r in recs),
                     all(r.complete for r in recs), d.label(), t.label(), e.label())


def reconcile(manifest: Manifest, records, key: bytes,
              claimed: Optional[Aggregate] = None) -> tuple[Optional[Aggregate], list[str]]:
    """Reconcile observed evidence against the frozen manifest.

    Returns (aggregate of the accepted records, problems). Any problem means
    the evidence package must not be accepted.
    """
    problems: list[str] = []
    accepted: list[Evidence] = []
    md = manifest.digest()
    for r in records:
        if r.run_id != manifest.run_id or r.manifest_digest != md:
            problems.append(f"provenance mismatch: {r.pid} belongs to run {r.run_id}")
            continue
        if not verify(r, key):
            problems.append(f"integrity mismatch: {r.pid} fails MAC verification")
            continue
        accepted.append(r)
    expected = {p for p, _lv, _sn in manifest.entries}
    seen: dict[str, int] = {}
    for r in accepted:
        seen[r.pid] = seen.get(r.pid, 0) + 1
    problems += [f"duplicate partition identity: {p} reported {n} times" for p, n in sorted(seen.items()) if n > 1]
    problems += [f"unknown partition: {p}" for p in sorted(set(seen) - expected)]
    missing = sorted(expected - set(seen))
    if missing:
        problems.append(f"incomplete coverage: missing {missing}")
    for r in accepted:
        own = manifest.key_of(r.pid)
        observed = {(tuple(lv), sn) for lv, sn in r.keys_observed}
        if own is not None and r.count and observed != {own}:
            problems.append(f"non-disjoint coverage: {r.pid} contains states outside its key {own}")
    agg = aggregate(accepted)
    if claimed is not None and claimed != agg:
        problems.append("arithmetic inconsistency: claimed aggregate differs from the verified records")
    return agg, problems
