"""Acceptance receipt for matrix v2 and the replica snapshot-integrity repair.

Development lineage only (PR #2). The published main baseline (c175fbd) has
the same head() defect and is neither repaired nor re-validated here.

    python scripts/v2_acceptance_receipt.py --out receipts/dev/v2-acceptance-<commit12> \
        --report docs/reports/dev-<commit12>.md \
        --historical "<path>=<label>" ...

Refuses to run unless tracked files are clean, and records the exact commit.
Every measured quantity is recomputed here from the committed code. Files
passed with --historical are stored as labeled historical artifacts, never
as evidence for this revision. The output directory is sealed like an R1
reproduction: output-hashes.sha256 (sha256sum -c) and a commitment to it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assurance_lab.distributed import mc  # noqa: E402
from assurance_lab.distributed import metamorphic as mm  # noqa: E402
from assurance_lab.distributed import snapshot_json as sj  # noqa: E402
from assurance_lab.distributed.replica import CorruptSnapshot, Replica  # noqa: E402
from assurance_lab.provenance import source_digest  # noqa: E402

SCOPE = ("development lineage (PR #2, branch ccr-305e61c7-7vpbq7). Not main: the published baseline c175fbd "
         "has the same head() KeyError defect (reproduced below) and is not repaired or re-validated here.")


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1, default=str)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Receipt:
    def __init__(self, out: Path):
        self.out, self.data, self.timing = out, {}, {}

    def file(self, name: str, text: str) -> str:
        (self.out / name).write_text(text, encoding="utf-8")
        return name

    def section(self, name: str, fn):
        t0 = time.monotonic()
        try:
            value = fn()
        except Exception as e:  # recorded, never hidden
            value = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
        self.timing[name] = round(time.monotonic() - t0, 1)
        self.data[name] = value
        self.file(f"{name}.json", canon(value) + "\n")
        return value


def source() -> dict:
    return {
        "scope": SCOPE,
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "tree_assurance_lab": git("rev-parse", "HEAD:assurance_lab"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "tracked_changes": git("status", "--porcelain", "--untracked-files=no"),
        "untracked": git("status", "--porcelain").splitlines(),
        "source_sha256_assurance_lab": source_digest(ROOT / "assurance_lab"),
        "python": sys.version.split()[0], "implementation": platform.python_implementation(),
        "platform": platform.platform(), "cpu_count": os.cpu_count(),
    }


def v1_matrix() -> dict:
    """The frozen v1 table, read from git at b535b1a: not from the copy in the package."""
    published = git("show", "b535b1a:docs/RESULTS.md")
    section = published.split("## Metamorphic relations × negative controls")[1].split("\n## ")[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
    packaged = [ln for ln in mm.PUBLISHED_V1_TABLE.splitlines() if ln.startswith("| `")]
    history = (ROOT / "docs/METAMORPHIC_MATRIX_HISTORY.md").read_text(encoding="utf-8").split("## v2")[0]
    documented = [ln for ln in history.splitlines() if ln.startswith("| `")]
    return {
        "source": "git show b535b1a:docs/RESULTS.md, section 'Metamorphic relations × negative controls'",
        "blob": git("rev-parse", "b535b1a:docs/RESULTS.md"),
        "report_sha256": sha256(published.encode()),
        "rows": rows, "rows_sha256": sha256("\n".join(rows).encode()),
        "packaged_copy_identical": packaged == rows,
        "history_doc_copy_identical": documented == rows,
        "categories": {f"{r}/{f}": v for (r, f), v in sorted(mm.published_v1_categories("\n".join(rows)).items())},
    }


def schema() -> dict:
    insts = mm.instances()
    return {
        "quantities": {
            "eligible": "instance satisfies the mutant's frozen predicate over the relation's alphabet, and the "
                        "relation performs the operation the mutated code is in",
            "deviating": "eligible, and on some execution the relation performs the mutant and the correct replica "
                         "differ in lockstep (state, head for head-time mutants, or a refused restore)",
            "detecting": "deviating, and the relation is violated on that instance",
            "exposure_rate": "deviating / eligible", "detection_breadth": "detecting / deviating",
            "anomalies": "deviation on an ineligible instance; detection without deviation; errored instance",
        },
        "categories": mm.CATEGORY_LABEL,
        "flags": list(mm.FLAGS), "relations": list(mm.RELATIONS), "operations": {k: sorted(v) for k, v in mm.OPERATIONS.items()},
        "head_time_flags": list(mm.HEAD_TIME_FLAGS),
        "execution_config": mm.EXECUTION_CONFIG,
        "eligibility": {flag: {"operation": op, "requires": what,
                               "eligible": {rel: [i.name for i in insts if mm.eligible(rel, flag, i)] for rel in mm.RELATIONS}}
                        for flag, (op, what, _pred) in mm.ELIGIBILITY.items()},
        "fixtures": {"v2": list(mm.KINDS), "v1": list(mm.KINDS_V1)},
        "alphabet": {
            "serialization_universe": [mm.label(e) for e in mm.event_universe(insts)],
            "boundary_events": {name: sorted(mm.BOUNDARY_EXPECT[name]) for name, _e in mm.named_boundary_events()},
            "boundary_sequences": {name: [mm.label(e) for e in seq] for name, seq in mm.boundary_sequences()},
            "rejection_rules": list(mm.REJECTION_RULES),
        },
        "cache_keys": {"v2": mm.matrix_inputs_fingerprint(mm.KINDS), "v1": mm.matrix_inputs_fingerprint(mm.KINDS_V1)},
        "code_fingerprint": mm.code_fingerprint(),
    }


def matrices() -> dict:
    out = {}
    for name, kinds in (("v2", mm.KINDS), ("v1_fixtures", mm.KINDS_V1)):
        t0 = time.monotonic()
        fresh = mm.mutant_matrix(kinds, cache=False)
        t1 = time.monotonic()
        cached_first = mm.mutant_matrix(kinds)  # computes and stores under the fingerprint
        cached_hit = mm.mutant_matrix(kinds)  # served from the cache
        out[name] = {
            "uncached_seconds": round(t1 - t0, 1),
            "canonical": mm.matrix_canonical(fresh),
            "cached_identical_to_uncached": mm.matrix_canonical(fresh) == mm.matrix_canonical(cached_first)
            == mm.matrix_canonical(cached_hit),
            "anomalies_uncached": mm.matrix_anomalies(fresh), "anomalies_cached": mm.matrix_anomalies(cached_hit),
            "markdown": mm.matrix_markdown(fresh),
            "mutation_score": list(mm.mutation_score(fresh)),
        }
        out[name]["canonical_sha256"] = sha256(canon(out[name]["canonical"]).encode())
    m = mm.mutant_matrix()
    v1 = mm.published_v1_categories(mm.PUBLISHED_V1_TABLE)
    changed = mm.v1_to_v2_changes(v1, m)
    out["v1_to_v2"] = {
        "changed": {f"{r}/{f}": list(v) for (r, f), v in sorted(changed.items())},
        "explained": {f"{r}/{f}": {"v1": a, "v2": b, "reason": why} for (r, f), (a, b, why) in sorted(mm.V1_TO_V2_CHANGES.items())},
        "every_change_explained_and_no_stale_entry": changed == {k: (a, b) for k, (a, b, _w) in mm.V1_TO_V2_CHANGES.items()},
    }
    partial = {f"{r}/{f}": {"eligible": len(c.eligible), "deviating": len(c.deviating), "detecting": len(c.detecting),
                            "undetected_deviating_instances": sorted(set(c.deviating) - set(c.detecting))}
               for (r, f), c in sorted(m.items()) if c.deviating and len(c.detecting) < len(c.deviating)}
    out["deviations_not_detected"] = partial  # kept visible: exposure the relations cannot see
    return out


_PROBE = r"""
import hashlib, json
from assurance_lab.distributed import metamorphic as mm
blob = {"v2": mm.matrix_canonical(mm.mutant_matrix(cache=False)),
        "v1": mm.matrix_canonical(mm.mutant_matrix(mm.KINDS_V1, cache=False)),
        "corruptions": mm.corruption_table(), "alphabet": mm.alphabet_exercise(),
        "keys": [mm.matrix_inputs_fingerprint(mm.KINDS), mm.matrix_inputs_fingerprint(mm.KINDS_V1)]}
