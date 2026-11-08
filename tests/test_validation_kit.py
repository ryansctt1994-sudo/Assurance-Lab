"""The R1 reproduction kit (assurance-validation/): a self-contained harness,
sealed evidence, a reveal that happens only after verification, and outcomes
that follow the frozen protocol (protocol/acceptance-criteria.md).

The end-to-end tests run the real harness against a throwaway git repository
holding a fake ``assurance_lab`` whose report is steered by ``FAKE_MODE``, so
sealing (reproduce.py) and verification (reconcile.py) are tested against
each other rather than against hand-built receipts."""

import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "assurance-validation"
FROZEN_MANIFEST = (KIT / "protocol/claim-subject-manifest.json").read_bytes()
PREREG = json.loads((KIT / "protocol/preregistration-receipt.json").read_text(encoding="utf-8"))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"kit_{name}", KIT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rp, rc = _load("reproduce"), _load("reconcile")

CLAIMS = [("Init ⇒ Inv", "PASS[induction; 3 records; 3,587,826 Inv-states]"),
          ("replica MC: fork", "PASS[bfs; 2 replicas; 3,990 states]"),
          ("replica MC: fork (+ passive 3rd replica)", "PASS[bfs; 3 replicas, ≤1 duplicated message; 24,454 states]")]

FAKE_MAIN = r'''
import argparse, hashlib, os, subprocess, sys
from pathlib import Path, PurePosixPath
ap = argparse.ArgumentParser(); ap.add_argument("--full", action="store_true"); ap.add_argument("--out")
a = ap.parse_args()
mode = os.environ.get("FAKE_MODE", "")
pkg = Path(__file__).resolve().parent
h = hashlib.sha256()
for rel, data in sorted(((p.relative_to(pkg).as_posix(), p.read_bytes()) for p in pkg.rglob("*.py")),
                        key=lambda e: PurePosixPath(e[0]).parts):
    h.update(rel.encode() + b"\0" + data + b"\0")
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
claims = CLAIMS_LITERAL
if mode == "mismatch": claims[1] = ("replica MC: fork", "PASS[bfs; 2 replicas; 3,991 states]")
if mode == "extra": claims.append(("an extra claim", "PASS[a; b; c]"))
if mode == "repeated": claims.append(claims[0])
if mode == "missing": claims.pop()
verdict = "UNBOUND" if mode == "unbound" else "BOUND"
commit = "0" * 40 if mode == "other-commit" else head
rows = "\n".join(f"| {o} | `{l}` |" for o, l in claims)
Path(a.out).write_text(
    f"# R\n\n## Provenance\n\n- Commit: `{commit}` (tree `t`)\n"
    f"- SHA-256 over executed `assurance_lab/**/*.py` at start: `{h.hexdigest()}`\n"
    f"- Provenance verdict: **{verdict}** to commit `x`\n\n"
    f"## Claims\n\n| Obligation | Verdict |\n|---|---|\n{rows}\n\n"
    f"## Run metadata (varies between runs; not part of any claim)\n\n- started_utc: now\n", encoding="utf-8")
sys.exit(1 if mode == "fail" and a.full else 0)
'''.replace("CLAIMS_LITERAL", repr(CLAIMS))


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _manifest_for(repo, commands=None):
    m = json.loads(FROZEN_MANIFEST)
    m["subject"].update(
        source_commit=_git(repo, "rev-parse", "HEAD"), source_tree=_git(repo, "rev-parse", "HEAD^{tree}"),
        source_tree_assurance_lab=_git(repo, "rev-parse", "HEAD:assurance_lab"),
        source_sha256_assurance_lab=rp.source_digest(repo / "assurance_lab"),
        expected_targets_sha256=rc.targets_digest(CLAIMS))
    m["claims"] = [{"id": f"C{i:02d}", "obligation": ob} for i, (ob, _) in enumerate(CLAIMS, 1)]
    py = ["python", "-m", "assurance_lab"]
    m["commands"] = commands or [
        {"role": "setup", "argv": ["python", "-c", "pass"]},
        {"role": "test", "argv": ["python", "-c", "print('tests ok')"]},
        {"role": "quick-campaign", "argv": py + ["--out", "results-quick.md"],
         "report": "results-quick.md", "artifact": "generated-results-quick.md"},
        {"role": "full-campaign", "argv": py + ["--full", "--out", "results-reproduction.md"],
         "report": "results-reproduction.md", "artifact": "generated-results.md"}]
    return m


