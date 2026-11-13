"""Reachability witnesses: intended behaviors are possible (existential, not liveness)."""

import pytest

from assurance_lab.checker.witnesses import SPECS, all_witnesses, confirms, find
from assurance_lab.core.model import Config, CoreModel, no_current

CFG = Config()  # the headline domain: the one the safety claims are made in


def test_every_intended_behavior_is_reachable_with_a_replayable_shortest_path():
    for w in all_witnesses(CFG):
        assert w.found, w.spec.name
        assert confirms(w.spec, CoreModel(CFG), w.actions), w.spec.name
        assert w.actions, w.spec.name  # not satisfied by Init itself


class NoReadmissionAfterInvalidation(CoreModel):
    """Over-constrained: a lineage that ever had an invalidated record is closed."""

    name = "no_readmission_after_invalidation"

    def admit_lineage_ok(self, s, lin):
        return no_current(s, lin) and not any(r.invalidated and r.lineage == lin for r in s.recs)


class NoGrant(CoreModel):
    """Over-constrained: authority can never be granted."""

    name = "no_grant"

    def grant(self, s):
        return iter(())


@pytest.mark.parametrize("model,witness", [
    (NoReadmissionAfterInvalidation, "re-admission after X_⊥"),
    (NoGrant, "granted authority exercised"),
])
def test_witness_search_reports_unreachable_behavior(model, witness):
    """Calibration: in a model that forbids the behavior, the search must say
    so rather than find something. Safety would still PASS there, which is
    exactly the vacuity witnesses exist to expose."""
    spec = next(s for s in SPECS if s.name == witness)
    w = find(spec, model(Config(n_records=2, n_lineages=1)))
    assert not w.found
