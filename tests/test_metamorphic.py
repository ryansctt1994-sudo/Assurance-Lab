"""METAMORPHIC_CONSISTENCY: relations hold, are calibrated, and can fail."""

import pytest

from assurance_lab.checker.induction import inv_states
from assurance_lab.core.model import Config, CoreModel
from assurance_lab.distributed.metamorphic import (
    EQUIVOCATION_PROBE, FLAGS, KINDS, RELATIONS, Cell, _shrink, duplicate_idempotence,
    instances, mutant_matrix, mutation_score, observe, order_invariance, precondition_violations,
    renaming_equivariance, run, shared_precondition,
)
from assurance_lab.statedigest import (
    MalformedEncoding, canon, canon_parts, decanon, malformed_accepted, round_trip_failures,
)

# The calibrated relation x mutant matrix. A change here is a change in what
# the suite can detect, so it must be reviewed, not just re-recorded.
EXPECTED = {
    "first_applied_wins": ("KILLED", "SURVIVED", "SURVIVED", "SURVIVED", "SURVIVED"),
    "naive_stale_guard": ("KILLED", "NOT_REACHED", "NOT_REACHED", "NOT_REACHED", "SURVIVED"),
    "replay_readmits": ("NOT_REACHED", "KILLED", "NOT_REACHED", "NOT_REACHED", "SURVIVED"),
    "global_dispute": ("NOT_REACHED", "NOT_REACHED", "KILLED", "NOT_REACHED", "SURVIVED"),
    "rid_tiebreak": ("SURVIVED", "SURVIVED", "SURVIVED", "KILLED", "SURVIVED"),
    "principal_order_quorum": ("SURVIVED", "SURVIVED", "SURVIVED", "KILLED", "SURVIVED"),
    "freeze_drops_invalidated": ("NOT_REACHED", "NOT_REACHED", "NOT_REACHED", "NOT_REACHED", "KILLED"),
}


@pytest.mark.parametrize("name", RELATIONS)
def test_relation_holds_on_the_corrected_implementation(name):
    r = RELATIONS[name]()
    assert r.held, "\n".join(w.pretty() for w in r.witnesses)
    assert r.checks > 0


def test_relation_mutant_matrix_is_calibrated():
    m = mutant_matrix()
    got = {flag: tuple(m[(rel, flag)][0] for rel in RELATIONS) for flag in FLAGS}
    assert got == EXPECTED
    for rel in RELATIONS:  # every relation kills at least one negative control
        assert any(m[(rel, f)][0] == Cell.KILLED for f in FLAGS), rel
    for f in FLAGS:  # every negative control is killed by some relation
        assert any(m[(rel, f)][0] == Cell.KILLED for rel in RELATIONS), f
    for (rel, f), (cell, hits, *_breadth) in m.items():  # SURVIVED means the branch really ran
        assert (cell == Cell.SURVIVED) <= (hits > 0)
    assert mutation_score(m) == (len(FLAGS), len(FLAGS))


def test_witness_replays_and_is_one_minimal():
    """The split-brain rule (first_applied_wins) is caught with a replayable,
    1-minimal witness: replaying it shows the difference, and dropping any
    event makes the difference disappear."""
    flags = {"first_applied_wins": True}
    r = order_invariance(instances(), flags)
    w = next(w for w in r.witnesses if w.instance == "fork+dispute")
    inst = next(i for i in instances() if i.name == "fork+dispute")
    a, b = w.executions
    assert observe(run(inst, a, flags), inst.lineages) != observe(run(inst, b, flags), inst.lineages)
    assert observe(run(inst, a, {}), inst.lineages) == observe(run(inst, b, {}), inst.lineages)
    for drop in a:
        a2, b2 = [e for e in a if e != drop], [e for e in b if e != drop]
        assert observe(run(inst, a2, flags), inst.lineages) == observe(run(inst, b2, flags), inst.lineages)
    assert sum(1 for e in a if type(e).__name__ == "Resolve") == 2  # both competing resolutions kept


def test_checker_rejects_a_wrong_expected_observation():
    """The relation checker is not accept-everything: inject an incorrect
    expected observation and the clean implementation must *fail*."""
    identity = lambda obs, rmap, pmap: obs  # noqa: E731 (wrong: ignores the renaming)
    assert not renaming_equivariance(KINDS, expected_map=identity).held
    wrong_head = lambda got, want: got == want and got[0] == (("L", "nobody"),)  # noqa: E731
    assert not order_invariance(instances()[:1], compare=wrong_head).held
    assert not duplicate_idempotence(instances()[:1], compare=lambda a, b: False).held


def test_preconditions_are_machine_checked():
    assert precondition_violations() == []
    # The equivocation probe is excluded because the checker flags it, not by prose.
    assert any("issued 2 times" in v for v in shared_precondition(EQUIVOCATION_PROBE))


def test_equivocation_is_a_documented_limitation_with_a_minimal_witness():
    """Outside the shared precondition (Byzantine origin): two different
    admissions for one id make the outcome order-dependent. Recorded, not
    hidden. If this starts passing, update docs/METAMORPHIC.md."""
    r = order_invariance([EQUIVOCATION_PROBE])
    assert not r.held
    (w,) = r.witnesses
    assert len(w.executions[0]) == 2 and {e.owner for e in w.executions[0]} == {"bob", "carol"}


def test_shrink_keeps_only_what_the_failure_needs():
    assert _shrink(list(range(10)), lambda xs: 3 in xs and 7 in xs) == [3, 7]


