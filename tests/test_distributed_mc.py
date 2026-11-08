"""Replica-level model checking (§27-28)."""

import itertools

import pytest

from assurance_lab.distributed.mc import (
    SCENARIOS, check_scenario, mutant_scenarios, with_observer,
)
from assurance_lab.distributed.replica import (
    CONFLICT, Admit, Edge, Replica, ResolutionPolicy, make_resolution,
)


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_scenario_holds_exhaustively(sc):
    r = check_scenario(sc)
    assert r.violation is None, (r.violation, r.trace)
    assert r.quiescent > 0
    # Vacuity guard: every outcome the scenario anticipates is actually reached,
    # e.g. both "only one authority resolved" and "both resolved concurrently".
    assert r.issued_sets == set(dict(sc.expect)), r.issued_sets


@pytest.mark.parametrize("sc", mutant_scenarios(), ids=lambda s: s.name)
def test_retained_counterexamples_are_found(sc):
    r = check_scenario(sc)
    assert r.violation and "divergence" in r.violation
    assert r.trace, "counterexample must come with a schedule"


def test_concurrent_disagreement_with_third_replica():
    sc = next(s for s in SCENARIOS if s.name.startswith("fork, two authorities"))
    r = check_scenario(with_observer(sc, max_dups=1))
    assert r.violation is None and r.issued_sets == set(dict(sc.expect))


# --- the resolution rule itself ------------------------------------------------

POL = ResolutionPolicy(frozenset({"alice", "bob", "carol"}), 2)
FORK = [Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0"),
        Admit("r2", "L", 2, "carol", pred="r0"), Edge("r0", "r1"), Edge("r0", "r2")]


def forked():
    rep = Replica(POL)
    for e in FORK:
        rep.deliver(e)
    return rep


def test_disagreeing_resolutions_fail_closed_in_any_order():
    ref = forked()
    a = make_resolution(ref, "r0", "r1", "r2", {"alice", "carol"})
    b = make_resolution(ref, "r0", "r2", "r1", {"alice", "bob"})
    states = set()
    for order in itertools.permutations([a, b]):
        rep = forked()
        for e in order:
            rep.deliver(e)
        assert rep.decision("r0")[0] == "disputed" and rep.head("L") == CONFLICT
        states.add(rep.canonical())
    assert len(states) == 1  # order-independent


def test_higher_epoch_settles_a_dispute_in_any_order():
    ref = forked()
    a = make_resolution(ref, "r0", "r1", "r2", {"alice", "carol"})
    b = make_resolution(ref, "r0", "r2", "r1", {"alice", "bob"})
    c = make_resolution(ref, "r0", "r2", "r1", {"alice", "bob", "carol"}, epoch=2)
    heads = set()
    for order in itertools.permutations([a, b, c]):
        rep = forked()
        for e in order:
            rep.deliver(e)
        heads.add(rep.head("L"))
    assert heads == {"r2"}


def test_agreeing_resolutions_from_different_quorums_agree():
    ref = forked()
    a = make_resolution(ref, "r0", "r2", "r1", {"alice", "bob"})
    b = make_resolution(ref, "r0", "r2", "r1", {"bob", "alice", "carol"})
    rep = forked()
    rep.deliver(a)
    rep.deliver(b)
    assert rep.decision("r0") == ("agreed", "r2", "r1") and rep.head("L") == "r2"
