"""Conformance harness: one trace, two implementations (gate and model).

A trace is a list of JSON-able operations, so a failing one can be kept as a
fixture and replayed. ``run_trace`` executes it against a real gate (temporary
directories, real files) and against ``GateModel``, compares the response of
every step, and compares the reconciler's findings with the model's after
every step. ``fuzz`` generates traces from a seed.

Comparison of findings is exact, except after an attack on ``state/``
(tampered log, complete rollback): the reconciler can then explain fewer
effects than exist, and only the attack's own finding is required.
"""

from __future__ import annotations

import json
import os
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..distributed.replica import Admit, Edge, Invalidate, Replica, make_resolution
from . import contract as C
from .gate import Config, Gate, Keys, SimulatedCrash, Witness
from .model import GateModel
from .reconcile import reconcile

PRINCIPALS = ("alice", "bob", "carol")
CONFIG = Config(frozenset(PRINCIPALS), 2, PRINCIPALS)
KEYS = Keys.public_test_keys(PRINCIPALS)
CONTENTS = (b"v1\n", b"v2\n", b"v3\n")
TARGETS = ("a.txt", "dir/b.txt", "dir/sub/c.txt", "../x", ".hidden", "dir//b")
ATTACK_PATHS = ("L/a.txt", "L/dir/b.txt", "L/dir/sub/c.txt", "M/a.txt", "L/zz.txt", "L/dir", "L/dir/sub", "M/dir")


def _authority_alphabet() -> tuple:
    a0, a1, a2 = Admit("r0", "L", 0, "alice"), Admit("r1", "L", 1, "bob", pred="r0"), Admit("r2", "L", 2, "carol", pred="r0")
    scratch = Replica(CONFIG.policy())
    for e in (a0, a1, a2, Edge("r0", "r1"), Edge("r0", "r2")):
        scratch.deliver(e)
    res = make_resolution(scratch, "r0", "r2", "r1", {"alice", "bob"})  # carol's r2 wins, by alice and bob
    return (a0, a1, a2, Edge("r0", "r1"), Edge("r0", "r2"), res, Invalidate("r2"),
            Admit("r3", "L", 3, "alice", pred="r2"), Edge("r2", "r3"),
            Admit("m0", "M", 0, "bob"), Admit("m1", "M", 1, "carol", pred="m0"), Edge("m0", "m1"))


AUTHORITY = _authority_alphabet()
RECORD_IDS = ("r0", "r1", "r2", "r3", "m0", "m1", "rX")
STATE_ATTACKS = ("tamper_log", "rollback_snapshot", "rollback_all")


def build_request(spec: dict):
    """(Request, content) from a JSON-able spec; deterministic."""
    content = CONTENTS[spec["content"]]
    signer = spec["requester"] if spec["auth"] != "forged" else "mallory"
    req = C.make_request(C.public_test_key(signer), spec["id"], spec["requester"], spec["lineage"], spec["cites"],
                         spec["target"], content)
    if spec["auth"] == "tampered":  # a field changed after the requester authenticated it
        req = C.Request(**{**req.__dict__, "target": spec["target"] + "x" if spec["target"] else "x"})
    if spec.get("bad_content"):
        content = content + b"!"
    return req, content


@dataclass
class World:
    root: Path
    mutants: frozenset = frozenset()
    reconciler_mutants: frozenset = frozenset()
    gate: Gate = None
    model: GateModel = None
    requests: list = field(default_factory=list)         # specs sent, for replays
    versions: list = field(default_factory=list)         # runtime (log bytes, snapshot bytes) per operational step
    model_versions: list = field(default_factory=list)   # model (log_len, snapshot digest) per operational step
    state_attacked: bool = False

    @property
    def gate_root(self) -> Path:
        return self.root / "gate"

    @property
    def witness_dir(self) -> Path:
        return self.root / "witness"

    @property
    def outside(self) -> Path:
        return self.root / "outside"

    def open_gate(self) -> Gate:
        return Gate.open(self.gate_root, CONFIG, KEYS, Witness(self.witness_dir), allow_public_test_keys=True,
                         mutants=self.mutants)

    def record_version(self) -> None:
        if self.model.state == C.OPERATIONAL:
            st = self.gate_root / "state"
            self.versions.append(((st / "log.jsonl").read_bytes(), (st / "snapshot.json").read_bytes()))
            self.model_versions.append((self.model.log_len, self.model.snapshot_digest()))


def new_world(root, mutants=(), reconciler_mutants=()) -> World:
    root = Path(root)
    w = World(root, frozenset(mutants), frozenset(reconciler_mutants))
    w.outside.mkdir(parents=True)
    (w.outside / "secret.txt").write_bytes(b"outside the sandbox\n")
    w.gate = Gate.create(w.gate_root, CONFIG, KEYS, Witness(w.witness_dir), allow_public_test_keys=True,
                         mutants=w.mutants)
    w.model = GateModel(CONFIG, KEYS)
    w.record_version()
    return w


