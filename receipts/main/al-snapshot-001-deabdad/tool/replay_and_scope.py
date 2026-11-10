"""AL-SNAPSHOT-001 receipt: replay the retained counterexamples, and measure
the repair's scope over the development lineage's snapshot-damage classes.

The code under test is ``assurance_lab/distributed/replica.py`` read from git
at each revision (never the imported package). The damage generators and the
snapshot codec come from an export of the development tree passed as
``--dev-src``; they only produce inputs.

For every damaged snapshot and revision, two paths are recorded separately:

* production: restore through ``thaw`` as production code does (validating
  where the revision has a boundary), then ask ``head()`` for every lineage
  and compare with the genuine snapshot's heads; if accepted, continue by
  re-delivering the prefix (duplicates) and then the instance's remaining
  events, and compare final heads with the genuine continuation;
* bypass (diagnostic only, not evidence that a boundary works): restore
  without validation and ask ``head()``.

Output is sorted JSON with no times, hosts or paths, so repeated runs are
byte-identical.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

LINEAGES = ("L", "M")


def conv(x, mod):
    """Rebuild a value with ``mod``'s event classes (any shape, so malformed
    snapshots convert too)."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        cls = getattr(mod, type(x).__name__)
        return cls(**{f.name: conv(getattr(x, f.name), mod) for f in dataclasses.fields(x)})
    if isinstance(x, frozenset):
        return frozenset(conv(v, mod) for v in x)
    if isinstance(x, tuple):
        return tuple(conv(v, mod) for v in x)
    if isinstance(x, list):
        return [conv(v, mod) for v in x]
    return x


def exc(e: BaseException, modfile: str) -> dict:
    tb = traceback.extract_tb(e.__traceback__)
    own = [f for f in tb if f.filename == modfile]
    f = own[-1] if own else tb[-1]
    return {"exception": type(e).__name__, "message": str(e), "line": f.lineno, "function": f.name,
            "source_line": (f.line or "").strip(), "raised_in_code_under_test": bool(own)}


class Rev:
    def __init__(self, label, sha, repo, tmp):
        from assurance_lab.distributed import snapshot_json as sj
        self.label, self.sha = label, sha
        self.mod = sj.load_replica_at(sha, repo, tmp)
        self.file = self.mod.__file__
        src = subprocess.run(["git", "show", f"{sha}:assurance_lab/distributed/replica.py"], cwd=repo,
                             capture_output=True, check=True).stdout
        self.sha256 = hashlib.sha256(src).hexdigest()
        self.has_boundary = "validate" in inspect.signature(self.mod.Replica.thaw).parameters

    def policy(self, pol):
        return self.mod.ResolutionPolicy(frozenset(pol.authorities), pol.min_quorum)

    def kind(self, v) -> str:
        if getattr(self.mod, "UNKNOWN", object()) == v:
            return "UNKNOWN"
        if v == self.mod.CONFLICT:
            return "CONFLICT"
        return "NONE" if v is None else "HEAD"

    def heads(self, rep) -> dict:
        out = {}
        for lin in LINEAGES:
            try:
                v = rep.head(lin)
                out[lin] = {"outcome": self.kind(v), "value": v}
            except Exception as e:  # noqa: BLE001 - the measurement is whether it raises
                out[lin] = {"outcome": "raises", **exc(e, self.file)}
        return out

    def restore(self, pol, frozen, validate: bool):
        """('accepted', replica) | ('refused', problems) | ('raises', exc)"""
        try:
            if self.has_boundary:
                rep = self.mod.Replica.thaw(pol, frozen, validate=validate)
            else:
                rep = self.mod.Replica.thaw(pol, frozen)
            return "accepted", rep
        except getattr(self.mod, "CorruptSnapshot", ()) as e:
            return "refused", {"status": e.status, "problems": list(e.problems)}
        except Exception as e:  # noqa: BLE001
            return "raises", exc(e, self.file)


def continuation(rv: Rev, pol, frozen, events) -> dict:
    ok, rep = rv.restore(pol, frozen, validate=False)
    if ok != "accepted":
        return {"outcome": "restore " + ok}
    try:
        for e in events:
            rep.deliver(e)
    except Exception as e:  # noqa: BLE001
        return {"outcome": "deliver raises", **exc(e, rv.file)}
    return {"outcome": "ok", "heads": rv.heads(rep)}