@pytest.fixture(scope="module")
def fake(tmp_path_factory):
    base = tmp_path_factory.mktemp("subject")
    repo = base / "repo"
    (repo / "assurance_lab").mkdir(parents=True)
    (repo / "assurance_lab/__init__.py").write_text("")
    (repo / "assurance_lab/__main__.py").write_text(FAKE_MAIN, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "subject")
    manifest = base / "manifest.json"
    manifest.write_text(json.dumps(_manifest_for(repo)), encoding="utf-8")
    rows = "\n".join(f"| {o} | `{l}` |" for o, l in CLAIMS)
    baseline = f"# Baseline\n\n## Claims\n\n| Obligation | Verdict |\n|---|---|\n{rows}\n"
    return SimpleNamespace(repo=repo, manifest=manifest, baseline=baseline)


def reproduce(fake, out, monkeypatch, mode="", label="TEST", manifest=None, source=None):
    monkeypatch.setenv("FAKE_MODE", mode)
    return rp.main(["--out", str(out), "--operator", "test fixture", "--label", label, "--no-venv",
                    "--source", str(source or fake.repo), "--manifest", str(manifest or fake.manifest)])


def never():
    raise AssertionError("the targets were revealed before the evidence was verified")


def judge(fake, out, load_targets=None, manifest=None, **kw):
    return rc.reconcile(out, (manifest or fake.manifest).read_bytes(), PREREG,
                        load_targets or (lambda: fake.baseline), **kw)


@pytest.fixture(scope="module")
def sealed(fake, tmp_path_factory):
    """One genuine MATCH run, copied by tests that tamper with it."""
    out = tmp_path_factory.mktemp("sealed") / "run"
    mp = pytest.MonkeyPatch()
    try:
        assert reproduce(fake, out, mp) == 0
    finally:
        mp.undo()
    return out


@pytest.fixture
def copy_of(sealed, tmp_path):
    dst = tmp_path / "copy"
    shutil.copytree(sealed, dst)
    return dst


def edit_and_reseal(run_dir, name, fn):
    """Simulate a harness that recorded something else but sealed it consistently."""
    p = run_dir / name
    obj = json.loads(p.read_text(encoding="utf-8"))
    fn(obj)
    p.write_text(json.dumps(obj), encoding="utf-8")
    env = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
    cmds = json.loads((run_dir / "commands.json").read_text(encoding="utf-8"))
    rp.seal(run_dir, rp.commitment_fields(env, cmds, run_dir))


def _misdescribe(field, value):
    """Edit only the commitment's description; its digest still matches."""
    def f(d):
        c = json.loads((d / rp.COMMITMENT).read_text(encoding="utf-8"))
        c[field] = value
        (d / rp.COMMITMENT).write_text(json.dumps(c), encoding="utf-8")
    return f


# --- the kit itself ------------------------------------------------------------

