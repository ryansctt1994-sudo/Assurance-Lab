"""Retained counterexamples: corrupted replica snapshots that made ``head()``
raise ``KeyError`` before the repair (tests/fixtures/snapshot_counterexamples.json).

A counterexample is kept as data and checked three ways: it is still
derivable from its recipe; it still crashes the code it was found in (and the
published main baseline, which was never repaired); and the repaired code
refuses it at the restore boundary and, with that boundary bypassed, answers
UNKNOWN (never CONFLICT, never a record) without changing the state."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from assurance_lab.distributed import replica  # noqa: E402
from assurance_lab.distributed import snapshot_json as sj  # noqa: E402
from assurance_lab.distributed.replica import (  # noqa: E402
    CONFLICT, INVALID_SNAPSHOT, UNKNOWN, CorruptSnapshot, Replica,
)
import make_snapshot_counterexamples as gen  # noqa: E402

DOC = json.loads((ROOT / "tests/fixtures/snapshot_counterexamples.json").read_text(encoding="utf-8"))
CES = {ce["id"]: ce for ce in DOC["counterexamples"]}


def _snapshot(ce, mod=replica):
    return sj.decode(ce["snapshot"], mod)


def _policy(ce):
    return gen.mm.build(ce["instance"]).policy


def _fields(rep):
    return copy.deepcopy({k: getattr(rep, k) for k in
                          ("admitted", "edges", "invalidated", "resolutions", "rejected", "pending", "seen")})


@pytest.mark.parametrize("cid", sorted(CES))
def test_counterexample_is_derivable_from_its_recipe(cid):
    ce = CES[cid]
    _inst, prefix, damaged = gen.derive(ce)
    assert sj.encode(damaged) == ce["snapshot"]
    assert sj.encode_events(prefix) == ce["prefix_events"]
    assert sj.decode(ce["snapshot"], replica) == damaged  # the codec round-trips


@pytest.mark.parametrize("cid", sorted(CES))
@pytest.mark.parametrize("rev", gen.REVISIONS)
def test_counterexample_still_crashes_the_unrepaired_code(cid, rev, tmp_path):
    """b535b1a is the development code it was found in; c175fbd is the
    published main baseline, which has the same defect and was not repaired."""
    try:
        mod = sj.load_replica_at(rev, ROOT, tmp_path)
    except Exception:
        pytest.skip(f"{rev} not in this clone")
    ce = CES[cid]
    got = gen.failure_at(mod, _policy(ce), ce["snapshot"])
    assert got == ce["historical_failures"][rev]
    assert got["exception"] == "KeyError"


@pytest.mark.parametrize("cid", sorted(CES))
def test_repaired_restore_refuses_it_deterministically(cid):
    ce = CES[cid]
    with pytest.raises(CorruptSnapshot) as first:
        Replica.thaw(_policy(ce), _snapshot(ce))
    with pytest.raises(CorruptSnapshot) as second:
        Replica.thaw(_policy(ce), _snapshot(ce))
    assert first.value.status == INVALID_SNAPSHOT
    assert first.value.problems == second.value.problems == tuple(sorted(first.value.problems))
    assert any("references a record that is not admitted" in p for p in first.value.problems)


@pytest.mark.parametrize("cid", sorted(CES))
def test_bypassed_boundary_gives_unknown_not_conflict_and_changes_nothing(cid):
    ce = CES[cid]
    rep = Replica.thaw(_policy(ce), _snapshot(ce), validate=False)  # diagnostic bypass only
    before = _fields(rep)
    verdict = rep.explain_head(ce["lineage"])
    assert verdict.status == "UNKNOWN" and verdict.value == UNKNOWN != CONFLICT
    assert verdict.reason.startswith("integrity: ") and "not admitted" in verdict.reason
    assert rep.head(ce["lineage"]) == UNKNOWN
    assert _fields(rep) == before  # head() repaired, discarded and normalized nothing


def test_the_same_fork_undamaged_is_a_genuine_conflict():
    """CE-2's instance, undamaged, is a real authority dispute: CONFLICT with
    its reason. Damaged, it is UNKNOWN: the two stay distinguishable."""
    inst = gen.mm.build("fork+dispute")
    rep = Replica(inst.policy)
    for e in inst.events:
        rep.deliver(e)
    verdict = rep.explain_head("L")
    assert verdict.status == "CONFLICT" and verdict.reason == "authorities disagree on the fork at r0"
    assert rep.integrity_problems() == []
