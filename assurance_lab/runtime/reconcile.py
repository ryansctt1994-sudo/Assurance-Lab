"""AL-RUNTIME-001 E1 reconciler: decisions against observed effects.

Independent of the gate: it shares only ``contract`` (encodings, MACs, path
rules). It reads the witness, the decision log and the ``effects/`` tree
itself, never the gate's memory or code, and reports the findings defined in
``docs/RUNTIME_CONTRACT.md``.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import contract as C

#: Each disables one control, for calibration only (a diagnostic, never evidence).
MUTANTS = frozenset({
    "trust_log",      # no filesystem walk: whatever the log explains is assumed present
    "skip_witness",   # the log is never compared with the witness
    "skip_mac",       # entries are not MAC-checked (chain and form still are)
})


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    detail: str = ""

    def key(self) -> tuple:
        return (self.kind, self.subject)


@dataclass
class Report:
    verdict: str
    findings: list = field(default_factory=list)
    log_entries: int = 0
    witness_seq: int = 0
    expected_files: dict = field(default_factory=dict)
    observed_files: dict = field(default_factory=dict)

    def keys(self) -> set:
        return {f.key() for f in self.findings}

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "log_entries": self.log_entries, "witness_seq": self.witness_seq,
                "findings": [{"kind": f.kind, "subject": f.subject, "detail": f.detail} for f in self.findings],
                "expected_files": dict(sorted(self.expected_files.items())),
                "observed_files": dict(sorted(self.observed_files.items()))}


def _read_witness(path: Path, gate_key: bytes):
    """Every commitment, verified, in order: ([(seq, log_head), ...], problem).

    The whole history is checked, not only the latest commitment: a gate that
    kept writing after a rollback publishes a new latest commitment that
    matches its forked log, but its earlier commitments no longer do."""
    try:
        lines = [ln for ln in path.read_bytes().split(b"\n") if ln]
    except FileNotFoundError:
        return [], ""
    history = []
    for n, line in enumerate(lines):
        try:
            w = json.loads(line)
            fields = {k: w[k] for k in ("contract", "seq", "log_head", "snapshot_digest")}
        except (ValueError, KeyError, TypeError) as e:
            return None, f"commitment {n} does not parse: {type(e).__name__}"
        if not isinstance(fields["seq"], int) or not C.mac_ok(gate_key, C.D_COMMIT, fields, w.get("mac")):
            return None, f"commitment {n} fails its MAC"
        history.append((fields["seq"], fields["log_head"]))
    return history, ""


def _read_log(path: Path, gate_key: bytes, check_mac: bool = True):
    """(entries, line_hashes, problem). Stops at the first unverifiable entry."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], [], "no log"
    raw = data.split(b"\n")
    if raw and raw[-1] == b"":
        raw = raw[:-1]
    else:
        return [], [], "log does not end with a complete line"
    entries, hashes, prev = [], [], C.GENESIS_HEAD
    for i, line in enumerate(raw):
        try:
            e = json.loads(line)
            body = {k: v for k, v in e.items() if k != "mac"}
            canon = C.canonical(e)
        except (ValueError, AttributeError, TypeError):
            return entries, hashes, f"entry {i} does not parse"
        if canon != line or e.get("i") != i or e.get("prev") != prev or e.get("contract") != C.CONTRACT_VERSION:
            return entries, hashes, f"entry {i} breaks the chain or its canonical form"
        if check_mac and not C.mac_ok(gate_key, C.D_ENTRY, body, e.get("mac")):
            return entries, hashes, f"entry {i} fails its MAC"
        entries.append(e)
        prev = C.line_hash(line)
        hashes.append(prev)
    return entries, hashes, ""


