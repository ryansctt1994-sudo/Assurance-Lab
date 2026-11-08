"""Snapshot execution and import-origin verification."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from assurance_lab.__main__ import preflight, start_receipt, QUICK_CORE
from assurance_lab.provenance import (
    PKG, Manifest, commit_digest, foreign_origins, make_snapshot, remove_snapshot,
    snapshot_command, source_digest,
)

REPO = PKG.parent


def worktree_commit(tmp_path) -> str:
    """A commit object of the working tree's assurance_lab/, made with a
    temporary index: HEAD, the real index and the working tree are untouched."""
    env = dict(os.environ, GIT_INDEX_FILE=str(tmp_path / "index"),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    run = lambda *a: subprocess.run(["git", *a], cwd=REPO, env=env, check=True,  # noqa: E731
                                    capture_output=True, text=True).stdout.strip()
    run("read-tree", "HEAD")
    run("add", "-A", "--", "assurance_lab")
    return run("commit-tree", run("write-tree"), "-p", "HEAD", "-m", "test snapshot")


@pytest.fixture
def snap(tmp_path):
    m = make_snapshot(REPO, worktree_commit(tmp_path), command="test")
    yield m
    remove_snapshot(m.snapshot_root)
    assert not Path(m.snapshot_root).exists()


def test_snapshot_matches_git_objects_and_working_tree(snap):
    root = Path(snap.snapshot_root)
    assert snap.snapshot_sha256 == snap.git_object_sha256 == source_digest(root / "assurance_lab")
    # The working tree (uncommitted edits included, via the temp commit) has the same bytes.
    assert snap.snapshot_sha256 == source_digest(PKG)
    assert commit_digest(REPO, snap.commit) == snap.git_object_sha256


def test_snapshot_is_read_only(snap):
    root = Path(snap.snapshot_root)
    f = root / "assurance_lab" / "provenance.py"
    assert stat.S_IMODE(f.stat().st_mode) == 0o444
    assert stat.S_IMODE((root / "assurance_lab").stat().st_mode) == 0o555
    if os.geteuid() != 0:  # root bypasses permission bits; documented limit
        with pytest.raises(PermissionError):
            f.write_text("tamper")


PROBE = r"""
import json, multiprocessing as mp, sys
import assurance_lab.provenance as p
with mp.get_context(sys.argv[1]).Pool(1) as pool:
    worker = pool.apply(p.loaded_origins)
print(json.dumps({"parent": p.loaded_origins(), "worker": worker, "path": sys.path,
                  "isolated": sys.flags.isolated, "no_site": sys.flags.no_site}))
"""


@pytest.mark.parametrize("method", [m for m in ("fork", "spawn", "forkserver")
                                    if m in __import__("multiprocessing").get_all_start_methods()])
def test_snapshot_interpreter_and_workers_import_only_from_snapshot(snap, method):
    # Same interpreter flags and sys.path bootstrap the campaign uses.
    cmd = snapshot_command(snap, [])[:4]  # [python, -I, -S, -B]
    boot = f"import sys; sys.path.insert(0, {snap.snapshot_root!r})\n" + PROBE
    out = subprocess.run([*cmd, "-c", boot, method], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    d = json.loads(out)
    snap_pkg = Path(snap.snapshot_root) / "assurance_lab"
    assert d["isolated"] and d["no_site"]
    assert not any("site-packages" in x for x in d["path"])
    assert str(REPO) not in d["path"] and "" not in d["path"]
    for origins in (d["parent"], d["worker"]):
        assert origins and foreign_origins(origins, snap_pkg) == []


def test_foreign_origin_detection():
    assert foreign_origins([str(PKG / "verdict.py")], PKG) == []
    assert foreign_origins(["/elsewhere/assurance_lab/verdict.py"], PKG) == [
        "/elsewhere/assurance_lab/verdict.py"]


def _manifest(**kw):
    rec = start_receipt(QUICK_CORE, 1, "fork", "t")
    base = dict(command="t", commit=rec["commit"], tree_id=rec["tree_id"], launcher_tree="clean",
                snapshot_root=str(PKG.parent), snapshot_sha256=rec["source_sha256"],
                git_object_sha256=rec["source_sha256"])
    base.update(kw)
    return Manifest(**base), rec


def test_snapshot_preflight_refusals():
    # Pretend this checkout is the snapshot so each check can be hit in isolation.
    m, rec = _manifest(snapshot_root="/somewhere/else")
    assert "not the snapshot" in preflight(rec, True, False, m)
    m, rec = _manifest(snapshot_sha256="0" * 64)
    assert "changed between export" in preflight(rec, True, False, m)
    m, rec = _manifest()
    if not (sys.flags.isolated and sys.flags.no_site):
        assert "isolated interpreter" in preflight(rec, True, False, m)


def test_manifest_round_trips():
    m, _ = _manifest()
    assert Manifest.from_json(m.to_json()) == m and json.loads(m.to_json())["commit"] == m.commit
