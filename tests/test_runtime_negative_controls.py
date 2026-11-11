"""AL-RUNTIME-001 E1: the five specified negative controls.

1. a forged requester; 2. a replayed approval; 3. a rolled-back snapshot (the
snapshot alone, and a complete older state); 4. a tampered log entry; 5. an
effect that bypasses the gate.

Each attack must be caught by the control meant to catch it. Attribution is
shown by running the same attack with only that control disabled (a mutant):
the attack then succeeds, or is caught only by a different, named control.
Mutant runs are diagnostics, never evidence that the gate works.
"""

import json

import pytest

from assurance_lab.runtime import contract as C
from tests.runtime_support import Env


def _env(tmp_path, *mutants):
    return Env(tmp_path, mutants=frozenset(mutants))


# 1 --------------------------------------------------------------- forged requester
@pytest.mark.parametrize("mutants,caught", [((), True), (("skip_auth",), False)])
def test_1_forged_requester_is_caught_by_request_authentication(tmp_path, mutants, caught):
    env = _env(tmp_path, *mutants)
    try:
        env.auth("r0")  # alice holds L
        r = env.send("req-forged-01", "alice", "L", "r0", "a.txt", b"mallory's content\n", signer="mallory")
        written = (env.eff / "L/a.txt").exists()
        if caught:
            assert (r.outcome, r.reason, written) == (C.REFUSED, "FORGED_REQUEST", False)
        else:
            assert (r.outcome, written) == (C.ADMITTED, True)
        # The reconciler trusts the gate's decisions, so it cannot see a forgery:
        # this control lives in the gate alone.
        assert env.reconcile().verdict == C.CONSISTENT
    finally:
        env.close()


# 2 --------------------------------------------------------------- replayed approval
@pytest.mark.parametrize("mutants,caught", [((), True), (("skip_replay",), False)])
def test_2_replayed_approval_is_caught_by_single_use_request_ids(tmp_path, mutants, caught):
    env = _env(tmp_path, *mutants)
    try:
        env.auth("r0")
        v1 = env.make("req-replay-01", "alice", "L", "r0", "a.txt", b"v1\n")
        assert env.gate.request(*v1).outcome == C.ADMITTED
        assert env.send("req-replay-02", "alice", "L", "r0", "a.txt", b"v2\n").outcome == C.ADMITTED
        r = env.gate.request(*v1)  # the captured v1 approval, sent again
        content = (env.eff / "L/a.txt").read_bytes()
        if caught:
            assert (r.reason, content) == ("REPLAYED_REQUEST", b"v2\n")
        else:  # the replay reverts the file to v1
            assert (r.outcome, content) == (C.ADMITTED, b"v1\n")
        admits = [e for e in env.log() if e["kind"] == "decision" and e["outcome"] == "ADMIT"
                  and e["request"]["request_id"] == "req-replay-01"]
        assert len(admits) == (1 if caught else 2)
    finally:
        env.close()


# 3 --------------------------------------------------------------- rolled-back snapshot
def _alice_then_bob(env):
    env.auth("r0")
    assert env.send("req-roll-0001", "alice", "L", "r0", "a.txt", b"alice\n").outcome == C.ADMITTED
    saved = env.copy_state()                  # alice holds L here
    env.auth("r1", "e01")                     # bob takes over
    assert env.send("req-roll-0002", "bob", "L", "r1", "a.txt", b"bob\n").outcome == C.ADMITTED
    return saved


@pytest.mark.parametrize("mutants,caught", [((), True), (("trust_snapshot",), False)])
def test_3a_rolled_back_snapshot_is_caught_by_the_log_fold(tmp_path, mutants, caught):
    env = _env(tmp_path, *mutants)
    try:
        saved = _alice_then_bob(env)
        env.gate.close()
        env.restore_state(saved, names=("snapshot.json",))  # an older, genuine snapshot
        g = env.reopen()
        r = env.send("req-roll-0003", "alice", "L", "r0", "a.txt", b"alice again\n")
        if caught:
            assert g.state == "SNAPSHOT_MISMATCH" and r.reason == "SNAPSHOT_MISMATCH"
        else:  # stale authority restored: alice acts after bob took over
            assert g.state == C.OPERATIONAL and env.gate._loaded.replica.head("L") == "r0"
            assert r.outcome == C.ADMITTED and (env.eff / "L/a.txt").read_bytes() == b"alice again\n"
    finally:
        env.close()


@pytest.mark.parametrize("mutants,caught", [((), True), (("skip_witness",), False)])
def test_3b_complete_older_state_is_caught_only_by_the_independent_witness(tmp_path, mutants, caught):
    """A complete older copy of state/ verifies internally (chain, MACs,
    snapshot = fold). Only the witness, retained apart from state/, knows
    there were later commitments."""
    env = _env(tmp_path, *mutants)
    try:
        saved = _alice_then_bob(env)
        env.gate.close()
        env.restore_state(saved)  # log and snapshot, consistently rolled back
        g = env.reopen()
        if caught:
            assert g.state == "ROLLBACK_DETECTED"
            assert env.send("req-roll-0003", "alice", "L", "r0", "z.txt").reason == "ROLLBACK_DETECTED"
        else:
            assert g.state == C.OPERATIONAL
            r = env.send("req-roll-0003", "alice", "L", "r0", "z.txt", b"stale authority\n")
            assert r.outcome == C.ADMITTED, "alice acts on authority bob had already taken over"
            # the consumed-id set was rolled back too: bob's earlier request id is reusable
            assert env.send("req-roll-0002", "alice", "L", "r0", "y.txt").outcome == C.ADMITTED
        # Independent of the gate, the reconciler sees the rollback either way, even
        # after the mutant kept writing and its new commitments match its forked log.
        assert ("LOG_BEHIND_WITNESS", "log") in env.reconcile().keys()
    finally:
        env.close()


