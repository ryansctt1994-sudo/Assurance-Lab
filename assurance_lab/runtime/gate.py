"""AL-RUNTIME-001 E1 gate: sandbox file writes, only when the contract admits them.

Implements ``docs/RUNTIME_CONTRACT.md`` (``al-runtime-001/e1/v1``):

* ``Gate.create`` initializes ``<root>/state`` and ``<root>/effects`` and
  publishes the first commitment; ``Gate.open`` runs the startup checks in the
  contract's order and leaves the gate OPERATIONAL or in a fail-closed state.
* ``request`` runs the ordered request checks; an ADMIT is logged and
  committed, then the effect is performed (directory handles, ``O_NOFOLLOW``,
  temporary file, fsync, rename), then completed or abandoned.
* ``apply_authority`` delivers an authority event (trusted channel in E1).
* ``recover`` settles the two recoverable states, explicitly and in the log.

The sandbox directory is not a security boundary (see the contract).

``mutants`` disables one control each. They exist only so tests can show
that each negative control is caught by the control meant to catch it;
every use is a diagnostic, never evidence that the gate works.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..distributed import replica as R
from ..distributed import snapshot_json as sj
from ..distributed.replica import CorruptSnapshot, Replica, ResolutionPolicy
from . import contract as C

try:  # POSIX only; the gate refuses to run without it (UNSUPPORTED_PLATFORM)
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None

MUTANTS = frozenset({
    "skip_auth",            # check 3 off: forged requests pass
    "skip_replay",          # check 4 off: consumed ids are admitted again
    "skip_witness",         # startup check 6 off: a complete older state is accepted
    "skip_log_verify",      # startup check 3 off: a tampered log is trusted
    "trust_snapshot",       # startup check 8 off: authority comes from the snapshot file, not the log
    "skip_prestate",        # checks 9-10 off: effects overwrite whatever is there, through links
    "follow_symlinks",      # effects written with path joins that follow symlinks
})

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class SimulatedCrash(Exception):
    """Fault injection: the process stops here (tests only)."""


class EffectError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    """Fixed in the genesis entry; ``open`` refuses any other (INVALID_CONFIG)."""

    authorities: frozenset
    min_quorum: int
    principals: tuple

    def policy(self) -> ResolutionPolicy:
        return ResolutionPolicy(frozenset(self.authorities), self.min_quorum)

    def genesis_fields(self) -> dict:
        return {"authorities": sorted(self.authorities), "min_quorum": self.min_quorum,
                "principals": sorted(self.principals), "scheme": C.SCHEME}


@dataclass(frozen=True)
class Keys:
    gate: bytes
    principals: dict
    public_test: bool = False

    @classmethod
    def public_test_keys(cls, principals) -> "Keys":
        """Reproducible keys from a published seed. They authenticate nothing."""
        return cls(C.public_test_key("gate"), {p: C.public_test_key(p) for p in principals}, True)


class Witness:
    """Append-only commitments, kept in a directory apart from the gate's state."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.path = self.dir / "commitments.jsonl"

    def latest(self) -> Optional[dict]:
        """The last commitment; None if there is none; {"unparsable": ...} if it cannot be read."""
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return None
        lines = [ln for ln in data.split(b"\n") if ln]
        if not lines:
            return None
        try:
            obj = json.loads(lines[-1])
        except ValueError as e:
            return {"unparsable": str(e)}
        return obj if isinstance(obj, dict) else {"unparsable": "not an object"}

    def publish(self, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, C.canonical(record) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)


def platform_supported() -> bool:
    # os.replace takes src_dir_fd/dst_dir_fd through the same renameat call as
    # os.rename, but CPython lists only ``rename`` in supports_dir_fd.
    need = (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    return (fcntl is not None and all(f in os.supports_dir_fd for f in need)
            and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))


