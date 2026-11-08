"""Property coverage: which property detects which mutant.

Method (one exploration per mutant, so every property sees the same graph):

1. Explore the **mutant's own** reachable graph completely
   (``stop_on_violation=False``), evaluating every property at every state
   and step and recording each property's first violation. Never reuse the
   clean model's graph: a mutation can change what is reachable.
2. Separately, compare the mutant's successor relation with the clean
   model's on every state the mutant reaches. If it never differs, the
   mutation does nothing observable in this domain: INVALID (an equivalent
   mutant), whatever the properties say.

Outcomes per (property, mutant):

* KILLED: the property is violated somewhere in the mutant's graph.
* SURVIVED: the graph was fully explored and the property never failed.
* INVALID: the mutant is equivalent to the clean model in this domain.
* ERROR: the exploration raised or exhausted its budget. Never a kill.

Reading the matrix: a property with no KILLED entry has *no demonstrated
sensitivity*, not proof of uselessness (it may need a better negative
control); a property that kills a mutant is not thereby complete.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from enum import Enum

from ..core.model import Config, CoreModel
from ..core.mutants import ALL_MUTANTS
from .explore import PROPERTIES, explore


class Outcome(Enum):
    KILLED = "KILLED"
    SURVIVED = "SURVIVED"
    INVALID = "INVALID"
    ERROR = "ERROR"


# Smallest domain in which each mutant is non-equivalent (INVALID otherwise).
_DEFAULT = Config(n_records=2, n_lineages=1)
_DOMAIN = {
    "stale_successor": Config(n_records=3, n_lineages=1),
    "lineage_rewrite": Config(n_records=2, n_lineages=2),
    "cross_lineage_supersede": Config(n_records=2, n_lineages=2),
}


def mutant_domain(name: str) -> Config:
    return _DOMAIN.get(name, _DEFAULT)


def is_equivalent(mutant: CoreModel, states) -> bool:
    clean = CoreModel(mutant.cfg)
    return all(set(mutant.next(s)) == set(clean.next(s)) for s in states)


@dataclass
class Row:
    mutant: str
    domain: str
    states: int = 0
    outcomes: dict = field(default_factory=dict)  # property -> Outcome
    witness: dict = field(default_factory=dict)  # property -> shortest action sequence
    error: str = ""

    def killers(self) -> list[str]:
        return [p for p, o in self.outcomes.items() if o is Outcome.KILLED]


def coverage_row(M, max_states: int = 2_000_000, cfg: Config | None = None) -> Row:
    cfg = cfg or mutant_domain(M.name)
    row = Row(M.name, cfg.describe())
    try:
        r = explore(M(cfg), max_states=max_states, stop_on_violation=False, keep_states=True)
        row.states = r.states
        if not r.complete:
            row.error = f"budget exhausted at {r.states:,} states"
            row.outcomes = {p: Outcome.ERROR for p in PROPERTIES}
            return row
        if is_equivalent(M(cfg), r.state_set):
            row.outcomes = {p: Outcome.INVALID for p in PROPERTIES}
            return row
        for p in PROPERTIES:
            v = r.violations.get(p)
            row.outcomes[p] = Outcome.KILLED if v else Outcome.SURVIVED
            if v:
                row.witness[p] = [lbl for lbl, _st in v.trace if lbl]
    except Exception:  # infrastructure failure: recorded, never a kill
        row.error = traceback.format_exc(limit=3)
        row.outcomes = {p: Outcome.ERROR for p in PROPERTIES}
    return row


@dataclass
class Coverage:
    rows: list[Row]

    def killed_by(self, prop: str) -> list[str]:
        return [r.mutant for r in self.rows if r.outcomes.get(prop) is Outcome.KILLED]

    def sole_witnesses(self, prop: str) -> list[str]:
        return [r.mutant for r in self.rows if r.killers() == [prop]]

    def unkilled_properties(self) -> list[str]:
        return [p for p in PROPERTIES if not self.killed_by(p)]

    def surviving_mutants(self) -> list[str]:
        """Mutants no property kills (the strongest signal of a gap)."""
        return [r.mutant for r in self.rows
                if not r.killers() and Outcome.ERROR not in r.outcomes.values()
                and Outcome.INVALID not in r.outcomes.values()]

    def markdown(self) -> str:
        short = {p: p.replace("NoSilentAuthorityIncrease", "NSAI").replace("HistoryMonotone", "HistMono")
                 .replace("NoResurrection", "NoResurr").replace("DiagnosisInert", "DiagInert")
                 .replace("AdjudicationBound", "AdjBound").replace("IndependentApproval", "IndepAppr")
                 .replace("ApproverAuthorized", "ApprAuth").replace("CurrentCoherent", "CurCoh")
                 for p in PROPERTIES}
        mark = {Outcome.KILLED: "K", Outcome.SURVIVED: "·", Outcome.INVALID: "I", Outcome.ERROR: "E"}
        out = ["| mutant | states | " + " | ".join(short[p] for p in PROPERTIES) + " |",
               "|---|---|" + "---|" * len(PROPERTIES)]
        for r in self.rows:
            cells = []
            for p in PROPERTIES:
                o = r.outcomes[p]
                c = mark[o]
                if o is Outcome.KILLED and r.killers() == [p]:
                    c = "**K**"  # sole witness
                cells.append(c)
            out.append(f"| `{r.mutant}` | {r.states:,} | " + " | ".join(cells) + " |")
        return "\n".join(out)


def property_coverage(mutants=ALL_MUTANTS) -> Coverage:
    return Coverage([coverage_row(M) for M in mutants])
