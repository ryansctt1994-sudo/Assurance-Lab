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
    "naive_stale_guard": ("KILLED", "NOT_REACHED", "NOT_REACHED", "NOT_REACHED", "NOT_REACHED"),
    "replay_readmits": ("NOT_REACHED", "KILLED", "NOT_REACHED", "NOT_REACHED", "SURVIVED"),
    "global_dispute": ("NOT_REACHED", "NOT_REACHED", "KILLED", "NOT_REACHED", "NOT_REACHED"),
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
    for (rel, f), (cell, hits) in m.items():  # SURVIVED means the branch really ran
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