def _fsync_dir(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as e:  # some platforms refuse fsync on a directory handle
        if e.errno not in (errno.EINVAL, errno.EBADF, errno.ENOTSUP):
            raise


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        b = os.read(fd, 1 << 16)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _describe(e: BaseException) -> str:
    """A platform-independent description for the log: errno *names*, never
    messages, and one wording for every way a path component is not a real
    directory (Linux says ENOTDIR for a symlink here, macOS says ELOOP)."""
    if isinstance(e, OSError) and not isinstance(e, EffectError):
        return f"OSError {errno.errorcode.get(e.errno, e.errno)}"
    return f"{type(e).__name__}: {e}"


def _not_a_dir(name: str, e: OSError) -> str:
    if e.errno in (errno.ENOTDIR, errno.ELOOP):
        return f"component {name!r} is not a real directory"
    return f"component {name!r}: {errno.errorcode.get(e.errno, e.errno)}"


@dataclass
class _Loaded:
    """What the startup checks establish, for an OPERATIONAL gate."""

    replica: Replica
    consumed: dict = field(default_factory=dict)       # request_id -> log index of the first decision
    effects: dict = field(default_factory=dict)        # "lineage/target" -> content sha of the last completed effect
    unresolved: dict = field(default_factory=dict)     # decision index -> decision entry (ADMIT without resolution)


class Gate:
    def __init__(self, root, config: Config, keys: Keys, witness: Witness, *, allow_public_test_keys=False,
                 mutants=frozenset(), faults=frozenset(), hook: Optional[Callable] = None):
        unknown = set(mutants) - MUTANTS
        if unknown:
            raise ValueError(f"unknown mutants {sorted(unknown)}")
        self.root = Path(root)
        self.state_dir = self.root / "state"
        self.effects_dir = self.root / "effects"
        self.config, self.keys, self.witness = config, keys, witness
        self.allow_public_test_keys = allow_public_test_keys
        self.mutants, self.faults = frozenset(mutants), set(faults)
        self.hook = hook or (lambda stage, **kw: None)
        self.state, self.detail = "UNOPENED", ""
        self._lock_fd: Optional[int] = None
        self._effects_fd: Optional[int] = None
        self._lines: list[bytes] = []
        self._entries: list[dict] = []
        self._loaded: Optional[_Loaded] = None

    # ------------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, root, config: Config, keys: Keys, witness: Witness, **kw) -> "Gate":
        """Initialize a new gate directory and open it."""
        g = cls(root, config, keys, witness, **kw)
        if not platform_supported():
            raise RuntimeError("UNSUPPORTED_PLATFORM: dir_fd, O_NOFOLLOW or flock missing; nothing was created")
        if g.state_dir.exists() or g.effects_dir.exists():
            raise FileExistsError(f"{g.root} already holds a gate")
        if witness.latest() is not None:
            raise FileExistsError(f"witness {witness.dir} already holds commitments")
        g.state_dir.mkdir(parents=True)
        g.effects_dir.mkdir()
        if not g._acquire_lock():
            raise RuntimeError("lock not acquired on a fresh state directory")
        empty = Replica(config.policy())
        g._write_snapshot(empty)
        g._append({"kind": "genesis", **config.genesis_fields(), "public_test_keys": keys.public_test},
                  snapshot_dig=C.snapshot_digest(sj.encode(empty.freeze())))
        g._release()
        return cls.open(root, config, keys, witness, **kw)

    @classmethod
    def open(cls, root, config: Config, keys: Keys, witness: Witness, **kw) -> "Gate":
        g = cls(root, config, keys, witness, **kw)
        if not platform_supported():
            g._fail("UNSUPPORTED_PLATFORM", "dir_fd, O_NOFOLLOW or flock missing")
            return g
        if not g._acquire_lock():
            g._fail("LOCKED", "another gate holds state/LOCK")
            return g
        try:
            g._verify()
        except BaseException:  # e.g. the sandbox root is not a real directory: release, then refuse
            g.close()
            raise
        return g

    def close(self) -> None:
        if self._effects_fd is not None:
            os.close(self._effects_fd)
            self._effects_fd = None
        self._release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _acquire_lock(self) -> bool:
        fd = os.open(self.state_dir / "LOCK", os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def _release(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # closing the descriptor releases the flock
            self._lock_fd = None

    def _fail(self, state: str, detail: str) -> None:
        self.state, self.detail, self._loaded = state, detail, None

    # --------------------------------------------------------- startup checks
    def _verify(self) -> None:
        """Startup checks 3-9 of the contract, in order (1-2 ran in ``open``)."""
        problem = self._read_log(verify="skip_log_verify" not in self.mutants)
        if problem:
            return self._fail("LOG_TAMPERED", problem)
        genesis = self._entries[0] if self._entries else {}
        expect = {"kind": "genesis", **self.config.genesis_fields(), "public_test_keys": self.keys.public_test}
        if {k: genesis.get(k) for k in expect} != expect:
            return self._fail("INVALID_CONFIG", "genesis entry does not match the configuration and keys")
        if self.keys.public_test and not self.allow_public_test_keys:
            return self._fail("INVALID_CONFIG", "public test keys refused (pass allow_public_test_keys=True)")
        w = self.witness.latest()
        seq, head = 0, C.GENESIS_HEAD
        if w is not None:
            fields = {k: w.get(k) for k in ("contract", "seq", "log_head", "snapshot_digest")}
            if ("unparsable" in w or not isinstance(fields["seq"], int) or isinstance(fields["seq"], bool)
                    or not C.mac_ok(self.keys.gate, C.D_COMMIT, fields, w.get("mac"))):
                return self._fail("WITNESS_INVALID", "latest commitment does not verify")
            seq, head = fields["seq"], fields["log_head"]
        n = len(self._lines)
        if "skip_witness" not in self.mutants:
            if seq > n:
                return self._fail("ROLLBACK_DETECTED", f"witness has {seq} entries committed, the log {n}")
            if seq > 0 and C.line_hash(self._lines[seq - 1]) != head:
                return self._fail("ROLLBACK_DETECTED", f"log diverges from the witness at entry {seq - 1}")
        try:
            fold, digests = self._fold()
        except Exception as e:  # a MAC-valid log the replica cannot fold: treat as tampering
            return self._fail("LOG_TAMPERED", f"log does not fold: {type(e).__name__}: {e}")
        if "skip_witness" not in self.mutants and seq > 0 and digests[seq - 1] != w.get("snapshot_digest"):
            return self._fail("ROLLBACK_DETECTED", "witness commitment names another authority state")
        snap, snap_problem = self._read_snapshot()
        if snap_problem:
            return self._fail("INVALID_SNAPSHOT", snap_problem)
        snap_dig = C.snapshot_digest(sj.encode(snap.freeze()))
        fold_dig = C.snapshot_digest(sj.encode(fold.freeze()))
        if snap_dig != fold_dig:
            if "trust_snapshot" not in self.mutants:
                return self._fail("SNAPSHOT_MISMATCH", f"snapshot {snap_dig[:12]} is not the log's {fold_dig[:12]}")
            fold = snap  # MUTANT: authority comes from the snapshot file
        try:
            loaded = self._index(fold)
        except (KeyError, TypeError, AttributeError) as e:  # only reachable when verification was skipped
            return self._fail("LOG_TAMPERED", f"log entries do not index: {type(e).__name__}: {e}")
        if n > seq:
            return self._fail("UNCERTAIN_STATE", f"{n - seq} log entries are not committed")
        if loaded.unresolved:
            return self._fail("UNCERTAIN_STATE", f"unresolved effects at log indices {sorted(loaded.unresolved)}")
        self._loaded, self.state, self.detail = loaded, C.OPERATIONAL, ""
        self._open_effects_root()

    def _read_log(self, verify: bool = True) -> str:
        """Parse and verify every entry; '' if the log is sound, else the first problem.
        ``verify=False`` (the ``skip_log_verify`` mutant only) trusts whatever parses."""
        self._lines, self._entries = [], []
        try:
            data = (self.state_dir / "log.jsonl").read_bytes()
        except FileNotFoundError:
            return "no log"
        if not data.endswith(b"\n"):
            return "log does not end with a complete line"
        prev = C.GENESIS_HEAD
        for i, line in enumerate(data[:-1].split(b"\n")):
            try:
                e = json.loads(line)
            except ValueError:
                return f"entry {i} does not parse"
            if not isinstance(e, dict):
                return f"entry {i} is not an object"
            if not verify:  # MUTANT: no chain, form or MAC check
                self._lines.append(line)
                self._entries.append(e)
                continue
            body = {k: v for k, v in e.items() if k != "mac"}
            try:
                canon = C.canonical(e)
            except TypeError:
                return f"entry {i} is not canonical JSON"
            if canon != line:
                return f"entry {i} is not in canonical form"
            if e.get("i") != i or e.get("prev") != prev or e.get("contract") != C.CONTRACT_VERSION:
                return f"entry {i} breaks the chain"
            if e.get("kind") not in C.ENTRY_KINDS or (e.get("kind") == "genesis") != (i == 0):
                return f"entry {i} has kind {e.get('kind')!r} out of place"
            if not C.mac_ok(self.keys.gate, C.D_ENTRY, body, e.get("mac")):
                return f"entry {i} fails its MAC"
            self._lines.append(line)
            self._entries.append(e)
            prev = C.line_hash(line)
        return "" if self._entries else "empty log"

    def _fold(self):
        """Replay authority entries from an empty replica; the digest after every entry."""
        rep, digests = Replica(self.config.policy()), []
        cur = C.snapshot_digest(sj.encode(rep.freeze()))
        for e in self._entries:
            if e.get("kind") == "authority":
                ev = sj.decode_events([e["event"]], R)[0]
                problems = R.event_problems(ev)
                if problems:
                    raise ValueError("; ".join(problems))
                rep.deliver(ev)
                cur = C.snapshot_digest(sj.encode(rep.freeze()))
                if cur != e.get("snapshot_digest"):
                    raise ValueError(f"entry {e['i']} records a digest the fold does not produce")
            digests.append(cur)
        return rep, digests

    def _read_snapshot(self):
        try:
            obj = json.loads((self.state_dir / "snapshot.json").read_bytes())
            if not isinstance(obj, dict) or obj.get("contract") != C.CONTRACT_VERSION:
                return None, "snapshot file is not a contract snapshot"
            if C.snapshot_digest(obj["snapshot"]) != obj.get("digest"):
                return None, "snapshot file's digest is not its content's"
            frozen = sj.decode(obj["snapshot"], R)
            return Replica.thaw(self.config.policy(), frozen), ""
        except CorruptSnapshot as e:
            return None, "INVALID_SNAPSHOT: " + "; ".join(e.problems[:3])
        except FileNotFoundError:
            return None, "no snapshot file"
        except Exception as e:  # malformed JSON or encoding: refused, never trusted
            return None, f"snapshot does not decode: {type(e).__name__}: {e}"

    def _index(self, rep: Replica) -> _Loaded:
        loaded = _Loaded(rep)
        for e in self._entries:
            if e["kind"] == "decision":
                if e.get("consumes") and e["request"]["request_id"] not in loaded.consumed:
                    loaded.consumed[e["request"]["request_id"]] = e["i"]
                if e.get("outcome") == "ADMIT":
                    loaded.unresolved[e["i"]] = e
            elif e["kind"] in ("complete", "abandoned"):
                d = loaded.unresolved.pop(e["decision"], None)
                if d is not None and e["kind"] == "complete":
                    loaded.effects[d["effect"]["path"]] = d["effect"]["content_sha256"]
        return loaded

    def _open_effects_root(self) -> None:
        if self._effects_fd is None:
            self._effects_fd = os.open(self.effects_dir, _DIR_FLAGS)

    # ----------------------------------------------------------- persistence
    def _append(self, fields: dict, *, snapshot_dig: Optional[str] = None, commit: bool = True) -> int:
        i = len(self._entries)
        prev = C.line_hash(self._lines[-1]) if self._lines else C.GENESIS_HEAD
        body = {**fields, "i": i, "prev": prev, "contract": C.CONTRACT_VERSION}
        entry = {**body, "mac": C.mac(self.keys.gate, C.D_ENTRY, body)}
        line = C.canonical(entry)
        fd = os.open(self.state_dir / "log.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            _write_all(fd, line + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        self._lines.append(line)
        self._entries.append(entry)
        if commit:
            self._commit(snapshot_dig)
        return i

    def _current_digest(self) -> str:
        return C.snapshot_digest(sj.encode(self._loaded.replica.freeze()))

    def _commit(self, snapshot_dig: Optional[str] = None) -> None:
        fields = C.commitment_fields(len(self._lines), C.line_hash(self._lines[-1]),
                                     snapshot_dig or self._current_digest())
        self.witness.publish({**fields, "mac": C.mac(self.keys.gate, C.D_COMMIT, fields)})

    def _write_snapshot(self, rep: Replica) -> None:
        enc = sj.encode(rep.freeze())
        data = C.canonical({"contract": C.CONTRACT_VERSION, "digest": C.snapshot_digest(enc), "snapshot": enc})
        tmp = self.state_dir / (C.TEMP_PREFIX + "snapshot")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.state_dir / "snapshot.json")
        dfd = os.open(self.state_dir, _DIR_FLAGS)
        try:
            _fsync_dir(dfd)
        finally:
            os.close(dfd)

    # ------------------------------------------------------------- authority
    def apply_authority(self, event) -> C.Response:
        """Deliver an authority event (trusted channel in E1). A malformed event
        is refused (MALFORMED_REQUEST) and changes nothing."""
        if self.state != C.OPERATIONAL:
            return C.Response(C.REFUSED, self.state, detail=self.detail)
        problems = R.event_problems(event)
        if problems:
            return C.Response(C.REFUSED, "MALFORMED_REQUEST", detail="; ".join(problems))
        # deliver to a copy: memory changes only once the log entry is durable
        rep = Replica.thaw(self.config.policy(), self._loaded.replica.freeze())
        rep.deliver(event)
        dig = C.snapshot_digest(sj.encode(rep.freeze()))
        i = self._append({"kind": "authority", "event": sj.encode_events([event])[0], "snapshot_digest": dig},
                         commit=False)
        self._loaded.replica = rep
        self._write_snapshot(rep)
        self._commit(dig)
        return C.Response(C.ADMITTED, None, i, detail="authority event delivered")

    # --------------------------------------------------------------- requests
    @staticmethod
    def _request_record(req) -> dict:
        if not isinstance(req, C.Request):
            return {"unrecognized": type(req).__name__}
        rec = {}
        for k, v in req.as_record().items():
            rec[k] = v if isinstance(v, (str, int)) and not isinstance(v, bool) else repr(v)[:200]
        return rec

    def _decide(self, req, reason: Optional[str], *, consumes: bool, head=None, effect=None, detail="") -> int:
        fields = {"kind": "decision", "outcome": "ADMIT" if reason is None else "REFUSE", "reason": reason,
                  "request": self._request_record(req), "consumes": consumes,
                  "head": None if head is None else {"status": head.status, "value": head.value, "reason": head.reason},
                  "snapshot_digest": self._current_digest(), "detail": detail[:500]}
        if effect is not None:
            fields["effect"] = effect
        i = self._append(fields)
        if consumes and req.request_id not in self._loaded.consumed:
            self._loaded.consumed[req.request_id] = i
        return i

    def _refuse(self, req, reason, *, consumes, head=None, detail="") -> C.Response:
        i = self._decide(req, reason, consumes=consumes, head=head, detail=detail)
        return C.Response(C.REFUSED, reason, i, None, head and head.status, head and head.value, detail)

    def request(self, req, content) -> C.Response:
        if self.state != C.OPERATIONAL:
            return C.Response(C.REFUSED, self.state, detail=self.detail)
        L = self._loaded
        problems = C.request_problems(req, content)
        if problems:
            return self._refuse(req, "MALFORMED_REQUEST", consumes=False, detail="; ".join(problems))
        key = self.keys.principals.get(req.requester) if req.requester in self.config.principals else None
        if key is None:
            return self._refuse(req, "UNKNOWN_PRINCIPAL", consumes=False)
        if "skip_auth" not in self.mutants and not C.mac_ok(key, C.D_REQUEST, req.fields(), req.mac):
            return self._refuse(req, "FORGED_REQUEST", consumes=False)
        if "skip_replay" not in self.mutants and req.request_id in L.consumed:
            return self._refuse(req, "REPLAYED_REQUEST", consumes=False,
                                detail=f"first decided at log index {L.consumed[req.request_id]}")
        tp = C.target_problems(req.target)
        if tp:
            return self._refuse(req, "INVALID_TARGET", consumes=True, detail="; ".join(tp))
        head = L.replica.explain_head(req.lineage)
        if head.status in C.HEAD_REFUSALS:
            return self._refuse(req, C.HEAD_REFUSALS[head.status], consumes=True, head=head)
        if req.cites != head.value:
            return self._refuse(req, "STALE_AUTHORITY", consumes=True, head=head,
                                detail=f"cites {req.cites}, head is {head.value}")
        if L.replica.admitted[head.value].owner != req.requester:
            return self._refuse(req, "NOT_AUTHORIZED", consumes=True, head=head,
                                detail=f"{head.value} is owned by {L.replica.admitted[head.value].owner}")
        comps = C.sandbox_components(req.lineage, req.target)
        path = "/".join(comps)
        if "skip_prestate" not in self.mutants:
            observed = self._observe(comps)
            if observed[0] == "unsafe":
                return self._refuse(req, "PATH_UNSAFE", consumes=True, head=head, detail=observed[1])
            expected = L.effects.get(path)
            ok = observed == ("absent",) if expected is None else observed == ("file", expected, 1)
            if not ok:
                return self._refuse(req, "PRESTATE_MISMATCH", consumes=True, head=head,
                                    detail=f"observed {observed[0]}, expected {'absent' if expected is None else 'a file'}")
        eid = C.effect_id(req.request_id, req.lineage, req.target, req.content_sha256)
        effect = {"effect_id": eid, "lineage": req.lineage, "target": req.target, "path": path,
                  "content_sha256": req.content_sha256}
        i = self._decide(req, None, consumes=True, head=head, effect=effect)
        L.unresolved[i] = self._entries[i]
        if "crash_after_admit" in self.faults:
            self.faults.discard("crash_after_admit")
            raise SimulatedCrash(f"after ADMIT at log index {i}")
        try:
            observed_sha = self._write_effect(comps, content, eid)
        except (OSError, EffectError) as e:
            self._append({"kind": "abandoned", "decision": i, "effect_id": eid, "detail": _describe(e)})
            del L.unresolved[i]
            return C.Response(C.REFUSED, C.EFFECT_FAILED, i, eid, head.status, head.value, _describe(e))
        self._append({"kind": "complete", "decision": i, "effect_id": eid, "content_sha256": req.content_sha256,
                      "observed_sha256": observed_sha})
        del L.unresolved[i]
        L.effects[path] = req.content_sha256
        return C.Response(C.ADMITTED, None, i, eid, head.status, head.value)

    # ------------------------------------------------------------ filesystem
    def _observe(self, comps) -> tuple:
        """('absent',) | ('file', sha256, nlink) | ('unsafe', why). Never follows a link."""
        dfd = os.dup(self._effects_fd)
        try:
            for name in comps[:-1]:
                try:
                    nfd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
                except FileNotFoundError:
                    return ("absent",)
                except OSError as e:
                    return ("unsafe", _not_a_dir(name, e))
                os.close(dfd)
                dfd = nfd
            last = comps[-1]
            try:
                st = os.stat(last, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                return ("absent",)
            if not stat.S_ISREG(st.st_mode):
                kind = "symlink" if stat.S_ISLNK(st.st_mode) else "directory" if stat.S_ISDIR(st.st_mode) else "special file"
                return ("unsafe", f"target {last!r} is a {kind}")
            try:
                ffd = os.open(last, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=dfd)
            except OSError as e:
                return ("unsafe", f"target {last!r} cannot be opened without following a link: "
                                  f"{errno.errorcode.get(e.errno, e.errno) if e.errno not in (errno.ELOOP,) else 'link'}")
            try:
                return ("file", C.sha256_hex(_read_all(ffd)), os.fstat(ffd).st_nlink)
            finally:
                os.close(ffd)
        finally:
            os.close(dfd)

    def _write_effect(self, comps, content: bytes, eid: str) -> str:
        self.hook("before_walk", components=comps)
        if "follow_symlinks" in self.mutants:  # MUTANT: plain path joins, links followed
            p = self.effects_dir.joinpath(*comps)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            return C.sha256_hex(p.read_bytes())
        dfd = os.dup(self._effects_fd)
        tmp = C.TEMP_PREFIX + eid[:16]
        made_tmp = False
        try:
            for name in comps[:-1]:
                try:
                    try:
                        nfd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
                    except FileNotFoundError:
                        os.mkdir(name, 0o755, dir_fd=dfd)
                        _fsync_dir(dfd)
                        nfd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
                except OSError as e:
                    raise EffectError(_not_a_dir(name, e)) from e
                os.close(dfd)
                dfd = nfd
            self.hook("before_temp", dir_fd=dfd, name=comps[-1])
            if "fail_write" in self.faults:
                self.faults.discard("fail_write")
                raise OSError(errno.EIO, "injected write failure")
            ffd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                          0o644, dir_fd=dfd)
            made_tmp = True
            try:
                _write_all(ffd, content)
                os.fsync(ffd)
            finally:
                os.close(ffd)
            if "crash_after_temp" in self.faults:
                self.faults.discard("crash_after_temp")
                raise SimulatedCrash("after the temporary file, before the rename")
            self.hook("before_rename", dir_fd=dfd, name=comps[-1])
            try:
                st = os.stat(comps[-1], dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    raise EffectError(f"target {comps[-1]!r} became a directory")
            except FileNotFoundError:
                pass
            os.replace(tmp, comps[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
            made_tmp = False
            _fsync_dir(dfd)
            rfd = os.open(comps[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=dfd)
            try:
                return C.sha256_hex(_read_all(rfd))
            finally:
                os.close(rfd)
        except SimulatedCrash:
            made_tmp = False  # a crash leaves the temporary file behind, as a real one would
            raise
        finally:
            if made_tmp:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass
            os.close(dfd)

    # --------------------------------------------------------------- recovery
    def recover(self) -> C.Response:
        """Settle SNAPSHOT_MISMATCH and UNCERTAIN_STATE, in the log; refuse every other state."""
        settled, seen_states = [], []
        while self.state in C.RECOVERABLE_STATES:
            if seen_states.count(self.state) >= 2:  # a repair that did not take: stop, stay fail-closed
                return C.Response(C.REFUSED, self.state, detail=f"recovery did not settle {self.state}")
            seen_states.append(self.state)
            if self.state == "SNAPSHOT_MISMATCH":
                fold, _ = self._fold()
                snap, _ = self._read_snapshot()
                self._loaded = self._index(fold)
                self._write_snapshot(fold)
                dig = self._current_digest()
                self._append({"kind": "recovery", "reason": "SNAPSHOT_MISMATCH",
                              "discarded_snapshot_digest": C.snapshot_digest(sj.encode(snap.freeze())) if snap else None,
                              "rebuilt_snapshot_digest": dig}, snapshot_dig=dig)
                settled.append("snapshot rebuilt from the log")
            else:  # UNCERTAIN_STATE
                fold, _ = self._fold()
                self._loaded = self._index(fold)
                self._open_effects_root()
                for i, d in sorted(self._loaded.unresolved.items()):
                    eff = d["effect"]
                    comps = C.sandbox_components(eff["lineage"], eff["target"])
                    self._remove_temp(comps, eff["effect_id"])
                    obs = self._observe(comps)
                    if obs[:2] == ("file", eff["content_sha256"]):
                        self._append({"kind": "complete", "decision": i, "effect_id": eff["effect_id"],
                                      "content_sha256": eff["content_sha256"], "observed_sha256": obs[1],
                                      "recovered": True}, commit=False)
                        settled.append(f"{i}: complete")
                    else:
                        self._append({"kind": "abandoned", "decision": i, "effect_id": eff["effect_id"],
                                      "detail": f"recovery observed {obs[0]}"}, commit=False)
                        settled.append(f"{i}: abandoned")
                self._loaded = self._index(fold)
                self._append({"kind": "recovery", "reason": "UNCERTAIN_STATE", "settled": settled[-50:]})
            self._verify()
        if self.state != C.OPERATIONAL:
            return C.Response(C.REFUSED, self.state, detail=self.detail)
        return C.Response(C.ADMITTED, None, len(self._entries) - 1, detail="; ".join(settled) or "nothing to recover")

    def _remove_temp(self, comps, eid: str) -> None:
        dfd = os.dup(self._effects_fd)
        try:
            for name in comps[:-1]:
                try:
                    nfd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
                except OSError:
                    return
                os.close(dfd)
                dfd = nfd
            try:
                os.unlink(C.TEMP_PREFIX + eid[:16], dir_fd=dfd)
            except FileNotFoundError:
                pass
        finally:
            os.close(dfd)

    # --------------------------------------------------------------- queries
    def log_entries(self) -> list:
        return list(self._entries)
