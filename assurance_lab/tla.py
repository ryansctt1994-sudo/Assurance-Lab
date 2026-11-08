"""Read TLC state dumps into the canonical state encoding.

``java tlc2.TLC -dump <file> ...`` writes every distinct state TLC found. This
module parses that file (TLA+ value syntax), maps each state of
``tla/AuthorityCore.tla`` onto the ``ac-state-v1`` encoding used by
``assurance_lab.statedigest``, and computes the same set digest. Equal
counts *and* equal digests mean the two checkers found the same states.

STATUS: tested against hand-written samples in TLC's value syntax and a
printer round-trip; **not yet validated against output from a real TLC run**
(running TLC is pending an approved execution path).

State correspondence (see docs/ORACLE.md):

    TLA+ recs[x].admitted .. .adj  <->  Rec fields (NULL = -1 <-> None)
    TLA+ current (set of R)        <->  State.current
    TLA+ edges (set of <<x, y>>)   <->  State.edges
    TLA+ levels (function P -> Lv) <->  State.levels
    TLA+ semNow                    <->  State.sem_now
"""

from __future__ import annotations

import re
from typing import Iterator

from .core.model import State
from .statedigest import SetDigest, canon_parts

FIELDS = ("admitted", "lineage", "owner", "approver", "sem", "invalidated", "retracted", "adj")

_TOKEN = re.compile(r"""\s*(?:
      (?P<sym><<|>>|\|->|:>|@@|/\\|[\[\]{}(),=])
    | (?P<int>-?\d+)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
    )""", re.VERBOSE)


class TLAParseError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise TLAParseError(f"unexpected input at {pos}: {text[pos:pos + 30]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.toks = _tokens(text)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if tok[0] is None or (val is not None and tok[1] != val):
            raise TLAParseError(f"expected {val!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def _seq(self, close):
        items = []
        if self.peek()[1] != close:
            items.append(self.value())
            while self.peek()[1] == ",":
                self.take(",")
                items.append(self.value())
        self.take(close)
        return items

    def value(self):
        kind, val = self.peek()
        if kind == "int":
            self.take()
            return int(val)
        if kind == "str":
            self.take()
            return val[1:-1]
        if kind == "id":
            self.take()
            return {"TRUE": True, "FALSE": False}.get(val, val)
        if val == "{":
            self.take("{")
            return frozenset(self._seq("}"))
        if val == "<<":
            self.take("<<")
            return tuple(self._seq(">>"))
        if val == "[":
            self.take("[")
            rec = {}
            while True:
                _k, name = self.take()
                self.take("|->")
                rec[name] = self.value()
                if self.peek()[1] == ",":
                    self.take(",")
                    continue
                self.take("]")
                return rec
        if val == "(":
            self.take("(")
            fn = {}
            while True:
                k = self.value()
                self.take(":>")
                fn[k] = self.value()
                if self.peek()[1] == "@@":
                    self.take("@@")
                    continue
                self.take(")")
                return fn
        raise TLAParseError(f"unexpected token {val!r}")

    def state(self) -> dict:
        """``/\\ v1 = e1 /\\ v2 = e2 ...`` (the leading ``/\\`` is optional)."""
        out = {}
        while self.peek()[0] is not None:
            if self.peek()[1] == "/\\":
                self.take("/\\")
            _k, name = self.take()
            self.take("=")
            out[name] = self.value()
        return out


def parse_value(text: str):
    p = _Parser(text)
    v = p.value()
    if p.peek()[0] is not None:
        raise TLAParseError(f"trailing input after value: {p.peek()[1]!r}")
    return v


def parse_state(text: str) -> dict:
    return _Parser(text).state()


_HEADER = re.compile(r"^State \d+:\s*$", re.MULTILINE)


def parse_dump(text: str) -> Iterator[dict]:
    """States of a TLC ``-dump`` file (anything before the first header is ignored)."""
    parts = _HEADER.split(text)
    for block in parts[1:]:
        if block.strip():
            yield parse_state(block)


def _fn_values(v, n: int, what: str) -> list:
    if isinstance(v, dict) and set(v) == set(range(n)):
        return [v[i] for i in range(n)]
    raise TLAParseError(f"{what}: expected a function with domain 0..{n - 1}, got {v!r}")


def state_line(d: dict, n_records: int, n_principals: int) -> str:
    recs = []
    for r in _fn_values(d["recs"], n_records, "recs"):
        if set(r) != set(FIELDS):
            raise TLAParseError(f"record fields {sorted(r)} != {sorted(FIELDS)}")
        recs.append(tuple(r[f] for f in FIELDS))
    edges = []
    for e in d["edges"]:
        if not (isinstance(e, tuple) and len(e) == 2):
            raise TLAParseError(f"edge {e!r} is not a pair")
        edges.append(e)
    return canon_parts(recs, d["current"], edges, _fn_values(d["levels"], n_principals, "levels"),
                       d["semNow"])


def dump_digest(text: str, n_records: int, n_principals: int) -> SetDigest:
    d = SetDigest()
    for st in parse_dump(text):
        d.add_line(state_line(st, n_records, n_principals))
    return d


# ------------------------------------------------------------ printer (tests)


def _tla(v) -> str:
    if v is None:
        return "-1"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    raise TypeError(v)


def to_tla(s: State) -> str:
    """Print a Python state the way TLC prints the corresponding TLA+ state."""
    recs = " @@ ".join(
        f"{i} :> [" + ", ".join(f"{f} |-> {_tla(x)}" for f, x in zip(FIELDS, r)) + "]"
        for i, r in enumerate(s.recs))
    cur = ", ".join(str(x) for x in sorted(s.current))
    edges = ", ".join(f"<<{a}, {b}>>" for a, b in sorted(s.edges))
    levels = " @@ ".join(f"{i} :> {v}" for i, v in enumerate(s.levels))
    return (f"/\\ recs = ({recs})\n/\\ current = {{{cur}}}\n/\\ edges = {{{edges}}}\n"
            f"/\\ levels = ({levels})\n/\\ semNow = {s.sem_now}\n")


# ------------------------------------------------------------ CLI


def main(argv=None) -> int:
    """``python -m assurance_lab.tla DUMP --records N --principals P [--expect LABEL]``"""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m assurance_lab.tla")
    ap.add_argument("dump", help="file written by TLC -dump")
    ap.add_argument("--records", type=int, required=True)
    ap.add_argument("--principals", type=int, required=True)
    ap.add_argument("--expect", help="expected digest label, e.g. ac-state-v1:670:90bd...")
    a = ap.parse_args(argv)
    with open(a.dump) as f:
        got = dump_digest(f.read(), a.records, a.principals).label()
    print(got)
    if a.expect is None:
        return 0
    if got == a.expect:
        print("MATCH")
        return 0
    print(f"MISMATCH: expected {a.expect}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