def production(rv: Rev, pol, genuine, damaged, cont_events) -> dict:
    gstatus, grep = rv.restore(pol, genuine, validate=True)
    if gstatus != "accepted":
        return {"category": f"DEFECT: genuine snapshot {gstatus}", "detail": grep}
    status, val = rv.restore(pol, damaged, validate=True)
    if status == "refused":
        return {"category": "refused (INVALID_SNAPSHOT)", "problems": val["problems"]}
    if status == "raises":
        return {"category": f"restore raises {val['exception']}", **val}
    heads, gheads = rv.heads(val), rv.heads(grep)
    if any(h["outcome"] == "raises" for h in heads.values()):
        bad = next(h for h in heads.values() if h["outcome"] == "raises")
        return {"category": f"accepted; head raises {bad['exception']}", "heads": heads}
    if heads != gheads:
        return {"category": "accepted; head differs from the genuine snapshot's", "heads": heads,
                "genuine_heads": gheads}
    c, g = continuation(rv, pol, damaged, cont_events), continuation(rv, pol, genuine, cont_events)
    if c != g:
        return {"category": "accepted; head unchanged now, differs after further deliveries",
                "continuation": c, "genuine_continuation": g}
    return {"category": "accepted; head unchanged now and after further deliveries"}


def bypass(rv: Rev, pol, damaged) -> dict:
    status, val = rv.restore(pol, damaged, validate=False)
    if status != "accepted":
        return {"category": f"unvalidated restore {status}", **({} if status == "refused" else val)}
    heads = rv.heads(val)
    return {"category": "head: " + ", ".join(f"{lin}={heads[lin]['outcome']}" for lin in LINEAGES),
            "heads": heads}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-src", required=True, help="export of the development tree (inputs only)")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--fixture", required=True, help="tests/fixtures/snapshot_counterexamples.json")
    ap.add_argument("--rev", action="append", required=True, metavar="LABEL=SHA")
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", help="also write a generated markdown summary here")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.dev_src).resolve()))
    from assurance_lab.distributed import metamorphic as mm
    from assurance_lab.distributed import snapshot_json as sj
    gen_files = {m.__name__: m.__file__ for m in (mm, sj)}
    assert all(Path(f).resolve().is_relative_to(Path(a.dev_src).resolve()) for f in gen_files.values()), gen_files

    tmp = tempfile.mkdtemp(prefix="al-snapshot-001-")
    revs = [Rev(lbl, sha, a.repo, tmp) for lbl, sha in (r.split("=", 1) for r in a.rev)]
    doc = {
        "schema": "al-snapshot-001/replay-and-scope/v1",
        "code_under_test": {rv.label: {"commit": rv.sha, "path": "assurance_lab/distributed/replica.py",
                                       "sha256": rv.sha256, "restore_boundary": rv.has_boundary} for rv in revs},
        "generators": {n: hashlib.sha256(Path(f).read_bytes()).hexdigest() for n, f in sorted(gen_files.items())},
        "lineages_queried": list(LINEAGES),
    }

    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
    doc["fixture_sha256"] = hashlib.sha256(Path(a.fixture).read_bytes()).hexdigest()
    ces = []
    for ce in fixture["counterexamples"]:
        pol = mm.build(ce["instance"]).policy
        row = {"id": ce["id"], "site": ce["site"], "by_revision": {}}
        for rv in revs:
            snap = sj.decode(ce["snapshot"], rv.mod)
            p = rv.policy(pol)
            status, val = rv.restore(p, snap, validate=True)
            row["by_revision"][rv.label] = {
                "restore": status if status != "accepted" else (
                    "accepted" if rv.has_boundary else "accepted (this revision has no restore boundary)"),
                **({"refusal": val} if status == "refused" else {"restore_exception": val} if status == "raises" else {}),
                "bypass_head": bypass(rv, p, snap)["heads"] if rv.restore(p, snap, validate=False)[0] == "accepted"
                else None,
            }
        ces.append(row)
    doc["counterexamples"] = ces

    # Genuine snapshots: every serialization prefix of every instance. Each
    # must restore through the production path, with heads equal to those of
    # a replica of the same revision that received the same events live.
    # The snapshot under test is the revision's own (its live replica frozen);
    # whether it equals the generator's snapshot is recorded separately,
    # because the damaged inputs below are derived from the generator's.
    genuine = {"snapshots": 0, "by_revision": {rv.label: Counter() for rv in revs},
               "equals_generator_snapshot": {rv.label: 0 for rv in revs}}
    for inst in mm.instances():
        for pre in mm.serialization_prefixes(inst):
            frozen = mm.run(inst, list(pre), {}).freeze()
            genuine["snapshots"] += 1
            for rv in revs:
                p = rv.policy(inst.policy)
                live = rv.mod.Replica(p)
                for e in conv(list(pre), rv.mod):
                    live.deliver(e)
                own = live.freeze()
                genuine["equals_generator_snapshot"][rv.label] += own == conv(frozen, rv.mod)
                status, val = rv.restore(p, own, validate=True)
                if status != "accepted":
                    cat = f"DEFECT: genuine snapshot {status}"
                else:
                    cat = ("accepted; heads equal the live replica's" if rv.heads(val) == rv.heads(live)
                           else "DEFECT: accepted; heads differ from the live replica's")
                genuine["by_revision"][rv.label][cat] += 1
    genuine["by_revision"] = {k: dict(sorted(v.items())) for k, v in genuine["by_revision"].items()}
    doc["genuine"] = genuine

    raise_sites = {rv.label: Counter() for rv in revs}
    scope, examples = {}, {}
    for name, (cls, fn) in mm.CORRUPTIONS.items():
        entry = {"class": cls, "damaged_snapshots": 0, "production": {rv.label: Counter() for rv in revs},
                 "bypass": {rv.label: Counter() for rv in revs}}
        for inst, pre, genuine, damaged in mm.corruption_snapshots(fn):
            entry["damaged_snapshots"] += 1
            dup_then_rest = list(pre) + [e for e in inst.events if e not in set(pre)]
            for rv in revs:
                p = rv.policy(inst.policy)
                g, d = conv(genuine, rv.mod), conv(damaged, rv.mod)
                cont = conv(dup_then_rest, rv.mod)
                prod = production(rv, p, g, d, cont)
                byp = bypass(rv, p, d)
                entry["production"][rv.label][prod["category"]] += 1
                entry["bypass"][rv.label][byp["category"]] += 1
                for lin, h in (byp.get("heads") or {}).items():
                    if h["outcome"] == "raises":
                        raise_sites[rv.label][f"{h['exception']} at line {h['line']}: {h['source_line']}"] += 1
                key = f"{name} | {rv.label} | {prod['category']}"
                if key not in examples:
                    examples[key] = {"instance": inst.name, "prefix_length": len(pre), **prod}
        entry["production"] = {k: dict(sorted(v.items())) for k, v in entry["production"].items()}
        entry["bypass"] = {k: dict(sorted(v.items())) for k, v in entry["bypass"].items()}
        scope[name] = entry
    doc["scope"] = scope
    doc["head_raise_sites_bypass"] = {k: dict(sorted(v.items())) for k, v in raise_sites.items()}
    doc["first_example_per_outcome"] = dict(sorted(examples.items()))
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if a.summary:
        Path(a.summary).write_text(summary(doc, [rv.label for rv in revs]), encoding="utf-8")
    return 0


