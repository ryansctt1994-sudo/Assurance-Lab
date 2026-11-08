"""Canonical state encoding and order-independent state-set digests.

Matching state *counts* between two checkers does not show they explored the
same states: two different sets can have the same size. A digest over a
canonical encoding of every state does, up to hash collisions, so the
campaign records one for the reachable set and one for the Inv-state set.
An independent oracle (e.g. TLC, via ``assurance_lab.tla``) must reproduce
both exactly.

Encoding ``ac-state-v1`` (one ASCII line per state, no whitespace):

    recs | current | edges | levels | sem_now

* ``recs``: per record in index order, 8 integers joined by ``,``:
  admitted, lineage, owner, approver, sem, invalidated, retracted, adj_sem,
  with booleans as 0/1 and "absent" (Python ``None``, TLA+ ``NULL``) as -1;
  records joined by ``;``.
* ``current``: ascending record indices joined by ``,``.
* ``edges``: ascending pairs ``a>b`` joined by ``,``.
* ``levels``: per principal in index order, joined by ``,``.
* ``sem_now``: integer.

Set digest: the count plus Σ SHA-256(line) mod 2^256. Order-independent, so
partitions computed by different workers combine by addition. It detects
any *accidental* difference in membership. It is not designed to resist
adversarially constructed collisions (sum-of-hashes is weaker than hashing a
sorted list), which is outside this repo's threat model.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from .core.model import State

ENCODING = "ac-state-v1"
_MOD = 1 << 256


def _i(v) -> str:
    if v is None:
        return "-1"
    if v is True:
        return "1"
    if v is False:
        return "0"
    return str(v)


def canon_parts(recs, current, edges, levels, sem_now) -> str:
    """Canonical line from plain components (shared by Python and TLA+ paths)."""
    r = ";".join(",".join(_i(f) for f in rec) for rec in recs)
    c = ",".join(str(x) for x in sorted(current))
    e = ",".join(f"{a}>{b}" for a, b in sorted(edges))
    lv = ",".join(str(x) for x in levels)
    return f"{r}|{c}|{e}|{lv}|{sem_now}"


def canon(s: State) -> str:
    return canon_parts(s.recs, s.current, s.edges, s.levels, s.sem_now)


@dataclass
class SetDigest:
    count: int = 0
    acc: int = 0

    def add_line(self, line: str) -> None:
        self.count += 1
        self.acc = (self.acc + int.from_bytes(sha256(line.encode()).digest(), "big")) % _MOD

    def add(self, s: State) -> None:
        self.add_line(canon(s))

    def merge(self, other: "SetDigest") -> "SetDigest":
        return SetDigest(self.count + other.count, (self.acc + other.acc) % _MOD)

    def hexdigest(self) -> str:
        return f"{self.acc:064x}"

    def label(self) -> str:
        return f"{ENCODING}:{self.count}:{self.hexdigest()}"


def set_digest(states: Iterable[State]) -> SetDigest:
    d = SetDigest()
    for s in states:
        d.add(s)
    return d


def decanon(line: str) -> State:
    """Inverse of ``canon``: parse an ``ac-state-v1`` line back into a State."""
    from .core.model import Rec

    def opt(v: str):
        return None if v == "-1" else int(v)

    recs_s, cur_s, edges_s, levels_s, sem_s = line.split("|")
    recs = []
    for r in recs_s.split(";"):
        a, lin, own, appr, sem, inv, ret, adj = r.split(",")
        recs.append(Rec(a == "1", opt(lin), opt(own), opt(appr), opt(sem), inv == "1", ret == "1", opt(adj)))
    current = frozenset(int(x) for x in cur_s.split(",") if x)
    edges = frozenset(tuple(int(v) for v in e.split(">")) for e in edges_s.split(",") if e)
    levels = tuple(int(x) for x in levels_s.split(","))
    return State(tuple(recs), current, edges, levels, int(sem_s))


def round_trip_failures(states, encode=canon, decode=decanon, model=None) -> list:
    """Serialization round-trip for the versioned encoding: decode(encode(s))
    must equal s and, if ``model`` is given, enable exactly the same steps."""
    bad = []
    for s in states:
        t = decode(encode(s))
        if t != s or (model is not None and set(model.next(t)) != set(model.next(s))):
            bad.append(s)
    return bad
