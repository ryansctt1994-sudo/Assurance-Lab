"""The restore boundary and head(): INVALID_SNAPSHOT, CONFLICT and UNKNOWN stay
distinct, head() is read-only, genuine states are never rejected, refusals
are deterministic, and the declared alphabet is exercised."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

from assurance_lab.distributed import metamorphic as mm
from assurance_lab.distributed.mc import SCENARIOS, check_scenario
from assurance_lab.distributed.replica import CONFLICT, UNKNOWN, CorruptSnapshot, Replica

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("admitted", "edges", "invalidated", "resolutions", "rejected", "pending", "seen")


def _genuine_states():
    for inst in mm.instances():
        for pre in mm.serialization_prefixes(inst):
            yield inst, mm.run(inst, list(pre), {})


def test_genuine_states_pass_integrity_and_are_never_unknown():
    """Zero false rejections and zero false UNKNOWN over every serialized
    prefix of every instance (the model checker separately thaws every
    explored state through the validator; see the next test)."""
    n = 0
    for inst, rep in _genuine_states():
        assert rep.integrity_problems() == [], inst.name
        Replica.thaw(inst.policy, rep.freeze())
        for lin in inst.lineages + ("M",):
            assert rep.explain_head(lin).status != "UNKNOWN", inst.name
        n += 1
    assert n == 164


def test_model_checker_explored_states_are_all_accepted(monkeypatch):
    """Every replica snapshot the replica model checker explores goes through
    the validating thaw; count them and require zero refusals."""
    thawed, refused = set(), []
    real = Replica.thaw.__func__

    def counting(cls, policy, frozen, validate=True, **flags):
        thawed.add(frozen)
        try:
            return real(cls, policy, frozen, validate, **flags)
        except CorruptSnapshot as e:
            refused.append(e)
            raise

    monkeypatch.setattr(Replica, "thaw", classmethod(counting))
    for sc in SCENARIOS[:2]:  # small scenarios keep this test fast; the receipt covers all
        assert check_scenario(sc).violation is None
    assert refused == [] and len(thawed) >= 30  # distinct replica snapshots; the receipt counts all scenarios


def test_head_is_read_only_on_genuine_and_damaged_states():
    states = [rep for _inst, rep in _genuine_states()]
    for _name, (cls, fn) in mm.CORRUPTIONS.items():
        if cls == "malformed":
            continue
        for inst, _pre, _genuine, damaged in mm.corruption_snapshots(fn):
            states.append(Replica.thaw(inst.policy, damaged, validate=False))
    for rep in states:
        before = copy.deepcopy([getattr(rep, f) for f in FIELDS])
        for lin in ("L", "M"):
            rep.explain_head(lin)
            rep.head(lin)
        assert [getattr(rep, f) for f in FIELDS] == before


def test_unknown_conflict_and_invalid_snapshot_are_distinct():
    fork = mm.build("fork")  # an unresolved fork: a genuine CONFLICT
    rep = Replica(fork.policy)
    for e in fork.events:
        rep.deliver(e)
    genuine = rep.explain_head("L")
    assert genuine.status == "CONFLICT" and genuine.reason == "unresolved fork at r0"
    damaged = mm.CORRUPTIONS["drop one admitted record"][1](rep.freeze())
    try:
        Replica.thaw(fork.policy, damaged)
        raise AssertionError("a damaged snapshot was restored")
    except CorruptSnapshot as e:
        assert e.status == "INVALID_SNAPSHOT" and e.problems
    bypassed = Replica.thaw(fork.policy, damaged, validate=False).explain_head("L")
    assert bypassed.status == "UNKNOWN" and bypassed.value == UNKNOWN != CONFLICT
    assert bypassed.reason.startswith("integrity: ")


_PROBE = r"""
import json
from assurance_lab.distributed import metamorphic as mm
from assurance_lab.distributed.replica import CorruptSnapshot, Replica
out = []
for name, (cls, fn) in mm.CORRUPTIONS.items():
    for inst, pre, _g, damaged in mm.corruption_snapshots(fn):
        try:
            Replica.thaw(inst.policy, damaged)
            out.append([name, inst.name, len(pre), "accepted"])
        except CorruptSnapshot as e:
            out.append([name, inst.name, len(pre), list(e.problems)])
print(json.dumps(out))
"""


def test_refusals_are_identical_across_processes_and_hash_seeds():
    runs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
        p = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                           check=True)
        runs.append(json.loads(p.stdout))
    assert runs[0] == runs[1] == runs[2] and len(runs[0]) > 500


def test_declared_alphabet_is_exercised_including_rejections():
    r = mm.alphabet_exercise()
    assert r["never_delivered"] == [] and r["unexpected"] == {}
    for name, outcomes in r["boundary"].items():
        assert sum(outcomes.values()) > 0, name
    assert all(any(o.startswith("rejected") for o in mm.BOUNDARY_EXPECT[b]) <=
               any(o.startswith("rejected") for o in r["boundary"][b]) for b in r["boundary"]
               if b not in ("Edge(r0->m0)",))
    # The cross-lineage rule needs both ends admitted, which no single event
    # after an L-only prefix can do; the declared two-event sequence reaches
    # it. The direct unit test (tests/test_distributed.py) stays as narrower,
    # separate evidence.
    assert r["rejection_rules_not_exercised"] == []
    assert r["boundary"]["cross-lineage edge, after admitting its M end"].get("rejected: cross-lineage edge", 0) > 0


def _unknown_when(pred):
    """explain_head that answers UNKNOWN exactly where ``pred(replica)`` holds."""
    from assurance_lab.distributed.replica import HeadResult
    real = Replica.explain_head

    def patched(self, lineage):
        if pred(self):
            return HeadResult("UNKNOWN", UNKNOWN, "integrity: injected by a negative control")
        return real(self, lineage)
    return patched


def test_unknown_fails_model_checking_at_an_intermediate_state(monkeypatch):
    monkeypatch.setattr(Replica, "explain_head", _unknown_when(lambda r: len(r.seen) == 1))
    v = check_scenario(SCENARIOS[0]).violation
    assert v and "NoFalseIntegrityFailure" in v and "quiescence" not in v


def test_unknown_fails_model_checking_at_quiescence_even_without_the_per_state_check(monkeypatch):
    """Negative control for the quiescence check on its own: the per-state
    check is disabled, and UNKNOWN appears only once every event is applied."""
    from assurance_lab.distributed import mc
    sc = SCENARIOS[0]
    total = len({e for script in sc.scripts for e in script})
    monkeypatch.setattr(mc, "safety_violations", lambda rep, lineage: [])
    monkeypatch.setattr(Replica, "explain_head", _unknown_when(lambda r: len(r.seen) == total and not r.pending))
    v = check_scenario(sc).violation
    assert v == "NoFalseIntegrityFailure: head UNKNOWN at quiescence"
