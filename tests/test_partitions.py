"""Partition integrity: reconciliation against a manifest frozen before execution.

Every case uses real evidence produced by real worker runs; the faults are
injected into the evidence package afterwards (or, for overlap, into a
worker's assignment), as a defective aggregator or scheduler would.
"""

import multiprocessing
import os
from concurrent.futures.process import BrokenProcessPool
from dataclasses import replace

import pytest

from assurance_lab.checker.induction import _check_parts, _evidence, check_induction
from assurance_lab.checker.partitions import aggregate, build_manifest, manifest_problems, reconcile
from assurance_lab.core.model import Config, CoreModel

CFG = Config(n_records=2, n_lineages=1)
FORK = multiprocessing.get_context("fork") if "fork" in multiprocessing.get_all_start_methods() else None


@pytest.fixture(scope="module")
def keyed():
    key = b"k" * 32
    m = build_manifest(CFG, "run-A")
    r = check_induction(CoreModel(CFG), manifest=m, key=key)
    assert r.complete and r.reconciliation_problems == []
    return m, key, r.evidence, aggregate(r.evidence)


def problems(m, key, evs, claimed=None):
    return reconcile(m, evs, key, claimed)[1]


def test_reorder_is_accepted_with_identical_aggregate(keyed):
    m, key, evs, agg = keyed
    got, probs = reconcile(m, list(reversed(evs)), key)
    assert probs == [] and got == agg


def test_removed_partition_is_incomplete_coverage(keyed):
    m, key, evs, _ = keyed
    assert any(p.startswith("incomplete coverage") for p in problems(m, key, evs[1:]))


def test_repeated_partition_is_duplicate_identity(keyed):
    m, key, evs, _ = keyed
    assert any(p.startswith("duplicate partition identity") for p in problems(m, key, evs + evs[:1]))


def test_overlapping_partitions_are_non_disjoint(keyed):
    """A worker wrongly assigned two partitions' keys under one id signs a
    perfectly valid record; reconciliation must still refuse it."""
    m, key, evs, _ = keyed
    (pid0, lv0, sn0), (_, lv1, sn1) = m.entries[0], m.entries[1]
    wide = _evidence(_check_parts(CoreModel(CFG), [(tuple(lv0), sn0), (tuple(lv1), sn1)], None), pid0, m, key)
    bad = [wide if e.pid == pid0 else e for e in evs]
    assert any(p.startswith("non-disjoint coverage") for p in problems(m, key, bad))


def test_changed_aggregate_count_is_arithmetic_inconsistency(keyed):
    m, key, evs, agg = keyed
    assert any(p.startswith("arithmetic inconsistency")
               for p in problems(m, key, evs, claimed=replace(agg, count=agg.count + 1)))


def test_partition_from_another_run_is_provenance_mismatch(keyed):
    m, key, evs, _ = keyed
    other = check_induction(CoreModel(CFG), manifest=build_manifest(CFG, "run-B"), key=b"z" * 32)
    swapped = [other.evidence[3] if e.pid == other.evidence[3].pid else e for e in evs]
    assert any(p.startswith("provenance mismatch") for p in problems(m, key, swapped))


def test_corrupted_partition_digest_is_integrity_mismatch(keyed):
    m, key, evs, _ = keyed
    e = evs[2]
    enc, count, hexd = e.digest.split(":")
    corrupt = replace(e, digest=f"{enc}:{count}:{'0' * 64}")
    bad = [corrupt if x.pid == e.pid else x for x in evs]
    assert any(p.startswith("integrity mismatch") for p in problems(m, key, bad))


def test_internally_consistent_but_incomplete_package_is_rejected(keyed):
    """The decisive case: drop a partition and recompute everything from the
    remainder. Every number in the package agrees with every other, so a
    self-consistency check accepts it; the frozen manifest does not."""
    m, key, evs, _ = keyed
    target = max(evs, key=lambda e: e.count)  # drop real content, not an empty partition
    package = [e for e in evs if e is not target]
    agg = aggregate(package)
    # self-consistent: aggregate count equals the digest's own count, etc.
    assert int(agg.digest.split(":")[1]) == agg.count
    assert int(agg.transition_digest.split(":")[1]) == agg.transitions
    assert agg.step_ok and agg.safety_ok and agg.complete
    probs = problems(m, key, package, claimed=agg)
    assert probs and all(p.startswith("incomplete coverage") for p in probs)


def test_manifest_coverage_obligation_is_checked_statically():
    m = build_manifest(CFG, "r")
    assert manifest_problems(m, CFG) == []
    assert manifest_problems(replace(m, entries=m.entries[1:]), CFG)  # a missing key
    dup = (m.entries[0][0],) + m.entries[1][1:]
    assert manifest_problems(replace(m, entries=(m.entries[0], dup) + m.entries[2:]), CFG)


def test_sequential_and_pool_reconcile_to_the_same_aggregate():
    if FORK is None:
        pytest.skip("fork unavailable")
    a = check_induction(CoreModel(CFG))
    b = check_induction(CoreModel(CFG), workers=2, mp_context=FORK)
    assert aggregate(a.evidence) == aggregate(b.evidence)


# --- worker crash and report publication ---------------------------------------------------

_PARENT = os.getpid()


class CrashInWorker(CoreModel):
    """Simulates a worker killed mid-partition."""

    name = "crash_in_worker"

    def next(self, s):
        if os.getpid() != _PARENT:
            os._exit(7)
        return super().next(s)


@pytest.mark.skipif(FORK is None, reason="fork unavailable")
def test_terminated_worker_raises_and_yields_no_report_object():
    with pytest.raises(BrokenProcessPool):
        check_induction(CrashInWorker(CFG), workers=2, mp_context=FORK)


@pytest.mark.skipif(FORK is None, reason="fork unavailable")
def test_campaign_with_a_terminated_worker_publishes_nothing(tmp_path, monkeypatch):
    import assurance_lab.__main__ as campaign
    out = tmp_path / "RESULTS.md"
    out.write_text("previous accepted report\n")
    published = []
    monkeypatch.setattr(campaign, "QUICK_CORE", CFG)
    monkeypatch.setattr(campaign, "CoreModel", CrashInWorker)
    monkeypatch.setattr(campaign, "publish", lambda *a: published.append(a))
    with pytest.raises(BrokenProcessPool):
        campaign.main(["--in-place", "--allow-dirty", "--out", str(out), "--workers", "2",
                       "--start-method", "fork"])
    assert published == [] and out.read_text() == "previous accepted report\n"


def test_publication_is_atomic(tmp_path, monkeypatch):
    """An interruption at the moment of publication leaves the previous file
    intact and no partially written report under the final name."""
    import assurance_lab.__main__ as campaign
    out = tmp_path / "RESULTS.md"
    out.write_text("previous accepted report\n")

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(campaign.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        campaign.publish(str(out), "# Verification Results\n...all PASS...\n")
    assert out.read_text() == "previous accepted report\n"
    assert [p.name for p in tmp_path.iterdir()] == ["RESULTS.md"]  # temp file cleaned up
