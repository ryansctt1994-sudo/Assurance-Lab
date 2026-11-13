"""AL-RUNTIME-001 E1 reference model: the contract's decisions without a filesystem.

A second, separate implementation of ``docs/RUNTIME_CONTRACT.md``: no
persistence, no file descriptors, an abstract view of ``effects/``. Runtime
traces are replayed against it step by step (``tests/test_runtime_conformance.py``).

Shared with the gate, and therefore not checked by conformance: the replica
(``explain_head``, verified separately by the replica model checker and the
metamorphic suite) and ``contract`` (encodings, MACs, name and path rules).
What conformance does check is everything the gate adds around them: the
order of checks, replay state, persistence across restarts, startup checks,
the effect protocol, and the filesystem itself.

Fail-closed states are reached through attacks on ``state/`` that the
harness performs while the gate is down (the gate holds the lock while it
runs). Attacks on ``effects/`` may happen at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..distributed import snapshot_json as sj
from ..distributed.replica import Replica
from . import contract as C


@dataclass
class GateModel:
    config: object
    keys: object
    state: str = C.OPERATIONAL
    replica: Replica = None
    consumed: set = field(default_factory=set)
    expected: dict = field(default_factory=dict)        # path -> sha of the last completed effect
    fs: dict = field(default_factory=dict)              # path -> ("file", sha) | ("symlink",) | ("dir",)
    admitted_dirs: set = field(default_factory=set)     # proper prefixes of every ADMIT path
    unresolved: dict = field(default_factory=dict)      # decision index -> (path, previous sha, admitted sha)
    log_len: int = 1                                    # entries in the log (genesis first)
    committed: int = 1                                  # entries the witness has committed
    tampered: bool = False
    rolled_back: bool = False
    snapshot_stale: bool = False

    def __post_init__(self):
        if self.replica is None:
            self.replica = Replica(self.config.policy())

    # ------------------------------------------------------------ helpers
    def snapshot_digest(self) -> str:
        return C.snapshot_digest(sj.encode(self.replica.freeze()))

    def _log(self, n: int = 1) -> int:
        i = self.log_len
        self.log_len += n
        self.committed = self.log_len
        return i

    def _refuse(self, reason, consumes=False, req=None):
        self._log()
        if consumes:
            self.consumed.add(req.request_id)
        return (C.REFUSED, reason)

    def _observe(self, comps) -> tuple:
        for k in range(1, len(comps)):
            o = self.fs.get("/".join(comps[:k]))
            if o is None:
                return ("absent",)
            if o[0] != "dir":
                return ("unsafe",)
        o = self.fs.get("/".join(comps))
        if o is None:
            return ("absent",)
        return ("file", o[1]) if o[0] == "file" else ("unsafe",)

    # ---------------------------------------------------------- operations
    def apply_authority(self, event):
        if self.state != C.OPERATIONAL:
            return (C.REFUSED, self.state)
        self.replica.deliver(event)
        self._log()
        return (C.ADMITTED, None)

    @staticmethod
    def race_component(fs: dict, comps) -> tuple:
        """Where a racing attacker strikes between the checks and the effect: the
        deepest existing directory on the path (moved away and replaced by a
        symlink), or else the lineage directory (a symlink planted there).
        Returns (prefix, existed)."""
        for k in range(len(comps) - 1, 0, -1):
            p = "/".join(comps[:k])
            if fs.get(p) == ("dir",):
                return p, True
        return comps[0], False

    def request(self, req, content, crash: bool = False, race: bool = False):
        """(outcome, reason), or ("CRASH", None) when the crash fault fires after
        the ADMIT. ``race``: an attacker swaps a directory on the path for a
        symlink after the checks, before the effect (see ``race_component``)."""
        if self.state != C.OPERATIONAL:
            return (C.REFUSED, self.state)
        if C.request_problems(req, content):
            return self._refuse("MALFORMED_REQUEST")
        if req.requester not in self.config.principals or req.requester not in self.keys.principals:
            return self._refuse("UNKNOWN_PRINCIPAL")
        if not C.mac_ok(self.keys.principals[req.requester], C.D_REQUEST, req.fields(), req.mac):
            return self._refuse("FORGED_REQUEST")
        if req.request_id in self.consumed:
            return self._refuse("REPLAYED_REQUEST")
        if C.target_problems(req.target):
            return self._refuse("INVALID_TARGET", True, req)
        head = self.replica.explain_head(req.lineage)
        if head.status in C.HEAD_REFUSALS:
            return self._refuse(C.HEAD_REFUSALS[head.status], True, req)
        if req.cites != head.value:
            return self._refuse("STALE_AUTHORITY", True, req)
        if self.replica.admitted[head.value].owner != req.requester:
            return self._refuse("NOT_AUTHORIZED", True, req)
        comps = C.sandbox_components(req.lineage, req.target)
        path = "/".join(comps)
        observed = self._observe(comps)
        if observed[0] == "unsafe":
            return self._refuse("PATH_UNSAFE", True, req)
        exp = self.expected.get(path)
        if observed != (("absent",) if exp is None else ("file", exp)):
            return self._refuse("PRESTATE_MISMATCH", True, req)
        # ADMIT: decision entry (committed), then effect, then completion
        self.consumed.add(req.request_id)
        i = self._log()
        self.admitted_dirs.update("/".join(comps[:k]) for k in range(1, len(comps)))
        if crash:
            self.unresolved[i] = (path, exp, req.content_sha256)
            return ("CRASH", None)
        if race:  # the walk meets the symlink and refuses it: abandoned, nothing written
            p, _existed = self.race_component(self.fs, comps)
            for q in [q for q in self.fs if q == p or q.startswith(p + "/")]:
                del self.fs[q]
            self.fs[p] = ("symlink",)
            self._log()
            return (C.REFUSED, C.EFFECT_FAILED)
        for k in range(1, len(comps)):
            self.fs.setdefault("/".join(comps[:k]), ("dir",))
        self.fs[path] = ("file", req.content_sha256)
        self.expected[path] = req.content_sha256
        self._log()
        return (C.ADMITTED, None)

    def restart(self) -> str:
        if self.tampered:
            self.state = "LOG_TAMPERED"
        elif self.rolled_back:
            self.state = "ROLLBACK_DETECTED"
        elif self.snapshot_stale:
            self.state = "SNAPSHOT_MISMATCH"
        elif self.unresolved:
            self.state = "UNCERTAIN_STATE"
        else:
            self.state = C.OPERATIONAL
        return self.state

    def recover(self):
        if self.state == C.OPERATIONAL:
            return (C.ADMITTED, None)
        if self.state not in C.RECOVERABLE_STATES:
            return (C.REFUSED, self.state)
        if self.state == "SNAPSHOT_MISMATCH":
            self.snapshot_stale = False
            self._log()  # the recovery entry
            self.restart()
        if self.state == "UNCERTAIN_STATE":
            for _i, (path, _prev, sha) in sorted(self.unresolved.items()):
                if self.fs.get(path) == ("file", sha):
                    self.expected[path] = sha  # complete (recovered); otherwise abandoned
            self._log(len(self.unresolved) + 1)  # one settlement per effect, then the recovery entry
            self.unresolved.clear()
            self.restart()
        return (C.ADMITTED, None) if self.state == C.OPERATIONAL else (C.REFUSED, self.state)

    # ---------------------------------------------------------------- attacks
    def can_bypass_write(self, path: str) -> bool:
        parts = path.split("/")
        if path in self.fs:
            return False
        return all(self.fs.get("/".join(parts[:k]), ("dir",))[0] == "dir" for k in range(1, len(parts)))

    def bypass_write(self, path: str, sha: str) -> None:
        parts = path.split("/")
        for k in range(1, len(parts)):
            self.fs.setdefault("/".join(parts[:k]), ("dir",))
        self.fs[path] = ("file", sha)

    def plant_symlink(self, path: str) -> None:
        parts = path.split("/")
        for k in range(1, len(parts)):
            self.fs.setdefault("/".join(parts[:k]), ("dir",))
        self.fs[path] = ("symlink",)

    def modify(self, path: str, sha: str) -> None:
        self.fs[path] = ("file", sha)

    def delete(self, path: str) -> None:
        del self.fs[path]

    # --------------------------------------------------------- reconciliation
    def expected_findings(self) -> set:
        out = {("UNRESOLVED_EFFECT", f"decision {i}") for i in self.unresolved}
        in_flight = {p: {prev, sha} for p, prev, sha in self.unresolved.values()}
        for path, sha in self.expected.items():
            if path in in_flight:
                continue
            o = self.fs.get(path)
            if o is None:
                out.add(("MISSING_EFFECT", path))
            elif o != ("file", sha):
                out.add(("MODIFIED_EFFECT", path))
        for path, o in self.fs.items():
            if o[0] == "dir":
                if path not in self.admitted_dirs:
                    out.add(("UNEXPECTED_DIRECTORY", path))
            elif o[0] != "file":
                if path not in self.expected:
                    out.add(("UNEXPECTED_NON_REGULAR", path))
            elif path in in_flight:
                if o[1] not in in_flight[path]:
                    out.add(("MODIFIED_EFFECT", path))
            elif path not in self.expected:
                out.add(("UNEXPECTED_FILE", path))
        return out
