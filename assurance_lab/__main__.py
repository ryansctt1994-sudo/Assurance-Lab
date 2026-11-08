"""Run the verification campaign and emit labeled results.

    python -m assurance_lab            # quick profile (~1 min)
    python -m assurance_lab --full     # headline domain, parallel (~3 min on 4 cores)
    python -m assurance_lab --out docs/RESULTS.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import os
import platform
import sys

from .checker.explore import explore
from .checker.induction import check_induction
from .core.model import Config, CoreModel
from .core.mutants import ALL_MUTANTS
from .distributed.replica import Admit, Edge, Replica, ResolutionPolicy, make_resolution
from .verdict import Result, Verdict, authorize

BASE_ASSUMPTIONS = (
    "BoundaryCompleteness: every covered authority relation is represented in the model",
    "Logical actions are atomic at the invariant's abstraction level",
    "Python interpreter and this checker are correct (no independent proof checker)",
)


def core_results(cfg: Config, workers: int) -> list[Result]:
    dom = cfg.describe()
    out = []
    r = explore(CoreModel(cfg), max_states=5_000_000)
    v = Verdict.UNKNOWN if not r.complete else (Verdict.FAIL if r.violation else Verdict.PASS)
    out.append(Result(
        "Reachable ⊨ Inv ∧ Safety ∧ TransitionProps", v, "bfs-exhaustive-reachability", dom,
        f"{r.states:,} reachable states, {r.transitions:,} transitions, depth {r.max_depth}",
        BASE_ASSUMPTIONS, ("finite domain only",),
        detail=r.violation.pretty() if r.violation else "",
    ))
    ir = check_induction(CoreModel(cfg), workers=workers)
    bounds = f"{ir.inv_state_count:,} Inv-states, {ir.transitions_checked:,} transitions, {ir.seconds:.0f}s"
    risks = ("finite domain only; no unbounded/parameterized proof",
             "Safety is encoded; its adequacy vs. the informal principle is a judgment")
    for name, ok in (("Init ⇒ Inv", ir.init_ok), ("Inv ∧ Next ⇒ Inv'", ir.step_ok),
                     ("Inv ⇒ Safety", ir.safety_ok)):
        v = Verdict.UNKNOWN if not ir.complete else (Verdict.PASS if ok else Verdict.FAIL)
        out.append(Result(name, v, "finite-exhaustive-induction", dom, bounds, BASE_ASSUMPTIONS, risks,
                          detail=str(ir.first_failure) if ir.first_failure else ""))
    return out


def mutant_results(cfg: Config) -> list[Result]:
    out = []
    for M in ALL_MUTANTS:
        r = explore(M(cfg), max_states=500_000)
        if r.violation:
            v, detail = Verdict.PASS, r.violation.pretty()
            b = f"counterexample length {len(r.violation.trace) - 1}"
        elif r.complete:
            v, detail, b = Verdict.FAIL, "mutant survived exhaustive exploration", f"{r.states:,} states"
        else:
            v, detail, b = Verdict.UNKNOWN, "budget exhausted", f"{r.states:,} states"
        out.append(Result(f"mutant detected: {M.name}", v, "bfs-reachability", cfg.describe(), b,
                          detail=detail))
    return out


def distributed_results() -> list[Result]:
    pol = ResolutionPolicy(frozenset({"alice", "bob", "carol"}), 2)
    chain = [Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0"),
             Admit("r2", "L", 2, "carol", pred="r1"), Edge("r0", "r1"), Edge("r1", "r2")]
    finals = set()
    for p in itertools.permutations(chain):
        rep = Replica(pol)
        for e in p:
            rep.deliver(e)
        finals.add((rep.canonical(), rep.head("L")))
    ok = len(finals) == 1 and next(iter(finals))[1] == "r2"
    out = [Result("eventual convergence (chain r0→r1→r2)", Verdict.PASS if ok else Verdict.FAIL,
                  "permutation-exhaustive", "5 events", "120/120 schedules",
                  ("causal deferral; reliable eventual delivery",),
                  ("NOT linearizability, consensus, or arbitrary partitions",
                   "replicas may serve a stale head before delivery"))]
    fork = [Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0"),
            Admit("r2", "L", 2, "carol", pred="r0"), Edge("r0", "r1"), Edge("r0", "r2")]
    ref = Replica(pol)
    for e in fork:
        ref.deliver(e)
    good = make_resolution(ref, "r0", "r2", "r1", {"alice", "bob"})
    finals = set()
    for p in itertools.permutations(fork + [good]):
        rep = Replica(pol)
        for e in p:
            rep.deliver(e)
        finals.add(rep.head("L"))
    out.append(Result("successor conflict resolves only via independent quorum",
                      Verdict.PASS if finals == {"r2"} else Verdict.FAIL,
                      "permutation-exhaustive", "6 events (fork + resolution)", "720/720 schedules",
                      ("resolution policy authorities are themselves trustworthy",),
                      ("a replica may act on the eventual loser before learning of the fork",)))
    return out


def render(results: list[Result], profile: str) -> str:
    lines = [
        "# Verification Results",
        "",
        f"Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M UTC} by "
        f"`python -m assurance_lab{' --full' if profile == 'full' else ''}` "
        f"(Python {platform.python_version()}).",
        "",
        "Status: research prototype. **Not production-certified. Not unboundedly verified.**",
        "",
        "| Obligation | Verdict |",
        "|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.obligation} | `{r.label()}` |")
    lines += ["", "## Assumptions and residual risks", ""]
    seen = set()
    for r in results:
        for a in r.assumptions + r.residual_risks:
            if a not in seen:
                seen.add(a)
                lines.append(f"- {a}")
    required = [r.obligation for r in results]
    lines += ["", f"Non-compensating gate over all {len(required)} obligations: "
              f"**{'all PASS' if authorize(results, required) else 'NOT all PASS'}**", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="assurance_lab")
    ap.add_argument("--full", action="store_true", help="headline domain (3 records, levels 0..2, sem 0..1)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", help="write markdown report here")
    a = ap.parse_args(argv)
    core_cfg = Config() if a.full else Config(n_records=3, n_lineages=2, max_level=1, n_sem=1)
    results = core_results(core_cfg, a.workers) + mutant_results(Config()) + distributed_results()
    for r in results:
        print(f"{r.verdict.value:8} {r.obligation}  {r.label()}")
    md = render(results, "full" if a.full else "quick")
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    return 0 if authorize(results, [r.obligation for r in results]) else 1


if __name__ == "__main__":
    sys.exit(main())
