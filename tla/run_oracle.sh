#!/usr/bin/env bash
# Run the TLC oracle and compare state-set digests with the Python campaign.
#
# STATUS: UNTESTED. Written without running TLC (execution awaits an approved
# path). It NEVER downloads anything: you supply a tla2tools.jar obtained
# through an approved channel, plus the checksum you expect it to have.
#
#   TLC_JAR=/path/to/tla2tools.jar TLC_JAR_SHA256=<sha256> tla/run_oracle.sh small|full|mutants
#
# Logs and dumps go to $OUT_DIR (default: a fresh temp dir). Record the TLC
# version banner from the logs alongside the results (docs/ORACLE.md).
set -euo pipefail
mode="${1:?usage: $0 small|full|mutants}"
: "${TLC_JAR:?set TLC_JAR to an approved tla2tools.jar}"
: "${TLC_JAR_SHA256:?set TLC_JAR_SHA256 to its expected checksum}"
actual="$(sha256sum "$TLC_JAR" | cut -d' ' -f1)"
if [ "$actual" != "$TLC_JAR_SHA256" ]; then
  echo "refusing: $TLC_JAR has sha256 $actual, expected $TLC_JAR_SHA256" >&2; exit 2
fi
repo="$(cd "$(dirname "$0")/.." && pwd)"
out="${OUT_DIR:-$(mktemp -d)}"
echo "tla2tools.jar sha256: $actual"; echo "output: $out"

tlc() {  # cfg -> runs TLC with a state dump; returns TLC's exit code
  (cd "$repo/tla" && java -cp "$TLC_JAR" tlc2.TLC -workers auto -config "$1.cfg" \
     -dump "$out/$1" AuthorityCore.tla) >"$out/$1.log" 2>&1
}
target() {  # set name in docs/RESULTS.md digest table -> digest label
  awk -F'|' -v n=" $1 " '$2 == n { gsub(/[ `,]/, "", $3); gsub(/[ `]/, "", $4);
    print "ac-state-v1:" $3 ":" $4 }' "$repo/docs/RESULTS.md"
}
compare() {  # cfg records expected-label
  python3 -m assurance_lab.tla "$out/$1.dump" --records "$2" --principals 2 --expect "$3"
}

case "$mode" in
  small)
    for cfg in small_reach small_ind small_ind_unpruned; do tlc "$cfg"; done
    compare small_reach 2 "ac-state-v1:670:90bd9c18894fdec12916ad1104da9df12c797dbb1fb75752fc6c67bcb3954774"
    compare small_ind 2 "ac-state-v1:812:44e4ac49ad9f94ba7045196517bb4c104cf9fbfa38245566e4ad230d0e0fb54d"
    compare small_ind_unpruned 2 "ac-state-v1:812:44e4ac49ad9f94ba7045196517bb4c104cf9fbfa38245566e4ad230d0e0fb54d"
    ;;
  full)
    for cfg in full_reach full_ind; do tlc "$cfg"; done
    compare full_reach 3 "$(target reachable)"
    compare full_ind 3 "$(target inv_states)"
    ;;
  mutants)  # each must make TLC report a violation
    for m in beneficiary_self_approval admit_into_occupied_lineage stale_successor resurrect_on_retract; do
      if tlc "mutant_$m"; then echo "FAIL: TLC found no violation for mutant $m"; exit 1; fi
      grep -q "violated" "$out/mutant_$m.log" || { echo "FAIL: $m: TLC failed without a violation"; exit 1; }
      echo "detected: $m"
    done
    ;;
  *) echo "unknown mode $mode" >&2; exit 2 ;;
esac
