"""AL-RUNTIME-001 E1 gate, against docs/RUNTIME_CONTRACT.md: the admit path,
every refusal reason and its order, replay semantics, every fail-closed state
(and that it writes nothing), recovery, path confinement under races, and
atomic effects."""

import json
import os

import pytest

from assurance_lab.distributed.replica import UNKNOWN, HeadResult, Invalidate, Replica
from assurance_lab.runtime import contract as C
from assurance_lab.runtime import gate as G
from assurance_lab.runtime import harness as H
from assurance_lab.runtime.gate import Gate, SimulatedCrash, Witness
from tests.runtime_support import Env


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


# ------------------------------------------------------------------- admit path
def test_an_admitted_request_writes_a_real_file_and_reconciles(env):
    env.auth("r0")
    r = env.send("req-00000001", "alice", "L", "r0", "config.txt", b"policy v1\n")
    assert (r.outcome, r.reason, r.head_status, r.head_value) == (C.ADMITTED, None, "HEAD", "r0")
    assert (env.eff / "L/config.txt").read_bytes() == b"policy v1\n"
    kinds = [e["kind"] for e in env.log()]
    assert kinds == ["genesis", "authority", "decision", "complete"]
    decision = env.log()[2]
    assert decision["outcome"] == "ADMIT" and decision["effect"]["effect_id"] == r.effect_id
    assert decision["effect"]["effect_id"] == C.effect_id("req-00000001", "L", "config.txt",
                                                         C.sha256_hex(b"policy v1\n"))
    rep = env.reconcile()
    assert rep.verdict == C.CONSISTENT and rep.log_entries == rep.witness_seq == 4


