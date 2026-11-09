"""AL-SNAPSHOT-001: a damaged replica snapshot made ``head()`` raise KeyError.

Found on the development lineage and reproduced on this baseline: dropping one
admitted record from an otherwise genuine frozen snapshot leaves edges (and,
with resolutions, a resolution source) that reference a record no longer
admitted. ``Replica.thaw`` restored it without checking, and ``head()`` then
indexed the missing record:

* CE-1 ``_chain_linked``: ``cur = self.admitted[cur.pred]``  -> KeyError('r1')
* CE-2 the disputed-source check: ``self.admitted[src]``      -> KeyError('r0')

Required after the repair: restoring such a snapshot is refused
(``CorruptSnapshot``, status INVALID_SNAPSHOT, stable reasons); if restoring is
deliberately unvalidated, ``head()`` answers UNKNOWN, never a record, None or
CONFLICT, and never raises; every genuine snapshot is still accepted.
"""

import pytest

from assurance_lab.distributed import replica as R
from assurance_lab.distributed.replica import (
    CONFLICT, Admit, Edge, Replica, ResolutionPolicy, make_resolution,
)

POLICY = ResolutionPolicy(frozenset({"alice", "bob", "carol"}), 2)
R0, R1 = Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0")
CHAIN = (R0, R1, Admit("r2", "L", 2, "carol", pred="r1"), Edge("r0", "r1"), Edge("r1", "r2"))
FORK = (R0, R1, Admit("r2", "L", 2, "carol", pred="r0"), Edge("r0", "r1"), Edge("r0", "r2"))


def _fork_dispute():
    scratch = Replica(POLICY)
    for e in FORK:
        scratch.deliver(e)
    a = make_resolution(scratch, "r0", "r1", "r2", {"alice", "carol"})
    b = make_resolution(scratch, "r0", "r2", "r1", {"alice", "bob"})
    return FORK + (a, b)


#: (name, delivered events) -> genuine snapshot; damage drops the first admitted record
CASES = {
    "CE-1 chain, reversed prefix of 4": list(reversed(CHAIN))[:4],
    "CE-2 fork with two disputing resolutions": list(_fork_dispute()),
}


def _drop_first_admitted(frozen: tuple) -> tuple:
    return (frozen[0][1:], *frozen[1:])


def _damaged(events):
    rep = Replica(POLICY)
    for e in events:
        rep.deliver(e)
    genuine = rep.freeze()
    damaged = _drop_first_admitted(genuine)
    assert damaged != genuine
    return damaged


def _thaw_unvalidated(frozen):
    """Restore without the boundary check (diagnostic path). Before the
    repair there is no check at all, so this is plain ``thaw``."""
    try:
        return Replica.thaw(POLICY, frozen, validate=False)
    except TypeError:  # unrepaired: thaw takes no ``validate`` argument
        return Replica.thaw(POLICY, frozen)


@pytest.mark.parametrize("name", sorted(CASES))
def test_head_on_a_damaged_snapshot_does_not_raise(name):
    """The reproduction: unrepaired, this raises KeyError inside head()."""
    rep = _thaw_unvalidated(_damaged(CASES[name]))
    result = rep.head("L")
    assert result == getattr(R, "UNKNOWN", "<no UNKNOWN outcome>")
    assert result != CONFLICT and result not in rep.admitted and result is not None


@pytest.mark.parametrize("name", sorted(CASES))
def test_restoring_a_damaged_snapshot_is_refused_with_stable_reasons(name):
    corrupt = getattr(R, "CorruptSnapshot", None)
    assert corrupt is not None, "no refusal outcome exists for a damaged snapshot"
    with pytest.raises(corrupt) as first:
        Replica.thaw(POLICY, _damaged(CASES[name]))
    with pytest.raises(corrupt) as second:
        Replica.thaw(POLICY, _damaged(CASES[name]))
    assert first.value.status == "INVALID_SNAPSHOT"
    assert first.value.problems == second.value.problems == tuple(sorted(first.value.problems))
    assert any("not admitted" in p for p in first.value.problems)


def test_every_genuine_snapshot_is_still_accepted():
    """Zero false refusals: every prefix of both delivery orders of both
    scenarios restores, and head() is never UNKNOWN on a genuine state."""
    n = 0
    for events in (CHAIN, FORK, tuple(_fork_dispute())):
        for order in (list(events), list(reversed(events))):
            for i in range(len(order) + 1):
                rep = Replica(POLICY)
                for e in order[:i]:
                    rep.deliver(e)
                restored = Replica.thaw(POLICY, rep.freeze())
                assert restored.head("L") == rep.head("L") != getattr(R, "UNKNOWN", object())
                n += 1
    assert n == 2 * (6 + 6 + 8)


# --- the model checker must not let the new outcomes pass silently -----------

def _patched_head(pred):
    real = Replica.head
    return lambda self, lineage: R.UNKNOWN if pred(self) else real(self, lineage)


def test_unknown_on_an_explored_state_fails_the_model_checker(monkeypatch):
    from assurance_lab.distributed.mc import SCENARIOS, check_scenario
    monkeypatch.setattr(Replica, "head", _patched_head(lambda r: len(r.seen) == 1))
    v = check_scenario(SCENARIOS[0]).violation
    assert v and "head UNKNOWN on an explored state" in v


def test_unknown_at_quiescence_fails_even_without_the_per_state_check(monkeypatch):
    """Replicas that all answer UNKNOWN agree, and that is not convergence."""
    from assurance_lab.distributed import mc
    sc = mc.SCENARIOS[0]
    total = len({e for script in sc.scripts for e in script})
    monkeypatch.setattr(mc, "safety_violations", lambda rep, lineage: [])
    monkeypatch.setattr(Replica, "head", _patched_head(lambda r: len(r.seen) == total and not r.pending))
    assert mc.check_scenario(sc).violation == "head UNKNOWN at quiescence"


def test_a_refused_snapshot_is_a_recorded_model_checker_failure(monkeypatch):
    """A refusal on an explored state is a failure with a trace, never an
    escaping exception and never a pass."""
    from assurance_lab.distributed.mc import SCENARIOS, check_scenario
    real = Replica.reference_problems
    monkeypatch.setattr(Replica, "reference_problems",
                        lambda self: ["injected refusal"] if len(self.seen) == 2 else real(self))
    r = check_scenario(SCENARIOS[0])
    assert r.violation.startswith("INVALID_SNAPSHOT") and "injected refusal" in r.violation and r.trace
