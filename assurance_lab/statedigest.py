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


class MalformedEncoding(ValueError):
    """An ``ac-state-v1`` line that is not the canonical encoding of a state."""


_INT = __import__("re").compile(r"-?(0|[1-9][0-9]*)$")


def decanon(line: str, cfg=None) -> State:
    """Strict inverse of ``canon``.

    Accepts a line only if every field is a well-formed integer, booleans are
    0/1, absent values are -1, and re-encoding the result reproduces the line
    byte for byte (so unsorted sets, duplicates, leading zeros or extra
    whitespace are rejected). With ``cfg``, also checks record count,
    principal count and every value range. A lax decoder would let the
    round-trip relation miss an encoder emitting non-canonical text, which
    would still change the digest.
    """
    from .core.model import Rec

    def num(v: str) -> int:
        if not _INT.match(v):
            raise MalformedEncoding(f"not a canonical integer: {v!r}")
        return int(v)

    def flag(v: str) -> bool:
        if v not in ("0", "1"):
            raise MalformedEncoding(f"not a boolean: {v!r}")
        return v == "1"

    def opt(v: str, n):
        x = num(v)
        if x == -1:
            return None
        if x < 0 or (n is not None and x >= n):
            raise MalformedEncoding(f"value out of range: {v!r}")
        return x

    parts = line.split("|")
    if len(parts) != 5:
        raise MalformedEncoding(f"expected 5 sections, got {len(parts)}")
    recs_s, cur_s, edges_s, levels_s, sem_s = parts
    nl = cfg.n_lineages if cfg else None
    np_ = cfg.n_principals if cfg else None
    ns = cfg.n_sem if cfg else None
    recs = []
    for r in recs_s.split(";"):
        f = r.split(",")
        if len(f) != 8:
            raise MalformedEncoding(f"record needs 8 fields, got {len(f)}")
        recs.append(Rec(flag(f[0]), opt(f[1], nl), opt(f[2], np_), opt(f[3], np_), opt(f[4], ns),
                        flag(f[5]), flag(f[6]), opt(f[7], ns)))
    n = len(recs)
    if cfg and n != cfg.n_records:
        raise MalformedEncoding(f"expected {cfg.n_records} records, got {n}")

    def rid(v: str) -> int:
        x = num(v)
        if not 0 <= x < n:
            raise MalformedEncoding(f"record index out of range: {v!r}")
        return x

    current = frozenset(rid(x) for x in cur_s.split(",")) if cur_s else frozenset()
    edges = set()
    for e in edges_s.split(",") if edges_s else []:
        ab = e.split(">")
        if len(ab) != 2:
            raise MalformedEncoding(f"malformed edge: {e!r}")
        edges.add((rid(ab[0]), rid(ab[1])))
    levels = tuple(num(x) for x in levels_s.split(","))
    if cfg and (len(levels) != cfg.n_principals or any(not 0 <= v <= cfg.max_level for v in levels)):
        raise MalformedEncoding(f"levels malformed or out of range: {levels_s!r}")
    sem_now = num(sem_s)
    if cfg and not 0 <= sem_now < cfg.n_sem:
        raise MalformedEncoding(f"sem_now out of range: {sem_s!r}")
    st = State(tuple(recs), current, frozenset(edges), levels, sem_now)
    if canon(st) != line:
        raise MalformedEncoding("not canonical: re-encoding differs from the input")
    return st


def round_trip_failures(states, encode=canon, decode=decanon, model=None) -> list:
    """Serialization round-trip for the versioned encoding: decode(encode(s))
    must equal s and, if ``model`` is given, enable exactly the same steps.
    A decode that raises counts as a failure."""
    bad = []
    for s in states:
        try:
            t = decode(encode(s))
        except MalformedEncoding:
            bad.append(s)
            continue
        if t != s or (model is not None and set(model.next(t)) != set(model.next(s))):
            bad.append(s)
    return bad


#: One malformed line per defect class; a strict decoder must reject all.
MALFORMED_CORPUS = (
    ("wrong record count", "0,-1,-1,-1,-1,0,0,-1|||2,0|0"),
    ("edge to a nonexistent record", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0|0>5|2,0|0"),
    ("lineage out of range", "1,7,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0||2,0|0"),
    ("non-boolean flag", "1,0,1,0,0,2,0,-1;0,-1,-1,-1,-1,0,0,-1|0||2,0|0"),
    ("unsorted set", "1,0,1,0,0,0,0,-1;1,0,0,1,0,0,0,-1|1,0||2,0|0"),
    ("duplicate set element", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0,0||2,0|0"),
    ("leading zero", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|00||2,0|0"),
    ("whitespace", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0|| 2,0|0"),
    ("missing section", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0||2,0"),
    ("short record", "1,0,1,0,0,0,0;0,-1,-1,-1,-1,0,0,-1|0||2,0|0"),
    ("level out of range", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0||3,0|0"),
    ("sem_now out of range", "1,0,1,0,0,0,0,-1;0,-1,-1,-1,-1,0,0,-1|0||2,0|2"),
    ("unsorted edges", "1,0,1,0,0,0,0,-1;1,0,0,1,0,0,0,-1|1|1>0,0>1|2,0|0"),
)


def malformed_accepted(cfg) -> list:
    """Corpus entries the strict decoder wrongly accepts (must be empty)."""
    out = []
    for name, line in MALFORMED_CORPUS:
        try:
            decanon(line, cfg)
            out.append(name)
        except MalformedEncoding:
            pass
    return out
