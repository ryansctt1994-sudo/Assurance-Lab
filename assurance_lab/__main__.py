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
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .checker.coverage import Outcome, property_coverage
from .checker.explore import explore
from .checker.relational import Spec, compare as oracle_compare
from .checker.witnesses import all_witnesses
from .checker.induction import check_induction, inv_states, unmatched_reachable
from .checker.partitions import Manifest as PartitionManifest, build_manifest
from .core.model import Config, CoreModel
from .core.mutants import ALL_MUTANTS
from .distributed import metamorphic as mm
from .distributed.mc import SCENARIOS, check_scenario, mutant_scenarios, with_observer
from .distributed.replica import Admit, Edge, Replica, ResolutionPolicy, make_resolution
from .statedigest import (
    ENCODING, MALFORMED_CORPUS, SetDigest, action_label, add_transitions, canon, decanon,
    malformed_accepted, round_trip_failures, set_digest, sorted_digest,
)
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
                  manifest: Optional[Manifest] = None, partitions: Optional[PartitionManifest] = None) -> dict:
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
    if partitions is not None:  # frozen before execution
        ident["partition_manifest"] = f"{len(partitions.entries)} partitions, content sha256 {partitions.content_digest()}"
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


def core_results(cfg: Config, workers: int, mp_context=None, manifest=None, key=None):
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
    ir = check_induction(CoreModel(cfg), workers=workers, mp_context=mp_context, manifest=manifest, key=key)
    out.append(Result("partition reconciliation: evidence matches the frozen manifest exactly",
                      Verdict.FAIL if ir.reconciliation_problems else Verdict.PASS, "manifest-reconciliation",
                      dom, f"{len(ir.manifest.entries)} partitions: complete, disjoint, authenticated, "
                      "arithmetic recomputed", residual_risks=("does not defend against a malicious worker",),
                      detail="; ".join(ir.reconciliation_problems)))
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
    t2 = time.perf_counter()
    digests = {}
    if r.complete:
        lines = {st: canon(st) for st in r.state_set}
        lab, edg = SetDigest(), SetDigest()
        model = CoreModel(cfg)
        for st, line in lines.items():
            add_transitions(lab, edg, line, [(action_label(lbl), lines[t]) for lbl, t in model.next(st)])
        digests["reachable states"] = (set_digest(r.state_set).label(), sorted_digest(lines.values()))
        digests["reachable transitions (source, action, destination)"] = (lab.label(), None)
        digests["reachable edges (source, destination)"] = (edg.label(), None)
    if ir.complete:
        digests["Inv-states"] = (ir.digest.label(), sorted_digest(canon(st) for st in inv_states(cfg)))
        digests["Inv-state transitions (source, action, destination)"] = (ir.transition_digest.label(), None)
        digests["Inv-state edges (source, destination)"] = (ir.edge_digest.label(), None)
    timing["digest_s"] = time.perf_counter() - t2
    if r.complete and ir.complete:
        pairs = [("reachable states", r.states, digests["reachable states"][0]),
                 ("reachable transitions", r.transitions,
                  digests["reachable transitions (source, action, destination)"][0]),
                 ("Inv-states", ir.inv_state_count, digests["Inv-states"][0]),
                 ("Inv-state transitions", ir.transitions_checked,
                  digests["Inv-state transitions (source, action, destination)"][0])]
        off = [f"{n}: checker {c:,} vs digest {lbl.split(':')[1]}" for n, c, lbl in pairs
               if int(lbl.split(":")[1]) != c]
        out.append(Result("digest counts equal the checkers' own counts", Verdict.FAIL if off else Verdict.PASS,
                          "self-consistency", dom, "states and transitions, reachable and Inv",
                          detail="; ".join(off)))
    out += transition_oracle_results(cfg, r.state_set if r.complete else None)
    timing["oracle_s"] = time.perf_counter() - t2 - timing["digest_s"]
    return out, timing, ir, digests


ORACLE_SMALL = (Config(n_records=2, n_lineages=1), Config(n_records=2, n_lineages=2),
                Config(n_records=3, n_lineages=1, max_level=1, n_sem=1))


