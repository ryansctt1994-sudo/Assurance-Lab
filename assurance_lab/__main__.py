"""Run the verification campaign and emit labeled results.

    python -m assurance_lab                       # quick profile, in-place, no report
    python -m assurance_lab --full --out docs/RESULTS.md
    python -m assurance_lab --out r.md --start-method spawn

Provenance discipline (see ``provenance.py`` for the limits):

* With ``--out``, the campaign runs from a **snapshot**: the committed
  ``assurance_lab/`` tree exported by ``git archive`` into a fresh temp
  directory, cross-checked against the git objects, executed by an isolated
  interpreter. ``--in-place`` opts out (development only).
* A *start receipt* is captured **before any enumeration** and never
  recomputed.
* A report is refused (exit 2) from a dirty tree, or if the snapshot's
  integrity cannot be established.
* Every process that runs campaign code reports where it imported
  ``assurance_lab`` from; any origin outside the executed tree, or any change
  to it by the end of the run, invalidates the report (exit 3).
* Claims (obligation labels) contain no run-dependent values; timing and
  environment live only in the trailing "Run metadata" section.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .checker.explore import explore
from .checker.induction import check_induction, unmatched_reachable
from .core.model import Config, CoreModel
from .core.mutants import ALL_MUTANTS
from .distributed.mc import SCENARIOS, check_scenario, mutant_scenarios, with_observer
from .distributed.replica import Admit, Edge, Replica, ResolutionPolicy, make_resolution
from .provenance import (
    PKG, Manifest, ProvenanceError, commit_digest, foreign_origins, git,
    interpreter_isolation, loaded_origins, make_snapshot, remove_snapshot,
    snapshot_command, source_digest, tree_state,
)
from .verdict import Result, Verdict, authorize

REPO = PKG.parent

BASE_ASSUMPTIONS = (
    "BoundaryCompleteness: every covered authority relation is represented in the model",
    "Logical actions are atomic at the invariant's abstraction level",
    "Python interpreter and this checker are correct (no independent proof checker)",
)

QUICK_CORE = Config(n_records=3, n_lineages=2, max_level=1, n_sem=1)
FULL_CORE = Config()
MUTANT_CFG = Config()


# ---------------------------------------------------------------- provenance


def start_receipt(core_cfg: Config, workers: int, start_method: str, command: str,
                  manifest: Optional[Manifest] = None) -> dict:
    """Identity of the computation, captured before it starts."""
    if manifest is not None:
        ident = {
            "execution": "snapshot",
            "command": manifest.command,
            "commit": manifest.commit,
            "tree_id": manifest.tree_id,
            "model_tree": manifest.launcher_tree,
            "git_object_sha256": manifest.git_object_sha256,
            "snapshot_sha256": manifest.snapshot_sha256,
        }
    else:
        commit = git(REPO, "rev-parse", "HEAD") or "unknown"
        try:
            gdig = commit_digest(REPO, commit) if commit != "unknown" else "unknown"
        except (OSError, subprocess.CalledProcessError):
            gdig = "unknown"
        ident = {
            "execution": "in-place",
            "command": command,
            "commit": commit,
            "tree_id": git(REPO, "rev-parse", "HEAD:assurance_lab") or "unknown",
            "model_tree": tree_state(REPO),
            "git_object_sha256": gdig,
        }
    ident.update({
        "source_sha256": source_digest(PKG),
        "core_domain": core_cfg.describe(),
        "mutant_domain": MUTANT_CFG.describe(),
        "workers": workers,
        "start_method": start_method,
    })
    ident["receipt_id"] = hashlib.sha256(
        json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16]
    return ident


def preflight(receipt: dict, writing_report: bool, allow_dirty: bool,
              manifest: Optional[Manifest] = None) -> Optional[str]:
    """Refuse to produce a report whose provenance can't be bound to a commit."""
    if manifest is not None:
        expected = Path(manifest.snapshot_root).resolve() / PKG.name
        if PKG != expected:
            return f"campaign code is running from {PKG}, not the snapshot {expected}"
        if not (receipt["source_sha256"] == manifest.snapshot_sha256 == manifest.git_object_sha256):
            return "snapshot bytes changed between export and the start of the run"
        if not (sys.flags.isolated and sys.flags.no_site):
            return "snapshot runs require an isolated interpreter (-I -S)"
    if writing_report and receipt["model_tree"] != "clean" and not allow_dirty:
        return (f"refusing to write a report: assurance_lab/ tree is {receipt['model_tree']}; "
                "commit first or pass --allow-dirty")
    return None