# 4 --------------------------------------------------------------- tampered log entry
@pytest.mark.parametrize("mutants,caught", [((), True), (("skip_log_verify",), False)])
def test_4_tampered_log_entry_is_caught_by_chain_and_mac_verification(tmp_path, mutants, caught):
    env = _env(tmp_path, *mutants)
    try:
        env.auth("r0")
        v1 = env.make("req-tamper-01", "alice", "L", "r0", "a.txt", b"v1\n")
        assert env.gate.request(*v1).outcome == C.ADMITTED
        assert env.send("req-tamper-02", "alice", "L", "r0", "a.txt", b"v2\n").outcome == C.ADMITTED
        env.gate.close()
        # Offline, rename the consumed id in the first decision, so its replay looks fresh.
        p = env.gdir / "state/log.jsonl"
        lines = p.read_bytes().split(b"\n")[:-1]
        j = next(k for k, ln in enumerate(lines) if b"req-tamper-01" in ln and b'"decision"' in ln)
        e = json.loads(lines[j])
        e["request"]["request_id"] = "req-tamper-xx"
        lines[j] = C.canonical(e)
        p.write_bytes(b"\n".join(lines) + b"\n")
        g = env.reopen()
        r = env.gate.request(*v1)
        if caught:
            assert g.state == "LOG_TAMPERED" and r.reason == "LOG_TAMPERED"
            assert (env.eff / "L/a.txt").read_bytes() == b"v2\n"
        else:
            assert g.state == C.OPERATIONAL and r.outcome == C.ADMITTED
            assert (env.eff / "L/a.txt").read_bytes() == b"v1\n", "the replay reverted the file"
        # The reconciler verifies the log itself: an independent second control.
        assert ("LOG_TAMPERED", "log") in env.reconcile().keys()
    finally:
        env.close()


def test_4_the_reconciler_catches_tampering_without_the_gate(tmp_path):
    env = _env(tmp_path)
    try:
        env.auth("r0")
        assert env.send("req-tamper-01", "alice", "L", "r0", "a.txt").outcome == C.ADMITTED
        p = env.gdir / "state/log.jsonl"
        lines = p.read_bytes().split(b"\n")[:-1]
        e = json.loads(lines[1])
        e["event"]["owner"] = "mallory"  # rewrite who owns r0
        lines[1] = C.canonical(e)
        p.write_bytes(b"\n".join(lines) + b"\n")
        rep = env.reconcile()
        assert rep.verdict == C.INCONSISTENT and ("LOG_TAMPERED", "log") in rep.keys()
    finally:
        env.close()


# 5 --------------------------------------------------------------- effect bypassing the gate
@pytest.mark.parametrize("reconciler_mutants,caught", [((), True), (("trust_log",), False)])
def test_5_effect_bypassing_the_gate_is_caught_by_independent_reconciliation(tmp_path, reconciler_mutants, caught):
    env = _env(tmp_path)
    try:
        env.auth("r0")
        assert env.send("req-bypass-01", "alice", "L", "r0", "a.txt", b"admitted\n").outcome == C.ADMITTED
        (env.eff / "L/a.txt").write_bytes(b"rewritten outside the gate\n")   # modifies an admitted effect
        (env.eff / "L/planted.txt").write_bytes(b"no decision for this\n")  # an effect with no decision
        rep = env.reconcile(mutants=frozenset(reconciler_mutants))
        if caught:
            assert {("MODIFIED_EFFECT", "L/a.txt"), ("UNEXPECTED_FILE", "L/planted.txt")} <= rep.keys()
        else:  # a reconciler that reads only the gate's log sees nothing
            assert rep.verdict == C.CONSISTENT
    finally:
        env.close()


@pytest.mark.parametrize("mutants,evidence_kept", [((), True), (("skip_prestate",), False)])
def test_5_the_gate_refuses_to_build_on_a_bypass_so_the_evidence_survives(tmp_path, mutants, evidence_kept):
    """The pre-state check is a second, gate-side control: without it the next
    admitted write silently overwrites the bypass, and the evidence is gone."""
    env = _env(tmp_path, *mutants)
    try:
        env.auth("r0")
        assert env.send("req-bypass-01", "alice", "L", "r0", "a.txt", b"admitted\n").outcome == C.ADMITTED
        (env.eff / "L/a.txt").write_bytes(b"rewritten outside the gate\n")
        r = env.send("req-bypass-02", "alice", "L", "r0", "a.txt", b"next\n")
        modified = ("MODIFIED_EFFECT", "L/a.txt") in env.reconcile().keys()
        if evidence_kept:
            assert r.reason == "PRESTATE_MISMATCH" and modified
        else:
            assert r.outcome == C.ADMITTED and not modified
    finally:
        env.close()