def transition_oracle_results(cfg: Config, reachable) -> list[Result]:
    """Independent transition relation (translated from tla/AuthorityCore.tla):
    identical action-labeled successors, not just identical states."""
    from .checker.coverage import mutant_domain
    from .core import mutants as mu

    out = []
    risk = ("same author wrote both relations: a shared misreading of the handoff would agree",)
    for small in ORACLE_SMALL:
        states = list(inv_states(small))
        bad = oracle_compare(CoreModel(small), Spec.for_config(small), states)
        n = sum(1 for st in states for _ in CoreModel(small).next(st))
        out.append(Result("TRANSITION ORACLE: action-labeled successors agree on every Inv-state",
                          Verdict.FAIL if bad else Verdict.PASS, "exhaustive-relation-comparison",
                          small.describe(), f"{len(states):,} states, {n:,} labeled transitions",
                          residual_risks=risk, detail=str(bad[:3]) if bad else ""))
    if reachable is not None:
        ordered = sorted(reachable, key=canon)
        sample = ordered[::max(1, len(ordered) // 20_000)]
        bad = oracle_compare(CoreModel(cfg), Spec.for_config(cfg), sample)
        out.append(Result("TRANSITION ORACLE: agreement on a sample of the core domain",
                          Verdict.FAIL if bad else Verdict.PASS, "sampled-relation-comparison",
                          cfg.describe(), f"{len(sample):,} of {len(ordered):,} reachable states "
                          "(every k-th in canonical order)", residual_risks=risk + ("sampled, not exhaustive",)))
    full = cfg == FULL_CORE
    pool = mu.ALL_MUTANTS if full else [mu.SelfGrant, mu.DiagnosisRevisesRoles, mu.CompactionDropsEdge]
    unseen = []
    for M in pool:
        mcfg = mutant_domain(M.name)
        states = explore(M(mcfg), stop_on_violation=False, keep_states=True, properties=frozenset()).state_set
        if not oracle_compare(M(mcfg), Spec.for_config(mcfg), states):
            unseen.append(M.name)
    out.append(Result("TRANSITION ORACLE: disagrees with every mutated transition relation",
                      Verdict.FAIL if unseen else Verdict.PASS, "mutant-calibration",
                      "each mutant's own graph", f"{len(pool) - len(unseen)}/{len(pool)} mutants seen"
                      + ("" if full else " (transition-only subset; --full checks all)"),
                      detail=f"unseen: {unseen}"))
    small, fork = Config(n_records=2, n_lineages=1), Config(n_records=3, n_lineages=1, max_level=1, n_sem=1)
    twins = [(mu.BeneficiarySelfApproval, "beneficiary_self_approval", small),
             (mu.AdmitIntoOccupiedLineage, "admit_into_occupied_lineage", small),
             (mu.StaleSuccessor, "stale_successor", fork), (mu.ResurrectOnRetract, "resurrect_on_retract", small)]
    off = [name for M, name, mc in twins
           if oracle_compare(M(mc), Spec.for_config(mc, name),
                             explore(M(mc), stop_on_violation=False, keep_states=True,
                                     properties=frozenset()).state_set)]
    out.append(Result("TRANSITION ORACLE: TLA+ mutant translations match their Python twins",
                      Verdict.FAIL if off else Verdict.PASS, "exhaustive-relation-comparison",
                      "each mutant's own graph", f"{len(twins) - len(off)}/{len(twins)} agree",
                      detail=f"disagree: {off}"))
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


def coverage_results() -> tuple[list[Result], str]:
    """Mutant × property matrix (each mutant explored once, completely, in its
    own graph). Returns obligations and the matrix as a report section."""
    cov = property_coverage()
    errs = [r.mutant for r in cov.rows if Outcome.ERROR in r.outcomes.values()]
    invalid = [r.mutant for r in cov.rows if Outcome.INVALID in r.outcomes.values()]
    dom = "each mutant in its smallest non-equivalent domain"
    unkilled, survivors = cov.unkilled_properties(), cov.surviving_mutants()

    def verdict(bad: list) -> Verdict:
        return Verdict.UNKNOWN if errs else (Verdict.FAIL if bad else Verdict.PASS)

    out = [
        Result("coverage: every property kills at least one mutant", verdict(unkilled),
               "mutant-x-property-matrix", dom,
               f"{len(cov.rows)} mutants × {len(cov.rows[0].outcomes)} properties",
               residual_risks=("a kill shows sensitivity, not completeness",),
               detail=f"unkilled: {unkilled}"),
        Result("coverage: no mutant survives every property", verdict(survivors + invalid),
               "mutant-x-property-matrix", dom, f"{len(cov.rows)} mutants, none equivalent",
               detail=f"survivors: {survivors}; invalid: {invalid}; errors: {errs}"),
    ]
    sole = {p: cov.sole_witnesses(p) for p in cov.rows[0].outcomes}
    section = ["## Property coverage (mutant × property)", "",
               "K = killed, **K** = sole detector, · = survived, I = invalid (equivalent mutant), "
               "E = error. Each row is one complete exploration of that mutant's own graph.", "",
               cov.markdown(), "",
               "Sole witnesses: " + "; ".join(f"`{p}`: {', '.join(m)}" for p, m in sole.items() if m) + ".",
               "`NoResurrection` cannot have a sole witness: it is implied by "
               "`NoSilentAuthorityIncrease` (a newly current record must be freshly admitted).", ""]
    return out, "\n".join(section)


def witness_results(cfg: Config) -> tuple[list[Result], str]:
    """Existential reachability witnesses (the model is not over-constrained)."""
    out, lines = [], ["## Reachability witnesses (existential, not liveness)", "",
                      "Shortest paths from Init, found by BFS in the core domain. Each shows a "
                      "behavior the handoff intends to be *possible*; none claims it eventually "
                      "happens.", "", "| Behavior | Handoff | Shortest path |", "|---|---|---|"]
    for w in all_witnesses(cfg):
        v = Verdict.PASS if w.found else Verdict.FAIL
        out.append(Result(f"witness: {w.spec.name}", v, "bfs-shortest-witness", cfg.describe(),
                          f"path length {len(w.actions)}" if w.found else "unreachable",
                          residual_risks=("existential only: says nothing about progress",)))
        path = " → ".join("".join(str(lbl)) for lbl in w.actions) if w.found else "**unreachable**"
        lines.append(f"| {w.spec.meaning} | {w.spec.section} | `{path}` |")
    return out, "\n".join(lines) + "\n"


def partition_section(ir) -> str:
    """Per-partition evidence, as reconciled against the frozen manifest."""
    lines = ["## Partition evidence (reconciled against the frozen manifest)", "",
             "Each row is one signed record from the worker that enumerated that partition. "
             "Reconciliation checked provenance, MAC integrity, identity, completeness, "
             "disjointness (observed keys) and arithmetic: "
             + ("**no problems**." if not ir.reconciliation_problems else
                "**" + "; ".join(ir.reconciliation_problems) + "**."), "",
             "| Partition | Key (levels, sem_now) | States | Transitions | State digest |", "|---|---|---|---|---|"]
    keys = {p: (lv, sn) for p, lv, sn in ir.manifest.entries}
    for e in sorted(ir.evidence, key=lambda e: e.pid):
        lv, sn = keys.get(e.pid, ("?", "?"))
        lines.append(f"| {e.pid} | {lv}, {sn} | {e.count:,} | {e.transitions:,} | "
                     f"`{e.digest.split(':')[2][:16]}…` |")
    return "\n".join(lines) + "\n"


def domain_adequacy(cfg: Config) -> str:
    """Which intended behaviors the run's own core domain can exercise. A
    behavior it cannot reach means the domain's claims say nothing about it:
    shown as NOT EXERCISED rather than omitted."""
    lines = [f"## Domain adequacy: {cfg.describe()}", "",
             "Whether this run's core domain can exhibit each intended behavior at all "
             "(existential BFS). The witness obligations above are always checked on the headline "
             "domain; this table qualifies the core claims of *this* run.", "",
             "| Behavior | On this domain |", "|---|---|"]
    for w in all_witnesses(cfg):
        lines.append(f"| {w.spec.name} | " + (f"exercised (path length {len(w.actions)})" if w.found
                                             else "**NOT EXERCISED**: claims on this domain say nothing about it")
                     + " |")
    return "\n".join(lines) + "\n"


_INTEGRITY_ASSUME = ("replica snapshots are not authenticated: a consistent omission (for example a "
                     "rolled-back invalidation) is indistinguishable from a replica that has not yet "
                     "received the events, passes the integrity check, and can restore a stale head",)


def snapshot_integrity_results() -> list[Result]:
    """The restore boundary, each control reported on its own (development
    lineage; the published main baseline has the same defect, unrepaired)."""
    from assurance_lab.distributed.replica import CorruptSnapshot, Replica
    rows = mm.corruption_table()
    hard = [r for r in rows if r["class"] in ("inconsistent", "malformed")]
    soft = [r for r in rows if r["class"] == "omission"]
    infected = sum(r["infected_snapshots"] for r in hard)
    leaks = [r["corruption"] for r in hard if r["validator"]["refused"] != r["infected_snapshots"]
             or r["validator"]["other_exceptions"] or not r["validator"]["deterministic"]]
    out = [Result("SNAPSHOT INTEGRITY: every inconsistent or malformed snapshot is refused at thaw (INVALID_SNAPSHOT)",
                  Verdict.FAIL if leaks else Verdict.PASS, "corruption-injection",
                  f"{len(hard)} corruption classes × every serialized prefix of {len(mm.KINDS)} instances",
                  f"{infected} damaged snapshots refused, deterministically, no other exception type",
                  _INTEGRITY_ASSUME, detail=f"leaks: {leaks}")]
    authority = [r["corruption"] for r in hard if r["bypass"]["authority_from_damaged_state"]
                 or r["bypass"]["exceptions"] or r["bypass"]["state_changed_by_head"]]
    out.append(Result("SNAPSHOT INTEGRITY (diagnostic, boundary bypassed): head() answers UNKNOWN on every damaged "
                      "state, never CONFLICT or a record, and changes nothing",
                      Verdict.FAIL if authority else Verdict.PASS, "defense-in-depth",
                      "thaw(validate=False), diagnostic only", "not evidence that the boundary works; evidence "
                      "that head() does not turn damaged evidence into authority", detail=f"failed: {authority}"))
    corpus = mm.valid_state_corpus()
    kinds = corpus["incomplete_but_valid"]
    out.append(Result("SNAPSHOT INTEGRITY: genuine states are never refused and never UNKNOWN, incomplete ones included",
                      Verdict.FAIL if corpus["problem_count"] or len(kinds) < 3 else Verdict.PASS,
                      "false-rejection-check",
                      f"{corpus['deliveries']:,} deliveries reaching {corpus['distinct_states']:,} distinct genuine "
                      "states: every serialized prefix, alone and followed by every alphabet event or declared "
                      "sequence; not every reachable replica state (plus every replica-checker state, thawed through "
                      "the validator with a refusal or UNKNOWN recorded as a failure)",
                      "zero false rejections; incomplete but valid states present (distinct): "
                      + ", ".join(f"{k} {d:,}" for k, (_n, d) in kinds.items()),
                      detail="\n".join(corpus["problems"])))
    ce_bad = []
    for spec in mm.RETAINED_COUNTEREXAMPLES:
        inst, _pre, damaged = mm.counterexample_snapshot(spec)
        try:
            Replica.thaw(inst.policy, damaged)
            ce_bad.append(f"{spec['id']}: restored")
        except CorruptSnapshot as e:
            if e.status != "INVALID_SNAPSHOT":
                ce_bad.append(f"{spec['id']}: status {e.status}")
        if Replica.thaw(inst.policy, damaged, validate=False).explain_head("L").status != "UNKNOWN":
            ce_bad.append(f"{spec['id']}: bypassed head() is not UNKNOWN")
    out.append(Result("SNAPSHOT INTEGRITY: retained KeyError counterexamples are refused, and UNKNOWN when bypassed",
                      Verdict.FAIL if ce_bad else Verdict.PASS, "regression",
                      f"{len(mm.RETAINED_COUNTEREXAMPLES)} counterexamples (crash b535b1a and main c175fbd)",
                      "INVALID_SNAPSHOT at thaw; UNKNOWN, not CONFLICT, with the boundary bypassed",
                      detail="\n".join(ce_bad)))
    blind = [r["corruption"] for r in soft if (r["relation"] or {}).get("exact-state") != r["infected_instances"]]
    out.append(Result("METAMORPHIC: exact-state comparison detects every consistent omission",
                      Verdict.FAIL if blind else Verdict.PASS, "corruption-injection",
                      f"{len(soft)} omission classes, which pass the boundary by construction",
                      "against a never-serialized reference (a deployment has no such reference)",
                      _INTEGRITY_ASSUME, detail=f"missed: {blind}"))
    return out


def snapshot_integrity_section() -> list[str]:
    rows = mm.corruption_table()
    fmt = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "-"  # noqa: E731
    lines = ["**Snapshot integrity: each control separately** (development lineage only; the published main "
             "baseline `c175fbd` has the same `head()` defect and is not repaired). *Validator*: the production "
             "boundary, `thaw` validating. *Bypassed*: `thaw(validate=False)`, a diagnostic only, never evidence "
             "that the boundary works. *Relation*: for consistent omissions, which pass the boundary by "
             "construction, the serialization relation's layers against a never-serialized reference.", "",
             "| Corruption | Class | Infected (instances / snapshots) | Validator: refused | Bypassed: head() | "
             "Relation: projection / next event / exact state |", "|---|---|---|---|---|---|"]
    for r in rows:
        rel = r["relation"]
        lines.append(f"| {r['corruption']} | {r['class']} | {r['infected_instances']} / {r['infected_snapshots']} | "
                     f"{r['validator']['refused']} | "
                     + ("refused even unvalidated" if r["bypass"]["refused_even_unvalidated"] else fmt(r["bypass"]["statuses"]))
                     + " | " + (f"{rel['projection']} / {rel['next-event']} / {rel['exact-state']}" if rel else "-") + " |")
    lines += ["", "Head results: `HEAD` a record, `NONE` X_⊥, `CONFLICT` a genuine fork or dispute on a valid state, "
              "`UNKNOWN` the state fails the integrity check. A consistent omission is the state of a replica that "
              "has not received some events: it passes the boundary, and a rolled-back invalidation restores a "
              "stale head (residual risk: snapshots are not authenticated). Projection equivalence does not imply "
              "behavioral equivalence (an omitted pending event), and behavioral equivalence does not imply "
              "exact-state equivalence (an omitted rejected event).", ""]
    return lines


def metamorphic_results() -> tuple[list[Result], str]:
    """METAMORPHIC_CONSISTENCY: relations on the corrected replica, calibrated
    by negative controls; plus the core encoding round-trip."""
    out = []
    assume = ("each record id is issued once (no Byzantine equivocation)",
              "fixed resolution policy; complete delivery of a fixed event set")
    for rel in mm.RELATIONS.values():
        r = rel()
        out.append(Result(f"METAMORPHIC: {r.relation}", Verdict.PASS if r.held else Verdict.FAIL,
                          "metamorphic-relation", f"{len(mm.KINDS)} replica source instances",
                          f"{r.checks:,} comparisons; {r.coverage_note()}", assume,
                          detail="\n".join(w.pretty() for w in r.witnesses)))
    out += snapshot_integrity_results()
    v1 = mm.instances(mm.KINDS_V1)
    v1_broken = [n for n, r in [("order", mm.order_invariance(v1)), ("duplicate", mm.duplicate_idempotence(v1)),
                                ("projection", mm.lineage_projection(v1)),
                                ("renaming", mm.renaming_equivariance(mm.KINDS_V1)),
                                ("serialization", mm.serialization_round_trip(v1))] if not r.held]
    out.append(Result("METAMORPHIC: frozen v1 fixture campaign still holds under the current code",
                      Verdict.FAIL if v1_broken else Verdict.PASS, "regression",
                      f"{len(mm.KINDS_V1)} baseline source instances", "all 5 relations",
                      detail=f"broken: {v1_broken}"))
    pre = mm.precondition_violations()
    out.append(Result("METAMORPHIC: relation preconditions hold on every source instance",
                      Verdict.FAIL if pre else Verdict.PASS, "precondition-check",
                      f"{len(mm.KINDS)} instances, renamings, added lineage",
                      "ids issued once; no cross-lineage references; disjoint added lineage; "
                      "renamings are bijections keeping seq and lineage", detail="\n".join(pre)))
    cfg = Config(n_records=2, n_lineages=1)
    states = list(inv_states(cfg))
    bad = round_trip_failures(states, decode=lambda line: decanon(line, cfg), model=CoreModel(cfg))
    out.append(Result(f"METAMORPHIC: {ENCODING} encoding round-trip (strict decoder)",
                      Verdict.FAIL if bad else Verdict.PASS, "metamorphic-relation", cfg.describe(),
                      f"all {len(states):,} Inv-states decode to the same state with the same steps"))
    accepted = malformed_accepted(cfg)
    out.append(Result(f"{ENCODING} strict decoder rejects every malformed encoding",
                      Verdict.FAIL if accepted else Verdict.PASS, "negative-corpus", cfg.describe(),
                      f"{len(MALFORMED_CORPUS)}/{len(MALFORMED_CORPUS)} defect classes rejected"
                      if not accepted else f"accepted: {accepted}"))
    m = mm.mutant_matrix()
    m_v1 = mm.mutant_matrix(mm.KINDS_V1)
    unkilled_rel = [r for r in mm.RELATIONS if not any(m[(r, f)].category == mm.Cell.KILLED for f in mm.FLAGS)]
    unkilled_mut = [f for f in mm.FLAGS if not any(m[(r, f)].category == mm.Cell.KILLED for r in mm.RELATIONS)]
    errors = [k for k, v in m.items() if v.errors]
    anomalies = mm.matrix_anomalies(m) + mm.matrix_anomalies(m_v1)
    out.append(Result("METAMORPHIC: matrix denominators complete (eligible ⊇ deviating ⊇ detecting, per instance)",
                      Verdict.UNKNOWN if errors else (Verdict.FAIL if anomalies else Verdict.PASS),
                      "relation-x-mutant-matrix", f"{len(mm.KINDS)} and {len(mm.KINDS_V1)} instances, one at a time",
                      "frozen eligibility predicates; deviation by lockstep differential execution; no deviation "
                      "outside eligibility, no detection without deviation",
                      detail="\n".join(anomalies)))
    v1_pub = mm.published_v1_categories(mm.PUBLISHED_V1_TABLE)
    changed = mm.v1_to_v2_changes(v1_pub, m)
    explained = {k: (a, b) for k, (a, b, _why) in mm.V1_TO_V2_CHANGES.items()}
    out.append(Result("METAMORPHIC: every category change from the published v1 matrix is explained",
                      Verdict.PASS if changed == explained else Verdict.FAIL, "matrix-versioning",
                      "v1 as published at b535b1a vs v2", f"{len(changed)} changed cells, each with a recorded reason",
                      detail=f"unexplained: {sorted(set(changed) - set(explained))}; "
                             f"stale: {sorted(set(explained) - set(changed))}"))
    vd = lambda bad: Verdict.UNKNOWN if errors else (Verdict.FAIL if bad else Verdict.PASS)  # noqa: E731
    out.append(Result("METAMORPHIC: every relation kills a negative control", vd(unkilled_rel),
                      "relation-x-mutant-matrix", f"{len(mm.RELATIONS)} relations × {len(mm.FLAGS)} mutants",
                      detail=f"unkilled relations: {unkilled_rel}"))
    out.append(Result("METAMORPHIC: every negative control is killed", vd(unkilled_mut),
                      "relation-x-mutant-matrix", f"{len(mm.RELATIONS)} relations × {len(mm.FLAGS)} mutants",
                      detail=f"unkilled mutants: {unkilled_mut}"))
    ax = mm.alphabet_exercise()
    gap = ax["rejection_rules_not_exercised"]
    out.append(Result("METAMORPHIC: declared event alphabet exercised, boundary events handled only as declared",
                      Verdict.PASS if not ax["never_delivered"] and not ax["unexpected"] and not gap
                      else Verdict.FAIL, "alphabet-exercise",
                      f"{ax['events']} events and declared sequences × every serialized prefix",
                      f"{ax['deliveries']:,} deliveries; {len(mm.REJECTION_RULES) - len(gap)}/{len(mm.REJECTION_RULES)} "
                      "rejection rules exercised (cross-lineage through a declared two-event sequence)",
                      detail=json.dumps({k: ax[k] for k in ("never_delivered", "unexpected", "rejection_rules_not_exercised")})))
    probe = mm.order_invariance([mm.EQUIVOCATION_PROBE])
    flagged = mm.shared_precondition(mm.EQUIVOCATION_PROBE)
    sec = ["## Metamorphic relations × negative controls", "",
           "Per cell: **E** eligible instances (frozen predicate over the inputs the relation can deliver, "
           "never an outcome), **D** deviating (eligible, and the mutant behaves differently from the correct "
           "replica on some execution the relation performs), **K** detecting (deviating, and the relation is "
           "violated). Exposure = D/E, detection breadth = K/D. KILLED: K > 0; survived: D > 0 = K; not "
           "reached: E > 0 = D; n/a: no eligible instance; equivalent?: no relation's inputs make the mutant "
           "deviate. Preconditions and justifications: `docs/METAMORPHIC.md`.", "",
           f"**Matrix v2** ({len(mm.KINDS)} source instances; history, the published v1 table and the reason "
           "for every changed cell: `docs/METAMORPHIC_MATRIX_HISTORY.md`):", "", mm.matrix_markdown(m), "",
           f"**Frozen v1 fixtures under v2** ({len(mm.KINDS_V1)} instances; same categories):", "",
           mm.matrix_markdown(m_v1), "",
           *snapshot_integrity_section(),
           "**Declared alphabet, exercised.** Every event of the serialization alphabet delivered after every "
           "serialized prefix, to a correct replica; how the replica handled each boundary event:", "",
           "| Boundary event | Handling (count) |", "|---|---|",
           *(f"| {b} | " + ", ".join(f"{o} {n}" for o, n in sorted(c.items())) + " |" for b, c in ax["boundary"].items()),
           "", f"Rejection rules exercised: {', '.join(f'{r} ({n})' for r, n in ax['rejection_rules_exercised'].items())}. "
           f"Not exercised: {', '.join(gap) or 'none'}. No single event after a single-lineage prefix can reach "
           "the cross-lineage rule (both ends must be admitted), so a declared two-event sequence does; a direct "
           "unit test covers the rule separately, as narrower evidence.", "",
           "**Known limitation (outside the precondition):** with Byzantine equivocation (two "
           "different admissions for one record id), the outcome depends on delivery order. The "
           f"precondition checker flags the probe ({'; '.join(flagged)}), which is why it is excluded. "
           "Minimal witness:", "", "```", *(w.pretty() for w in probe.witnesses), "```", ""]
    return out, "\n".join(sec)


def mc_verdict(r, anticipated: set) -> tuple:
    """Disposition of one replica-checker run. Only an exhausted state budget
    is inconclusive; a refused snapshot or an UNKNOWN head on an explored
    state is a failure, with the evidence kept in the detail."""
    if r.violation and r.violation.startswith("UNKNOWN"):
        return Verdict.UNKNOWN, r.violation
    if r.violation and r.violation.startswith("INVALID_SNAPSHOT"):
        return Verdict.FAIL, "\n".join([r.violation, "refusal reasons:", *r.invalid_reasons,
                                         "offending snapshot:", repr(r.invalid_snapshot), "trace:", *r.trace])
    if r.violation:
        return Verdict.FAIL, f"{r.violation}\n" + "\n".join(r.trace)
    if r.issued_sets != anticipated:
        return Verdict.FAIL, f"vacuity: anticipated outcomes {anticipated} reached {r.issued_sets}"
    return Verdict.PASS, ""


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
        v, detail = mc_verdict(r, set(dict(sc.expect)))
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


def publish(path: str, text: str) -> None:
    """Write a report atomically: a temp file in the same directory, flushed
    and fsynced, then renamed over the target. An interruption leaves either
    the previous file or the complete new one, never a partial report."""
    import tempfile
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-report-", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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
           procs_checked: int = 0, foreign: Optional[list[str]] = None,
           digests: Optional[dict] = None, sections: tuple = ()) -> str:
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
        *([f"- Partition manifest (frozen before execution): {receipt['partition_manifest']}"]
          if receipt.get("partition_manifest") else []),
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
    if digests:
        lines += ["", "## State-set and transition digests (deterministic; exact targets for independent oracles)", "",
                  f"Encoding `{ENCODING}` (see `assurance_lab/statedigest.py`). Core domain: "
                  f"{receipt['core_domain']}. `{ENCODING}:<count>:<hex>` = count and Σ SHA-256 mod "
                  "2^256 (combinable across workers); `sorted-v1:<count>:<hex>` = SHA-256 over the sorted, "
                  "duplicate-free, length-delimited stream. Transitions are the set of (source, action, "
                  "destination) triples: distinct actions reaching the same state count separately, "
                  "stutter steps included. Edges are (source, destination) pairs, each counted once.", "",
                  "| Set | Sum digest | Sorted digest |", "|---|---|---|"]
        for name, (sum_label, sorted_label) in digests.items():
            lines.append(f"| {name} | `{sum_label}` | " + (f"`{sorted_label}`" if sorted_label else "—") + " |")
    for sec in sections:
        lines += ["", sec]
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
    run_id = secrets.token_hex(8)
    pmanifest, pkey = build_manifest(core_cfg, run_id), secrets.token_bytes(32)
    receipt = start_receipt(core_cfg, a.workers, ctx.get_start_method(),
                            "python -m assurance_lab " + " ".join(argv), manifest, pmanifest)  # BEFORE enumeration
    why = preflight(receipt, bool(a.out), a.allow_dirty, manifest)
    if why:
        print(f"refusing: {why}", file=sys.stderr)
        return 2

    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    results, timing, ir, digests = core_results(core_cfg, a.workers, ctx, pmanifest, pkey)
    t1 = time.perf_counter()
    results += mutant_results(MUTANT_CFG)
    t2 = time.perf_counter()
    results += distributed_results() + replica_mc_results(a.full)
    t3 = time.perf_counter()
    cov_results, cov_section = coverage_results()
    # Witnesses always use the headline domain: the quick domain has a single
    # semantic version, so role revision (I_5) is unreachable there.
    wit_results, wit_section = witness_results(FULL_CORE)
    mm_results, mm_section = metamorphic_results()
    results += cov_results + wit_results + mm_results
    timing["coverage_witness_metamorphic_s"] = time.perf_counter() - t3
    procs_checked, foreign = origin_check(ir.origins)
    drifted = source_drifted(receipt)

    meta = {
        "started_utc": f"{started:%Y-%m-%d %H:%M:%S}",
        "run_id (partition evidence binding)": run_id,
        "python": platform.python_version(),
        "interpreter": interpreter_isolation(),
        **timing,
        "core_total_s": t1 - t0,
        "mutants_s": t2 - t1,
        "distributed_s": t3 - t2,
    }
    for r in results:
        print(f"{r.verdict.value:8} {r.obligation}  {r.label()}")
    for name, (sum_label, sorted_label) in digests.items():
        print(f"DIGEST   {name}  {sum_label}" + (f"  {sorted_label}" if sorted_label else ""))
    if drifted:
        print("ERROR: executed assurance_lab/ source changed during the run", file=sys.stderr)
    if foreign:
        print(f"ERROR: campaign code imported from outside the executed tree: {foreign}",
              file=sys.stderr)
    if a.out:
        publish(a.out, render(results, receipt, meta, drifted, procs_checked, foreign, digests,
                              (wit_section, domain_adequacy(core_cfg), cov_section, mm_section,
                               partition_section(ir))))
    if not provenance_valid(drifted, foreign, receipt["execution"], bool(a.out)):
        if receipt["execution"] != "snapshot" and not (drifted or foreign):
            print("provenance UNBOUND: in-place reports are not accepted", file=sys.stderr)
        return 3
    return 0 if authorize(results, [r.obligation for r in results]) else 1


if __name__ == "__main__":
    sys.exit(main())
