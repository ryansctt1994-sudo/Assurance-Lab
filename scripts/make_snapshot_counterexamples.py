"""Rebuild the retained snapshot counterexamples and record how the code at
historical revisions fails on them.

    python scripts/make_snapshot_counterexamples.py   # writes tests/fixtures/snapshot_counterexamples.json

Each counterexample is fully determined by (instance, delivery order, prefix
length, corruption); the snapshot stored in the fixture is checked against
that derivation by tests/test_snapshot_counterexamples.py.
"""

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assurance_lab.distributed import metamorphic as mm  # noqa: E402
from assurance_lab.distributed import replica  # noqa: E402
from assurance_lab.distributed import snapshot_json as sj  # noqa: E402

SPECS = mm.RETAINED_COUNTEREXAMPLES
#: b535b1a: development lineage before the repair. c175fbd: published main baseline.
REVISIONS = ("b535b1a", "c175fbd")


def derive(spec):
    return mm.counterexample_snapshot(spec)


def failure_at(mod, policy, snapshot_obj):
    pol = mod.ResolutionPolicy(policy.authorities, policy.min_quorum)
    try:
        mod.Replica.thaw(pol, sj.decode(snapshot_obj, mod)).head("L")
    except Exception as e:  # the historical failure is what we record
        fr = traceback.extract_tb(e.__traceback__)[-1]
        return {"outcome": "uncaught exception", "exception": type(e).__name__, "message": str(e),
                "line": fr.lineno, "source_line": fr.line}
    return {"outcome": "no exception"}


def main():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        mods = {rev: sj.load_replica_at(rev, ROOT, tmp) for rev in REVISIONS}
        for spec in SPECS:
            inst, prefix, damaged = derive(spec)
            snap = sj.encode(damaged)
            out.append({**spec, "lineage": "L", "prefix_events": sj.encode_events(prefix), "snapshot": snap,
                        "historical_failures": {rev: failure_at(m, inst.policy, snap) for rev, m in mods.items()}})
    doc = {"description": "Retained counterexamples: corrupted replica snapshots that made head() raise "
                          "KeyError before the repair. Kept as data so they can be replayed against any "
                          "revision. Regenerate with scripts/make_snapshot_counterexamples.py.",
           "revisions": {"b535b1a": "development lineage before the repair",
                         "c175fbd": "published main baseline (not repaired; not ported)"},
           "counterexamples": out}
    path = ROOT / "tests/fixtures/snapshot_counterexamples.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for ce in out:
        print(ce["id"], {rev: (f["outcome"], f.get("exception"), f.get("message"), f.get("line"))
                         for rev, f in ce["historical_failures"].items()})


if __name__ == "__main__":
    main()
