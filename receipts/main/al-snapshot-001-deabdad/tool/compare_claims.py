"""Compare a campaign report's claims with a baseline report's, row by row.

  compare_claims.py BASELINE.md CANDIDATE.md

Prints one line per obligation and a verdict line. Exit 0 only when both
reports have the same obligations in the same order with identical verdict
text, and identical "Assumptions and residual risks" sections. Provenance
and run metadata are expected to differ and are not compared."""

import sys


def section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def claims(text: str) -> list:
    rows = []
    for line in section(text, "Claims").splitlines():
        if line.startswith("| ") and not line.startswith(("| Obligation", "|---")):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append((cells[0], cells[1]))
    return rows


def main(base_path: str, cand_path: str) -> int:
    base, cand = (open(p, encoding="utf-8").read() for p in (base_path, cand_path))
    a, b = claims(base), claims(cand)
    ok = len(a) == len(b)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        same = x == y
        ok &= same
        print(f"{'SAME' if same else 'DIFF'}  {(x or y)[0]}" + ("" if same else f"\n      baseline:  {x}\n      candidate: {y}"))
    risks_same = section(base, "Assumptions and residual risks") == section(cand, "Assumptions and residual risks")
    ok &= risks_same
    print(f"claims: baseline {len(a)}, candidate {len(b)}; residual-risk section identical: {risks_same}")
    print("VERDICT: IDENTICAL CLAIMS" if ok else "VERDICT: CLAIMS DIFFER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
