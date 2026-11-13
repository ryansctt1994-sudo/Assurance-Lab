"""AL-RUNTIME-001 E1 reconciler: every finding kind is produced by the
interference it names, a clean run is CONSISTENT, the report is
deterministic, and the reconciler is independent of the gate's code."""

import ast
import json
import os
from pathlib import Path

import pytest

from assurance_lab.runtime import contract as C
from assurance_lab.runtime.gate import SimulatedCrash
from tests.runtime_support import Env

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.auth("r0")
    assert e.send("req-00000001", "alice", "L", "r0", "a.txt", b"a\n").outcome == C.ADMITTED
    assert e.send("req-00000002", "alice", "L", "r0", "dir/b.txt", b"b\n").outcome == C.ADMITTED
    yield e
    e.close()


def _rewrite_last_line(p: Path, f):
    lines = p.read_bytes().split(b"\n")[:-1]
    lines[-1] = f(lines[-1])
    p.write_bytes(b"\n".join(lines) + b"\n")


def test_a_clean_run_is_consistent_and_lists_what_it_compared(env):
    rep = env.reconcile()
    assert rep.verdict == C.CONSISTENT and rep.findings == []
    assert rep.expected_files == rep.observed_files == {"L/a.txt": C.sha256_hex(b"a\n"),
                                                        "L/dir/b.txt": C.sha256_hex(b"b\n")}


def _interfere(kind, env):
    eff = env.eff
    if kind == "MISSING_EFFECT":
        os.unlink(eff / "L/a.txt")
        return "L/a.txt"
    if kind == "MODIFIED_EFFECT":
        (eff / "L/a.txt").write_bytes(b"changed\n")
        return "L/a.txt"
    if kind == "UNEXPECTED_FILE":
        (eff / "L/extra.txt").write_bytes(b"x\n")
        return "L/extra.txt"
    if kind == "UNEXPECTED_NON_REGULAR":
        os.symlink(env.outside, eff / "L/link")
        return "L/link"
    if kind == "UNEXPECTED_DIRECTORY":
        (eff / "M").mkdir()
        return "M"
    if kind == "MULTI_LINKED_FILE":
        os.link(eff / "L/a.txt", env.outside / "alias")
        return "L/a.txt"
    if kind == "TEMP_LEFTOVER":
        env.gate.faults.add("crash_after_temp")
        with pytest.raises(SimulatedCrash):
            env.send("req-00000003", "alice", "L", "r0", "c.txt")
        return next(p.relative_to(eff).as_posix() for p in (eff / "L").iterdir() if p.name.startswith(C.TEMP_PREFIX))
    if kind == "UNRESOLVED_EFFECT":
        env.gate.faults.add("crash_after_admit")
        with pytest.raises(SimulatedCrash):
            env.send("req-00000003", "alice", "L", "r0", "c.txt")
        return f"decision {len(env.log()) - 1}"
    if kind == "LOG_TAMPERED":
        _rewrite_last_line(env.gdir / "state/log.jsonl", lambda ln: ln.replace(b'"kind"', b'"kind" ', 1))
        return "log"
    if kind == "LOG_BEHIND_WITNESS":
        p = env.gdir / "state/log.jsonl"
        p.write_bytes(b"".join(p.read_bytes().splitlines(keepends=True)[:-2]))
        return "log"
    if kind == "LOG_AHEAD_OF_WITNESS":
        p = env.wdir / "commitments.jsonl"
        p.write_bytes(b"".join(p.read_bytes().splitlines(keepends=True)[:-1]))
        return "log"
    if kind == "WITNESS_INVALID":
        def bad_mac(ln):
            c = json.loads(ln)
            c["mac"] = "f" * 64
            return C.canonical(c)
        _rewrite_last_line(env.wdir / "commitments.jsonl", bad_mac)
        return "witness"
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", C.FINDINGS)
def test_every_finding_is_produced_by_the_interference_it_names(kind, env):
    subject = _interfere(kind, env)
    rep = env.reconcile()
    assert rep.verdict == C.INCONSISTENT
    assert (kind, subject) in rep.keys(), rep.keys()
    if kind == "UNRESOLVED_EFFECT":
        assert rep.keys() == {(kind, subject)}, "an in-flight effect is not also a missing or modified one"


def test_the_report_is_deterministic(env):
    _interfere("UNEXPECTED_FILE", env)
    _interfere("MODIFIED_EFFECT", env)
    a, b = env.reconcile().as_dict(), env.reconcile().as_dict()
    assert a == b and [f["kind"] for f in a["findings"]] == ["MODIFIED_EFFECT", "UNEXPECTED_FILE"]


def test_findings_are_ordered_by_contract_then_subject(env):
    for k in ("UNEXPECTED_FILE", "MISSING_EFFECT", "UNEXPECTED_DIRECTORY"):
        _interfere(k, env)
    kinds = [f.kind for f in env.reconcile().findings]
    assert kinds == sorted(kinds, key=C.FINDINGS.index)


def test_the_reconciler_does_not_import_the_gate_or_the_model():
    """It shares only the contract: its view of effects comes from the log and the filesystem."""
    tree = ast.parse((ROOT / "assurance_lab/runtime/reconcile.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(f"{node.module or ''}.{a.name}".lstrip(".") for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any(x.split(".")[0] in ("gate", "model", "harness") or ".gate" in x or ".model" in x
                   for x in imported), imported
    assert "contract" in imported


def test_unknown_reconciler_mutants_are_rejected(env):
    with pytest.raises(ValueError):
        env.reconcile(mutants={"no_such_mutant"})