def _racing_attacker(w: World):
    """A hook that, once, swaps a directory on the effect's path for a symlink
    to ``outside`` after the checks and before the effect's walk. The place is
    chosen from the real filesystem by the same rule as the model's."""
    fired = []

    def hook(stage, **kw):
        if stage != "before_walk" or fired:
            return
        fired.append(True)
        comps, eff = kw["components"], w.gate_root / "effects"
        target = None
        for k in range(len(comps) - 1, 0, -1):
            p = eff.joinpath(*comps[:k])
            if p.is_dir() and not p.is_symlink():
                target = p
                os.rename(p, w.outside / f"moved-{len(list(w.outside.iterdir()))}")
                break
        if target is None:
            target = eff / comps[0]
        os.symlink(w.outside, target)
    return hook


def _restart(w: World):
    w.gate.close()
    w.gate = w.open_gate()
    return (w.gate.state, None), (w.model.restart(), None)


def apply_op(w: World, op: list):
    """Run one operation on both sides; (runtime key, model key)."""
    kind = op[0]
    eff = w.gate_root / "effects"
    if kind == "authority":
        ev = AUTHORITY[op[1]]
        return w.gate.apply_authority(ev).key(), w.model.apply_authority(ev)
    if kind == "request":
        spec = w.requests[op[1]["replay"]] if "replay" in op[1] else op[1]
        if "replay" not in op[1]:
            w.requests.append(spec)
        req, content = build_request(spec)
        crash, race = bool(op[1].get("crash")), bool(op[1].get("race"))
        if crash:
            w.gate.faults.add("crash_after_admit")
        if race:
            w.gate.hook = _racing_attacker(w)
        try:
            rk = w.gate.request(req, content).key()
        except SimulatedCrash:
            rk = ("CRASH", None)
        finally:
            w.gate.faults.discard("crash_after_admit")
            w.gate.hook = lambda stage, **kw: None
        mk = w.model.request(req, content, crash=crash, race=race)
        if rk == ("CRASH", None) or mk == ("CRASH", None):
            r2, m2 = _restart(w)  # the process died; a new one starts
            return (rk, r2), (mk, m2)
        return rk, mk
    if kind == "restart":
        return _restart(w)
    if kind == "recover":
        return w.gate.recover().key(), w.model.recover()
    sha = None
    if kind in ("bypass_write", "modify"):
        data = CONTENTS[op[2]] + b"(outside the gate)\n"
        sha = C.sha256_hex(data)
    if kind == "bypass_write":
        p = eff / op[1]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        w.model.bypass_write(op[1], sha)
        return ("attack", None), ("attack", None)
    if kind == "modify":
        (eff / op[1]).write_bytes(data)
        w.model.modify(op[1], sha)
        return ("attack", None), ("attack", None)
    if kind == "delete":
        (eff / op[1]).unlink()
        w.model.delete(op[1])
        return ("attack", None), ("attack", None)
    if kind == "plant_symlink":
        p = eff / op[1]
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(w.outside, p)
        w.model.plant_symlink(op[1])
        return ("attack", None), ("attack", None)
    st = w.gate_root / "state"
    if kind == "tamper_log":
        lines = (st / "log.jsonl").read_bytes().split(b"\n")[:-1]
        j = len(lines) // 2
        e = json.loads(lines[j])
        e["tampered"] = True
        lines[j] = C.canonical(e)
        (st / "log.jsonl").write_bytes(b"\n".join(lines) + b"\n")
        w.model.tampered = True
        w.state_attacked = True
        return _restart(w)
    if kind == "rollback_snapshot":
        (st / "snapshot.json").write_bytes(w.versions[op[1]][1])
        w.model.snapshot_stale = w.model_versions[op[1]][1] != w.model.snapshot_digest()
        return _restart(w)
    if kind == "rollback_all":
        log, snap = w.versions[op[1]]
        (st / "log.jsonl").write_bytes(log)
        (st / "snapshot.json").write_bytes(snap)
        w.model.rolled_back = w.model_versions[op[1]][0] < w.model.committed
        w.state_attacked = w.state_attacked or w.model.rolled_back
        return _restart(w)
    raise ValueError(f"unknown operation {kind!r}")


def findings_agree(w: World) -> tuple:
    """(agree, runtime findings, model findings)."""
    rep = reconcile(w.gate_root, w.witness_dir, KEYS.gate, w.reconciler_mutants)
    got, want = rep.keys(), w.model.expected_findings()
    if w.model.tampered:
        want = {("LOG_TAMPERED", "log")}
        return want <= got, got, want
    if w.model.rolled_back:
        want = {("LOG_BEHIND_WITNESS", "log")}
        return want <= got, got, want
    return got == want, got, want


