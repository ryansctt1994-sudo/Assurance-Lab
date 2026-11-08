"""Run the verification campaign and emit labeled results.

    python -m assurance_lab            # quick profile (~1 min)
    python -m assurance_lab --full     # headline domain, parallel (~6 min on 4 cores)
    python -m assurance_lab --full --out docs/RESULTS.md

Provenance discipline:

* A *start receipt* (source digest, git commit, tree state, domains,
  workers, command) is captured **before any enumeration** and is never
  recomputed afterwards.
* Writing a report (``--out``) from a dirty ``assurance_lab/`` tree is
  refused unless ``--allow-dirty`` is given, and then flagged in the report.
* At the end the source is re-hashed; a mismatch marks the report as not
  bound to any single source and exits non-zero. (Process-pool workers under
  spawn/forkserver re-import the package from disk, so a mid-run edit could
  otherwise change the code that actually ran.)
* Claims (obligation labels) contain no run-dependent values; timing and
  environment live only in the "Run metadata" section.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import pathlib
import platform
import subprocess
import sys
import time

from .checker.explore import explore
from .checker.induction import check_induction
from .core.model import Config, CoreModel
from .core.mutants import ALL_MUTANTS
from .distributed.replica import Admit, Edge, Replica, ResolutionPolicy, make_resolution
from .verdict import Result, Verdict, authorize

PKG = pathlib.Path(__file__).resolve().parent

BASE_ASSUMPTIONS = (
    "BoundaryCompleteness: every covered authority relation is represented in the model",
    "Logical actions are atomic at the invariant's abstraction level",
    "Python interpreter and this checker are correct (no independent proof checker)",
)

QUICK_CORE = Config(n_records=3, n_lineages=2, max_level=1, n_sem=1)
FULL_CORE = Config()
MUTANT_CFG = Config()


# ---------------------------------------------------------------- provenance


def source_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(PKG.rglob("*.py")):
        h.update(str(p.relative_to(PKG)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=PKG.parent, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def start_receipt(core_cfg: Config, workers: int, argv: list[str]) -> dict:
    """Identity of the computation, captured before it starts."""
    dirty = _git("status", "--porcelain", "--", "assurance_lab")
    ident = {
        "command": "python -m assurance_lab " + " ".join(argv),
        "commit": _git("rev-parse", "HEAD"),
        "model_tree": "unknown" if dirty == "unknown" else ("DIRTY" if dirty else "clean"),
        "source_sha256": source_digest(),
        "core_domain": core_cfg.describe(),
        "mutant_domain": MUTANT_CFG.describe(),
        "workers": workers,
    }
    ident["receipt_id"] = hashlib.sha256(
        json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16]
    return ident


def preflight(receipt: dict, writing_report: bool, allow_dirty: bool) -> str | None:
    """Refuse to produce a report whose provenance can't be bound to a commit."""
    if writing_report and receipt["model_tree"] != "clean" and not allow_dirty:
        return (f"refusing to write a report: assurance_lab/ tree is {receipt['model_tree']}; "
                "commit first or pass --allow-dirty")
    return None


def source_drifted(receipt: dict) -> bool:
    return source_digest() != receipt["source_sha256"]


# ---------------------------------------------------------------- campaign


def core_results(cfg: Config, workers: int) -> tuple[list[Result], dict[str, float]]:
    dom = cfg.describe()
    timing: dict[str, float] = {}
    out = []
    t0 = time.perf_counter()
    r = explore(CoreModel(cfg), max_states=5_000_000)
    timing["reachability_s"] = time.perf_counter() - t0
    v = Verdict.UNKNOWN if not r.complete else (Verdict.FAIL if r.violation else Verdict.PASS)
    out.append(Result(
        "Reachable ⊨ Inv ∧ Safety ∧ TransitionProps", v, "bfs-exhaustive-reachability", dom,
        f"{r.states:,} reachable states, {r.transitions:,} transitions, depth {r.max_depth}",
        BASE_ASSUMPTIONS, ("finite domain only",),
        detail=r.violation.pretty() if r.violation else "",
    ))
    ir = check_induction(CoreModel(cfg), workers=workers)
    timing["induction_s"] = ir.seconds
    bounds = f"{ir.inv_state_count:,} Inv-states, {ir.transitions_checked:,} transitions"
    risks = ("finite domain only; no unbounded/parameterized proof",
             "Safety is encoded; its adequacy vs. the informal principle is a judgment")
    for name, ok in (("Init ⇒ Inv", ir.init_ok), ("Inv ∧ Next ⇒ Inv'", ir.step_ok),
                     ("Inv ⇒ Safety", ir.safety_ok)):
        v = Verdict.UNKNOWN if not ir.complete else (Verdict.PASS if ok else Verdict.FAIL)
        out.append(Result(name, v, "finite-exhaustive-induction", dom, bounds, BASE_ASSUMPTIONS, risks,
                          detail=str(ir.first_failure) if ir.first_failure else ""))
    return out, timing


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


