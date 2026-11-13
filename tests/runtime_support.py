"""Shared helpers for the AL-RUNTIME-001 tests (not a test module)."""

import os
import stat
from pathlib import Path

from assurance_lab.runtime import contract as C
from assurance_lab.runtime import harness as H
from assurance_lab.runtime.gate import Gate, Witness
from assurance_lab.runtime.reconcile import reconcile

#: H.AUTHORITY, by name: r0 (alice); r1 (bob, pred r0); r2 (carol, pred r0); the fork's
#: two edges; carol's r2 wins by alice+bob; invalidate r2; r3 (alice, pred r2) and its
#: edge; lineage M: m0 (bob), m1 (carol, pred m0) and its edge.
EV = dict(zip(("r0", "r1", "r2", "e01", "e02", "res", "inv_r2", "r3", "e23", "m0", "m1", "em"), H.AUTHORITY))


class Env:
    def __init__(self, tmp: Path, **kw):
        self.root = Path(tmp)
        self.gdir, self.wdir, self.outside = self.root / "gate", self.root / "witness", self.root / "outside"
        self.outside.mkdir(parents=True, exist_ok=True)
        (self.outside / "secret.txt").write_bytes(b"outside the sandbox\n")
        self.kw = kw
        self.gate = Gate.create(self.gdir, H.CONFIG, H.KEYS, Witness(self.wdir), allow_public_test_keys=True, **kw)

    @property
    def eff(self) -> Path:
        return self.gdir / "effects"

    def reopen(self, **kw) -> Gate:
        self.gate.close()
        opts = {"allow_public_test_keys": True, **self.kw, **kw}
        self.gate = Gate.open(self.gdir, H.CONFIG, H.KEYS, Witness(self.wdir), **opts)
        return self.gate

    def auth(self, *names):
        for n in names:
            r = self.gate.apply_authority(EV[n])
            assert r.outcome == C.ADMITTED, (n, r)

    @staticmethod
    def make(rid, who, lineage, cites, target, content=b"v1\n", signer=None):
        return C.make_request(C.public_test_key(signer or who), rid, who, lineage, cites, target, content), content

    def send(self, rid, who, lineage, cites, target, content=b"v1\n", signer=None):
        req, content = self.make(rid, who, lineage, cites, target, content, signer)
        return self.gate.request(req, content)

    def reconcile(self, **kw):
        return reconcile(self.gdir, self.wdir, H.KEYS.gate, **kw)

    def tree(self, base: Path = None) -> dict:
        """Everything under ``base`` (default: effects/), never following a link."""
        base = base or self.eff
        out = {}
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                st = os.lstat(p)
                rel = p.relative_to(base).as_posix()
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = ("link", os.readlink(p))
                elif stat.S_ISDIR(st.st_mode):
                    out[rel] = ("dir",)
                else:
                    out[rel] = ("file", p.read_bytes(), st.st_nlink)
        return out

    def persisted(self) -> dict:
        """Bytes of everything the gate persists or publishes."""
        out = {}
        for p in (self.gdir / "state/log.jsonl", self.gdir / "state/snapshot.json", self.wdir / "commitments.jsonl"):
            out[p.name] = p.read_bytes() if p.exists() else None
        out["effects"] = self.tree()
        return out

    def copy_state(self) -> dict:
        st = self.gdir / "state"
        return {n: (st / n).read_bytes() for n in ("log.jsonl", "snapshot.json")}

    def restore_state(self, saved: dict, names=("log.jsonl", "snapshot.json")) -> None:
        for n in names:
            (self.gdir / "state" / n).write_bytes(saved[n])

    def log(self) -> list:
        import json
        return [json.loads(x) for x in (self.gdir / "state/log.jsonl").read_bytes().splitlines()]

    def close(self):
        self.gate.close()
