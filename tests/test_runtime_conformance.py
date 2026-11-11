"""AL-RUNTIME-001 E1: model/runtime conformance.

Fuzzed traces (authority events, requests, restarts, crashes, races, recovery,
attacks on effects/ and state/) run against the real gate and the reference
model; every response and every reconciliation must agree. The harness is
calibrated: every gate mutant and every reconciler mutant is caught, and one
shrunk counterexample per mutant is kept and replayed.
"""

import collections
import json
from pathlib import Path

import pytest

from assurance_lab.runtime import contract as C
from assurance_lab.runtime import harness as H
from assurance_lab.runtime.gate import MUTANTS as GATE_MUTANTS
from assurance_lab.runtime.reconcile import MUTANTS as RECONCILER_MUTANTS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/runtime_counterexamples"
SEEDS, LENGTH = 60, 30  # the PR-level run; the receipt runs more


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gen")
    return [H.generate(s, LENGTH, tmp / f"g{s}") for s in range(SEEDS)]


def test_fuzzed_traces_conform(traces, tmp_path):
    for seed, tr in enumerate(traces):
        r = H.run_trace(tmp_path / "run", tr)
        assert r.ok, f"seed {seed}: {json.dumps(r.failure, default=list)}"


def test_the_fuzz_reaches_every_refusal_and_state_it_can(traces, tmp_path):
    """AUTHORITY_UNKNOWN cannot arise from a validated authority state; its
    mapping is checked with a stub in test_runtime_gate.py."""
    seen = collections.Counter()
    for tr in traces:
        for rk, _mk in H.run_trace(tmp_path / "run", tr).responses:
            for key in ([rk] if isinstance(rk[0], str) else rk):
                seen[tuple(key)] += 1
    reasons = set(C.REQUEST_REASONS) - {"AUTHORITY_UNKNOWN"} | {C.EFFECT_FAILED}
    states = {"LOG_TAMPERED", "ROLLBACK_DETECTED", "SNAPSHOT_MISMATCH", "UNCERTAIN_STATE"}
    missing = {r for r in reasons | states if (C.REFUSED, r) not in seen}
    assert not missing, sorted(missing)
    assert (C.ADMITTED, None) in seen and ("CRASH", None) in seen


def test_generation_and_execution_are_deterministic(tmp_path):
    a = H.generate(7, LENGTH, tmp_path / "a")
    b = H.generate(7, LENGTH, tmp_path / "b")
    assert a == b
    assert H.run_trace(tmp_path / "r1", a).responses == H.run_trace(tmp_path / "r2", a).responses


def _first_failure(traces, tmp_path, **kw):
    for seed, tr in enumerate(traces):
        r = H.run_trace(tmp_path / "run", tr, **kw)
        if not r.ok:
            return seed, r.failure
    return None


@pytest.mark.parametrize("mutant", sorted(GATE_MUTANTS))
def test_conformance_catches_every_gate_mutant(mutant, traces, tmp_path):
    assert _first_failure(traces, tmp_path, mutants=[mutant]) is not None, f"{mutant} survived {SEEDS} traces"


@pytest.mark.parametrize("mutant", sorted(RECONCILER_MUTANTS))
def test_conformance_catches_every_reconciler_mutant(mutant, traces, tmp_path):
    assert _first_failure(traces, tmp_path, reconciler_mutants=[mutant]) is not None, f"{mutant} survived"


def _fixtures():
    return sorted(FIXTURES.glob("*.json"))


def test_one_counterexample_is_kept_per_mutant():
    names = {p.stem for p in _fixtures()}
    assert names == {f"gate-{m}" for m in GATE_MUTANTS} | {f"reconciler-{m}" for m in RECONCILER_MUTANTS}


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_kept_counterexamples_still_fail_with_their_mutant_and_pass_without(path, tmp_path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["status"] == "detected", f"{path.name}: the mutant was never caught"
    kw = {"mutants": [doc["mutant"]]} if doc["side"] == "gate" else {"reconciler_mutants": [doc["mutant"]]}
    broken = H.run_trace(tmp_path / "m", doc["trace"], **kw)
    assert not broken.ok and broken.failure["step"] == doc["failure"]["step"]
    assert json.loads(json.dumps(broken.failure, default=list)) == doc["failure"]
    assert H.run_trace(tmp_path / "c", doc["trace"]).ok, "the correct gate must conform on it"
