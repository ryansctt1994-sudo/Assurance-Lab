"""Property selection is observational; the coverage matrix measures what it claims."""

import pytest

from assurance_lab.checker.coverage import Outcome, coverage_row, property_coverage
from assurance_lab.checker.explore import PROPERTIES, explore
from assurance_lab.core.model import Config, CoreModel
from assurance_lab.core.mutants import (
    ALL_MUTANTS, AdmitWithoutHead, CompactionDropsEdge, CrossLineageSupersede, SelfGrant,
)
from assurance_lab.statedigest import set_digest

CFG = Config(n_records=2, n_lineages=1)


def graph(model, props):
    r = explore(model, stop_on_violation=False, keep_states=True, properties=props)
    return r.states, r.transitions, set_digest(r.state_set).label()


@pytest.mark.parametrize("model", [CoreModel(CFG), CrossLineageSupersede(Config(n_records=2, n_lineages=2))],
                         ids=["clean", "mutant"])
def test_property_selection_never_changes_the_explored_graph(model):
    every, one, none = (graph(model, p) for p in (None, frozenset({"HeadUnique"}), frozenset()))
    assert every == one == none


def test_no_properties_means_no_checks_and_unknown_names_raise():
    r = explore(CrossLineageSupersede(Config(n_records=2, n_lineages=2)), properties=frozenset())
    assert r.violation is None and r.complete
    with pytest.raises(ValueError, match="unknown properties"):
        explore(CoreModel(CFG), properties=frozenset({"HeadUnique", "NoSuchProperty"}))


@pytest.mark.parametrize("mutant,prop,cfg", [
    (CrossLineageSupersede, "EdgesWF", Config(n_records=2, n_lineages=2)),
    (SelfGrant, "NoSilentAuthorityIncrease", CFG),
    (AdmitWithoutHead, "NoOrphanLive", CFG),
    (CompactionDropsEdge, "HistoryMonotone", CFG),
])
def test_disabling_the_sole_detector_lets_the_mutant_survive(mutant, prop, cfg):
    """The matrix measures the intended relationship: remove the one property
    that detects the mutant and it survives; restore it and it is caught."""
    without = explore(mutant(cfg), properties=frozenset(PROPERTIES) - {prop})
    assert without.violation is None and without.complete
    only = explore(mutant(cfg), properties=frozenset({prop}))
    assert only.violation is not None


def test_coverage_matrix():
    cov = property_coverage()
    assert {r.mutant for r in cov.rows} == {M.name for M in ALL_MUTANTS}
    for r in cov.rows:
        assert not r.error, (r.mutant, r.error)
        assert Outcome.INVALID not in r.outcomes.values(), r.mutant
    assert cov.surviving_mutants() == []
    assert cov.unkilled_properties() == []  # every property has demonstrated sensitivity
    for prop, m in [("EdgesWF", "cross_lineage_supersede"), ("NoSilentAuthorityIncrease", "self_grant"),
                    ("HistoryMonotone", "compaction_drops_edge"), ("NoOrphanLive", "admit_without_head"),
                    ("DiagnosisInert", "diagnosis_revises_roles")]:
        assert m in cov.sole_witnesses(prop)
    # NoResurrection is implied by NoSilentAuthorityIncrease, so it can never
    # be a sole detector; documented, not "fixed".
    assert cov.sole_witnesses("NoResurrection") == []


def test_equivalent_mutant_is_invalid_not_survived():
    row = coverage_row(SelfGrant, cfg=Config(n_records=2, n_lineages=1, max_level=1))
    assert set(row.outcomes.values()) == {Outcome.INVALID}


def test_budget_exhaustion_is_error_never_a_kill():
    row = coverage_row(CrossLineageSupersede, max_states=50, cfg=Config(n_records=2, n_lineages=2))
    assert set(row.outcomes.values()) == {Outcome.ERROR} and "budget" in row.error


def test_kill_witnesses_are_concrete_action_sequences():
    row = coverage_row(AdmitWithoutHead)
    w = row.witness["NoOrphanLive"]
    assert w and w[0][0] == "Admit"