def test_core_encoding_round_trip_and_dropped_field_control():
    cfg = Config(n_records=2, n_lineages=1)
    states = list(inv_states(cfg))
    strict = lambda line: decanon(line, cfg)  # noqa: E731
    assert round_trip_failures(states, decode=strict, model=CoreModel(cfg)) == []
    lossy = lambda s: canon_parts([r._replace(retracted=False) for r in s.recs],  # noqa: E731
                                  s.current, s.edges, s.levels, s.sem_now)
    failed = set(round_trip_failures(states, encode=lossy, decode=strict))
    infected = {s for s in states if lossy(s) != canon(s)}
    # Not a partially passing control: it fails on exactly the inputs it
    # corrupts (states with a retracted record); the rest are not infected.
    assert failed == infected == {s for s in states if any(r.retracted for r in s.recs)}


def test_strict_decoder_rejects_every_malformed_encoding():
    assert malformed_accepted(Config(n_records=2, n_lineages=1)) == []
    with pytest.raises(MalformedEncoding):
        decanon("1,0,1,0,0,0,0,-1;1,0,0,1,0,0,0,-1|1,0||2,0|0")  # unsorted: not canonical



def test_every_kill_has_full_breadth_and_thin_controls_are_thickened():
    """Kill breadth = detecting eligible instances / eligible instances.
    Every KILLED cell detects its mutant in every eligible instance, and the
    two controls the audit called thin are now eligible in several."""
    m = mutant_matrix()
    for (rel, flag), c in m.items():
        assert c.errors == 0
        if c.category == Cell.KILLED:
            assert c.detecting == c.eligible > 0, (rel, flag, c)
    assert m[("order", "naive_stale_guard")].eligible >= 4
    assert m[("serialization", "freeze_drops_invalidated")].eligible >= 5


def test_frozen_v1_fixture_campaign_still_holds_and_reads_the_same():
    """Regression: the original fixtures, re-run under the current code,
    still satisfy every relation and give the same categories."""
    from assurance_lab.distributed.metamorphic import KINDS_V1
    v1 = instances(KINDS_V1)
    for name, rel in [("order", order_invariance), ("duplicate", duplicate_idempotence)]:
        assert rel(v1).held, name
    assert renaming_equivariance(KINDS_V1).held
    m1, m2 = mutant_matrix(KINDS_V1), mutant_matrix()
    assert {k: c.category for k, c in m1.items()} == {k: c.category for k, c in m2.items()}


def test_coverage_is_labeled_sampled_or_exhaustive():
    from assurance_lab.distributed.metamorphic import lineage_projection
    order = order_invariance(instances())
    assert [name for name, _done, _total in order.coverage] == ["fork+dispute+escalation"]
    assert "11/12 instances exhaustive" in order.coverage_note()
    assert lineage_projection(instances()).coverage_note() == "all 12 instances exhaustive"


def test_corrupted_snapshots_fail_closed_and_each_layer_is_measured():
    """Every corruption is refused at thaw wherever it infects. With
    validation off, each later layer's own detection is measured:
    projection ⇏ next-event ⇏ exact-state equivalence."""
    from assurance_lab.distributed.metamorphic import corruption_table
    rows = {name: (inf, thaw, mech) for name, inf, thaw, mech in corruption_table()}
    for name, (inf, thaw, mech) in rows.items():
        assert inf > 0 and thaw == inf, name
        assert mech["exact-state"] == inf, name  # the independent extractor sees everything
    assert rows["drop pending queue"][2]["projection"] == 0  # invisible right after thaw ...
    assert rows["drop pending queue"][2]["next-event"] == rows["drop pending queue"][0]  # ... not later
    assert rows["drop seen-set (duplicate filter)"][2] == {"projection": 0, "next-event": 0, "exact-state": 12}


def test_thaw_refuses_inconsistent_snapshots_and_head_never_crashes():
    from assurance_lab.distributed.metamorphic import CORRUPTIONS
    from assurance_lab.distributed.replica import CorruptSnapshot, Replica
    inst = next(i for i in instances() if i.name == "fork+resolution+invalidate-winner")
    evs = list(inst.events)
    states = [run(inst, o[:i], {}).freeze() for o in (evs, evs[::-1]) for i in range(len(evs) + 1)]
    for frozen in states:
        Replica.thaw(inst.policy, frozen)  # every genuine snapshot is accepted
    for name, fn in CORRUPTIONS.items():
        infected = [f for f in states if fn(f) != f]
        assert infected, name  # some state this corruption actually damages
        for frozen in infected:
            with pytest.raises(CorruptSnapshot):
                Replica.thaw(inst.policy, fn(frozen))
            unsafe = Replica.thaw(inst.policy, fn(frozen), validate=False)
            unsafe.head("L")  # inconsistent state fails closed, never raises


def test_exact_state_does_not_use_the_serializer_under_test(monkeypatch):
    from assurance_lab.distributed.metamorphic import exact_state
    from assurance_lab.distributed.replica import Replica
    inst = instances()[1]
    rep = run(inst, inst.events, {})
    monkeypatch.setattr(Replica, "freeze", lambda self: (_ for _ in ()).throw(AssertionError("freeze used")))
    assert exact_state(rep)  # works without freeze


def test_serialization_claims_are_scoped_to_the_declared_universe():
    from assurance_lab.distributed.metamorphic import event_universe, serialization_round_trip
    from assurance_lab.distributed.metamorphic import boundary_events
    u = event_universe(instances())
    assert len(u) == len(set(u)) and any(getattr(e, "lineage", None) == "M" for e in u)
    assert set(boundary_events()) <= set(u)  # invalid and boundary-case events are declared too
    assert serialization_round_trip(instances(), universe=u).held
