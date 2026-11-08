import pytest

from assurance_lab.store.recovery import (
    Disk, MonotoneAnchor, commit, derive_heads, is_torn, recover, supersede_with_crash,
    verify_checkpoint,
)
from assurance_lab.store.retrieval import NoCurrent, StoredRecord, Store, SupersessionConflict


def chain_store():
    st = Store()
    # Stale records are *more* relevant than the live successor.
    st.put(StoredRecord("r0", "L", 0.9))
    st.put(StoredRecord("r1", "L", 0.8))
    st.put(StoredRecord("r2", "L", 0.5))
    st.supersede("r0", "r1")
    st.supersede("r1", "r2")
    return st


# --- §23 filter before rank ---------------------------------------------------


def test_rank_then_filter_loses_live_successor():
    assert chain_store().rank_then_filter(k=2) == []  # budget eaten by stale records


def test_filter_then_rank_resolves_to_head():
    assert chain_store().filter_then_rank(k=1) == ["r2"]


def test_cross_lineage_supersession_rejected():
    st = chain_store()
    st.put(StoredRecord("x", "OTHER", 0.1))
    with pytest.raises(ValueError):
        st.supersede("r2", "x")


# --- §24 fast-forward read repair ---------------------------------------------


def test_stale_handle_fast_forwards_at_effect_time():
    st = Store()
    st.put(StoredRecord("r0", "L", 1.0))
    handle = st.filter_then_rank(1)[0]
    assert handle == "r0"
    st.put(StoredRecord("r1", "L", 0.1))
    st.supersede("r0", "r1")  # supersession between read and consequence
    assert st.resolve_at_effect(handle) == "r1"


def test_read_repair_never_falls_back_to_predecessor():
    st = chain_store()
    st.records["r2"].invalidated = True
    with pytest.raises(NoCurrent):
        st.resolve_at_effect("r0")  # NOT r1: NoCurrent ⇏ ∃ usable x


def test_read_repair_fails_closed_on_fork():
    st = chain_store()
    st.put(StoredRecord("r3", "L", 0.1))
    st.supersede("r1", "r3")
    with pytest.raises(SupersessionConflict):
        st.resolve_at_effect("r0")


# --- §25 compaction ---------------------------------------------------------


def test_safe_compaction_preserves_supersession():
    st = chain_store()
    st.compact_safe(keep_payload=set())
    assert st.records["r0"].payload is None and st.records["r2"].payload is not None
    assert not st.is_current("r0") and st.filter_then_rank(3) == ["r2"]


def test_unsafe_compaction_resurrects_stale_record():
    """Negative control: dropping r1 and its edges makes r0 look terminal."""
    st = chain_store()
    st.compact_unsafe({"r1"})
    assert st.is_current("r0")  # the dangerous outcome is real
    assert set(st.filter_then_rank(3)) == {"r0", "r2"}  # two heads in lineage L


# --- §26 crash recovery ---------------------------------------------------------


def base_disk():
    d = Disk(nodes={"r0", "r1"}, edges={("r0", "r1")}, lineage={"r0": "L", "r1": "L", "r2": "L"})
    d.head_cache = derive_heads(d.nodes, d.edges, d.lineage)
    return d


@pytest.mark.parametrize("persist_edge", [False, True])
@pytest.mark.parametrize("persist_head", [False, True])
def test_four_crash_combinations_recover(persist_edge, persist_head):
    crashed = supersede_with_crash(base_disk(), "r1", "r2", persist_edge, persist_head)
    expect_torn = persist_edge != persist_head
    assert is_torn(crashed) == expect_torn
    rec = recover(crashed)
    assert not is_torn(rec)
    assert rec.head_cache["L"] == ("r2" if persist_edge else "r1")


def test_rollback_to_coherent_stale_generation_needs_checkpoint():
    anchor = MonotoneAnchor()
    g1 = commit(base_disk(), anchor)
    old = g1.snapshot()
    g2 = commit(supersede_with_crash(g1, "r1", "r2", True, True), anchor)
    assert verify_checkpoint(g2, anchor)
    # Attacker/operator restores the older, internally coherent generation.
    assert not is_torn(old)  # internal consistency cannot see the rollback ...
    assert not verify_checkpoint(old, anchor)  # ... the monotone checkpoint can.


def test_anchor_is_monotone():
    a = MonotoneAnchor()
    a.commit(1, "x")
    with pytest.raises(ValueError):
        a.commit(1, "y")
