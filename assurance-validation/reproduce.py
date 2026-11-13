#!/usr/bin/env python3
"""Reproduce the frozen baseline from a fresh clone and seal the evidence.

    python assurance-validation/reproduce.py --out <new-dir> --label R1 \\
        --operator "<who you are; your role, if any, in the baseline; what you have seen of the targets>"

Self-contained on purpose: stdlib only and nothing imported from
``assurance_lab``, so an operator audits this file and the frozen manifest,
not the system under test.

What it does, in order:

1. clones ``--source`` (default: this checkout; or a URL) into a fresh
   temporary directory and checks out the subject commit, detached;
2. records ``git rev-parse HEAD``, ``HEAD^{tree}``, the ``assurance_lab/``
   tree id and ``git status --porcelain``, and **recomputes the executed-source
   SHA-256 itself** from the subject's published definition (not its code);
3. creates a fresh virtual environment (``--no-venv`` uses this interpreter)
   and runs the frozen commands with ``PYTHON*`` variables cleared, keeping
   raw stdout, stderr, exit codes and durations, and copying each declared
   report;
4. seals the directory: ``output-hashes.sha256`` lists the SHA-256 of every
   evidence file (``sha256sum -c output-hashes.sha256`` verifies it), and
   ``pre-reveal-commitment.json`` carries the SHA-256 of that file. Publish
   that commitment to an independently timestamped place **before** running
   ``reconcile.py``. The expected values are never read by this script.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "protocol" / "claim-subject-manifest.json"
HASHES, COMMITMENT = "output-hashes.sha256", "pre-reveal-commitment.json"
UNSEALED = (HASHES, COMMITMENT, "timestamp-anchor.json", "reconciliation.json")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def run(argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout.strip()


def source_digest(pkg: Path) -> str:
    """Re-implemented from the subject's published definition (manifest
    field ``source_digest_definition``): SHA-256 over every ``*.py`` file,
    sorted by relative path components, each as path, NUL, bytes, NUL."""
    entries = sorted(((p.relative_to(pkg).as_posix(), p.read_bytes()) for p in pkg.rglob("*.py")),
                     key=lambda e: PurePosixPath(e[0]).parts)
    h = hashlib.sha256()
    for rel, data in entries:
        h.update(rel.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def make_venv(where: Path) -> str:
    subprocess.run([sys.executable, "-m", "venv", str(where)], check=True, capture_output=True)
    for rel in ("bin/python", "Scripts/python.exe"):
        if (where / rel).exists():
            return str(where / rel)
    raise RuntimeError("the new virtual environment has no interpreter")


def commitment_fields(environment: dict, commands: list, out: Path) -> dict:
    """What the published commitment says about the evidence it seals."""
    co = environment.get("checkout") or {}
    report = next((c["artifact"] for c in commands if c.get("role") == "full-campaign" and c.get("artifact")), None)
    return {"evidence_id": environment.get("evidence_id"), "label": environment.get("label"),
            "operator_declaration": environment.get("operator_declaration"),
            "subject_commit": co.get("head"), "subject_tree": co.get("tree"),
            "source_sha256_assurance_lab": environment.get("source_sha256_assurance_lab"),
            "report_sha256": sha256_file(out / report) if report else None}


def seal(out: Path, fields: dict) -> str:
    """Hash every evidence file into ``output-hashes.sha256`` (sha256sum
    format), then commit to that file. Returns the commitment."""
    names = sorted(f.name for f in out.iterdir() if f.is_file() and f.name not in UNSEALED)
    (out / HASHES).write_text("".join(f"{sha256_file(out / n)}  {n}\n" for n in names), encoding="utf-8")
    commitment = sha256_file(out / HASHES)
    write_json(out / COMMITMENT, {**fields, "algorithm": "sha256", "covers": HASHES, "commitment": commitment})
    return commitment


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="new or empty directory for the sealed evidence")
    ap.add_argument("--operator", required=True,
                    help="identity, role in the baseline (if any), and prior exposure to the expected values")
    ap.add_argument("--label", default="R1", help="R1 for an independent run; R0-* for author-controlled runs")
    ap.add_argument("--source", default=str(HERE.parent), help="repository to clone (path or URL)")
    ap.add_argument("--no-venv", action="store_true", help="use this interpreter instead of a fresh venv")
    ap.add_argument("--manifest", default=str(MANIFEST),
                    help="claim-subject manifest (reconcile.py rejects evidence made against any but the frozen one)")
    a = ap.parse_args(argv)

    manifest_bytes = Path(a.manifest).read_bytes()
    manifest = json.loads(manifest_bytes)
    subject = manifest["subject"]
    out = Path(a.out).resolve()
    if out.exists() and any(out.iterdir()):
        ap.error(f"{out} is not empty: every file in it would be sealed into the evidence")
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    evidence_id = f"{a.label}:{uuid.uuid4()}"
    environment = {
        "evidence_id": evidence_id, "label": a.label, "operator_declaration": a.operator,
        "harness": {"reproduce.py": sha256_file(Path(__file__).resolve())},
        "manifest_sha256": sha256_bytes(manifest_bytes), "started_utc": now(),
        "host": {"python": sys.version.split()[0], "implementation": platform.python_implementation(),
                 "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count()},
        "venv": not a.no_venv,
        "cleared_env_vars": sorted(k for k in os.environ if k.startswith("PYTHON")),
    }
    commands = []
    work = Path(tempfile.mkdtemp(prefix="assurance-reproduction-"))
    clone = work / "repo"
    try:
        p = run(["git", "clone", "--quiet", a.source, str(clone)])
        if p.returncode != 0:
            raise RuntimeError(f"git clone failed: {p.stderr.strip()}")
        git(clone, "checkout", "--quiet", "--detach", subject["source_commit"])
        environment["checkout"] = {
            "source": a.source,
            "head": git(clone, "rev-parse", "HEAD"),
            "tree": git(clone, "rev-parse", "HEAD^{tree}"),
            "tree_assurance_lab": git(clone, "rev-parse", "HEAD:assurance_lab"),
            "status_porcelain": git(clone, "status", "--porcelain"),
        }
        environment["source_sha256_assurance_lab"] = source_digest(clone / "assurance_lab")
        environment["git_version"] = git(clone, "--version")
        python = sys.executable if a.no_venv else make_venv(work / "venv")
        v = run([python, "--version"], env=env)
        environment["python_version"] = (v.stdout or v.stderr).strip()
        for i, cmd in enumerate(manifest["commands"]):
            argv_ = [python if c == "python" else c for c in cmd["argv"]]
            t0 = time.monotonic()
            p = run(argv_, cwd=clone, env=env)
            stem = f"cmd{i}-{cmd['role']}"
            (out / f"{stem}.stdout.log").write_text(p.stdout, encoding="utf-8")
            (out / f"{stem}.stderr.log").write_text(p.stderr, encoding="utf-8")
            rec = {"role": cmd["role"], "argv": cmd["argv"], "exit_code": p.returncode,
                   "seconds": round(time.monotonic() - t0, 1),
                   "stdout": f"{stem}.stdout.log", "stderr": f"{stem}.stderr.log"}
            if "report" in cmd and (clone / cmd["report"]).is_file():
                shutil.copy(clone / cmd["report"], out / cmd["artifact"])
                rec["artifact"] = cmd["artifact"]
            commands.append(rec)
        # Tracked files only: the commands are expected to add their reports.
        environment["checkout"]["tracked_changes_after"] = git(
            clone, "status", "--porcelain", "--untracked-files=no")
        environment["pip_freeze"] = run([python, "-m", "pip", "freeze"], env=env).stdout.splitlines()
    except Exception as e:  # recorded and sealed: a failed run is evidence too
        environment["harness_error"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    environment["finished_utc"] = now()
    write_json(out / "environment.json", environment)
    write_json(out / "commands.json", commands)
    commitment = seal(out, {**commitment_fields(environment, commands, out), "sealed_utc": now()})
    print(f"sealed: {out}")
    print(f"commitment (publish BEFORE running reconcile.py): {commitment}")
    ok = "harness_error" not in environment and all(c["exit_code"] == 0 for c in commands)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