def test_kit_is_self_contained_stdlib_only():
    """An operator audits two stdlib-only files, not the system under test."""
    for name in ("reproduce", "reconcile"):
        tree = ast.parse((KIT / f"{name}.py").read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert mods - {"__future__"} <= set(sys.stdlib_module_names), (name, mods)


def test_preregistration_freezes_the_protocol_and_the_harness():
    """Changing any frozen file requires re-issuing the receipt, visibly."""
    assert set(PREREG["files"]) == {"protocol/claim-subject-manifest.json", "protocol/discrepancy-rubric.md",
                                    "protocol/acceptance-criteria.md", "reproduce.py", "reconcile.py"}
    for rel, digest in PREREG["files"].items():
        assert hashlib.sha256((KIT / rel).read_bytes()).hexdigest() == digest, rel


def test_manifest_withholds_the_targets_but_commits_to_them():
    m = json.loads(FROZEN_MANIFEST)
    assert m["subject"]["source_commit"].startswith("c175fbd") and len(m["claims"]) == 32
    assert all(not ({"label", "verdict", "result"} & set(c)) for c in m["claims"])
    try:
        baseline = rc.git_targets(m["subject"])
    except Exception:
        pytest.skip("baseline commit not in this clone")
    pairs = rc.claims(baseline)
    assert len(pairs) == 32 and all(v.startswith("PASS[") for v in pairs.values())
    assert rc.targets_digest(pairs.items()) == m["subject"]["expected_targets_sha256"]
    assert [c["obligation"] for c in m["claims"]] == list(pairs)
    text = FROZEN_MANIFEST.decode()
    for label in pairs.values():  # no result value (the withheld part) appears in the package
        assert rc.label_fields(label)["result"] not in text, label


def test_independent_source_digest_matches_the_published_one(tmp_path):
    try:
        archive = subprocess.run(["git", "archive", "c175fbd", "assurance_lab"], cwd=ROOT,
                                 capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        pytest.skip("subject commit not in this clone")
    subprocess.run(["tar", "-x", "-C", str(tmp_path)], input=archive, check=True)
    want = json.loads(FROZEN_MANIFEST)["subject"]["source_sha256_assurance_lab"]
    assert rp.source_digest(tmp_path / "assurance_lab") == want


def test_label_fields_and_field_diff():
    assert rc.label_fields("PASS[m; s; r]") == {"verdict": "PASS", "method": "m", "scope": "s", "result": "r"}
    assert rc.field_diff("PASS[m; s; 1]", "FAIL[m; s; 2]") == {
        "result": {"baseline": "1", "reproduction": "2"}, "verdict": {"baseline": "PASS", "reproduction": "FAIL"}}


# --- end to end ----------------------------------------------------------------

def test_genuine_run_matches_and_the_seal_checks_with_standard_tools(fake, sealed):
    r = judge(fake, sealed)
    assert r["outcome"] == "MATCH" and (r["matched"], r["denominator"]) == (3, 3) and r["targets_revealed"]
    commitment = json.loads((sealed / rp.COMMITMENT).read_text(encoding="utf-8"))
    assert commitment["commitment"] == hashlib.sha256((sealed / rp.HASHES).read_bytes()).hexdigest()
    env = json.loads((sealed / "environment.json").read_text(encoding="utf-8"))
    assert env["checkout"]["status_porcelain"] == "" and env["source_sha256_assurance_lab"]
    if shutil.which("sha256sum"):
        assert subprocess.run(["sha256sum", "-c", rp.HASHES], cwd=sealed, capture_output=True).returncode == 0
    assert judge(fake, sealed, published=commitment["commitment"])["outcome"] == "MATCH"


@pytest.mark.parametrize("mode,outcome,detail", [
    ("mismatch", "MISMATCH", "fields"),
    ("extra", "MISMATCH", "extra"),
    ("repeated", "MISMATCH", "extra"),
    ("fail", "MISMATCH", "failed"),
    ("missing", "INCONCLUSIVE", "missing"),
    ("unbound", "INCONCLUSIVE", "not BOUND"),
    ("other-commit", "INCONCLUSIVE", "subject commit"),
])
def test_outcomes_follow_the_frozen_protocol(fake, tmp_path, monkeypatch, mode, outcome, detail):
    reproduce(fake, tmp_path / "run", monkeypatch, mode=mode)
    r = judge(fake, tmp_path / "run")
    assert r["outcome"] == outcome, r
    if detail == "fields":
        (row,) = [c for c in r["claims"] if c["result"] == "MISMATCH"]
        assert row["id"] == "C02" and row["fields"] == {
            "result": {"baseline": "3,990 states", "reproduction": "3,991 states"}}
    elif detail == "extra":
        assert len(r["extra_claims"]) == 1
    elif detail == "failed":
        assert r["failed_commands"] == ["full-campaign"] and r["matched"] == 3
    elif detail == "missing":
        assert r["missing"] == ["C03"]
    else:
        assert any(detail in x for x in r["reasons"])


def test_failed_setup_is_inconclusive_not_a_mismatch(fake, tmp_path, monkeypatch):
    m = _manifest_for(fake.repo)
    m["commands"][0]["argv"] = ["python", "-c", "raise SystemExit(1)"]
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps(m), encoding="utf-8")
    reproduce(fake, tmp_path / "run", monkeypatch, manifest=mp)
    r = judge(fake, tmp_path / "run", manifest=mp)
    assert r["outcome"] == "INCONCLUSIVE" and r["reasons"] == ["environment setup failed"]


def test_harness_error_is_sealed_and_inconclusive(fake, tmp_path, monkeypatch):
    assert reproduce(fake, tmp_path / "run", monkeypatch, source=tmp_path / "no-such-repo") == 1
    r = judge(fake, tmp_path / "run", load_targets=never)
    assert r["outcome"] == "INCONCLUSIVE" and "git clone failed" in r["reasons"][0]


def test_output_directory_must_be_new_or_empty(fake, tmp_path, monkeypatch):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "stale.log").write_text("from an earlier run")
    with pytest.raises(SystemExit):
        reproduce(fake, tmp_path / "run", monkeypatch)


def test_custodian_error_in_the_revealed_targets_is_inconclusive(fake, sealed):
    swapped = fake.baseline.replace("3,990 states", "3,991 states")
    r = judge(fake, sealed, load_targets=lambda: swapped)
    assert r["outcome"] == "INCONCLUSIVE" and "preregistered" in r["reasons"][0]


# --- integrity and identity: INVALID EVIDENCE, and the targets are never read ---

def _tamper_report(d):
    p = d / "generated-results.md"
    p.write_text(p.read_text(encoding="utf-8").replace("3,990", "3,991"), encoding="utf-8")


def _unseal_environment(d):
    lines = [x for x in (d / rp.HASHES).read_text().splitlines(True) if not x.endswith(" environment.json\n")]
    (d / rp.HASHES).write_text("".join(lines))
    c = json.loads((d / rp.COMMITMENT).read_text(encoding="utf-8"))
    c["commitment"] = hashlib.sha256((d / rp.HASHES).read_bytes()).hexdigest()
    (d / rp.COMMITMENT).write_text(json.dumps(c), encoding="utf-8")