def _walk(root: Path):
    """Every entry under ``root``, never following a link: path -> (kind, sha256 or None, nlink)."""
    out = {}

    def visit(d: Path, rel: str):
        with os.scandir(d) as it:
            for de in sorted(it, key=lambda x: x.name):
                p = f"{rel}/{de.name}" if rel else de.name
                st = de.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    out[p] = ("dir", None, st.st_nlink)
                    visit(Path(de.path), p)
                elif stat.S_ISREG(st.st_mode):
                    fd = os.open(de.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    try:
                        chunks = []
                        while True:
                            b = os.read(fd, 1 << 16)
                            if not b:
                                break
                            chunks.append(b)
                    finally:
                        os.close(fd)
                    out[p] = ("file", C.sha256_hex(b"".join(chunks)), st.st_nlink)
                else:
                    out[p] = ("symlink" if stat.S_ISLNK(st.st_mode) else "special", None, st.st_nlink)
    if root.is_dir() and not root.is_symlink():
        visit(root, "")
    return out


def reconcile(gate_root, witness_dir, gate_key: bytes, mutants=frozenset()) -> Report:
    unknown = set(mutants) - MUTANTS
    if unknown:
        raise ValueError(f"unknown reconciler mutants {sorted(unknown)}")
    gate_root, witness_dir = Path(gate_root), Path(witness_dir)
    findings = []
    wit, wproblem = _read_witness(witness_dir / "commitments.jsonl", gate_key)
    if wproblem:
        findings.append(Finding("WITNESS_INVALID", "witness", wproblem))
    entries, hashes, lproblem = _read_log(gate_root / "state/log.jsonl", gate_key, "skip_mac" not in mutants)
    if lproblem:
        findings.append(Finding("LOG_TAMPERED", "log", lproblem))
    if wit is not None and not lproblem and "skip_witness" not in mutants:
        diverged = [(s, h) for s, h in wit if s > len(entries) or (s > 0 and hashes[s - 1] != h)]
        latest = wit[-1][0] if wit else 0
        if diverged:
            s, _h = diverged[0]
            findings.append(Finding("LOG_BEHIND_WITNESS", "log",
                                    f"{len(diverged)} commitments not in the log (first: seq {s}); log has {len(entries)}"))
        elif len(entries) > latest:
            findings.append(Finding("LOG_AHEAD_OF_WITNESS", "log", f"{len(entries) - latest} uncommitted entries"))

    # Fold the verified log: what the effects tree should hold.
    expected, previous, admitted, unresolved = {}, {}, {}, {}
    for e in entries:
        if e.get("kind") == "decision" and e.get("outcome") == "ADMIT":
            eff = e["effect"]
            admitted[e["i"]] = eff
            unresolved[e["i"]] = eff
            previous[e["i"]] = expected.get(eff["path"])
        elif e.get("kind") in ("complete", "abandoned"):
            eff = unresolved.pop(e.get("decision"), None)
            if eff is not None and e["kind"] == "complete":
                expected[eff["path"]] = eff["content_sha256"]
    for i, eff in sorted(unresolved.items()):
        findings.append(Finding("UNRESOLVED_EFFECT", f"decision {i}", eff["path"]))
    allowed_dirs = set()
    for eff in admitted.values():
        parts = eff["path"].split("/")
        allowed_dirs.update("/".join(parts[:k]) for k in range(1, len(parts)))
    in_flight = {eff["path"]: {previous[i], eff["content_sha256"]} for i, eff in unresolved.items()}

    if "trust_log" in mutants:  # MUTANT: what the log explains is assumed to be there
        observed = {p: ("file", sha, 1) for p, sha in expected.items()}
        observed.update({d: ("dir", None, 2) for d in allowed_dirs})
    else:
        observed = _walk(gate_root / "effects")
    for path, sha in sorted(expected.items()):
        o = observed.get(path)
        if path in in_flight:
            continue
        if o is None:
            findings.append(Finding("MISSING_EFFECT", path))
        elif o[0] != "file" or o[1] != sha:
            findings.append(Finding("MODIFIED_EFFECT", path, o[0]))
    for path, (kind, sha, nlink) in sorted(observed.items()):
        name = path.rsplit("/", 1)[-1]
        if kind == "dir":
            if path not in allowed_dirs:
                findings.append(Finding("UNEXPECTED_DIRECTORY", path))
            continue
        if name.startswith(C.TEMP_PREFIX):
            findings.append(Finding("TEMP_LEFTOVER", path))
            continue
        if kind != "file":
            if path not in expected:
                findings.append(Finding("UNEXPECTED_NON_REGULAR", path, kind))
            continue
        if nlink > 1:
            findings.append(Finding("MULTI_LINKED_FILE", path, f"{nlink} links"))
        if path in in_flight:
            if sha not in in_flight[path]:
                findings.append(Finding("MODIFIED_EFFECT", path, "neither the previous nor the admitted content"))
        elif path not in expected:
            findings.append(Finding("UNEXPECTED_FILE", path))
    findings.sort(key=lambda f: (C.FINDINGS.index(f.kind), f.subject))
    return Report(C.CONSISTENT if not findings else C.INCONSISTENT, findings, len(entries),
                  wit[-1][0] if wit else 0, expected,
                  {p: v[1] for p, v in observed.items() if v[0] == "file"})
