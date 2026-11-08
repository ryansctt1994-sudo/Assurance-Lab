"""Binding verification results to the code that produced them.

Two execution modes:

* **in-place**: the campaign imports ``assurance_lab`` from the working tree.
  Start/end hashing detects *net* source change only; an edit reverted
  before the end (A→B→A) is invisible, yet a spawn/forkserver worker started
  in between would have run B.
* **snapshot**: the committed ``assurance_lab/`` tree is exported with
  ``git archive`` into a fresh temporary directory, its bytes are
  cross-checked against the git objects, and the campaign runs there in an
  isolated interpreter (``-I -S -B``: no environment, no site-packages, no
  bytecode writes). The working tree cannot affect execution, so A→B→A in
  the working tree is irrelevant. Every process that runs campaign code
  reports where it imported ``assurance_lab`` from, and those origins are
  verified rather than assumed.

Limits, stated plainly:

* Snapshot files are made read-only, but **permissions do not bind root**.
  The snapshot's protection against modification is that nothing else knows
  its random path; tampering by a process that does is *detected* by the
  end-of-run re-hash only if it is a net change.
* The launcher itself runs from the working tree, which is why a report is
  refused when that tree is dirty.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

PKG = Path(__file__).resolve().parent
PKG_NAME = PKG.name  # "assurance_lab"


class ProvenanceError(Exception):
    pass


# ---------------------------------------------------------------- digests


def digest_entries(entries: Iterable[tuple[str, bytes]]) -> str:
    """SHA-256 over (posix relative path, bytes) pairs, order-independent input."""
    h = sha256()
    for rel, data in sorted(entries, key=lambda e: PurePosixPath(e[0]).parts):
        h.update(rel.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def source_digest(pkg: Path = PKG) -> str:
    """Digest of every ``*.py`` file under a package directory on disk."""
    return digest_entries(
        (p.relative_to(pkg).as_posix(), p.read_bytes()) for p in pkg.rglob("*.py")
    )


def git(repo: Path, *args: str) -> Optional[str]:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def tree_state(repo: Path) -> str:
    out = git(repo, "status", "--porcelain", "--", PKG_NAME)
    if out is None:
        return "unknown"
    return "DIRTY" if out else "clean"


def commit_digest(repo: Path, rev: str) -> str:
    """The same digest computed straight from git objects at ``rev``.

    Independent of any checkout or export: reads each tracked ``*.py`` blob
    under the package with ``git cat-file``.
    """
    listing = subprocess.run(["git", "ls-tree", "-r", "-z", rev, "--", PKG_NAME], cwd=repo,
                             capture_output=True, check=True).stdout.decode()
    entries = []
    for rec in filter(None, listing.split("\0")):
        meta, path = rec.split("\t", 1)
        _mode, kind, oid = meta.split()
        if kind != "blob" or not path.endswith(".py"):
            continue
        blob = subprocess.run(["git", "cat-file", "blob", oid], cwd=repo,
                              capture_output=True, check=True).stdout
        entries.append((path[len(PKG_NAME) + 1:], blob))
    return digest_entries(entries)


# ---------------------------------------------------------------- import origins


def loaded_origins() -> tuple[str, ...]:
    """Files this process imported any ``assurance_lab`` module from.

    Keyed by the module spec name, so a package ``__main__`` re-imported by a
    spawn worker as ``__mp_main__`` is still counted.
    """
    out = set()
    for key, mod in list(sys.modules.items()):
        spec = getattr(mod, "__spec__", None)
        name = getattr(spec, "name", None) or key
        if name == PKG_NAME or name.startswith(PKG_NAME + "."):
            f = getattr(mod, "__file__", None)
            if f:
                out.add(os.path.realpath(f))
    return tuple(sorted(out))


def foreign_origins(origins: Iterable[str], expected_pkg: Path) -> list[str]:
    root = os.path.realpath(expected_pkg)
    return [o for o in origins if os.path.commonpath([o, root]) != root]


# ---------------------------------------------------------------- snapshot


@dataclass(frozen=True)
class Manifest:
    """What the launcher established before handing over to the snapshot."""

    command: str
    commit: str
    tree_id: str
    launcher_tree: str
    snapshot_root: str
    snapshot_sha256: str
    git_object_sha256: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(s: str) -> "Manifest":
        return Manifest(**json.loads(s))


def _extract(tar_bytes: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python without tarfile filters
            for m in tf.getmembers():
                p = PurePosixPath(m.name)
                if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                    raise ProvenanceError(f"unsafe archive member: {m.name}")
            tf.extractall(dest)


def _set_readonly(root: Path, readonly: bool) -> None:
    fmode = 0o444 if readonly else 0o644
    dmode = 0o555 if readonly else 0o755
    for dirpath, dirnames, filenames in os.walk(root):
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), fmode)
        os.chmod(dirpath, dmode)


def make_snapshot(repo: Path, rev: str = "HEAD", command: str = "") -> Manifest:
    commit = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    tree_id = git(repo, "rev-parse", f"{commit}:{PKG_NAME}") if commit else None
    if not commit or not tree_id:
        raise ProvenanceError(f"cannot resolve {rev}:{PKG_NAME} in {repo}")
    root = Path(tempfile.mkdtemp(prefix="assurance-snapshot-"))
    tar = subprocess.run(["git", "archive", "--format=tar", commit, PKG_NAME], cwd=repo,
                         capture_output=True, check=True).stdout
    _extract(tar, root)
    _set_readonly(root, True)
    snap_digest = source_digest(root / PKG_NAME)
    git_digest = commit_digest(repo, commit)
    if snap_digest != git_digest:
        remove_snapshot(root)
        raise ProvenanceError("exported snapshot differs from git objects "
                              "(export-subst / export-ignore attributes?)")
    return Manifest(command=command, commit=commit, tree_id=tree_id,
                    launcher_tree=tree_state(repo), snapshot_root=str(root),
                    snapshot_sha256=snap_digest, git_object_sha256=git_digest)


def remove_snapshot(root) -> None:
    root = Path(root)
    if root.exists():
        _set_readonly(root, False)
        shutil.rmtree(root)


_BOOT = ("import sys, runpy; sys.path.insert(0, sys.argv.pop(1)); "
         "runpy.run_module('assurance_lab', run_name='__main__', alter_sys=True)")


def snapshot_command(m: Manifest, argv: list[str]) -> list[str]:
    """Interpreter command that runs the campaign from the snapshot only.

    ``-I`` ignores PYTHON* variables and user site and keeps the current
    directory off ``sys.path``; ``-S`` drops site-packages (so no editable
    install finder can redirect ``import assurance_lab``); ``-B`` writes no
    bytecode into the snapshot. multiprocessing passes these flags and the
    parent's ``sys.path`` on to spawn/forkserver workers.
    """
    return [sys.executable, "-I", "-S", "-B", "-c", _BOOT, m.snapshot_root, *argv,
            "--_manifest", m.to_json()]


def _flag(name: str) -> int:
    return int(getattr(sys.flags, name, 0))


def interpreter_isolation() -> str:
    return (f"isolated={_flag('isolated')} no_site={_flag('no_site')} "
            f"dont_write_bytecode={_flag('dont_write_bytecode')}")
