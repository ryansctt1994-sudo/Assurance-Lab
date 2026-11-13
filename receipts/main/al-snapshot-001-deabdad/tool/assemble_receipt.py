"""Assemble the AL-SNAPSHOT-001 receipt from raw run outputs.

  assemble_receipt.py WORK_DIR REPO OUT_DIR

Copies the outputs unchanged into OUT_DIR, computes every gate from those
copies (no number is typed by hand), writes receipt.json, then seals the
directory: output-hashes.sha256 (``sha256sum -c`` format) over every file
except itself and commitment.json, and commitment.json holding the SHA-256 of
output-hashes.sha256. The receipt is UNSIGNED; commitment.json names the
exact bytes a signer would sign and the verification command.

Exit 0 only if every gate passes."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAMESPACE = "assurance-lab-receipt"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def summary_line(log: str) -> str:
    lines = [ln for ln in log.splitlines() if re.search(r"\b(passed|failed|error|errors|collected)\b", ln)]
    return lines[-1].strip("= ").strip() if lines else ""


def main(work: str, repo: str, out: str) -> int:
    W, O = Path(work), Path(out)
    if O.exists():
        raise SystemExit(f"refusing: {O} exists (a receipt directory is written once)")
    base, fix, dev = (W / "scope/revs.txt").read_text().split()  # written as: base fix dev
    published = git(repo, "rev-parse", "c175fbd")
    first_fail = git(repo, "rev-parse", "dbdbed7")
    tree = {c: git(repo, "rev-parse", f"{c}^{{tree}}") for c in (fix, base, published, first_fail)}
    code = {c: git(repo, "rev-parse", f"{c}:assurance_lab") for c in (fix, base, published)}

    copies = {
        "campaign/campaign-report.md": "campaign-deabdad.md",
        "campaign/campaign.log": "campaign.log",
        "campaign/campaign.exit": "campaign.exit",
        "campaign/baseline-report-ba10a0c.md": "baseline-ba10a0c-RESULTS.md",
        "campaign/claims-vs-baseline.txt": "campaign-vs-baseline.txt",
        "campaign/claims-vs-baseline.exit": "campaign-vs-baseline.exit",
        "regression/failing-regression-at-dbdbed7.log": "regression-at-dbdbed7/failing-regression-at-dbdbed7.log",
        "regression/failing-regression-at-dbdbed7.exit": "regression-at-dbdbed7/failing-regression-at-dbdbed7.exit",
        "regression/failing-regression-at-dbdbed7.run.log": "regression-at-dbdbed7/run.log",
        "regression/before-repair-tests-on-published-code.log": "tests-work/before-regression.log",
        "regression/before-repair-tests-on-published-code.exit": "tests-work/before-regression.exit",
        "regression/after-repair-tests-on-repair-code.log": "tests-work/after-regression.log",
        "regression/after-repair-tests-on-repair-code.exit": "tests-work/after-regression.exit",
        "regression/inventory-base.log": "tests-work/inventory-base.log",
        "regression/inventory-base.exit": "tests-work/inventory-base.exit",
        "regression/inventory-repair.log": "tests-work/inventory-fix.log",
        "regression/inventory-repair.exit": "tests-work/inventory-fix.exit",
        "regression/trees.txt": "tests-work/trees.txt",
        "regression/run.log": "tests-work/run.log",
        "full-suite/full-suite-in-clone.log": "clone-work/full-suite-clone.log",
        "full-suite/full-suite-in-clone.exit": "clone-work/full-suite-clone.exit",
        "full-suite/run.log": "clone-work/run.log",
        "invalid-runs/full-suite-in-archive-export.log": "tests-work/full-suite.log",
        "invalid-runs/full-suite-in-archive-export.exit": "tests-work/full-suite.exit",
        "historical/first-failing-regression-2026-11-09.log": "before-regression.log",
        "historical/first-failing-regression-2026-11-09.exit": "before-regression.exit",
        "scope/scope.json": "scope/run-I.json",
        "scope/scope-summary.md": "scope/run-I.md",
        "tool/replay_and_scope.py": "tool/replay_and_scope.py",
        "tool/run_tests.sh": "tests-work/run_tests.sh.executed",
        "tool/run_full_in_clone.sh": "tool/run_full_in_clone.sh",
        "tool/run_regression_at.sh": "tool/run_regression_at.sh",
        "tool/compare_claims.py": "tool/compare_claims.py",
        "tool/assemble_receipt.py": "tool/assemble_receipt.py",
        "tool/assembler_controls.sh": "tool/assembler_controls.sh",
        "controls/assembler-negative-controls.txt": "assembler-controls/assembler-negative-controls.txt",
    }
    for dst, src in copies.items():
        (O / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(W / src, O / dst)
    rd = lambda p: (O / p).read_text(encoding="utf-8")  # noqa: E731
    ex = lambda p: rd(p).strip().removeprefix("exit=")  # noqa: E731

    # determinism of the scope measurement: three runs, byte-identical
    det = {n: sha(W / "scope" / n) for n in ("run-I.json", "run-seed0.json", "run-seed4242.json",
                                               "run-I.md", "run-seed0.md", "run-seed4242.md")}
    (O / "scope/determinism.json").write_text(json.dumps({
        "runs": {"run-I": "python3 -I -S -B (isolated)", "run-seed0": "env -i PYTHONHASHSEED=0 python3 -S -B",
                 "run-seed4242": "env -i PYTHONHASHSEED=4242 python3 -S -B"},
        "sha256": det}, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    gates = []

    def gate(gid, statement, ok, evidence, detail=""):
        gates.append({"id": gid, "statement": statement, "result": "PASS" if ok else "FAIL",
                      "evidence": evidence, **({"detail": detail} if detail else {})})

    rep = rd("campaign/campaign-report.md")
    rows = [ln for ln in rep.split("\n## Claims\n", 1)[1].split("\n## ", 1)[0].splitlines()
            if ln.startswith("| ") and not ln.startswith(("| Obligation", "|---"))]
    gate("G1", "campaign at the repair commit: exit 0, snapshot-bound to it, origins verified, source unchanged, "
               "32 obligations all PASS",
         ex("campaign/campaign.exit") == "0" and f"Commit: `{fix}`" in rep
         and f"Provenance verdict: **BOUND** to commit `{fix[:12]}`" in rep
         and "Snapshot bytes = git objects: **yes**" in rep and "outside executed tree: **none**" in rep
         and "Executed source re-hashed at end: **unchanged**" in rep
         and len(rows) == 32 and all("`PASS[" in r for r in rows),
         ["campaign/campaign-report.md", "campaign/campaign.exit"], f"{len(rows)} obligations")
    recomputed = subprocess.run([sys.executable, "-I", str(O / "tool/compare_claims.py"),
                                 str(O / "campaign/baseline-report-ba10a0c.md"), str(O / "campaign/campaign-report.md")],
                                capture_output=True, text=True)
    gate("G2", "its 32 claims and its residual-risk section are identical to the published baseline report "
               "(ba10a0c:docs/RESULTS.md, the c175fbd results); recomputed here, equal to the recorded comparison",
         recomputed.returncode == 0 and recomputed.stdout == rd("campaign/claims-vs-baseline.txt")
         and ex("campaign/claims-vs-baseline.exit") == "0" and "VERDICT: IDENTICAL CLAIMS" in recomputed.stdout
         and sha(O / "campaign/baseline-report-ba10a0c.md") == hashlib.sha256(
             subprocess.run(["git", "show", f"{base}:docs/RESULTS.md"], cwd=repo, capture_output=True,
                            check=True).stdout).hexdigest(),
         ["campaign/claims-vs-baseline.txt", "campaign/baseline-report-ba10a0c.md"])

    def run_ok(log, exitf, want_exit, want_summary, sites=()):
        lg = rd(log)
        return (ex(exitf) == want_exit and summary_line(lg).startswith(want_summary)
                and all(s in lg for s in sites)), summary_line(lg)

    sites = ("assurance_lab/distributed/replica.py:221: KeyError", "assurance_lab/distributed/replica.py:245: KeyError")
    ok, s = run_ok("regression/failing-regression-at-dbdbed7.log", "regression/failing-regression-at-dbdbed7.exit",
                   "1", "4 failed, 1 passed", sites)
    line = rd("regression/failing-regression-at-dbdbed7.run.log")
    ok &= f"expected-tree={tree[first_fail]} tree-before={tree[first_fail]} tree-after={tree[first_fail]}" in line
    gate("G3", "the regression as first committed (dbdbed7, before any fix) fails on its own code, at both "
               "published KeyError sites", ok, ["regression/failing-regression-at-dbdbed7.log"], s)
    ok, s = run_ok("regression/before-repair-tests-on-published-code.log",
                   "regression/before-repair-tests-on-published-code.exit", "1", "7 failed, 1 passed", sites)
    gate("G4", "the repair's 8 regression tests on the published code: 7 fail (both KeyError sites reproduced), "
               "1 passes (genuine snapshots are accepted there too)", ok,
         ["regression/before-repair-tests-on-published-code.log"], s)
    ok, s = run_ok("regression/after-repair-tests-on-repair-code.log",
                   "regression/after-repair-tests-on-repair-code.exit", "0", "8 passed")
    gate("G5", "the same 8 tests on the repair code all pass", ok, ["regression/after-repair-tests-on-repair-code.log"], s)

    ids = lambda p: {ln for ln in rd(p).splitlines() if "::" in ln}  # noqa: E731
    b_ids, f_ids = ids("regression/inventory-base.log"), ids("regression/inventory-repair.log")
    added, removed = sorted(f_ids - b_ids), sorted(b_ids - f_ids)
    gate("G6", "test inventory: the repair adds exactly the 8 regression tests and removes or renames none",
         len(b_ids) == 126 and len(f_ids) == 134 and not removed and len(added) == 8
         and all(a.startswith("tests/test_al_snapshot_001.py::") for a in added),
         ["regression/inventory-base.log", "regression/inventory-repair.log"],
         f"base {len(b_ids)}, repair {len(f_ids)}, added {len(added)}, removed {len(removed)}")

    full = rd("full-suite/full-suite-in-clone.log")
    clone_log = rd("full-suite/run.log")
    clean = f"head={fix} tree={tree[fix]} status=[]"
    s = summary_line(full)
    gate("G7", "full suite at the repair commit, in a fresh clone: exit 0, 134 passed, nothing failed or errored; "
               "the clone was clean and at the commit's tree before and after",
         ex("full-suite/full-suite-in-clone.exit") == "0" and s.startswith("134 passed")
         and " failed" not in s and "error" not in s and clone_log.count(clean) == 2,
         ["full-suite/full-suite-in-clone.log", "full-suite/run.log"], s)

    trees_ok = True
    for ln in rd("regression/trees.txt").splitlines():
        name, want, before, after = ln.split()
        trees_ok &= want == before == after
    # the before-run's tree: the base tree plus exactly the repair's regression file, from git objects
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "idx")}
        run = lambda *a: subprocess.run(["git", *a], cwd=repo, env=env, capture_output=True, text=True,  # noqa: E731
                                        check=True).stdout.strip()
        run("read-tree", base)
        blob = git(repo, "rev-parse", f"{fix}:tests/test_al_snapshot_001.py")
        run("update-index", "--add", "--cacheinfo", f"100644,{blob},tests/test_al_snapshot_001.py")
        before_tree = run("write-tree")
    exp = {"before-regression": before_tree, "after-regression": tree[fix], "inventory-base": tree[base],
           "inventory-fix": tree[fix], "full-suite": tree[fix]}
    for ln in rd("regression/trees.txt").splitlines():
        name, want, *_ = ln.split()
        if exp.get(name):
            trees_ok &= want == exp[name]
    gate("G8", "every test run's tested tree equals its expected tree, before and after the run",
         trees_ok, ["regression/trees.txt", "regression/run.log"])

    sc = json.loads(rd("scope/scope.json"))
    fixture = json.loads(subprocess.run(["git", "show", f"{dev}:tests/fixtures/snapshot_counterexamples.json"],
                                        cwd=repo, capture_output=True, check=True).stdout)
    hist = {ce["id"]: ce["historical_failures"]["c175fbd"] for ce in fixture["counterexamples"]}
    ok = all(sc["code_under_test"][lbl]["sha256"] == hashlib.sha256(subprocess.run(
        ["git", "show", f"{c}:assurance_lab/distributed/replica.py"], cwd=repo, capture_output=True,
        check=True).stdout).hexdigest() for lbl, c in (("published-main", base), ("repair", fix), ("dev-lineage", dev)))
    for ce in sc["counterexamples"]:
        pm, rp = ce["by_revision"]["published-main"], ce["by_revision"]["repair"]
        h = pm["bypass_head"]["L"]
        ok &= (h["outcome"] == "raises" and h["exception"] == hist[ce["id"]]["exception"]
               and h["message"] == hist[ce["id"]]["message"] and h["line"] == hist[ce["id"]]["line"]
               and h["source_line"] == hist[ce["id"]]["source_line"])
        ok &= rp["restore"] == "refused" and rp["refusal"]["status"] == "INVALID_SNAPSHOT"
        ok &= rp["bypass_head"]["L"]["outcome"] == "UNKNOWN"
    gate("G9", "retained counterexamples CE-1, CE-2: on the published code head() raises exactly the recorded "
               "KeyErrors (c175fbd record in the dev fixture); on the repair, restoring them is refused "
               "(INVALID_SNAPSHOT) and with validation bypassed head() answers UNKNOWN", ok, ["scope/scope.json"])
    rep_prod = {}
    for e in sc["scope"].values():
        for k, v in e["production"]["repair"].items():
            rep_prod[k] = rep_prod.get(k, 0) + v
    n_damaged = sum(e["damaged_snapshots"] for e in sc["scope"].values())
    gate("G10", f"over all {n_damaged} damaged snapshots, head() on the repair code raises nowhere "
                "(production path or validation bypassed)",
         not sc["head_raise_sites_bypass"]["repair"] and not any("head raises" in k for k in rep_prod),
         ["scope/scope.json"])
    g = sc["genuine"]
    gate("G11", "zero false refusals: every one of the 164 genuine snapshots restores on the repair code with "
                "heads equal to a live replica's",
         g["snapshots"] == 164 and g["by_revision"]["repair"] == {"accepted; heads equal the live replica's": 164},
         ["scope/scope.json"])
    gate("G12", "the scope measurement is deterministic: three runs (isolated; two hash seeds) byte-identical",
         len({det["run-I.json"], det["run-seed0.json"], det["run-seed4242.json"]}) == 1
         and len({det["run-I.md"], det["run-seed0.md"], det["run-seed4242.md"]}) == 1, ["scope/determinism.json"])

    receipt = {
        "schema": "al-snapshot-001/receipt/v1",
        "finding": "AL-SNAPSHOT-001: head() raised KeyError on a replica snapshot with dangling references",
        "subject": {
            "repair_commit": fix, "repair_tree": tree[fix], "repair_code_tree": code[fix],
            "base_commit": base, "base_tree": tree[base], "base_code_tree": code[base],
            "published_baseline_commit": published, "published_baseline_code_tree": code[published],
            "base_code_equals_published_baseline_code": code[base] == code[published],
            "first_failing_regression_commit": first_fail,
        },
        "clock": "execution times are this machine's clock; no trusted timestamp exists for any of them",
        "gates": gates,
        "results": {
            "damaged_snapshots": n_damaged,
            "repair_production_outcomes": dict(sorted(rep_prod.items())),
            "note": "outcomes per damage class and revision, with the published code and the development lineage "
                    "for comparison: scope/scope-summary.md (generated from scope/scope.json)",
        },
        "invalid_runs": [{
            "files": ["invalid-runs/full-suite-in-archive-export.log", "invalid-runs/full-suite-in-archive-export.exit"],
            "outcome": summary_line(rd("invalid-runs/full-suite-in-archive-export.log")),
            "reason": "run in a `git archive` export, which has no git repository; the five provenance tests that "
                      "need one error at setup (`git read-tree HEAD`, exit 128). An environment defect of the "
                      "runner, not a result about the code; superseded by G7, kept unchanged",
        }],
        "historical": [{
            "files": ["historical/first-failing-regression-2026-11-09.log",
                      "historical/first-failing-regression-2026-11-09.exit"],
            "note": "the first run of the failing regression, in the repair worktree at dbdbed7; that run did not "
                    "record its tested tree. G3 re-runs it with the tree recorded",
        }],
        "custody": {
            "signed": False,
            "independent_witness": "none: every run here was made by the author's own agent in the author's container",
            "trusted_timestamp": None,
        },
    }
    (O / "receipt.json").write_text(json.dumps(receipt, indent=1, sort_keys=False) + "\n", encoding="utf-8")

    files = sorted(p.relative_to(O).as_posix() for p in O.rglob("*") if p.is_file()
                   and p.name not in ("output-hashes.sha256", "commitment.json"))
    (O / "output-hashes.sha256").write_text("".join(f"{sha(O / f)}  {f}\n" for f in files), encoding="utf-8")
    c = sha(O / "output-hashes.sha256")
    (O / "commitment.json").write_text(json.dumps({
        "output_hashes_sha256": c,
        "signed": False,
        "status": "UNSIGNED: no signature exists for this receipt",
        "bytes_to_sign": "output-hashes.sha256 (exactly the file's bytes; its SHA-256 is output_hashes_sha256)",
        "sign_command": f"ssh-keygen -Y sign -f <dedicated Ed25519 key, held outside this repository> "
                        f"-n {NAMESPACE} output-hashes.sha256",
        "verify_command": f"ssh-keygen -Y verify -f <allowed_signers> -I <signer identity> -n {NAMESPACE} "
                          f"-s output-hashes.sha256.sig < output-hashes.sha256",
        "check_command": "sha256sum -c output-hashes.sha256",
    }, indent=1) + "\n", encoding="utf-8")
    failed = [g["id"] for g in gates if g["result"] != "PASS"]
    print(f"{len(gates) - len(failed)}/{len(gates)} gates PASS; commitment {c}")
    for g_ in gates:
        print(f"  {g_['id']:4s} {g_['result']}  {g_['statement'][:100]}" + (f"  [{g_['detail']}]" if g_.get("detail") else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