# ---------------------------------------------------------------- report

METADATA_HEADING = "## Run metadata (varies between runs; not part of any claim)"


def render(results: list[Result], receipt: dict, meta: dict, drifted: bool) -> str:
    lines = ["# Verification Results", ""]
    if drifted:
        lines += ["> **INVALID PROVENANCE: `assurance_lab/` source changed during the run.**",
                  "> These results are not bound to any single source tree.", ""]
    if receipt["model_tree"] != "clean":
        lines += [f"> **Model tree was {receipt['model_tree']} at start.** Results are not "
                  "bound to the recorded commit.", ""]
    lines += [
        "Status: research prototype. **Not production-certified. Not unboundedly verified.**",
        "",
        "These results come from a local run of the command below, **not from CI**.",
        "PR CI runs the test suite and the *quick* campaign only; its `full-induction`",
        "job is skipped on PRs by design. Green CI ≠ full induction completed.",
        "",
        "## Provenance (start receipt, captured before enumeration)",
        "",
        f"- Command: `{receipt['command'].strip()}`",
        f"- Commit: `{receipt['commit']}`",
        f"- `assurance_lab/` tree at start: **{receipt['model_tree']}**",
        f"- SHA-256 over `assurance_lab/**/*.py` at start: `{receipt['source_sha256']}`",
        f"- Source re-hashed at end: **{'MISMATCH' if drifted else 'unchanged'}**",
        f"- Core domain: {receipt['core_domain']}",
        f"- Mutant domain: {receipt['mutant_domain']}",
        f"- Receipt id: `{receipt['receipt_id']}`",
        "",
        "## Claims",
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
    gate = authorize(results, required) and not drifted
    lines += ["", f"Non-compensating gate over all {len(required)} obligations: "
              f"**{'all PASS' if gate else 'NOT all PASS'}**", ""]
    # Run metadata goes last, so two reports of the same code differ only below
    # this heading. Receipt fields above stay the same for the same commit and workers.
    lines += [METADATA_HEADING, ""]
    for k, v in meta.items():
        lines.append(f"- {k}: {v:.1f}" if isinstance(v, float) else f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="assurance_lab")
    ap.add_argument("--full", action="store_true", help="headline domain (3 records, levels 0..2, sem 0..1)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", help="write markdown report here")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="write a report even if assurance_lab/ has uncommitted changes (flagged)")
    a = ap.parse_args(argv)
    core_cfg = FULL_CORE if a.full else QUICK_CORE

    receipt = start_receipt(core_cfg, a.workers, argv)  # BEFORE any enumeration
    why = preflight(receipt, bool(a.out), a.allow_dirty)
    if why:
        print(why, file=sys.stderr)
        return 2

    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    results, timing = core_results(core_cfg, a.workers)
    t1 = time.perf_counter()
    results += mutant_results(MUTANT_CFG)
    t2 = time.perf_counter()
    results += distributed_results()
    t3 = time.perf_counter()
    drifted = source_drifted(receipt)

    meta = {
        "started_utc": f"{started:%Y-%m-%d %H:%M:%S}",
        "python": platform.python_version(),
        **timing,
        "core_total_s": t1 - t0,
        "mutants_s": t2 - t1,
        "distributed_s": t3 - t2,
    }
    for r in results:
        print(f"{r.verdict.value:8} {r.obligation}  {r.label()}")
    if drifted:
        print("ERROR: assurance_lab/ source changed during the run", file=sys.stderr)
    if a.out:
        with open(a.out, "w") as f:
            f.write(render(results, receipt, meta, drifted))
    if drifted:
        return 3
    return 0 if authorize(results, [r.obligation for r in results]) else 1


if __name__ == "__main__":
    sys.exit(main())
