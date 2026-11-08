"""An independent transition relation, translated from ``tla/AuthorityCore.tla``.

Purpose: two implementations can reach exactly the same *states* (matching
digests) while disagreeing about which *actions* are legal. This module is
a second transition relation, written line by line from the TLA+ text, not
from ``core/model.py``:

* its own state representation (dicts keyed by TLA+ field names,
  ``NULL = -1``, sets of tuples), with its own reader and writer for the
  ``ac-state-v1`` text, so the encoders are cross-checked too;
* each TLA+ action is a function returning the primed state, or ``None``
  when a guard conjunct is false; ``Next`` enumerates the ``\\E``
  quantifiers in the order the spec writes them;
* the TLA+ ``Mutant`` constant is supported, so the four mutant cfgs get an
  executable reading too.

Labels are TLA+ action instances: ``Admit(x,l,o,a)``, ``Supersede(x,y,o,a)``,
``Adjudicate(x)``, ``Diagnose(x)``, ``Invalidate(x)``, ``Retract(x)``,
``RoleRevise`` (no parameter in the spec), ``Grant(p,q)``.

Independence is of code and representation, not of authorship: the same
author wrote both, so a shared misreading of the handoff would agree. Until
TLC runs, this is also the only executable reading of the TLA+ spec.
"""

from __future__ import annotations

from typing import Iterator, Optional

NULL = -1
FIELDS = ("admitted", "lineage", "owner", "approver", "sem", "invalidated", "retracted", "adj")


# ------------------------------------------------------------ own codec


def read(line: str) -> dict:
    recs_s, cur_s, edges_s, levels_s, sem_s = line.split("|")
    recs = {}
    for i, r in enumerate(recs_s.split(";")):
        v = [int(x) for x in r.split(",")]
        recs[i] = dict(zip(FIELDS, v))
        for b in ("admitted", "invalidated", "retracted"):
            recs[i][b] = bool(recs[i][b])
    return {
        "recs": recs,
        "current": frozenset(int(x) for x in cur_s.split(",") if x),
        "edges": frozenset(tuple(map(int, e.split(">"))) for e in edges_s.split(",") if e),
        "levels": {i: int(v) for i, v in enumerate(levels_s.split(","))},
        "semNow": int(sem_s),
    }


def write(st: dict) -> str:
    def f(v):
        return str(int(v)) if isinstance(v, bool) else str(v)

    recs = ";".join(",".join(f(st["recs"][i][k]) for k in FIELDS) for i in sorted(st["recs"]))
    cur = ",".join(str(x) for x in sorted(st["current"]))
    edges = ",".join(f"{a}>{b}" for a, b in sorted(st["edges"]))
    levels = ",".join(str(st["levels"][p]) for p in sorted(st["levels"]))
    return f"{recs}|{cur}|{edges}|{levels}|{st['semNow']}"


# ------------------------------------------------------------ the spec


