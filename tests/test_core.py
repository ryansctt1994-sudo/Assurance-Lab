import itertools

import pytest

from assurance_lab.checker.explore import explore
from assurance_lab.checker.induction import (
    _record_local_ok, check_induction, inv_states, partitions, record_candidates, record_domain,
)
from assurance_lab.core.model import ACTION_KINDS, UNADMITTED, Config, CoreModel, State
from assurance_lab.core.properties import (
    adjudication_bound, independent_approval, inv, record_wf, safety_failures,
)

SMALL = [
    Config(n_records=2, n_lineages=1),
    Config(n_records=2, n_lineages=2),
    Config(n_records=3, n_lineages=2, max_level=1, n_sem=1),
]


@pytest.mark.parametrize("cfg", SMALL, ids=lambda c: c.describe())
def test_clean_reachability_has_no_violation(cfg):
    r = explore(CoreModel(cfg))
    assert r.complete
    assert r.violation is None, r.violation.pretty()


@pytest.mark.parametrize("cfg", SMALL, ids=lambda c: c.describe())
def test_clean_induction(cfg):
    r = check_induction(CoreModel(cfg))
    assert r.complete and r.init_ok and r.step_ok and r.safety_ok, r.first_failure
    # Induction covers strictly more than reachability: unreachable Inv-states.
    assert r.inv_state_count > explore(CoreModel(cfg)).states


def test_every_action_kind_is_exercised():
    """Vacuity guard: the clean pass is not due to disabled actions."""
    r = explore(CoreModel(Config(n_records=2, n_lineages=2)))
    assert set(ACTION_KINDS) <= set(r.actions_seen), r.actions_seen


def test_stable_no_current_state_is_reachable_and_inert():
    """X_⊥ is first-class: a lineage can sit with no current record, and
    nothing but fresh admission leaves it."""
    m = CoreModel(Config(n_records=2, n_lineages=1))
    s = m.init()
    s = dict(m.next(s))[("Admit", 0, 0, 1, 0)]
    s = dict(m.next(s))[("Invalidate", 0)]
    assert s.current == frozenset()
    for lbl, t in m.next(s):
        if t.current:
            assert lbl[0] == "Admit" and lbl[1] == 1  # only fresh admission


# --- generator soundness/completeness vs brute force -------------------------


@pytest.mark.parametrize("cfg", [Config(n_records=1, n_lineages=2), Config(n_records=1, n_sem=1)])
def test_record_local_prune_matches_clauses(cfg):
    for r in record_domain(cfg):
        st = State((r,), frozenset(), frozenset(), cfg.levels0(), 0)
        expect = record_wf(cfg, st) and adjudication_bound(cfg, st) and independent_approval(cfg, st)
        assert _record_local_ok(r) == expect, r


def test_inv_generator_equals_brute_force_filter():
    cfg = Config(n_records=2, n_lineages=1, max_level=1, n_sem=2)
    cands = record_candidates(cfg)
    n = cfg.n_records
    all_pairs = [(a, b) for a in range(n) for b in range(n)]
    brute = set()
    for levels, sem_now in partitions(cfg):
        for recs in itertools.product(cands, repeat=n):
            for k in range(n + 1):
                for cur in itertools.combinations(range(n), k):
                    for m in range(len(all_pairs) + 1):
                        for es in itertools.combinations(all_pairs, m):
                            s = State(recs, frozenset(cur), frozenset(es), levels, sem_now)
                            if inv(cfg, s):
                                brute.add(s)
    gen = list(inv_states(cfg))
    assert len(gen) == len(set(gen))
    assert set(gen) == brute


def test_inv_implies_safety_is_not_trivially_satisfied():
    """Safety rejects states Inv also rejects; check it actually bites."""
    cfg = Config(n_records=1)
    bad = State((UNADMITTED,), frozenset({0}), frozenset(), cfg.levels0(), 0)
    assert safety_failures(cfg, bad)
