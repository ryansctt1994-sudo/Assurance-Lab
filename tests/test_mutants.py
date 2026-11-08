"""Mutation testing (§22) and retained counterexamples (§21)."""

import pytest

from assurance_lab.checker.explore import explore
from assurance_lab.checker.induction import check_induction
from assurance_lab.core.model import Config, CoreModel
from assurance_lab.core.mutants import ALL_MUTANTS, SelfGrant

CFG = Config()  # 3 records, 2 principals, 2 lineages, levels 0..2, sem 0..1


@pytest.mark.parametrize("mutant", ALL_MUTANTS, ids=lambda m: m.name)
def test_mutant_is_detected_by_bfs(mutant):
    r = explore(mutant(CFG), max_states=200_000)
    assert r.violation is not None, f"{mutant.name} survived ({r.states} states)"


@pytest.mark.parametrize("mutant", ALL_MUTANTS, ids=lambda m: m.name)
def test_counterexample_step_is_not_in_clean_next(mutant):
    """Regression: the violating step of each retained counterexample is
    rejected by the clean model (either not enabled, or yields a different,
    property-satisfying successor)."""
    r = explore(mutant(CFG), max_states=200_000)
    v = r.violation
    *_prefix, (lbl, bad) = v.trace
    prev = v.trace[-2][1]
    clean_succ = dict(CoreModel(CFG).next(prev))
    assert clean_succ.get(lbl) != bad, v.pretty()


def test_counterexamples_are_short():
    """Retained traces stay human-readable."""
    for m in ALL_MUTANTS:
        v = explore(m(CFG), max_states=200_000).violation
        assert len(v.trace) <= 6, (m.name, v.pretty())


def test_equivalent_mutant_is_reported_not_counted():
    """With levels 0..1, SelfGrant's extra transition can never fire. That is
    an *equivalent* mutant for this domain: it must survive, and a mutation
    score that counted it as detected would be dishonest."""
    cfg = Config(n_records=2, n_lineages=1, max_level=1)
    r = explore(SelfGrant(cfg))
    assert r.complete and r.violation is None


# Smallest domain in which each mutant is *non-equivalent*: a fork needs three
# records; lineage mutants need two lineages.
_INDUCTION_CFG = {
    "stale_successor": Config(n_records=3, n_lineages=1),
    "lineage_rewrite": Config(n_records=2, n_lineages=2),
    "cross_lineage_supersede": Config(n_records=2, n_lineages=2),
}


@pytest.mark.parametrize("mutant", ALL_MUTANTS, ids=lambda m: m.name)
def test_mutant_is_detected_by_induction(mutant):
    cfg = _INDUCTION_CFG.get(mutant.name, Config(n_records=2, n_lineages=1))
    r = check_induction(mutant(cfg), stop_on_failure=True)
    assert not (r.init_ok and r.step_ok and r.safety_ok), mutant.name


# --- the three retained handoff counterexamples, as explicit scenarios -------


def test_handoff_failure_1_historical_admission_is_not_successor_eligibility():
    m = CoreModel(Config(n_records=3, n_lineages=1))
    s = m.init()
    s = dict(m.next(s))[("Admit", 0, 0, 1, 0)]
    s = dict(m.next(s))[("Supersede", 0, 1, 1, 0)]
    s = dict(m.next(s))[("Invalidate", 1)]  # lineage now X_⊥
    # r1 (historically admitted, invalidated) can never be re-promoted, and r0
    # (superseded) cannot be resurrected; only fresh r2 can become current.
    for lbl, t in m.next(s):
        assert not (t.current & {0, 1}), lbl


def test_handoff_failure_2_single_head_per_lineage():
    m = CoreModel(Config(n_records=3, n_lineages=1))
    s = dict(m.next(m.init()))[("Admit", 0, 0, 1, 0)]
    assert not any(lbl[0] == "Admit" for lbl, _ in m.next(s))