def source_drifted(receipt: dict) -> bool:
    return source_digest(PKG) != receipt["source_sha256"]


def origin_check(worker_origins: dict) -> tuple[int, list[str]]:
    """(number of processes checked, origins outside the executed tree)."""
    procs = dict(worker_origins)
    procs[os.getpid()] = loaded_origins()  # parent, re-read at the end
    foreign = sorted({o for origins in procs.values() for o in foreign_origins(origins, PKG)})
    return len(procs), foreign


# ---------------------------------------------------------------- campaign


def core_results(cfg: Config, workers: int, mp_context=None):
    """Returns (results, timing, induction report)."""
    dom = cfg.describe()
    timing: dict[str, float] = {}
    out = []
    t0 = time.perf_counter()
    r = explore(CoreModel(cfg), max_states=5_000_000, keep_states=True)
    timing["reachability_s"] = time.perf_counter() - t0
    v = Verdict.UNKNOWN if not r.complete else (Verdict.FAIL if r.violation else Verdict.PASS)
    out.append(Result(
        "Reachable ⊨ Inv ∧ Safety ∧ TransitionProps", v, "bfs-exhaustive-reachability", dom,
        f"{r.states:,} reachable states, {r.transitions:,} transitions, depth {r.max_depth}",
        BASE_ASSUMPTIONS, ("finite domain only",),
        detail=r.violation.pretty() if r.violation else "",
    ))
    ir = check_induction(CoreModel(cfg), workers=workers, mp_context=mp_context)
    timing["induction_s"] = ir.seconds
    bounds = f"{ir.inv_state_count:,} Inv-states, {ir.transitions_checked:,} transitions"
    risks = ("finite domain only; no unbounded/parameterized proof",
             "Safety is encoded; its adequacy vs. the informal principle is a judgment")
    for name, ok in (("Init ⇒ Inv", ir.init_ok), ("Inv ∧ Next ⇒ Inv'", ir.step_ok),
                     ("Inv ⇒ Safety", ir.safety_ok)):
        v = Verdict.UNKNOWN if not ir.complete else (Verdict.PASS if ok else Verdict.FAIL)
        out.append(Result(name, v, "finite-exhaustive-induction", dom, bounds, BASE_ASSUMPTIONS, risks,
                          detail=str(ir.first_failure) if ir.first_failure else ""))
    t1 = time.perf_counter()
    missing = unmatched_reachable(cfg, r.state_set) if r.complete else None
    timing["subset_check_s"] = time.perf_counter() - t1
    if missing is None:
        v, b = Verdict.UNKNOWN, "reachability incomplete"
    else:
        v = Verdict.PASS if not missing else Verdict.FAIL
        b = f"all {r.states:,} reachable states produced by the generator" if not missing \
            else f"{len(missing):,} reachable states missing from generator"
    out.append(Result("Reach ⊆ Inv-states (BFS vs generator)", v, "exhaustive-cross-check", dom, b))
    return out, timing, ir


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


def replica_mc_results(full: bool) -> list[Result]:
    """Replica-level model checking: concurrent origination, reordering,
    bounded duplication, concurrent resolutions."""
    out = []
    scenarios = [(sc, f"2 replicas, ≤{sc.max_dups} duplicated messages") for sc in SCENARIOS]
    if full:
        scenarios += [(with_observer(sc, 1), "3 replicas, ≤1 duplicated message")
                      for sc in SCENARIOS if sc.resolvers]
    risks = ("no message loss, crashes, or Byzantine replicas modeled",
             "outcomes checked only for the scripted scenarios")
    for sc, dom in scenarios:
        r = check_scenario(sc)
        anticipated = set(dict(sc.expect))
        if r.violation and r.violation.startswith("UNKNOWN"):
            v, detail = Verdict.UNKNOWN, r.violation
        elif r.violation:
            v, detail = Verdict.FAIL, f"{r.violation}\n" + "\n".join(r.trace)
        elif r.issued_sets != anticipated:
            v, detail = Verdict.FAIL, f"vacuity: anticipated outcomes {anticipated} reached {r.issued_sets}"
        else:
            v, detail = Verdict.PASS, ""
        out.append(Result(f"replica MC: {sc.name}", v, "bfs-exhaustive-replica-network", dom,
                          f"{r.states:,} states, {r.quiescent} quiescent, "
                          f"{len(r.issued_sets)} resolution outcome(s) reached",
                          ("reliable eventual delivery (no loss)",), risks, detail=detail))
    for sc in mutant_scenarios():
        r = check_scenario(sc)
        v = Verdict.PASS if r.violation and "divergence" in r.violation else Verdict.FAIL
        out.append(Result(f"replica mutant detected: {sc.name}", v, "bfs-exhaustive-replica-network",
                          f"2 replicas, ≤{sc.max_dups} duplicated messages",
                          f"counterexample schedule length {len(r.trace)}", detail=r.violation or ""))
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


