"""§27-28: finite-schedule eventual convergence and successor conflicts."""

import itertools

from assurance_lab.distributed.replica import (
    CONFLICT, Admit, Edge, Replica, ResolutionPolicy, make_resolution,
)

POLICY = ResolutionPolicy(authorities=frozenset({"alice", "bob", "carol"}), min_quorum=2)

CHAIN = [
    Admit("r0", "L", 0, "alice"),
    Admit("r1", "L", 1, "bob", pred="r0"),
    Admit("r2", "L", 2, "carol", pred="r1"),
    Edge("r0", "r1"),
    Edge("r1", "r2"),
]


def run(events, **kw):
    rep = Replica(POLICY, **kw)
    heads = []
    for e in events:
        rep.deliver(e)
        heads.append(rep.head("L"))
    return rep, heads


def test_all_120_schedules_converge():
    finals = set()
    perms = list(itertools.permutations(CHAIN))
    assert len(perms) == 120
    for p in perms:
        rep, heads = run(p)
        finals.add((rep.canonical(), rep.head("L")))
        assert not rep.pending and not rep.rejected
        assert CONFLICT not in heads  # no transient double head
    assert len(finals) == 1 and next(iter(finals))[1] == "r2"


def test_duplicate_delivery_is_idempotent():
    ref, _ = run(CHAIN)
    for p in itertools.permutations(CHAIN):
        for i in range(len(p) + 1):
            for dup in p:
                rep, _ = run(list(p[:i]) + [dup] + list(p[i:]))
                assert rep.canonical() == ref.canonical()


def test_out_of_order_edge_is_deferred_not_dropped():
    rep = Replica(POLICY)
    rep.deliver(Edge("r0", "r1"))
    assert rep.pending and not rep.edges
    rep.deliver(CHAIN[0])
    rep.deliver(CHAIN[1])
    assert ("r0", "r1") in rep.edges and not rep.pending


def test_retained_partial_chain_counterexample():
    """Found while building this repo: with Edge(r1,r2) applied before
    Edge(r0,r1), checking only a record's own edge made r0 and r2 both
    eligible. Eligibility now requires the whole chain to be linked."""
    rep, heads = run([CHAIN[0], CHAIN[1], CHAIN[2], Edge("r1", "r2"), Edge("r0", "r1")])
    assert heads == ["r0", "r0", "r0", "r0", "r2"]


def test_cross_lineage_edge_rejected():
    rep, _ = run([Admit("a", "L", 0, "alice"), Admit("b", "M", 1, "bob", pred="a"), Edge("a", "b")])
    assert not rep.edges and any("cross-lineage" in why for _, why in rep.rejected)


def test_handoff_failure_3_naive_stale_guard_diverges():
    """Retained counterexample: rejecting an edge because the successor was
    already superseded breaks convergence. The fixed replica appends it."""
    order = [CHAIN[0], CHAIN[1], CHAIN[2], Edge("r1", "r2"), Edge("r0", "r1")]
    naive, _ = run(order, naive_stale_guard=True)
    fixed, _ = run(order)
    # The naive replica drops r0→r1 for good, so the superseded r0 keeps
    # authority forever: permanent divergence, and a stale head.
    assert ("r0", "r1") not in naive.edges and naive.head("L") == "r0"
    assert fixed.head("L") == "r2"
    naive_finals = {run(p, naive_stale_guard=True)[0].canonical()
                    for p in itertools.permutations(CHAIN)}
    assert len(naive_finals) > 1


# --- successor conflict ---------------------------------------------------------

FORK = [
    Admit("r0", "L", 0, "alice"),
    Admit("r1", "L", 1, "bob", pred="r0"),  # replica A's successor
    Admit("r2", "L", 2, "carol", pred="r0"),  # replica B's concurrent successor
    Edge("r0", "r1"),
    Edge("r0", "r2"),
]


def merged():
    rep, _ = run(FORK)
    return rep


def test_merge_of_concurrent_successors_is_conflict_not_choice():
    rep = merged()
    assert sorted(b for a, b in rep.edges if a == "r0") == ["r1", "r2"]
    assert rep.head("L") == CONFLICT


def test_resolution_by_beneficiary_alone_is_rejected():
    rep = merged()
    r = make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"carol"}, quorum=1)
    rep.deliver(r)
    assert rep.head("L") == CONFLICT
    assert "self-authorized" in rep.rejected[-1][1]


def test_beneficiary_does_not_count_toward_quorum():
    rep = merged()
    r = make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"carol", "alice"})
    rep.deliver(r)
    assert rep.head("L") == CONFLICT and "quorum" in rep.rejected[-1][1]


def test_unlisted_approvers_do_not_count():
    rep = merged()
    r = make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"mallory", "eve", "alice"})
    rep.deliver(r)
    assert rep.head("L") == CONFLICT


def test_tampered_roots_are_rejected():
    rep = merged()
    good = make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"alice", "bob"})
    from dataclasses import replace
    for bad in (replace(good, pre_state_root="0" * 16), replace(good, post_state_root="0" * 16),
                replace(good, winner="r1", loser="r2")):
        rep2 = merged()
        rep2.deliver(bad)
        assert rep2.head("L") == CONFLICT


def test_valid_resolution_converges_under_all_720_schedules():
    good = make_resolution(merged(), "r0", winner="r2", loser="r1", approvals={"alice", "bob"})
    events = FORK + [good]
    finals = set()
    saw_stale_loser = False
    for p in itertools.permutations(events):
        rep = Replica(POLICY)
        heads = []
        for e in p:
            rep.deliver(e)
            h = rep.head("L")
            heads.append(h)
            # Once a replica has applied the resolution, the loser never heads.
            if "r0" in rep.resolutions:
                assert h == "r2", (p, heads)
        finals.add((rep.canonical(), rep.head("L")))
        saw_stale_loser |= "r1" in heads
    assert len(finals) == 1 and next(iter(finals))[1] == "r2"
    # Documented residual risk (no consensus): some replica may serve the
    # eventual loser r1 as head before it learns of r0→r2. Reliance receipts
    # + descendant closure are the mitigation, not this layer.
    assert saw_stale_loser


def test_loser_descendants_are_not_eligible():
    rep = merged()
    rep.deliver(Admit("r3", "L", 3, "bob", pred="r1"))
    rep.deliver(Edge("r1", "r3"))
    rep.deliver(make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"alice", "carol"}))
    # carol owns the winner, so only alice is independent: quorum 2 not met.
    assert rep.head("L") == CONFLICT
    rep.deliver(make_resolution(rep, "r0", winner="r2", loser="r1", approvals={"alice", "bob"}))
    assert rep.head("L") == "r2"