class Spec:
    def __init__(self, n_rec: int, n_prin: int, n_lin: int, max_level: int, n_sem: int,
                 mutant: str = "none"):
        self.R, self.P, self.L = range(n_rec), range(n_prin), range(n_lin)
        self.MaxLevel, self.NSem, self.Mutant = max_level, n_sem, mutant

    @classmethod
    def for_config(cls, cfg, mutant: str = "none") -> "Spec":
        return cls(cfg.n_records, cfg.n_principals, cfg.n_lineages, cfg.max_level, cfg.n_sem, mutant)

    # Unadmitted / Fresh(l, o, a)
    @staticmethod
    def unadmitted() -> dict:
        return dict(admitted=False, lineage=NULL, owner=NULL, approver=NULL, sem=NULL,
                    invalidated=False, retracted=False, adj=NULL)

    def fresh(self, s, l, o, a) -> dict:
        return dict(admitted=True, lineage=l, owner=o, approver=a, sem=s["semNow"],
                    invalidated=False, retracted=False, adj=NULL)

    def init(self) -> dict:
        return {"recs": {x: self.unadmitted() for x in self.R}, "current": frozenset(),
                "edges": frozenset(), "levels": {p: self.MaxLevel if p == 0 else 0 for p in self.P},
                "semNow": 0}

    # derived operators
    @staticmethod
    def superseded(s, x) -> bool:
        return any(e[0] == x for e in s["edges"])

    def no_current_in(self, s, l) -> bool:
        return all(s["recs"][x]["lineage"] != l for x in s["current"])

    def approval(self, s, o, a) -> bool:
        return (self.Mutant == "beneficiary_self_approval" or a != o) and s["levels"][a] >= 1

    @staticmethod
    def except_rec(s, x, rec) -> dict:  # [recs EXCEPT ![x] = rec]
        recs = dict(s["recs"])
        recs[x] = rec
        return recs

    # actions: guards first, then primed assignments, then UNCHANGED
    def Admit(self, s, x, l, o, a) -> Optional[dict]:
        if s["recs"][x]["admitted"]:
            return None
        if not (self.Mutant == "admit_into_occupied_lineage" or self.no_current_in(s, l)):
            return None
        if not self.approval(s, o, a):
            return None
        return dict(s, recs=self.except_rec(s, x, self.fresh(s, l, o, a)), current=s["current"] | {x})

    def Supersede(self, s, x, y, o, a) -> Optional[dict]:
        if not (s["recs"][x]["admitted"] if self.Mutant == "stale_successor" else x in s["current"]):
            return None
        if not x < y or s["recs"][y]["admitted"] or not self.approval(s, o, a):
            return None
        return dict(s, recs=self.except_rec(s, y, self.fresh(s, s["recs"][x]["lineage"], o, a)),
                    current=(s["current"] - {x}) | {y}, edges=s["edges"] | {(x, y)})

    def Adjudicate(self, s, x) -> Optional[dict]:
        r = s["recs"][x]
        if not (r["admitted"] and r["adj"] == NULL):
            return None
        return dict(s, recs=self.except_rec(s, x, dict(r, adj=r["sem"])))

    def Diagnose(self, s, x) -> Optional[dict]:
        return dict(s) if s["recs"][x]["admitted"] else None

    def Invalidate(self, s, x) -> Optional[dict]:
        r = s["recs"][x]
        if not (r["admitted"] and not r["invalidated"]):
            return None
        return dict(s, recs=self.except_rec(s, x, dict(r, invalidated=True)), current=s["current"] - {x})

    def Retract(self, s, x) -> Optional[dict]:
        r = s["recs"][x]
        if not (r["admitted"] and not r["retracted"]):
            return None
        if self.Mutant == "resurrect_on_retract" and x in s["current"]:
            cur = (s["current"] - {x}) | {e[0] for e in s["edges"] if e[1] == x}
        else:
            cur = s["current"] - {x}
        return dict(s, recs=self.except_rec(s, x, dict(r, retracted=True)), current=cur)

    def RoleRevise(self, s) -> Optional[dict]:
        if not s["semNow"] + 1 < self.NSem:
            return None
        return dict(s, semNow=s["semNow"] + 1)

    def Grant(self, s, p, q) -> Optional[dict]:
        if not (s["levels"][p] < self.MaxLevel and q != p and s["levels"][q] > s["levels"][p]):
            return None
        levels = dict(s["levels"])
        levels[p] = s["levels"][p] + 1
        return dict(s, levels=levels)

    def next(self, s) -> Iterator[tuple[str, dict]]:
        for x in self.R:
            for l in self.L:
                for o in self.P:
                    for a in self.P:
                        t = self.Admit(s, x, l, o, a)
                        if t is not None:
                            yield f"Admit({x},{l},{o},{a})", t
        for x in self.R:
            for y in self.R:
                for o in self.P:
                    for a in self.P:
                        t = self.Supersede(s, x, y, o, a)
                        if t is not None:
                            yield f"Supersede({x},{y},{o},{a})", t
        for x in self.R:
            for name in ("Adjudicate", "Diagnose", "Invalidate", "Retract"):
                t = getattr(self, name)(s, x)
                if t is not None:
                    yield f"{name}({x})", t
        t = self.RoleRevise(s)
        if t is not None:
            yield "RoleRevise", t
        for p in self.P:
            for q in self.P:
                t = self.Grant(s, p, q)
                if t is not None:
                    yield f"Grant({p},{q})", t

    def successors(self, line: str) -> set[tuple[str, str]]:
        return {(lbl, write(t)) for lbl, t in self.next(read(line))}


# ------------------------------------------------------------ comparison


def python_label(lbl: tuple) -> str:
    """The TLA+ action instance a CoreModel label denotes."""
    from ..statedigest import action_label
    return action_label(lbl)


def compare(model, spec: Spec, states) -> list[dict]:
    """States where the two relations disagree on (action, successor) pairs."""
    from ..statedigest import canon

    out = []
    for s in states:
        line = canon(s)
        py = {(python_label(lbl), canon(t)) for lbl, t in model.next(s)}
        tla = spec.successors(line)
        if py != tla:
            out.append({"state": line, "only_python": sorted(py - tla), "only_tla": sorted(tla - py)})
    return out