def provenance_valid(drifted: bool, foreign: list[str], execution: str,
                     writing_report: bool) -> bool:
    """A report is accepted only if its results are bound to a commit.

    In-place reports are never accepted: scripts/demo_edit_and_revert.sh
    shows every per-line provenance check passing while sabotaged code ran.
    """
    if drifted or foreign:
        return False
    return execution == "snapshot" or not writing_report


def provenance_verdict(receipt: dict, drifted: bool, foreign: list[str]) -> str:
    if drifted or foreign:
        return "**INVALID** (see warnings above)"
    if receipt["execution"] != "snapshot":
        return ("**UNBOUND**: in-place execution cannot bind results to a commit "
                "(an edit reverted mid-run is invisible; see `scripts/demo_edit_and_revert.sh`)")
    return f"**BOUND** to commit `{receipt['commit'][:12]}`"


def render(results: list[Result], receipt: dict, meta: dict, drifted: bool,
           procs_checked: int = 0, foreign: Optional[list[str]] = None) -> str:
    foreign = foreign or []
    lines = ["# Verification Results", ""]
    if drifted:
        lines += ["> **INVALID PROVENANCE: the executed `assurance_lab/` source changed during the run.**",
                  "> These results are not bound to any single source tree.", ""]
    if foreign:
        lines += ["> **INVALID PROVENANCE: campaign code was imported from outside the executed tree:**"]
        lines += [f"> - `{o}`" for o in foreign] + [""]
    if receipt["model_tree"] != "clean":
        lines += [f"> **Model tree was {receipt['model_tree']} at start.** Results are not "
                  "bound to the recorded commit.", ""]
    if receipt["execution"] == "in-place":
        lines += ["> Executed **in place** from the working tree: an edit reverted before the end "
                  "of the run (A→B→A) would not be detected.", ""]
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
        f"- Commit: `{receipt['commit']}` (tree `{receipt['tree_id']}`)",
    ]
    if receipt["execution"] == "snapshot":
        lines += [
            "- Execution: **snapshot**: committed tree exported with `git archive` into a fresh "
            "temp directory, run by an isolated interpreter (`-I -S -B`)",
            f"- Snapshot bytes = git objects: **{'yes' if receipt['snapshot_sha256'] == receipt['git_object_sha256'] else 'NO'}**",
        ]
    else:
        lines.append("- Execution: **in-place** (working tree)")
    lines += [
        f"- `assurance_lab/` working tree at start: **{receipt['model_tree']}**",
        f"- SHA-256 over executed `assurance_lab/**/*.py` at start: `{receipt['source_sha256']}`",
        f"- Same digest from git objects at the commit: `{receipt['git_object_sha256']}`",
        f"- Import origins verified in every process that ran campaign code: the parent + "
        f"{max(procs_checked - 1, 0)} pool worker(s) that executed jobs (start method "
        f"`{receipt['start_method']}`); outside executed tree: "
        f"**{len(foreign) if foreign else 'none'}**",
        f"- Executed source re-hashed at end: **{'MISMATCH' if drifted else 'unchanged'}**",
        f"- Core domain: {receipt['core_domain']}",
        f"- Mutant domain: {receipt['mutant_domain']}",
        f"- Workers: {receipt['workers']}",
        f"- Receipt id: `{receipt['receipt_id']}`",
        f"- Provenance verdict: {provenance_verdict(receipt, drifted, foreign)}",
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
    gate = authorize(results, required) and provenance_valid(
        drifted, foreign, receipt["execution"], writing_report=True)
    lines += ["", f"Non-compensating gate over all {len(required)} obligations"
              f" and provenance: **{'all PASS' if gate else 'NOT all PASS'}**", ""]
    # Run metadata goes last, so two reports of the same code differ only below
    # this heading.
    lines += [METADATA_HEADING, ""]
    for k, v in meta.items():
        lines.append(f"- {k}: {v:.3f}" if isinstance(v, float) else f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- entry


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="assurance_lab")
    ap.add_argument("--full", action="store_true", help="headline domain (3 records, levels 0..2, sem 0..1)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--start-method", choices=multiprocessing.get_all_start_methods(),
                    help="process-pool start method (default: platform default)")
    ap.add_argument("--out", help="write markdown report here (runs from a snapshot of HEAD)")
    ap.add_argument("--in-place", action="store_true",
                    help="with --out: run from the working tree instead of a snapshot "
                         "(development only; edit-and-revert is undetectable)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="write a report even if assurance_lab/ has uncommitted changes (flagged)")
    ap.add_argument("--_manifest", help=argparse.SUPPRESS)
    return ap


def launch_snapshot(a: argparse.Namespace, argv: list[str]) -> int:
    state = tree_state(REPO)
    if state != "clean" and not a.allow_dirty:
        print(f"refusing to write a report: assurance_lab/ tree is {state}; "
              "commit first or pass --allow-dirty", file=sys.stderr)
        return 2
    try:
        m = make_snapshot(REPO, "HEAD", command="python -m assurance_lab " + " ".join(argv))
    except (ProvenanceError, OSError, subprocess.CalledProcessError) as e:
        print(f"refusing to write a report: cannot build snapshot: {e}", file=sys.stderr)
        return 2
    child = ["--workers", str(a.workers), "--out", os.path.abspath(a.out)]
    child += ["--full"] if a.full else []
    child += ["--start-method", a.start_method] if a.start_method else []
    child += ["--allow-dirty"] if a.allow_dirty else []
    try:
        return subprocess.run(snapshot_command(m, child), cwd=REPO).returncode
    finally:
        remove_snapshot(m.snapshot_root)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = _parser().parse_args(argv)
    if a.out and not a.in_place and a._manifest is None:
        return launch_snapshot(a, argv)

    manifest = Manifest.from_json(a._manifest) if a._manifest else None
    core_cfg = FULL_CORE if a.full else QUICK_CORE
    ctx = multiprocessing.get_context(a.start_method)  # None -> platform default
    receipt = start_receipt(core_cfg, a.workers, ctx.get_start_method(),
                            "python -m assurance_lab " + " ".join(argv), manifest)  # BEFORE enumeration
    why = preflight(receipt, bool(a.out), a.allow_dirty, manifest)
    if why:
        print(f"refusing: {why}", file=sys.stderr)
        return 2

    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    results, timing, ir = core_results(core_cfg, a.workers, ctx)
    t1 = time.perf_counter()
    results += mutant_results(MUTANT_CFG)
    t2 = time.perf_counter()
    results += distributed_results() + replica_mc_results(a.full)
    t3 = time.perf_counter()
    procs_checked, foreign = origin_check(ir.origins)
    drifted = source_drifted(receipt)

    meta = {
        "started_utc": f"{started:%Y-%m-%d %H:%M:%S}",
        "python": platform.python_version(),
        "interpreter": interpreter_isolation(),
        **timing,
        "core_total_s": t1 - t0,
        "mutants_s": t2 - t1,
        "distributed_s": t3 - t2,
    }
    for r in results:
        print(f"{r.verdict.value:8} {r.obligation}  {r.label()}")
    if drifted:
        print("ERROR: executed assurance_lab/ source changed during the run", file=sys.stderr)
    if foreign:
        print(f"ERROR: campaign code imported from outside the executed tree: {foreign}",
              file=sys.stderr)
    if a.out:
        with open(a.out, "w") as f:
            f.write(render(results, receipt, meta, drifted, procs_checked, foreign))
    if not provenance_valid(drifted, foreign, receipt["execution"], bool(a.out)):
        if receipt["execution"] != "snapshot" and not (drifted or foreign):
            print("provenance UNBOUND: in-place reports are not accepted", file=sys.stderr)
        return 3
    return 0 if authorize(results, [r.obligation for r in results]) else 1


if __name__ == "__main__":
    sys.exit(main())