print(hashlib.sha256(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest())
"""


def determinism() -> dict:
    digests = {}
    for seed in ("1", "2"):
        p = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)})
        digests[f"PYTHONHASHSEED={seed}"] = p.stdout.strip() if p.returncode == 0 else f"exit {p.returncode}: {p.stderr[-500:]}"
    return {"digests": digests, "identical": len(set(digests.values())) == 1,
            "covers": "canonical v2 and v1-fixture matrices (uncached), corruption table, alphabet exercise, cache keys"}


def corruptions() -> dict:
    rows = mm.corruption_table()
    for r in rows:
        if r["class"] in ("inconsistent", "malformed"):
            r["credited_control"] = {
                "production": "thaw validator (INVALID_SNAPSHOT)",
                "bypassed_diagnostic_only": "head() integrity guard (UNKNOWN); not evidence that the boundary works"}
        else:
            r["credited_control"] = {
                "production": "none: a consistent omission passes the boundary by construction (integrity is not authenticity)",
                "test_oracle_only": "serialization relation against a never-serialized reference: " + ", ".join(
                    f"{k} {v}" for k, v in sorted((r['relation'] or {}).items()))}
    hard = [r for r in rows if r["class"] != "omission"]
    return {"rows": rows,
            "summary": {"inconsistent_or_malformed_classes": len(hard),
                        "damaged_snapshots": sum(r["infected_snapshots"] for r in hard),
                        "refused_at_thaw": sum(r["validator"]["refused"] for r in hard),
                        "other_exceptions": sum(len(r["validator"]["other_exceptions"]) for r in rows),
                        "all_deterministic": all(r["validator"]["deterministic"] for r in rows),
                        "bypassed_authority_from_damaged_state": sum(r["bypass"]["authority_from_damaged_state"] for r in hard),
                        "state_changed_by_head": sum(r["bypass"]["state_changed_by_head"] for r in rows)}}


def mc_accounting() -> dict:
    """State visits, distinct frozen snapshots and validation calls are
    different quantities; each is counted, none stands in for another."""
    scenarios = [(sc, "clean") for sc in mc.SCENARIOS]
    scenarios += [(mc.with_observer(sc, 1), "clean") for sc in mc.SCENARIOS if sc.resolvers]
    scenarios += [(sc, "mutant") for sc in mc.mutant_scenarios()]
    real_thaw, real_head = Replica.thaw.__func__, Replica.explain_head
    rows, totals, union = [], Counter(), set()
    for sc, kind in scenarios:
        calls, distinct, refused, heads = [0], set(), [], Counter()

        def thaw(cls, policy, frozen, validate=True, **flags):
            calls[0] += 1
            distinct.add(frozen)
            try:
                return real_thaw(cls, policy, frozen, validate, **flags)
            except CorruptSnapshot as e:
                refused.append(str(e))
                raise

        def head(self, lineage):
            v = real_head(self, lineage)
            heads[v.status] += 1
            return v

        Replica.thaw, Replica.explain_head = classmethod(thaw), head
        try:
            r = mc.check_scenario(sc)
        finally:
            Replica.thaw, Replica.explain_head = classmethod(real_thaw), real_head
        row = {"scenario": sc.name, "kind": kind, "replicas": len(sc.scripts), "max_dups": sc.max_dups,
               "model_checker_states_visited": r.states, "transitions": r.transitions, "quiescent": r.quiescent,
               "thaw_calls": calls[0], "distinct_snapshots_validated": len(distinct), "refused": len(refused),
               "head_calls": sum(heads.values()), "head_statuses": dict(sorted(heads.items())),
               "violation": r.violation}
        rows.append(row)
        if kind == "clean":
            union |= {(tuple(sorted(sc.policy.authorities)), f) for f in distinct}
            for k in ("model_checker_states_visited", "thaw_calls", "distinct_snapshots_validated", "refused", "head_calls"):
                totals[k] += row[k]
            totals["head_unknown"] += heads.get("UNKNOWN", 0)
            totals["clean_violations"] += r.violation is not None
    totals["distinct_snapshots_validated_union"] = len(union)
    return {"rows": rows, "clean_totals": dict(totals),
            "note": "model_checker_states_visited counts network states (all replicas plus in-flight messages); "
                    "distinct_snapshots_validated counts distinct single-replica frozen states thawed through the "
                    "validator, per scenario (the total sums them; _union counts each (policy, snapshot) once "
                    "across scenarios); thaw_calls counts validations. The earlier '~31k states' wording "
                    "(0fe2c2b) was the 2-replica network-state count, not a snapshot count."}


def valid_corpus() -> dict:
    corpus = mm.valid_state_corpus()
    n, problems, statuses = 0, [], Counter()
    for inst in mm.instances():
        for pre in mm.serialization_prefixes(inst):
            rep = mm.run(inst, list(pre), {})
            n += 1
            if rep.integrity_problems():
                problems.append(f"{inst.name}/{len(pre)}: {rep.integrity_problems()}")
            try:
                Replica.thaw(inst.policy, rep.freeze())
            except CorruptSnapshot as e:
                problems.append(f"{inst.name}/{len(pre)}: refused: {e}")
            for lin in inst.lineages:
                statuses[rep.explain_head(lin).status] += 1
    return {"serialized_states": n, "false_rejections_or_problems": problems + corpus["problems"],
            "head_statuses": dict(sorted(statuses.items())),
            "alphabet_corpus": {k: corpus[k] for k in ("deliveries", "distinct_states", "problem_count",
                                                       "incomplete_but_valid", "domain", "deduplication", "not_claimed")}}


def counterexamples() -> dict:
    fixture = json.loads((ROOT / "tests/fixtures/snapshot_counterexamples.json").read_text(encoding="utf-8"))
    from assurance_lab.distributed import replica
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_snapshot_counterexamples as gen
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        mods = {rev: sj.load_replica_at(rev, ROOT, tmp) for rev in gen.REVISIONS}
        for ce in fixture["counterexamples"]:
            inst, _prefix, damaged = mm.counterexample_snapshot(ce)
            row = {"id": ce["id"], "site": ce["site"], "recipe": {k: ce[k] for k in ("instance", "order", "prefix", "corruption")},
                   "snapshot_sha256": sha256(canon(ce["snapshot"]).encode()),
                   "fixture_matches_recipe": sj.encode(damaged) == ce["snapshot"],
                   "recorded_historical_failures": ce["historical_failures"],
                   "replayed_now": {rev: gen.failure_at(m, inst.policy, ce["snapshot"]) for rev, m in mods.items()}}
            try:
                Replica.thaw(inst.policy, sj.decode(ce["snapshot"], replica))
                row["after_repair_thaw"] = {"outcome": "restored (FAILURE)"}
            except CorruptSnapshot as e:
                row["after_repair_thaw"] = {"outcome": e.status, "problems": list(e.problems)}
            v = Replica.thaw(inst.policy, sj.decode(ce["snapshot"], replica), validate=False).explain_head(ce["lineage"])
            row["after_repair_bypassed_head"] = {"status": v.status, "value": v.value, "reason": v.reason}
            row["regression_tests"] = [
                f"tests/test_snapshot_counterexamples.py::test_counterexample_still_crashes_the_unrepaired_code[{rev}-{ce['id']}]"
                for rev in gen.REVISIONS] + [
                f"tests/test_snapshot_counterexamples.py::{t}[{ce['id']}]" for t in (
                    "test_counterexample_is_derivable_from_its_recipe", "test_repaired_restore_refuses_it_deterministically",
                    "test_bypassed_boundary_gives_unknown_not_conflict_and_changes_nothing")]
            out.append(row)
    return {"counterexamples": out,
            "historical_defect": "confirmed at b535b1a (development) and c175fbd (published main); repaired on the "
                                 "development lineage only; not applied to main"}


def dispositions(data) -> list:
    c, mx = data["corruptions"]["summary"], data["matrices"]
    v2 = mx["v2"]["canonical"]
    cells = lambda cat: sorted(k for k, v in v2.items() if v["category"] == cat)  # noqa: E731
    return [
        {"failure": "inconsistent or malformed snapshot", "disposition": "INVALID_SNAPSHOT (restore refused)",
         "assigned_by": "thaw validator, the production boundary",
         "evidence": f"{c['refused_at_thaw']}/{c['damaged_snapshots']} refused, deterministic={c['all_deterministic']}"},
        {"failure": "damaged state with the boundary bypassed (diagnostic only)", "disposition": "UNKNOWN, never CONFLICT or a record",
         "assigned_by": "head() integrity guard", "evidence": f"authority from damaged state: {c['bypassed_authority_from_damaged_state']}"},
        {"failure": "genuine fork or authority dispute on a valid state", "disposition": "CONFLICT, with its reason",
         "assigned_by": "head() derivation", "evidence": f"valid corpus statuses {data['valid_corpus']['head_statuses']}"},
        {"failure": "consistent omission (missing evidence, rolled-back snapshot)", "disposition": "accepted; may restore a stale head (residual risk)",
         "assigned_by": "none in production (integrity is not authenticity); exact-state comparison against a reference in tests",
         "evidence": "corruptions.rows, class omission"},
        {"failure": "a replica snapshot of an explored state refused at thaw", "disposition":
         "INVALID_SNAPSHOT violation: FAIL, with the offending snapshot, the reasons and the trace kept",
         "assigned_by": "replica model checker (check_scenario) and campaign (mc_verdict)",
         "evidence": "test_a_refused_snapshot_is_a_recorded_failure_of_the_model_checker; clean scenarios refused: "
                     f"{data['mc_accounting']['clean_totals'].get('refused', 'n/a')}"},
        {"failure": "causally incomplete but genuine state (deferred, deferred forever, predecessor never arrives)",
         "disposition": "accepted: not an integrity failure", "assigned_by": "integrity check",
         "evidence": data["valid_corpus"].get("alphabet_corpus")},
        {"failure": "UNKNOWN on a state the replica model checker explores", "disposition": "safety violation: FAIL, not inconclusive",
         "assigned_by": "replica model checker, per state and again at quiescence",
         "evidence": "tests test_unknown_fails_model_checking_*; clean scenarios: "
                     f"{data['mc_accounting']['clean_totals'].get('head_unknown', 'n/a')} UNKNOWN"},
        {"failure": "checker budget exhausted", "disposition": "UNKNOWN verdict (inconclusive), never PASS",
         "assigned_by": "campaign", "evidence": "replica_mc_results"},
        {"failure": "mutant exposed but not detected by a relation", "disposition": "survived (D > K), instances listed",
         "assigned_by": "matrix", "evidence": f"{len(mx['deviations_not_detected'])} cells with undetected deviations"},
        {"failure": "mutant never exposed by a relation's inputs", "disposition": "not reached (E > 0, D = 0)",
         "assigned_by": "matrix", "evidence": cells("NOT_REACHED")},
        {"failure": "mutated code unreachable by a relation", "disposition": "n/a (E = 0)",
         "assigned_by": "frozen eligibility predicate", "evidence": cells("NOT_APPLICABLE")},
        {"failure": "rejection rule not exercised by the alphabet", "disposition": "listed as a gap",
         "assigned_by": "alphabet exercise", "evidence": data["alphabet"]["rejection_rules_not_exercised"]},
        {"failure": "reproduction evidence missing or unbound (R1)", "disposition": "INCONCLUSIVE",
         "assigned_by": "assurance-validation/reconcile.py", "evidence": "assurance-validation/protocol/acceptance-criteria.md"},
    ]


def run_tests(r: Receipt) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider"], cwd=ROOT,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    r.file("pytest.stdout.log", p.stdout)
    r.file("pytest.stderr.log", p.stderr)
    lines = p.stdout.splitlines()
    import re
    # "tests/x.py::test_y[2 records, 2 principals] PASSED   [ 12%]": ids may hold spaces, so the
    # outcome token is matched before the progress marker, never found by splitting on whitespace
    # (the 3c6fad1 receipt's D-1 defect).
    line_re = re.compile(r"^(?P<id>\S.*::.*?) (?P<outcome>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s+\[\s*\d+%\]$")
    per_test = {}
    for ln in lines:
        m = line_re.match(ln)
        if m:
            per_test[m["id"]] = m["outcome"]
    r.file("pytest-outcomes.json", canon(per_test) + "\n")
    summary = next((ln for ln in reversed(lines) if re.search(r"\d+ (passed|failed|error)", ln)), "")
    reported = {k.lower(): int(n) for n, k in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)", summary)}
    reported = {("error" if k.startswith("error") else k): n for k, n in reported.items()}
    counted = Counter({"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "ERROR": "error",
                       "XFAIL": "xfailed", "XPASS": "xpassed"}[v] for v in per_test.values())
    return {"command": "python -m pytest -v -p no:cacheprovider", "exit_code": p.returncode, "per_test_outcomes": per_test,
            "seconds": round(time.monotonic() - t0, 1), "summary": summary,
            "outcomes": dict(Counter(per_test.values())), "summary_counts": reported,
            "index_matches_summary": dict(counted) == reported, "per_test": "pytest-outcomes.json",
            "stdout": "pytest.stdout.log", "stderr": "pytest.stderr.log"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", help="the snapshot-bound campaign report for this commit")
    ap.add_argument("--historical", action="append", default=[], help="PATH=LABEL, stored as a labeled historical artifact")
    ap.add_argument("--skip-tests", action="store_true", help="debugging only: the receipt then says so")
    a = ap.parse_args(argv)
    if git("status", "--porcelain", "--untracked-files=no"):
        ap.error("tracked files are modified: a receipt must name a clean committed revision")
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        ap.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    r = Receipt(out)
    started = now()
    r.section("source", source)
    r.section("v1_matrix", v1_matrix)
    r.section("schema", schema)
    r.section("matrices", matrices)
    r.section("determinism", determinism)
    r.section("corruptions", corruptions)
    r.section("alphabet", mm.alphabet_exercise)
    r.section("mc_accounting", mc_accounting)
    r.section("valid_corpus", valid_corpus)
    r.section("counterexamples", counterexamples)
    r.section("dispositions", lambda: dispositions(r.data))
    r.section("tests", (lambda: {"skipped": True}) if a.skip_tests else (lambda: run_tests(r)))
    if a.report:
        text = Path(a.report).read_text(encoding="utf-8")
        name = r.file("campaign-report.md", text)
        r.section("campaign", lambda: {
            "path": a.report, "stored_as": name, "sha256": sha256(text.encode()),
            "commit_line": next((ln for ln in text.splitlines() if ln.startswith("- Commit:")), None),
            "provenance_verdict": next((ln for ln in text.splitlines() if "Provenance verdict" in ln), None),
            "gate": next((ln for ln in text.splitlines() if ln.startswith("Non-compensating gate")), None)})
    hist = []
    for spec in a.historical:
        path, label = spec.split("=", 1)
        dst = "historical-" + Path(path).name
        shutil.copy(path, out / dst)
        hist.append({"file": dst, "label": label, "sha256": sha256((out / dst).read_bytes())})
    r.section("historical_artifacts", lambda: {
        "files": hist, "note": "stored for the record only; none is evidence for the revision named in source.json"})
    finished = now()
    outcomes = r.data["tests"].get("per_test_outcomes", {})

    def passed(*names) -> bool:
        """Each named test ran in this receipt's own test run, and passed."""
        return bool(outcomes) and all(any(name in k for k in outcomes) and
                                      all(v == "PASSED" for k, v in outcomes.items() if name in k) for name in names)

    gates = {
        "snapshot_integrity": r.data["corruptions"]["summary"]["refused_at_thaw"] == r.data["corruptions"]["summary"]["damaged_snapshots"]
        and r.data["corruptions"]["summary"]["all_deterministic"] and r.data["corruptions"]["summary"]["other_exceptions"] == 0,
        "unknown_propagation": r.data["mc_accounting"]["clean_totals"].get("head_unknown") == 0
        and r.data["mc_accounting"]["clean_totals"].get("clean_violations") == 0
        and passed("test_unknown_fails_model_checking_at_an_intermediate_state",
                   "test_unknown_fails_model_checking_at_quiescence_even_without_the_per_state_check"),
        "refused_snapshot_is_a_recorded_failure": passed(
            "test_a_refused_snapshot_is_a_recorded_failure_of_the_model_checker",
            "test_only_an_exhausted_budget_is_inconclusive"),
        "read_only_queries": r.data["corruptions"]["summary"]["state_changed_by_head"] == 0
        and passed("test_head_is_read_only_on_genuine_and_damaged_states",
                   "test_bypassed_boundary_gives_unknown_not_conflict_and_changes_nothing"),
        "valid_state_preservation": not r.data["valid_corpus"]["false_rejections_or_problems"]
        and r.data["valid_corpus"]["alphabet_corpus"]["problem_count"] == 0
        and r.data["mc_accounting"]["clean_totals"].get("refused") == 0
        and passed("test_incomplete_but_valid_delivery_states_are_accepted"),
        "mutation_accounting_and_anomalies": r.data["matrices"]["v2"]["anomalies_uncached"] == []
        and r.data["matrices"]["v1_fixtures"]["anomalies_uncached"] == [],
        "corruption_attribution": all("credited_control" in x for x in r.data["corruptions"]["rows"]),
        "event_alphabet": not r.data["alphabet"]["never_delivered"] and not r.data["alphabet"]["unexpected"]
        and r.data["alphabet"]["rejection_rules_not_exercised"] == [],
        "historical_matrix": r.data["v1_matrix"]["packaged_copy_identical"] and r.data["v1_matrix"]["history_doc_copy_identical"]
        and r.data["matrices"]["v1_to_v2"]["every_change_explained_and_no_stale_entry"],
        "cache_independence": r.data["matrices"]["v2"]["cached_identical_to_uncached"]
        and r.data["matrices"]["v1_fixtures"]["cached_identical_to_uncached"]
        and passed("test_cached_and_uncached_matrices_are_identical", "test_cache_key_binds_every_input",
                   "test_stale_cache_cannot_turn_a_new_failure_into_an_old_pass"),
        "determinism": r.data["determinism"]["identical"],
        "counterexamples": all(c["after_repair_thaw"]["outcome"] == "INVALID_SNAPSHOT"
                               and c["after_repair_bypassed_head"]["status"] == "UNKNOWN"
                               and all(f.get("exception") == "KeyError" for f in c["replayed_now"].values())
                               for c in r.data["counterexamples"]["counterexamples"])
        and passed("test_snapshot_counterexamples.py"),
        "tests": (r.data["tests"].get("exit_code") == 0 and r.data["tests"].get("index_matches_summary") is True)
        if not a.skip_tests else "skipped",
    }
    summary = {"receipt_version": 1, "scope": SCOPE, "started_utc": started, "finished_utc": finished,
               "commit": r.data["source"]["commit"], "section_seconds": r.timing, "gates": gates,
               "all_gates_pass": all(v is True for v in gates.values()),
               "disposition": "evidence for the owner's decision; promotion: NONE (not decided here)",
               "sections": {k: f"{k}.json" for k in r.data}}
    r.file("receipt.json", canon(summary) + "\n")
    names = sorted(f.name for f in out.iterdir() if f.is_file() and f.name not in ("output-hashes.sha256", "commitment.json"))
    (out / "output-hashes.sha256").write_text("".join(f"{sha256((out / n).read_bytes())}  {n}\n" for n in names), encoding="utf-8")
    commitment = sha256((out / "output-hashes.sha256").read_bytes())
    (out / "commitment.json").write_text(canon({"commit": summary["commit"], "covers": "output-hashes.sha256",
                                                "commitment": commitment}) + "\n", encoding="utf-8")
    print(json.dumps({"commit": summary["commit"], "gates": gates, "commitment": commitment}, indent=1))
    return 0 if summary["all_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