def test_authority_moves_with_supersession_and_the_effect_follows_it(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", b"alice\n").outcome == C.ADMITTED
    env.auth("r1", "e01")  # bob's r1 supersedes r0
    assert env.send("req-00000002", "alice", "L", "r0", "a.txt", b"alice again\n").reason == "STALE_AUTHORITY"
    assert env.send("req-00000003", "bob", "L", "r1", "a.txt", b"bob\n").outcome == C.ADMITTED
    assert (env.eff / "L/a.txt").read_bytes() == b"bob\n"
    assert env.reconcile().verdict == C.CONSISTENT


def test_nested_targets_create_directories_without_following_links(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "dir/sub/c.txt").outcome == C.ADMITTED
    assert env.tree() == {"L": ("dir",), "L/dir": ("dir",), "L/dir/sub": ("dir",),
                          "L/dir/sub/c.txt": ("file", b"v1\n", 1)}


# -------------------------------------------------------------- refusal reasons
def _setup_for(reason, env, monkeypatch):
    """Arrange the state, return the request arguments that must be refused for ``reason``."""
    env.auth("r0")
    base = ("req-00000009", "alice", "L", "r0", "a.txt")
    if reason == "MALFORMED_REQUEST":
        return "content", base
    if reason == "UNKNOWN_PRINCIPAL":
        return None, ("req-00000009", "mallory", "L", "r0", "a.txt")
    if reason == "FORGED_REQUEST":
        return "forged", base
    if reason == "REPLAYED_REQUEST":
        assert env.send(*base).outcome == C.ADMITTED
        return None, base
    if reason == "INVALID_TARGET":
        return None, ("req-00000009", "alice", "L", "r0", "../a.txt")
    if reason == "AUTHORITY_UNKNOWN":  # unreachable from a validated state; the mapping is still checked
        real = Replica.explain_head
        monkeypatch.setattr(Replica, "explain_head", lambda self, lin: HeadResult(
            "UNKNOWN", UNKNOWN, "integrity: injected") if lin == "L" else real(self, lin))
        return None, base
    if reason == "AUTHORITY_CONFLICT":
        env.auth("r1", "r2", "e01", "e02")
        return None, base
    if reason == "NO_AUTHORITY":
        assert env.gate.apply_authority(Invalidate("r0")).outcome == C.ADMITTED
        return None, base
    if reason == "STALE_AUTHORITY":
        env.auth("r1", "e01")
        return None, base
    if reason == "NOT_AUTHORIZED":
        return None, ("req-00000009", "bob", "L", "r0", "a.txt")
    if reason == "PATH_UNSAFE":
        (env.eff / "L").mkdir(parents=True)
        os.symlink(env.outside, env.eff / "L/dir")
        return None, ("req-00000009", "alice", "L", "r0", "dir/b.txt")
    if reason == "PRESTATE_MISMATCH":
        (env.eff / "L").mkdir(parents=True)
        (env.eff / "L/a.txt").write_bytes(b"written outside the gate\n")
        return None, base
    raise AssertionError(reason)


CONSUMING = {"INVALID_TARGET", "AUTHORITY_UNKNOWN", "AUTHORITY_CONFLICT", "NO_AUTHORITY", "STALE_AUTHORITY",
             "NOT_AUTHORIZED", "PATH_UNSAFE", "PRESTATE_MISMATCH"}


@pytest.mark.parametrize("reason", C.REQUEST_REASONS)
def test_every_refusal_reason_writes_nothing_and_is_logged(reason, env, monkeypatch):
    twist, args = _setup_for(reason, env, monkeypatch)
    req, content = env.make(*args, signer="mallory" if twist == "forged" else None)
    if twist == "content":
        content = b"not the content that was authenticated"
    before = env.tree()
    r = env.gate.request(req, content)
    assert (r.outcome, r.reason) == (C.REFUSED, reason)
    assert env.tree() == before, "a refusal changed effects/"
    last = env.log()[-1]
    assert (last["kind"], last["outcome"], last["reason"]) == ("decision", "REFUSE", reason)
    assert last["consumes"] is (reason in CONSUMING), "only authenticated requests consume their id"
    assert last["i"] == r.log_index


def test_checks_run_in_the_contract_order(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").outcome == C.ADMITTED
    # forged and replayed: authentication comes first
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", signer="mallory").reason == "FORGED_REQUEST"
    # invalid target and stale authority: the target comes first
    env.auth("r1", "e01")
    assert env.send("req-00000002", "alice", "L", "r0", "../x").reason == "INVALID_TARGET"
    # stale and not authorized: staleness comes first
    assert env.send("req-00000003", "carol", "L", "r0", "a.txt").reason == "STALE_AUTHORITY"


def test_a_forged_request_does_not_use_up_the_genuine_requesters_id(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", signer="mallory").reason == "FORGED_REQUEST"
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").outcome == C.ADMITTED


def test_a_refused_request_consumes_its_id_and_a_replay_names_the_first_decision(env):
    env.auth("r0")
    first = env.send("req-00000001", "alice", "L", "r0", "../bad")
    assert first.reason == "INVALID_TARGET"
    again = env.send("req-00000001", "alice", "L", "r0", "../bad")
    assert again.reason == "REPLAYED_REQUEST" and f"log index {first.log_index}" in again.detail


def test_replays_are_refused_across_restarts(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").outcome == C.ADMITTED
    env.reopen()
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").reason == "REPLAYED_REQUEST"


# ------------------------------------------------------------- fail-closed states
def _tamper_entry(env, j=1):
    p = env.gdir / "state/log.jsonl"
    lines = p.read_bytes().split(b"\n")[:-1]
    e = json.loads(lines[j])
    e["tampered"] = True
    lines[j] = C.canonical(e)
    p.write_bytes(b"\n".join(lines) + b"\n")


def _fail_closed(state, env, monkeypatch):
    """Put the gate in ``state``; returns the (possibly new) gate to probe."""
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").outcome == C.ADMITTED
    if state == "UNSUPPORTED_PLATFORM":
        monkeypatch.setattr(G, "platform_supported", lambda: False)
        return env.reopen()
    if state == "LOCKED":
        return Gate.open(env.gdir, H.CONFIG, H.KEYS, Witness(env.wdir), allow_public_test_keys=True)
    if state == "LOG_TAMPERED":
        env.gate.close()
        _tamper_entry(env)
        return env.reopen()
    if state == "INVALID_CONFIG":
        env.gate.close()
        other = G.Config(H.CONFIG.authorities, 3, H.CONFIG.principals)
        return Gate.open(env.gdir, other, H.KEYS, Witness(env.wdir), allow_public_test_keys=True)
    if state == "WITNESS_INVALID":
        env.gate.close()
        p = env.wdir / "commitments.jsonl"
        lines = p.read_bytes().split(b"\n")[:-1]
        c = json.loads(lines[-1])
        c["mac"] = "0" * 64
        lines[-1] = C.canonical(c)
        p.write_bytes(b"\n".join(lines) + b"\n")
        return env.reopen()
    if state == "ROLLBACK_DETECTED":
        saved = env.copy_state()
        env.auth("r1", "e01")
        env.gate.close()
        env.restore_state(saved)  # a complete, internally consistent, older state
        return env.reopen()
    if state == "INVALID_SNAPSHOT":
        env.gate.close()
        (env.gdir / "state/snapshot.json").write_bytes(b'{"not": "a snapshot"}')
        return env.reopen()
    if state == "SNAPSHOT_MISMATCH":
        saved = env.copy_state()
        env.auth("r1", "e01")
        env.gate.close()
        env.restore_state(saved, names=("snapshot.json",))
        return env.reopen()
    if state == "UNCERTAIN_STATE":
        env.gate.faults.add("crash_after_admit")
        with pytest.raises(SimulatedCrash):
            env.send("req-00000002", "alice", "L", "r0", "b.txt")
        return env.reopen()
    raise AssertionError(state)


@pytest.mark.parametrize("state", C.STARTUP_STATES)
def test_every_fail_closed_state_refuses_everything_and_writes_nothing(state, env, monkeypatch):
    g = _fail_closed(state, env, monkeypatch)
    try:
        assert g.state == state, g.detail
        before = env.persisted()
        r = g.request(*env.make("req-00000077", "alice", "L", "r0", "z.txt"))
        assert (r.outcome, r.reason) == (C.REFUSED, state)
        a = g.apply_authority(H.AUTHORITY[9])
        assert (a.outcome, a.reason) == (C.REFUSED, state)
        if state not in C.RECOVERABLE_STATES:
            rec = g.recover()
            assert (rec.outcome, rec.reason) == (C.REFUSED, state)
        assert env.persisted() == before, "a fail-closed gate wrote something"
    finally:
        if g is not env.gate:
            g.close()


def test_public_test_keys_need_explicit_permission(env):
    env.gate.close()
    g = Gate.open(env.gdir, H.CONFIG, H.KEYS, Witness(env.wdir))
    assert g.state == "INVALID_CONFIG" and "public test keys" in g.detail
    g.close()


def test_a_truncated_log_tail_is_tampering(env):
    env.auth("r0")
    env.gate.close()
    p = env.gdir / "state/log.jsonl"
    p.write_bytes(p.read_bytes()[:-5])
    assert env.reopen().state == "LOG_TAMPERED"


def test_a_log_shorter_than_the_witness_is_a_rollback(env):
    env.auth("r0", "r1")
    env.gate.close()
    p = env.gdir / "state/log.jsonl"
    p.write_bytes(b"".join(p.read_bytes().splitlines(keepends=True)[:-1]))
    g = env.reopen()
    assert g.state == "ROLLBACK_DETECTED" and "witness has" in g.detail


def test_a_damaged_but_self_consistent_snapshot_is_refused_at_restore(env):
    """The development lineage's integrity check guards the gate's restore boundary."""
    from assurance_lab.distributed import snapshot_json as sj
    env.auth("r0", "r1", "e01")
    env.gate.close()
    p = env.gdir / "state/snapshot.json"
    obj = json.loads(p.read_bytes())
    frozen = sj.decode(obj["snapshot"], __import__("assurance_lab.distributed.replica", fromlist=["x"]))
    damaged = sj.encode((frozen[0][1:], *frozen[1:]))  # drop one admitted record
    p.write_bytes(C.canonical({"contract": C.CONTRACT_VERSION, "digest": C.snapshot_digest(damaged),
                               "snapshot": damaged}))
    g = env.reopen()
    assert g.state == "INVALID_SNAPSHOT" and "INVALID_SNAPSHOT" in g.detail


def test_log_ahead_of_the_witness_is_uncertain_and_recoverable(env):
    env.auth("r0")
    env.gate.close()
    p = env.wdir / "commitments.jsonl"
    p.write_bytes(b"".join(p.read_bytes().splitlines(keepends=True)[:-1]))
    g = env.reopen()
    assert g.state == "UNCERTAIN_STATE" and "not committed" in g.detail
    assert g.recover().outcome == C.ADMITTED and g.state == C.OPERATIONAL
    assert env.reconcile().verdict == C.CONSISTENT


# ---------------------------------------------------------------------- recovery
def test_recover_rebuilds_a_mismatched_snapshot_from_the_log(env, monkeypatch):
    g = _fail_closed("SNAPSHOT_MISMATCH", env, monkeypatch)
    r = g.recover()
    assert r.outcome == C.ADMITTED and g.state == C.OPERATIONAL
    last = env.log()[-1]
    assert last["kind"] == "recovery" and last["reason"] == "SNAPSHOT_MISMATCH"
    assert last["discarded_snapshot_digest"] != last["rebuilt_snapshot_digest"]
    assert env.send("req-00000002", "bob", "L", "r1", "a.txt", b"bob\n").outcome == C.ADMITTED
    assert env.reconcile().verdict == C.CONSISTENT


def test_recover_abandons_an_effect_that_never_happened(env, monkeypatch):
    g = _fail_closed("UNCERTAIN_STATE", env, monkeypatch)
    rep = env.reconcile()
    assert {f.kind for f in rep.findings} == {"UNRESOLVED_EFFECT"}
    r = g.recover()
    assert r.outcome == C.ADMITTED and g.state == C.OPERATIONAL and "abandoned" in r.detail
    assert not (env.eff / "L/b.txt").exists()
    assert env.send("req-00000002", "alice", "L", "r0", "b.txt").reason == "REPLAYED_REQUEST"
    assert env.reconcile().verdict == C.CONSISTENT


def test_recover_completes_an_effect_that_did_happen(env, monkeypatch):
    g = _fail_closed("UNCERTAIN_STATE", env, monkeypatch)
    (env.eff / "L/b.txt").write_bytes(b"v1\n")  # the rename had completed before the crash
    r = g.recover()
    assert r.outcome == C.ADMITTED and "complete" in r.detail
    complete = [e for e in env.log() if e["kind"] == "complete" and e.get("recovered")]
    assert len(complete) == 1
    assert env.reconcile().verdict == C.CONSISTENT


def test_a_crash_between_temporary_file_and_rename_leaves_the_target_untouched(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", b"old\n").outcome == C.ADMITTED
    env.gate.faults.add("crash_after_temp")
    with pytest.raises(SimulatedCrash):
        env.send("req-00000002", "alice", "L", "r0", "a.txt", b"new\n")
    assert (env.eff / "L/a.txt").read_bytes() == b"old\n"
    kinds = {f.kind for f in env.reconcile().findings}
    assert kinds == {"UNRESOLVED_EFFECT", "TEMP_LEFTOVER"}
    g = env.reopen()
    assert g.state == "UNCERTAIN_STATE"
    assert g.recover().outcome == C.ADMITTED
    assert (env.eff / "L/a.txt").read_bytes() == b"old\n"
    assert env.reconcile().verdict == C.CONSISTENT


def test_a_failed_write_is_abandoned_and_refused(env):
    env.auth("r0")
    env.gate.faults.add("fail_write")
    r = env.send("req-00000001", "alice", "L", "r0", "a.txt")
    assert (r.outcome, r.reason) == (C.REFUSED, C.EFFECT_FAILED)
    assert [e["kind"] for e in env.log()][-2:] == ["decision", "abandoned"]
    assert not (env.eff / "L/a.txt").exists() and env.reconcile().verdict == C.CONSISTENT


# ------------------------------------------------------------- confinement, races
def _swap_dir_for_symlink(env):
    def hook(stage, **kw):
        if stage == "before_walk":
            os.rename(env.eff / "L/dir", env.outside / "moved")
            os.symlink(env.outside, env.eff / "L/dir")
    return hook


@pytest.mark.parametrize("mutants,escapes", [((), False), (("follow_symlinks",), True)])
def test_a_symlink_swapped_in_after_the_checks_is_refused_not_followed(tmp_path, mutants, escapes):
    """The checks pass, then a directory on the path becomes a symlink to
    outside. The gate's no-follow walk refuses it; the follow_symlinks mutant
    (the negative control) writes outside the sandbox."""
    env = Env(tmp_path, mutants=frozenset(mutants))
    try:
        env.auth("r0")
        assert env.send("req-00000001", "alice", "L", "r0", "dir/b.txt").outcome == C.ADMITTED
        env.gate.hook = _swap_dir_for_symlink(env)
        r = env.send("req-00000002", "alice", "L", "r0", "dir/c.txt", b"escape?\n")
        assert (env.outside / "c.txt").exists() is escapes
        if not escapes:
            assert (r.outcome, r.reason) == (C.REFUSED, C.EFFECT_FAILED)
            assert [e["kind"] for e in env.log()][-2:] == ["decision", "abandoned"]
    finally:
        env.close()


def test_a_target_replaced_by_a_symlink_before_the_rename_is_replaced_not_written_through(env):
    env.auth("r0")
    secret = (env.outside / "secret.txt").read_bytes()

    def hook(stage, **kw):
        if stage == "before_rename":
            os.symlink(env.outside / "secret.txt", env.eff / "L/a.txt")
    env.gate.hook = hook
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", b"mine\n").outcome == C.ADMITTED
    assert (env.outside / "secret.txt").read_bytes() == secret
    assert not os.path.islink(env.eff / "L/a.txt") and (env.eff / "L/a.txt").read_bytes() == b"mine\n"
    assert env.reconcile().verdict == C.CONSISTENT


def test_a_write_racing_the_rename_is_overwritten_by_the_admitted_effect(env):
    env.auth("r0")
    env.gate.hook = lambda stage, **kw: (env.eff / "L/a.txt").write_bytes(b"racer\n") if stage == "before_rename" else None
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", b"admitted\n").outcome == C.ADMITTED
    assert (env.eff / "L/a.txt").read_bytes() == b"admitted\n"
    assert env.reconcile().verdict == C.CONSISTENT


def test_a_hard_linked_target_is_not_what_the_gate_wrote(env):
    env.auth("r0")
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt", b"same\n").outcome == C.ADMITTED
    (env.outside / "copy.txt").write_bytes(b"same\n")
    os.unlink(env.eff / "L/a.txt")
    os.link(env.outside / "copy.txt", env.eff / "L/a.txt")  # same content, two links
    assert env.send("req-00000002", "alice", "L", "r0", "a.txt", b"next\n").reason == "PRESTATE_MISMATCH"
    assert ("MULTI_LINKED_FILE", "L/a.txt") in env.reconcile().keys()


def test_a_directory_at_the_target_is_unsafe(env):
    env.auth("r0")
    (env.eff / "L/a.txt").mkdir(parents=True)
    assert env.send("req-00000001", "alice", "L", "r0", "a.txt").reason == "PATH_UNSAFE"


def test_a_symlinked_sandbox_root_is_refused_at_open(env):
    """Not a named state in contract v1: open() raises and nothing is written."""
    env.auth("r0")
    env.gate.close()
    os.rename(env.eff, env.outside / "real-effects")
    os.symlink(env.outside / "real-effects", env.eff)
    before = env.persisted()
    with pytest.raises(OSError):
        Gate.open(env.gdir, H.CONFIG, H.KEYS, Witness(env.wdir), allow_public_test_keys=True)
    assert env.persisted() == before
    os.unlink(env.eff)
    os.rename(env.outside / "real-effects", env.eff)
    env.reopen()


# ------------------------------------------------------- persistence and platform
def test_every_entry_is_committed_and_the_witness_names_the_log_head(env):
    env.auth("r0", "r1", "e01")
    env.send("req-00000001", "bob", "L", "r1", "a.txt")
    lines = (env.gdir / "state/log.jsonl").read_bytes().splitlines()
    w = json.loads((env.wdir / "commitments.jsonl").read_bytes().splitlines()[-1])
    assert w["seq"] == len(lines) and w["log_head"] == C.line_hash(lines[-1])


def test_the_platform_check_accepts_this_platform_and_create_refuses_without_writing(tmp_path, monkeypatch):
    """Regression for two defects found while building the gate: CPython lists
    ``rename``, not ``replace``, in os.supports_dir_fd (the gate failed closed
    on Linux), and create() wrote its state before checking the platform."""
    assert G.platform_supported()
    monkeypatch.setattr(G, "platform_supported", lambda: False)
    with pytest.raises(RuntimeError, match="nothing was created"):
        Gate.create(tmp_path / "g", H.CONFIG, H.KEYS, Witness(tmp_path / "w"), allow_public_test_keys=True)
    assert not (tmp_path / "g").exists() and not (tmp_path / "w").exists()


def test_create_refuses_an_existing_gate_or_witness(env, tmp_path):
    with pytest.raises(FileExistsError):
        Gate.create(env.gdir, H.CONFIG, H.KEYS, Witness(env.wdir), allow_public_test_keys=True)
    with pytest.raises(FileExistsError):
        Gate.create(tmp_path / "fresh", H.CONFIG, H.KEYS, Witness(env.wdir), allow_public_test_keys=True)


def test_unknown_mutants_are_rejected():
    with pytest.raises(ValueError):
        Gate("x", H.CONFIG, H.KEYS, Witness("y"), mutants={"no_such_mutant"})