@dataclass
class TraceResult:
    ok: bool
    steps: int
    trace: list
    failure: dict = None
    responses: list = field(default_factory=list)


def run_trace(root, trace, mutants=(), reconciler_mutants=()) -> TraceResult:
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    w = new_world(root, mutants, reconciler_mutants)
    responses = []
    try:
        for n, op in enumerate(trace):
            rk, mk = apply_op(w, op)
            responses.append([list(rk), list(mk)])
            if rk != mk:
                return TraceResult(False, n + 1, trace[:n + 1],
                                   {"step": n, "op": op, "runtime": rk, "model": mk, "kind": "response"}, responses)
            agree, got, want = findings_agree(w)
            if not agree:
                return TraceResult(False, n + 1, trace[:n + 1],
                                   {"step": n, "op": op, "runtime_findings": sorted(got),
                                    "model_findings": sorted(want), "kind": "findings"}, responses)
            w.record_version()
        return TraceResult(True, len(trace), trace, None, responses)
    finally:
        w.gate.close()


# ------------------------------------------------------------------ generation
def _random_request(rng: random.Random, w: World, n: int) -> dict:
    if w.requests and rng.random() < 0.15:
        return {"replay": rng.randrange(len(w.requests))}
    lineage = rng.choice(("L", "M"))
    head = w.model.replica.explain_head(lineage)
    if head.status == "HEAD" and rng.random() < 0.65:
        requester, cites = w.model.replica.admitted[head.value].owner, head.value
    else:
        requester = rng.choice(PRINCIPALS + ("mallory",))
        cites = rng.choice(RECORD_IDS)
    spec = {"id": f"req-{n:06d}-{rng.randrange(10**6):06d}", "requester": requester, "lineage": lineage,
            "cites": cites, "target": rng.choice(TARGETS[:3]) if rng.random() < 0.85 else rng.choice(TARGETS[3:]),
            "content": rng.randrange(len(CONTENTS)),
            "auth": rng.choices(("valid", "forged", "tampered"), (0.84, 0.08, 0.08))[0]}
    if rng.random() < 0.04:
        spec["bad_content"] = True
    r = rng.random()
    if r < 0.08:
        spec["crash"] = True
    elif r < 0.14:
        spec["race"] = True
    return spec


def _authority_op(rng: random.Random, w: World) -> list:
    """Mostly the next undelivered event in scenario order (so forks exist
    before their resolution), sometimes any event (reordering, duplicates)."""
    undelivered = [i for i, e in enumerate(AUTHORITY) if e not in w.model.replica.seen]
    if undelivered and rng.random() < 0.6:
        return ["authority", undelivered[0]]
    return ["authority", rng.randrange(len(AUTHORITY))]


def random_op(rng: random.Random, w: World, n: int) -> list:
    m = w.model
    r = rng.random()
    if r < 0.30:
        return _authority_op(rng, w)
    if r < 0.72:
        return ["request", _random_request(rng, w, n)]
    if r < 0.77:
        return ["restart"]
    if r < 0.82:
        return ["recover"]
    if r < 0.86 and m.state == C.OPERATIONAL and not w.state_attacked and len(w.versions) > 1:
        kind = rng.choice(STATE_ATTACKS)
        return [kind] if kind == "tamper_log" else [kind, rng.randrange(len(w.versions))]
    # attacks on effects/
    path = rng.choice(ATTACK_PATHS)
    files = [p for p, o in m.fs.items() if o[0] == "file"]
    choice = rng.random()
    if files and choice < 0.35:
        return ["modify", rng.choice(files), rng.randrange(len(CONTENTS))]
    if files and choice < 0.55:
        return ["delete", rng.choice(files)]
    if choice < 0.8 and m.can_bypass_write(path):
        return ["bypass_write", path, rng.randrange(len(CONTENTS))]
    if m.can_bypass_write(path):
        return ["plant_symlink", path]
    return _authority_op(rng, w)


def generate(seed: int, length: int, root) -> list:
    """A trace from a seed. Generation reads the model's state to pick useful
    inputs (it runs the model alongside); decisions are compared only in
    ``run_trace``."""
    rng = random.Random(seed)
    w = new_world(Path(root), ())
    trace = []
    try:
        for n in range(length):
            op = random_op(rng, w, n)
            trace.append(op)
            apply_op(w, op)
            w.record_version()
    finally:
        w.gate.close()
    return trace


def shrink(root, trace, mutants=(), reconciler_mutants=()) -> list:
    """Remove operations one at a time while the trace still fails (1-minimal)."""
    def fails(t):
        try:
            return not run_trace(root, t, mutants, reconciler_mutants).ok
        except Exception:  # a shrunk trace may break a precondition: not a failure of the gate
            return False
    cur = list(trace)
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            cand = cur[:i] + cur[i + 1:]
            if cand and fails(cand):
                cur, changed = cand, True
                break
    return cur
