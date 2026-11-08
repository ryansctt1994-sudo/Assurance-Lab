import pytest

from assurance_lab.admission import ProjectionViolation, run_admission
from assurance_lab.reliance import (
    BoundaryVerdict, Impact, Receipt, RelianceLedger, classify_boundary,
)
from assurance_lab.core.model import Config
from assurance_lab.verdict import Result, Verdict, authorize

STATE = {"evidence": {"hash": "abc", "witness": True}, "policy": "p1",
         "diagnostics": ["result looked wrong"]}
PI_A = frozenset({"evidence", "policy"})


# --- I_2 admission causal closure ---------------------------------------------


def test_admission_within_projection_yields_bound_receipt():
    rec = run_admission(lambda v: v["evidence"]["witness"] and v["policy"] == "p1", STATE, PI_A)
    assert rec.decision is True and rec.reads_within_projection()
    assert rec.observed_reads == ("evidence", "policy")


def test_admission_reading_diagnostics_is_blocked():
    with pytest.raises(ProjectionViolation):
        run_admission(lambda v: not v["diagnostics"], STATE, PI_A)


def test_receipt_is_insensitive_to_diagnostics_but_sensitive_to_projection():
    f = lambda v: v["evidence"]["witness"]  # noqa: E731
    a = run_admission(f, STATE, PI_A)
    b = run_admission(f, {**STATE, "diagnostics": ["totally different"]}, PI_A)
    c = run_admission(f, {**STATE, "policy": "p2"}, PI_A)
    assert a.rho == b.rho and a.rho != c.rho


# --- §13-14 reliance & descendant closure -----------------------------------


def ledger():
    L = RelianceLedger(evidence_hash={"d": "Hd", "u1": "Hu1", "u2": "Hu2"})
    L.declared = {("u1", "d"), ("u2", "u1"), ("u3", "d")}
    L.record_use("u1", "d", Receipt("u1", "d", "Hd", "full", "premise", "C1", 0))
    L.record_use("u2", "u1", Receipt("u2", "u1", "Hu1", "partial", "context", "C1", 0))
    return L


def test_use_without_receipt_and_undeclared_use_are_distinguished():
    L = ledger()
    L.record_use("u3", "d", None)  # declared, missing receipt
    L.record_use("rogue", "d", None)  # undeclared
    a = L.audit()
    assert a["missing_receipt"] == [("u3", "d")]
    assert a["undeclared"] == [("rogue", "d")]


def test_receipt_must_bind_current_evidence():
    L = ledger()
    with pytest.raises(ValueError):
        L.record_use("u3", "d", Receipt("u3", "d", "STALE", "full", "premise", "C1", 0))


def test_descendant_closure_classification_and_unknown_scope_fails_closed():
    L = ledger()
    L.evidence_hash["x"] = "Hx"
    L.record_use("u4", "d", Receipt("u4", "d", "Hd", None, "premise", "C1", 0))  # unknown scope
    L.record_use("y", "x", Receipt("y", "x", "Hx", "full", "premise", "C1", 0))
    imp = L.descendants("d")
    assert imp["u1"] is Impact.INVALIDATED
    assert imp["u2"] is Impact.REVIEW_REQUIRED
    assert imp["u4"] is Impact.REVIEW_REQUIRED
    assert imp["y"] is Impact.UNAFFECTED


# --- §15 BoundaryCompleteness ---------------------------------------------------


@pytest.mark.parametrize("effect,expect", [
    ("g_modeled", BoundaryVerdict.COVERED),
    ("g_outside", BoundaryVerdict.OUT_OF_SCOPE),
    ("g_external_ok", BoundaryVerdict.ENLARGE_MODEL),
    ("g_rogue", BoundaryVerdict.UNAUTHORIZED),
])
def test_boundary_triage(effect, expect):
    got = classify_boundary(effect, modeled_paths={"g_modeled"},
                            in_scope={"g_modeled", "g_external_ok", "g_rogue"},
                            legitimate_external={"g_external_ok"})
    assert got is expect


# --- §16 verdicts ---------------------------------------------------------------


def R(name, v):
    return Result(name, v, "test", "toy")


def test_non_compensating_authorization():
    assert authorize([R("a", Verdict.PASS), R("b", Verdict.PASS)], ["a", "b"])
    assert not authorize([R("a", Verdict.PASS), R("b", Verdict.UNKNOWN)], ["a", "b"])
    assert not authorize([R("a", Verdict.PASS), R("b", Verdict.FAIL)], ["a", "b"])
    assert not authorize([R("a", Verdict.PASS)], ["a", "b"])  # missing ⇒ UNKNOWN
    # Many strong PASSes elsewhere don't compensate.
    assert not authorize([R("a", Verdict.PASS)] * 10 + [R("b", Verdict.UNKNOWN)], ["a", "b"])


def test_label_carries_scope():
    r = Result("x", Verdict.PASS, "finite-exhaustive-induction", "3 records", "all Inv-states")
    assert r.label() == "PASS[finite-exhaustive-induction; 3 records; all Inv-states]"


# --- report provenance -----------------------------------------------------------


def _receipt(**kw):
    from assurance_lab.__main__ import QUICK_CORE, start_receipt
    r = start_receipt(QUICK_CORE, 1, "fork", "python -m assurance_lab --out x.md")
    r.update(kw)
    return r


def test_start_receipt_binds_source_before_run():
    from assurance_lab.__main__ import source_digest
    r = _receipt()
    assert r["source_sha256"] == source_digest() and len(r["receipt_id"]) == 16
    assert r["model_tree"] in {"clean", "DIRTY", "unknown"}


def test_dirty_tree_refused_unless_allowed():
    from assurance_lab.__main__ import preflight
    dirty = _receipt(model_tree="DIRTY")
    assert preflight(dirty, writing_report=True, allow_dirty=False)
    assert preflight(dirty, writing_report=True, allow_dirty=True) is None
    assert preflight(dirty, writing_report=False, allow_dirty=False) is None
    assert preflight(_receipt(model_tree="clean"), True, False) is None


def test_source_change_during_run_is_detected():
    from assurance_lab.__main__ import source_drifted
    assert not source_drifted(_receipt())
    assert source_drifted(_receipt(source_sha256="0" * 64))


def test_claims_are_stable_under_run_metadata_and_drift_is_flagged():
    from assurance_lab.__main__ import METADATA_HEADING, render
    rec = _receipt(model_tree="clean")
    res = [R("a", Verdict.PASS)]
    a = render(res, rec, {"started_utc": "t0", "induction_s": 1.0}, drifted=False)
    b = render(res, rec, {"started_utc": "t1", "induction_s": 999.0}, drifted=False)
    assert a != b and a.split(METADATA_HEADING)[0] == b.split(METADATA_HEADING)[0]
    assert "Green CI ≠ full induction completed" in a
    bad = render(res, rec, {}, drifted=True)
    assert "INVALID PROVENANCE" in bad and "NOT all PASS" in bad


def test_claim_labels_contain_no_timing():
    from assurance_lab.__main__ import core_results
    cfg = Config(n_records=2, n_lineages=1, max_level=1, n_sem=1)
    labels1 = [r.label() for r in core_results(cfg, 1)[0]]
    labels2 = [r.label() for r in core_results(cfg, 2)[0]]  # worker count must not leak into claims
    assert labels1 == labels2
