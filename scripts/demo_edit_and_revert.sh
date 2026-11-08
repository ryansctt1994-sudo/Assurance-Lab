#!/usr/bin/env bash
# Demonstrate the edit-and-revert (A→B→A) provenance gap, and its closure.
#
#   scripts/demo_edit_and_revert.sh in-place   # gap: wrong numbers, clean-looking provenance
#   scripts/demo_edit_and_revert.sh snapshot   # closed: working-tree edits cannot reach the run
#
# Runs the quick campaign with spawn workers (which import assurance_lab from
# disk when they start). After the start receipt is taken, it swaps in a
# sabotaged induction checker that silently skips Diagnose transitions, waits
# for the pool workers to start, then reverts the file before the run ends,
# so the start and end hashes match.
#
# It temporarily modifies assurance_lab/checker/induction.py and always
# restores it (trap). It refuses to start unless that file is unmodified.
set -euo pipefail

mode="${1:?usage: $0 in-place|snapshot}"
cd "$(git rev-parse --show-toplevel)"
target=assurance_lab/checker/induction.py
out="${OUT_DIR:-$(mktemp -d)}/demo-${mode}.md"

if [ -n "$(git status --porcelain -- assurance_lab)" ]; then
  echo "assurance_lab/ must be clean" >&2; exit 2
fi
restore() { git checkout -q -- "$target"; }
trap restore EXIT

extra=()
[ "$mode" = "in-place" ] && extra=(--in-place)
python3 -m assurance_lab --out "$out" --start-method spawn --workers 4 "${extra[@]}" \
  >"${out%.md}.log" 2>&1 &
pid=$!

# Let the receipt be taken (the quick profile's BFS phase runs ~7 s before
# the pool exists), then inject B.
sleep 1
python3 - "$target" <<'EOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
needle = "        for lbl, t in model.next(s):\n            transitions += 1\n"
assert needle in s, "sabotage anchor not found"
p.write_text(s.replace(needle, "        for lbl, t in model.next(s):\n"
                       "            if lbl[0] == \"Diagnose\":\n"
                       "                continue  # SABOTAGE (demo)\n"
                       "            transitions += 1\n"))
EOF
echo "[$(date +%T)] injected B"

# Wait for spawned pool workers, give them time to import, then revert to A.
until pgrep -f "multiprocessing.spawn import spawn_main" >/dev/null; do
  kill -0 "$pid" 2>/dev/null || { echo "campaign exited before the pool started" >&2; exit 1; }
  sleep 0.2
done
sleep 2
restore
echo "[$(date +%T)] reverted to A (tree clean again: $( [ -z "$(git status --porcelain -- assurance_lab)" ] && echo yes || echo no ))"

set +e; wait "$pid"; rc=$?; set -e
echo "campaign exit code: $rc"
grep -E "Execution:|working tree at start|re-hashed at end|outside executed tree|Provenance verdict|^\| Inv ∧ Next|gate" "$out"
