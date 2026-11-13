#!/usr/bin/env python3
"""Reconcile a sealed reproduction against the frozen baseline: the reveal step.

    python assurance-validation/reconcile.py <run-dir> --published <commitment>

Decision order (``protocol/acceptance-criteria.md``): integrity, identity,
decidability, comparison. The expected values are read from git **only after**
the sealed evidence has passed integrity and identity, so a receipt that
fails those checks is judged without the targets ever being loaded.

Self-contained: stdlib only, nothing imported from ``assurance_lab``. The
subject and the claim inventory come from the frozen manifest in this
repository, never from the evidence being judged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "protocol" / "claim-subject-manifest.json"
PREREG = HERE / "protocol" / "preregistration-receipt.json"
HASHES, COMMITMENT, ANCHOR, RESULT = ("output-hashes.sha256", "pre-reveal-commitment.json",
                                      "timestamp-anchor.json", "reconciliation.json")
OUTCOMES = ("MATCH", "MISMATCH", "INCONCLUSIVE", "INVALID EVIDENCE")
SUBJECT_ROLES = ("test", "quick-campaign", "full-campaign")
_ROW = re.compile(r"^\| (?P<ob>.+?) \| `(?P<label>[A-Z]+\[.*\])` \|$")
_HASHLINE = re.compile(r"^(?P<h>[0-9a-f]{64})  (?P<name>[A-Za-z0-9._-]+)$")
_LABEL = re.compile(r"^(?P<verdict>[A-Z]+)\[(?P<body>.*)\]$")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def claims(report_text: str) -> dict:
    """Rows of the report's 'Claims' table, obligation -> verdict label, in
    report order. A repeated obligation gets a suffixed key (``ob #2``), so it
    never overwrites the first and always surfaces as an extra claim."""
    out, seen, inside = {}, Counter(), False
    for line in report_text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Claims"
            continue
        m = _ROW.match(line) if inside else None
        if m:
            seen[m["ob"]] += 1
            n = seen[m["ob"]]
            out[m["ob"] if n == 1 else f"{m['ob']} #{n}"] = m["label"]
    return out


def targets_digest(pairs) -> str:
    """The preregistered commitment to the expected values (manifest field
    ``expected_targets_sha256``): SHA-256 of the canonical JSON list of
    [obligation, label] pairs in report order."""
    return sha256_bytes(canonical([[ob, label] for ob, label in pairs]))


def label_fields(label: str) -> dict:
    """``VERDICT[method; scope; result]`` -> its fields, for a field-level diff."""
    m = _LABEL.match(label or "")
    if not m:
        return {"label": label}
    parts = m["body"].split("; ")
    names = ["method", "scope", "result"] if len(parts) == 3 else [f"part{i}" for i in range(len(parts))]
    return {"verdict": m["verdict"], **dict(zip(names, parts))}


def field_diff(want: str, got: str) -> dict:
    a, b = label_fields(want), label_fields(got)
    return {k: {"baseline": a.get(k), "reproduction": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


def git_targets(subject: dict) -> str:
    ref = subject["baseline_report"]
    return subprocess.run(["git", "show", f"{ref['commit']}:{ref['path']}"], cwd=HERE.parent,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _integrity(run_dir: Path, published):
    """Sealed files are unchanged and the commitment covers them."""
    bad = []
    raw = (run_dir / HASHES).read_bytes()
    commitment = json.loads((run_dir / COMMITMENT).read_text(encoding="utf-8"))
    digest = sha256_bytes(raw)
    if commitment.get("commitment") != digest:
        bad.append(f"{COMMITMENT} does not commit to {HASHES}")
    if published is not None and published != digest:
        bad.append(f"the published commitment does not match {HASHES}")
    sealed = {}
    for line in raw.decode().splitlines():
        m = _HASHLINE.match(line)
        if not m or m["name"] in (HASHES, COMMITMENT, ANCHOR, RESULT) or m["name"] in sealed:
            bad.append(f"malformed or duplicate line in {HASHES}: {line!r}")
            continue
        sealed[m["name"]] = m["h"]
        f = run_dir / m["name"]
        if not f.is_file() or sha256_bytes(f.read_bytes()) != m["h"]:
            bad.append(f"{m['name']} missing or altered after sealing")
    for name in ("environment.json", "commands.json"):
        if name not in sealed:
            bad.append(f"{name} is not sealed")
    return bad, sealed, commitment


def reconcile(run_dir: Path, manifest_bytes: bytes | None = None, prereg: dict | None = None,
              load_targets=None, published: str | None = None) -> dict:
    manifest_bytes = MANIFEST.read_bytes() if manifest_bytes is None else manifest_bytes
    prereg = json.loads(PREREG.read_text(encoding="utf-8")) if prereg is None else prereg
    manifest = json.loads(manifest_bytes)
    subject = manifest["subject"]
    load_targets = (lambda: git_targets(subject)) if load_targets is None else load_targets
    me = sha256_bytes(Path(__file__).resolve().read_bytes())
    res = {"subject": subject["source_commit"], "targets_revealed": False,
           "reconciled_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "reconciler_sha256": me, "reconciler_preregistered": me == prereg["files"].get("reconcile.py")}

    def done(outcome, **kw):
        return {**res, "outcome": outcome, **kw}

    # 1. Integrity and identity. Any failure: INVALID EVIDENCE, targets never loaded.
    try:
        bad, sealed, commitment = _integrity(run_dir, published)
        if bad:
            return done("INVALID EVIDENCE", reasons=bad)
        env = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
        cmds = json.loads((run_dir / "commands.json").read_text(encoding="utf-8"))
        res.update(label=env.get("label"), evidence_id=env.get("evidence_id"),
                   operator_declaration=env.get("operator_declaration"))
        if env.get("manifest_sha256") != sha256_bytes(manifest_bytes):
            bad.append("the evidence was made against a different claim-subject manifest")
        if (env.get("harness") or {}).get("reproduce.py") != prereg["files"].get("reproduce.py"):
            bad.append("the evidence was not produced by the preregistered harness")
        if commitment.get("evidence_id") != env.get("evidence_id"):
            bad.append("commitment and environment name different evidence ids")
        co = env.get("checkout") or {}
        report = next((c["artifact"] for c in cmds if c.get("role") == "full-campaign" and c.get("artifact")), None)
        described = {"label": env.get("label"), "operator_declaration": env.get("operator_declaration"),
                     "subject_commit": co.get("head"), "subject_tree": co.get("tree"),
                     "source_sha256_assurance_lab": env.get("source_sha256_assurance_lab"),
                     "report_sha256": sealed.get(report)}
        wrong = sorted(k for k, v in described.items() if commitment.get(k) != v)
        if wrong:
            bad.append(f"the pre-reveal commitment misdescribes the sealed evidence: {', '.join(wrong)}")
        frozen = manifest["commands"]
        expect = frozen[:len(cmds)] if env.get("harness_error") else frozen
        if len(cmds) != len(expect) or any(
                c.get("role") != f["role"] or c.get("argv") != f["argv"] or c.get("artifact") not in (None, f.get("artifact"))
                for c, f in zip(cmds, expect)):
            bad.append("commands.json does not record exactly the frozen commands, in order")
        for c in cmds:
            for key in ("stdout", "stderr", "artifact"):
                if c.get(key) and c[key] not in sealed:
                    bad.append(f"{c[key]} (named by commands.json) is not sealed")
        if bad:
            return done("INVALID EVIDENCE", reasons=bad)
        if env.get("harness_error"):
            return done("INCONCLUSIVE", reasons=[f"harness error before the commands completed: {env['harness_error']}"])
        for field, want, what in (("head", subject["source_commit"], "commit"),
                                  ("tree", subject["source_tree"], "tree"),
                                  ("tree_assurance_lab", subject["source_tree_assurance_lab"], "assurance_lab/ tree")):
            if co.get(field) != want:
                bad.append(f"checked-out {what} {co.get(field)} is not the subject's {want}")
        if co.get("status_porcelain") != "":
            bad.append("the working tree was not clean (or its status was not recorded)")
        if env.get("source_sha256_assurance_lab") != subject["source_sha256_assurance_lab"]:
            bad.append("the harness-recomputed source digest is not the subject's")
        if bad:
            return done("INVALID EVIDENCE", reasons=bad)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return done("INVALID EVIDENCE", reasons=[f"evidence unreadable or malformed: {type(e).__name__}: {e}"])

    # 2. Decidability. Genuine evidence that cannot decide the claims: INCONCLUSIVE.
    by_role = {c["role"]: c for c in cmds if "role" in c}
    failed = [c["role"] for c in cmds if c.get("exit_code") != 0]
    if "setup" in failed:
        return done("INCONCLUSIVE", reasons=["environment setup failed"], failed_commands=failed)
    full = by_role.get("full-campaign", {}).get("artifact")
    text = (run_dir / full).read_text(encoding="utf-8") if full else ""
    unbound = [] if text else ["the full campaign produced no report"]
    if text:
        if "Provenance verdict: **BOUND**" not in text:
            unbound.append("the report's provenance is not BOUND")
        if f"Commit: `{subject['source_commit']}`" not in text:
            unbound.append("the report is not bound to the subject commit")
        if f"at start: `{subject['source_sha256_assurance_lab']}`" not in text:
            unbound.append("the report's executed-source digest is not the subject's")
    if unbound:
        return done("INCONCLUSIVE", reasons=unbound, failed_commands=failed)

    # 3. Reveal: only now are the expected values read, and they must be the
    # preregistered ones.
    res["targets_revealed"] = True
    want = claims(load_targets())
    inventory = [c["obligation"] for c in manifest["claims"]]
    if targets_digest(want.items()) != subject["expected_targets_sha256"] or list(want) != inventory:
        return done("INCONCLUSIVE", reasons=["the revealed targets are not the preregistered ones (custodian error)"])

    # 4. Comparison.
    got = claims(text)
    rows = []
    for c in manifest["claims"]:
        ob = c["obligation"]
        result = "MATCH" if got.get(ob) == want[ob] else "MISSING" if ob not in got else "MISMATCH"
        row = {"id": c["id"], "obligation": ob, "result": result, "baseline": want[ob], "reproduction": got.get(ob)}
        if result == "MISMATCH":
            row["fields"] = field_diff(want[ob], got[ob])
        rows.append(row)
    missing = [r["id"] for r in rows if r["result"] == "MISSING"]
    mismatched = [r["id"] for r in rows if r["result"] == "MISMATCH"]
    extra = [ob for ob in got if ob not in want]
    subject_failed = [r for r in failed if r in SUBJECT_ROLES]
    outcome = ("INCONCLUSIVE" if missing else
               "MISMATCH" if mismatched or extra or subject_failed else "MATCH")
    return done(outcome, denominator=len(rows), matched=sum(r["result"] == "MATCH" for r in rows),
                mismatched=mismatched, missing=missing, extra_claims=extra,
                failed_commands=failed, claims=rows)


def g1_status(res: dict, anchor) -> str:
    """What this reconciliation can support (acceptance-criteria.md, G1)."""
    if res["outcome"] != "MATCH":
        return f"not eligible: outcome {res['outcome']}"
    if not str(res.get("label", "")).startswith("R1"):
        return f"not eligible: {res.get('label')} is not an independent (R1) run"
    if not anchor:
        return "not eligible: no timestamp anchor recorded for the pre-reveal commitment"
    return ("eligible pending review: a second party must verify that the anchor precedes this "
            "reconciliation and accept the operator declaration; reconcile.py cannot check either")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--published", help="the commitment as published before reveal")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir).resolve()
    res = reconcile(run_dir, published=a.published)
    anchor_file = run_dir / ANCHOR
    anchor = json.loads(anchor_file.read_text(encoding="utf-8")) if anchor_file.is_file() else None
    res["anchor"] = {"recorded": anchor, "verified_by_reconcile": False}
    res["g1"] = g1_status(res, anchor)
    (run_dir / RESULT).write_text(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {k: res[k] for k in ("label", "outcome", "g1") if k in res}
    if "matched" in res:
        summary["matched"] = f"{res['matched']}/{res['denominator']}"
    print(json.dumps(summary, ensure_ascii=False))
    return OUTCOMES.index(res["outcome"])


if __name__ == "__main__":
    raise SystemExit(main())
