"""Find, shrink and keep one conformance counterexample per runtime mutant.

    python scripts/runtime_counterexamples.py [--seeds N] [--length L] [--out DIR]

For every gate mutant and reconciler mutant, run fuzzed traces (seeds 0..N-1)
until one fails conformance, shrink it to a 1-minimal trace, and write it to
``tests/fixtures/runtime_counterexamples/<side>-<mutant>.json``. The test
suite replays each: it must fail with the mutant, at the recorded step, and
pass without it. A mutant no trace detects is reported and written as
``undetected`` (never silently dropped).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assurance_lab.runtime import harness as H  # noqa: E402
from assurance_lab.runtime.gate import MUTANTS as GATE_MUTANTS  # noqa: E402
from assurance_lab.runtime.reconcile import MUTANTS as RECONCILER_MUTANTS  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=120)
    ap.add_argument("--length", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "tests/fixtures/runtime_counterexamples"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="al-runtime-ce-"))
    traces = [H.generate(s, a.length, tmp / f"gen-{s}") for s in range(a.seeds)]
    status = 0
    for side, names in (("gate", sorted(GATE_MUTANTS)), ("reconciler", sorted(RECONCILER_MUTANTS))):
        for m in names:
            kw = {"mutants": [m]} if side == "gate" else {"reconciler_mutants": [m]}
            found = None
            for seed, tr in enumerate(traces):
                r = H.run_trace(tmp / "run", tr, **kw)
                if not r.ok:
                    found = (seed, r.trace)
                    break
            path = out / f"{side}-{m}.json"
            if found is None:
                path.write_text(json.dumps({"side": side, "mutant": m, "status": "undetected",
                                            "seeds": a.seeds, "length": a.length}, indent=1) + "\n")
                print(f"{side:10s} {m:16s} UNDETECTED in {a.seeds} traces")
                status = 1
                continue
            seed, tr = found
            small = H.shrink(tmp / "shrink", tr, **kw)
            final = H.run_trace(tmp / "final", small, **kw)
            clean = H.run_trace(tmp / "clean", small)
            doc = {"side": side, "mutant": m, "status": "detected", "found_in_seed": seed, "length": a.length,
                   "trace": small, "failure": final.failure, "passes_without_the_mutant": clean.ok}
            path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=list) + "\n")
            print(f"{side:10s} {m:16s} seed {seed:3d}: {len(tr)} -> {len(small)} ops; "
                  f"fails at step {final.failure['step']} ({final.failure['kind']}); clean passes: {clean.ok}")
            status |= 0 if clean.ok and not final.ok else 1
    return status


if __name__ == "__main__":
    sys.exit(main())