def summary(doc: dict, labels: list) -> str:
    """Markdown tables generated from the JSON (no hand transcription)."""
    out = ["# AL-SNAPSHOT-001: replay and scope (generated from scope.json)", ""]
    out += ["Code under test (`assurance_lab/distributed/replica.py`, read from git):", ""]
    out += ["| Label | Commit | replica.py SHA-256 | Restore boundary |", "|---|---|---|---|"]
    for lbl in labels:
        c = doc["code_under_test"][lbl]
        out.append(f"| {lbl} | `{c['commit']}` | `{c['sha256'][:16]}…` | {'yes' if c['restore_boundary'] else 'no'} |")
    out += ["", "## Retained counterexamples (production restore; then head() with validation bypassed)", ""]
    out += ["| CE | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for ce in doc["counterexamples"]:
        cells = []
        for lbl in labels:
            r = ce["by_revision"][lbl]
            h = (r["bypass_head"] or {}).get("L", {})
            hd = (f"raises {h['exception']}({h['message']}) at line {h['line']}" if h.get("outcome") == "raises"
                  else h.get("outcome", "n/a"))
            cells.append(f"restore {r['restore']}; head(L) {hd}")
        out.append(f"| {ce['id']} | " + " | ".join(cells) + " |")
    g = doc["genuine"]
    out += ["", f"## Genuine snapshots ({g['snapshots']}: every serialization prefix of every instance)", ""]
    for lbl in labels:
        out.append(f"- {lbl}: " + "; ".join(f"{k}: {v}" for k, v in g["by_revision"][lbl].items())
                   + f" (equal to the generator's snapshot: {g['equals_generator_snapshot'][lbl]}/{g['snapshots']})")
    out += ["", "## Damaged snapshots, production path, by damage class", ""]
    out += ["| Damage | Class | n | " + " | ".join(labels) + " |", "|---|---|---|" + "---|" * len(labels)]
    for name, e in doc["scope"].items():
        cells = ["<br>".join(f"{k}: {v}" for k, v in e["production"][lbl].items()) for lbl in labels]
        out.append(f"| {name} | {e['class']} | {e['damaged_snapshots']} | " + " | ".join(cells) + " |")
    tot = {lbl: Counter() for lbl in labels}
    for e in doc["scope"].values():
        for lbl in labels:
            tot[lbl].update(e["production"][lbl])
    n = sum(e["damaged_snapshots"] for e in doc["scope"].values())
    out += ["", f"Totals over {n} damaged snapshots:", ""]
    for lbl in labels:
        out.append(f"- {lbl}: " + "; ".join(f"{k}: {v}" for k, v in sorted(tot[lbl].items())))
    out += ["", "## head() raise sites with validation bypassed (diagnostic)", ""]
    for lbl in labels:
        sites = doc["head_raise_sites_bypass"][lbl]
        out.append(f"- {lbl}: " + ("; ".join(f"{k} ×{v}" for k, v in sites.items()) if sites else "none"))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