@pytest.mark.parametrize("tamper,reason", [
    (_tamper_report, "generated-results.md missing or altered"),
    (lambda d: (d / "cmd1-test.stdout.log").unlink(), "cmd1-test.stdout.log missing or altered"),
    (lambda d: (d / rp.COMMITMENT).write_text("{not json"), "malformed"),
    (_unseal_environment, "environment.json is not sealed"),
    (lambda d: edit_and_reseal(d, "environment.json", lambda e: e["checkout"].update(head="0" * 40)),
     "checked-out commit"),
    (lambda d: edit_and_reseal(d, "environment.json", lambda e: e["checkout"].update(tree="0" * 40)),
     "checked-out tree"),
    (lambda d: edit_and_reseal(d, "environment.json", lambda e: e["checkout"].update(status_porcelain=" M x")),
     "not clean"),
    (lambda d: edit_and_reseal(d, "environment.json", lambda e: e.update(source_sha256_assurance_lab="0" * 64)),
     "harness-recomputed source digest"),
    (lambda d: edit_and_reseal(d, "environment.json", lambda e: e["harness"].update({"reproduce.py": "0" * 64})),
     "preregistered harness"),
    (_misdescribe("evidence_id", "R1:other"), "different evidence ids"),
    (lambda d: edit_and_reseal(d, "commands.json", lambda c: c.pop(1)), "frozen commands"),
    (lambda d: edit_and_reseal(d, "commands.json", lambda c: c[1]["argv"].append("-x")), "frozen commands"),
    (lambda d: edit_and_reseal(d, "commands.json", lambda c: c[3].update(artifact="generated-results-quick.md")),
     "frozen commands"),
    (_misdescribe("subject_commit", "0" * 40), "misdescribes the sealed evidence: subject_commit"),
    (_misdescribe("label", "R1"), "misdescribes the sealed evidence: label"),
    (_misdescribe("report_sha256", "0" * 64), "misdescribes the sealed evidence: report_sha256"),
])
def test_untrustworthy_evidence_is_invalid_and_never_reveals(fake, copy_of, tamper, reason):
    tamper(copy_of)
    r = judge(fake, copy_of, load_targets=never)
    assert r["outcome"] == "INVALID EVIDENCE" and not r["targets_revealed"], r
    assert any(reason in x for x in r["reasons"]), r["reasons"]


def test_published_commitment_must_match(fake, sealed):
    r = judge(fake, sealed, load_targets=never, published="0" * 64)
    assert r["outcome"] == "INVALID EVIDENCE" and "published commitment" in r["reasons"][0]


def test_evidence_made_against_another_manifest_is_invalid(fake, sealed, tmp_path):
    other = tmp_path / "other.json"
    other.write_bytes(fake.manifest.read_bytes() + b"\n")
    r = judge(fake, sealed, load_targets=never, manifest=other)
    assert r["outcome"] == "INVALID EVIDENCE" and "different claim-subject manifest" in r["reasons"][0]


# --- what an outcome permits -----------------------------------------------------

def test_g1_requires_an_independent_label_a_match_and_an_anchor():
    match = {"outcome": "MATCH", "label": "R1"}
    assert rc.g1_status({**match, "label": "R0-SELF-dry-run"}, {"url": "u"}).startswith("not eligible")
    assert rc.g1_status({"outcome": "MISMATCH", "label": "R1"}, {"url": "u"}).startswith("not eligible")
    assert "no timestamp anchor" in rc.g1_status(match, None)
    assert rc.g1_status(match, {"url": "u"}).startswith("eligible pending review")


def test_frozen_identities_are_git_facts():
    """P0-01, re-derived from git rather than trusted: the subject's trees,
    its tracked-file inventory, and that main's code is the subject's code
    (the merge changed only README.md and docs/RESULTS.md)."""
    m = json.loads(FROZEN_MANIFEST)["subject"]
    commit, base = m["source_commit"], m["baseline_report"]["commit"]
    try:
        tree = _git(ROOT, "rev-parse", f"{commit}^{{tree}}")
    except subprocess.CalledProcessError:
        pytest.skip("subject commit not in this clone (shallow checkout)")
    assert tree == m["source_tree"]
    assert _git(ROOT, "rev-parse", f"{commit}:assurance_lab") == m["source_tree_assurance_lab"]
    assert _git(ROOT, "rev-parse", f"{base}:assurance_lab") == m["source_tree_assurance_lab"]
    assert _git(ROOT, "diff", "--name-only", commit, base).split() == ["README.md", "docs/RESULTS.md"]
    inventory = subprocess.run(["git", "ls-tree", "-r", "--long", commit], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
    assert (KIT / "baseline/tracked-files-c175fbd.txt").read_text(encoding="utf-8") == inventory
