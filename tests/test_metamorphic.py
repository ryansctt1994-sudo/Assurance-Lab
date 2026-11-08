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
    "global_dispute": ("NOT_APPLICABLE", "NOT_APPLICABLE", "KILLED", "NOT_APPLICABLE", "SURVIVED"),
    "rid_tiebreak": ("SURVIVED", "SURVIVED", "SURVIVED", "KILLED", "SURVIVED"),
    "principal_order_quorum": ("SURVIVED", "SURVIVED", "SURVIVED", "KILLED", "SURVIVED"),
    "freeze_drops_invalidated": ("NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "KILLED"),
}

# (eligible, deviating, detecting) per cell, relations in RELATIONS order, on
# the 12 v2 fixtures. Reviewed values: a change means the suite measures
# something different and must be explained in METAMORPHIC_MATRIX_HISTORY.md.
COUNTS = {
    "first_applied_wins": ((2, 2, 2), (2, 2, 0), (12, 12, 0), (2, 2, 0), (12, 2, 0)),
    "naive_stale_guard": ((5, 5, 5), (5, 0, 0), (5, 0, 0), (5, 0, 0), (12, 5, 0)),
    "replay_readmits": ((6, 0, 0), (6, 6, 6), (6, 0, 0), (6, 0, 0), (12, 12, 0)),
    "global_dispute": ((0, 0, 0), (0, 0, 0), (12, 12, 9), (0, 0, 0), (12, 2, 0)),
    "rid_tiebreak": ((7, 7, 0), (7, 7, 0), (12, 12, 0), (7, 7, 2), (12, 7, 0)),
    "principal_order_quorum": ((5, 5, 0), (5, 5, 0), (12, 12, 0), (5, 5, 5), (12, 5, 0)),
    "freeze_drops_invalidated": ((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (6, 6, 6)),
}


@pytest.mark.parametrize("name", RELATIONS)
def test_relation_holds_on_the_corrected_implementation(name):
    r = RELATIONS[name]()
    assert r.held, "\n".join(w.pretty() for w in r.witnesses)
    assert r.checks > 0


def test_relation_mutant_matrix_is_calibrated():
    m = mutant_matrix()
    got = {flag: tuple(m[(rel, flag)].category for rel in RELATIONS) for flag in FLAGS}
    assert got == EXPECTED
    for rel in RELATIONS:  # every relation kills at least one negative control
        assert any(m[(rel, f)].category == Cell.KILLED for f in FLAGS), rel
    for f in FLAGS:  # every negative control is killed by some relation
        assert any(m[(rel, f)].category == Cell.KILLED for rel in RELATIONS), f
    for c in m.values():  # cross-check: a deviation means the mutant's own counter saw its branch run
        assert bool(c.deviating) <= (c.hits > 0)
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



def test_matrix_has_all_three_denominators_and_no_anomalies():
    """Eligible (frozen predicate) ⊇ deviating (differential execution) ⊇
    detecting (relation verdict), all per source instance. A deviation the
    predicate did not anticipate, or a detection without a deviation, is an
    anomaly and fails here instead of being absorbed into a count."""
    from assurance_lab.distributed.metamorphic import matrix_anomalies
    m = mutant_matrix()
    assert matrix_anomalies(m) == []
    got = {flag: tuple(m[(rel, flag)].counts for rel in RELATIONS) for flag in FLAGS}
    assert got == COUNTS
    for c in m.values():
        assert set(c.detecting) <= set(c.deviating) <= set(c.eligible)
        assert c.evaluated == 12 and c.errors == 0
    # partial breadth stays visible: deviations the relation cannot see
    assert m[("renaming", "rid_tiebreak")].detection_breadth == (2, 7)
    assert m[("projection", "global_dispute")].detection_breadth == (9, 12)
    assert m[("serialization", "first_applied_wins")].exposure == (2, 12)


def test_deviation_witnesses_replay():
    """Each recorded deviation is reproducible: replaying its operations with
    and without the mutant gives different states (or heads) at that step."""
    from assurance_lab.distributed.metamorphic import _lockstep, _programs, build
    m = mutant_matrix()
    for (rel, flag), c in m.items():
        for name, d in c.deviations.items():
            inst = build(name)
            policy, steps, lineages = list(_programs(rel, inst))[d.execution]
            assert _lockstep(policy, steps, flag, lineages) == (d.step, d.what), (rel, flag, name)


def test_eligibility_never_reads_an_outcome():
    """The predicates take the relation's alphabet and the policy only."""
    import inspect
    from assurance_lab.distributed.metamorphic import ELIGIBILITY
    for flag, (op, what, pred) in ELIGIBILITY.items():
        assert op in {"deliver", "head", "freeze"} and what
        assert list(inspect.signature(pred).parameters) in (["evs", "pol"], ["evs", "policy"]), flag


def test_head_reads_only_the_head_time_flags():
    """Premise of the deviation measure: for every other mutant, equal states
    imply equal heads, because head() reads no other mutant flag."""
    import inspect
    from assurance_lab.distributed.metamorphic import HEAD_TIME_FLAGS
    from assurance_lab.distributed.replica import Replica
    src = "".join(inspect.getsource(f) for f in (Replica.explain_head, Replica._derive_head, Replica._losers,
                                                 Replica._chain_linked, Replica.decision,
                                                 Replica.integrity_problems, Replica._type_problems,
                                                 Replica._edge_rule, Replica._resolution_rule))
    assert {f for f in FLAGS if f in src} == set(HEAD_TIME_FLAGS)


def test_every_v1_to_v2_change_is_explained():
    """v2 is additive: v1 stays published verbatim, and every cell whose
    category differs from it has a recorded reason (exact match both ways)."""
    from assurance_lab.distributed.metamorphic import (
        PUBLISHED_V1_TABLE, V1_TO_V2_CHANGES, published_v1_categories, v1_to_v2_changes,
    )
    v1 = published_v1_categories(PUBLISHED_V1_TABLE)
    assert len(v1) == len(FLAGS) * len(RELATIONS)
    assert v1_to_v2_changes(v1, mutant_matrix()) == {k: (a, b) for k, (a, b, _why) in V1_TO_V2_CHANGES.items()}
    assert all(why for _a, _b, why in V1_TO_V2_CHANGES.values())


def test_documented_v1_table_is_the_published_one():
    """The packaged v1 rows, and the rows in the history file, are
    byte-identical to the matrix published in docs/RESULTS.md at b535b1a."""
    import subprocess
    from pathlib import Path
    from assurance_lab.distributed.metamorphic import PUBLISHED_V1_TABLE
    root = Path(__file__).resolve().parents[1]
    rows = lambda s: [ln for ln in s.splitlines() if ln.startswith("| `")]  # noqa: E731
    history = (root / "docs/METAMORPHIC_MATRIX_HISTORY.md").read_text().split("## v2")[0]
    assert rows(history) == rows(PUBLISHED_V1_TABLE) and len(rows(PUBLISHED_V1_TABLE)) == len(FLAGS)
    try:
        published = subprocess.run(["git", "show", "b535b1a:docs/RESULTS.md"], cwd=root, capture_output=True,
                                   text=True, check=True).stdout
    except subprocess.CalledProcessError:
        pytest.skip("b535b1a not in this clone")
    section = published.split("## Metamorphic relations × negative controls")[1].split("\n## ")[0]
    assert rows(section) == rows(PUBLISHED_V1_TABLE)


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


def test_corrupted_snapshots_are_refused_and_each_control_is_reported_separately():
    """Production boundary: every inconsistent or malformed snapshot is
    refused at thaw, deterministically, with no other exception. Bypassed
    (diagnostic only): head() answers UNKNOWN, never authority, and changes
    nothing. Consistent omissions pass the boundary by construction; the
    relation's layers are measured on them with the boundary on."""
    from assurance_lab.distributed.metamorphic import corruption_table
    rows = {r["corruption"]: r for r in corruption_table()}
    for name, r in rows.items():
        assert r["infected_snapshots"] > 0, name
        assert r["validator"]["other_exceptions"] == [] and r["validator"]["deterministic"], name
        assert r["bypass"]["exceptions"] == [] and r["bypass"]["state_changed_by_head"] == 0, name
        if r["class"] in ("inconsistent", "malformed"):
            assert r["validator"]["refused"] == r["infected_snapshots"], name
            assert r["bypass"]["authority_from_damaged_state"] == 0, name
            assert set(r["bypass"]["statuses"]) <= {"UNKNOWN"}, name
        else:
            assert r["validator"]["accepted"] == r["infected_snapshots"], name  # integrity is not authenticity
    assert rows["truncate the snapshot"]["bypass"]["refused_even_unvalidated"] == \
        rows["truncate the snapshot"]["infected_snapshots"]
    rollback = rows["roll back an applied invalidation"]["relation"]
    pending = rows["omit a pending event"]["relation"]
    rejected = rows["omit a rejected event"]["relation"]
    assert rollback["projection"] > 0  # a stale head is restored: visible only against the true state
    assert pending["projection"] == 0 < pending["next-event"] <= pending["exact-state"]  # projection ⇏ behavior
    assert rejected["projection"] == rejected["next-event"] == 0 < rejected["exact-state"]  # behavior ⇏ exact


def test_thaw_refuses_inconsistent_snapshots_and_head_never_crashes():
    from assurance_lab.distributed.metamorphic import CORRUPTIONS
    from assurance_lab.distributed.replica import CorruptSnapshot, Replica
    inst = next(i for i in instances() if i.name == "fork+resolution+invalidate-winner")
    evs = list(inst.events)
    states = [run(inst, o[:i], {}).freeze() for o in (evs, evs[::-1]) for i in range(len(evs) + 1)]
    for frozen in states:
        Replica.thaw(inst.policy, frozen)  # every genuine snapshot is accepted
    for name, (cls, fn) in CORRUPTIONS.items():
        infected = [f for f in states if fn(f) != f]
        assert infected or cls == "omission", name
        for frozen in infected:
            if cls == "omission":
                Replica.thaw(inst.policy, fn(frozen))  # indistinguishable from a replica with less history
                continue
            with pytest.raises(CorruptSnapshot):
                Replica.thaw(inst.policy, fn(frozen))
            if cls == "malformed" and not isinstance(fn(frozen), tuple) or len(fn(frozen)) != 7:
                continue  # not a snapshot at all: refused even without validation
            unsafe = Replica.thaw(inst.policy, fn(frozen), validate=False)
            assert unsafe.head("L") == "UNKNOWN"  # fails closed, never raises, never CONFLICT


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


# ------------------------------------------------------------ the matrix cache

SMALL = ("chain", "fork")


def test_cached_and_uncached_matrices_are_identical():
    """Every cell, denominator, anomaly and recorded deviation."""
    from assurance_lab.distributed.metamorphic import matrix_anomalies, matrix_canonical
    cached, fresh = mutant_matrix(), mutant_matrix(cache=False)
    assert matrix_canonical(cached) == matrix_canonical(fresh)
    assert matrix_anomalies(cached) == matrix_anomalies(fresh) == []


def test_cache_key_binds_every_input(monkeypatch):
    """Live code changes the code fingerprint; with that held fixed, each data
    input on its own (execution configuration, policy, events, alphabet,
    eligibility values, fixture set) still changes the key."""
    from dataclasses import replace
    from assurance_lab.distributed import metamorphic as mm
    from assurance_lab.distributed.replica import Invalidate, Replica, ResolutionPolicy
    base = mm.matrix_inputs_fingerprint(SMALL)
    assert mm.matrix_inputs_fingerprint(SMALL) == base
    code = mm.code_fingerprint()
    for change in (lambda m: m.setattr(Replica, "_edge_rule", staticmethod(lambda a, b: None)),
                   lambda m: m.setitem(mm.ELIGIBILITY, "rid_tiebreak", ("head", "x", lambda evs, pol: True))):
        with monkeypatch.context() as m:
            change(m)
            assert mm.code_fingerprint() != code
    monkeypatch.setattr(mm, "code_fingerprint", lambda: "held fixed")
    fixed = mm.matrix_inputs_fingerprint(SMALL)
    build, boundary = mm.build, mm.named_boundary_events
    data_changes = {
        "fixture set": lambda m: m.setattr(mm, "KINDS", mm.KINDS),  # placeholder: checked via kinds below
        "execution configuration": lambda m: m.setitem(mm.EXECUTION_CONFIG, "projection_max_exhaustive", 100),
        "policy": lambda m: m.setattr(mm, "build", lambda kind, **kw: replace(
            build(kind, **kw), policy=ResolutionPolicy(build(kind, **kw).policy.authorities, 3))),
        "events": lambda m: m.setattr(mm, "build", lambda kind, **kw: replace(
            build(kind, **kw), events=build(kind, **kw).events + (Invalidate("r0"),))),
        "alphabet": lambda m: m.setattr(mm, "named_boundary_events", lambda: boundary()[:-1]),
        "eligibility values": lambda m: m.setitem(mm.ELIGIBILITY, "rid_tiebreak",
                                                  ("head", "x", lambda evs, pol: False)),
    }
    assert mm.matrix_inputs_fingerprint(("chain",)) != fixed
    for name, change in data_changes.items():
        if name == "fixture set":
            continue
        with monkeypatch.context() as m:
            change(m)
            assert mm.matrix_inputs_fingerprint(SMALL) != fixed, name


def test_stale_cache_cannot_turn_a_new_failure_into_an_old_pass(monkeypatch):
    """Negative control: compute (and cache) a clean matrix, then break a
    frozen predicate. The next call must recompute and report the anomaly,
    not return the cached PASS."""
    from assurance_lab.distributed import metamorphic as mm
    assert mm.matrix_anomalies(mm.mutant_matrix(SMALL)) == []
    op, what, _pred = mm.ELIGIBILITY["naive_stale_guard"]
    monkeypatch.setitem(mm.ELIGIBILITY, "naive_stale_guard", (op, what, lambda evs, pol: False))
    anomalies = mm.matrix_anomalies(mm.mutant_matrix(SMALL))
    assert "order/naive_stale_guard: deviation on ineligible chain" in anomalies


_DETERMINISM_PROBE = r"""
import hashlib, json
from assurance_lab.distributed import metamorphic as mm
m = mm.mutant_matrix(("fork+dispute", "chain+invalidate"), cache=False)
blob = json.dumps({"matrix": mm.matrix_canonical(m), "key": mm.matrix_inputs_fingerprint(("fork+dispute", "chain+invalidate")),
                   "corruptions": mm.corruption_table(), "alphabet": mm.alphabet_exercise()}, sort_keys=True)
print(hashlib.sha256(blob.encode()).hexdigest())
"""


def test_matrix_and_tables_are_identical_across_hash_seeds():
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    digests = {subprocess.run([sys.executable, "-c", _DETERMINISM_PROBE], cwd=root, capture_output=True, text=True,
                              check=True, env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(root)}
                              ).stdout.strip() for seed in ("0", "1", "987")}
    assert len(digests) == 1
