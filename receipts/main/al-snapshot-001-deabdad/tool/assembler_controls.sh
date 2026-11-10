#!/usr/bin/env bash
# Negative controls for assemble_receipt.py: damage one input in a scratch
# copy of the work directory, run the assembler, and require exit 1 with the
# named gate FAIL. Each control starts from an undamaged copy.
#
#   assembler_controls.sh WORK_DIR REPO OUT_FILE
set -u
WORK=$1 REPO=$2 OUT=$3
TOOL="$WORK/tool/assemble_receipt.py"
T=$(mktemp -d)
: >"$OUT"
control() {  # name gate python-snippet-that-damages-$C
  local name=$1 gate=$2 damage=$3
  rm -rf "$T/w" "$T/out" && cp -a "$WORK" "$T/w"
  mkdir -p "$T/w/assembler-controls" && : >"$T/w/assembler-controls/assembler-negative-controls.txt"
  C="$T/w" python3 -I -c "import os
C = os.environ['C']
$damage" || { echo "CONTROL-ERROR $name" >>"$OUT"; return; }
  out=$(python3 -I "$TOOL" "$T/w" "$REPO" "$T/out" 2>&1); rc=$?
  if [ "$rc" = 1 ] && grep -qE "^  $gate +FAIL" <<<"$out"; then verdict=CAUGHT; else verdict=MISSED; fi
  printf '%s  %-44s expected %s FAIL; assembler exit %s; failing gates: %s\n' "$verdict" "$name" "$gate" "$rc" \
    "$(grep -oE '^  G[0-9]+ +FAIL' <<<"$out" | awk '{print $1}' | tr '\n' ' ')" >>"$OUT"
}
rw='def rw(p, a, b):
    p = C + "/" + p; s = open(p).read(); assert a in s, (p, a); open(p, "w").write(s.replace(a, b, 1))
'
control "campaign obligation turned FAIL"      G1  "$rw
rw('campaign-deabdad.md', '| \`PASS[bfs-exhaustive-reachability', '| \`FAIL[bfs-exhaustive-reachability')"
control "campaign claim text edited"           G2  "$rw
rw('campaign-deabdad.md', '1,314,526 reachable states, 10,580,311', '1,314,527 reachable states, 10,580,311')"
control "first failing regression passes"      G3  "$rw
rw('regression-at-dbdbed7/failing-regression-at-dbdbed7.exit', '1', '0')"
control "before-run lost a KeyError site"      G4  "$rw
rw('tests-work/before-regression.log', 'replica.py:245: KeyError', 'replica.py:245: Error')"
control "after-run has a failure"              G5  "$rw
rw('tests-work/after-regression.log', '8 passed', '7 passed, 1 failed')"
control "inventory lost a published test"      G6  "$rw
import re; p=C+'/tests-work/inventory-fix.log'; s=open(p).read().splitlines(True)
i=next(k for k,l in enumerate(s) if '::' in l and 'test_al_snapshot_001' not in l); del s[i]; open(p,'w').writelines(s)"
control "full suite exit 1"                    G7  "$rw
rw('clone-work/full-suite-clone.exit', '0', '1')"
control "clone dirty after the run"            G7  "$rw
p=C+'/clone-work/run.log'; s=open(p).read(); i=s.rfind('status=[]'); open(p,'w').write(s[:i]+'status=[?? x;]'+s[i+9:])"
control "tested tree changed during a run"     G8  "$rw
p=C+'/tests-work/trees.txt'; s=open(p).read().splitlines(); f=s[1].split(); f[3]='0'*40; s[1]=' '.join(f); open(p,'w').write(chr(10).join(s)+chr(10))"
control "counterexample accepted by the repair" G9 "$rw
import json; p=C+'/scope/run-I.json'; d=json.load(open(p)); d['counterexamples'][0]['by_revision']['repair']['restore']='accepted'; open(p,'w').write(json.dumps(d, indent=1, sort_keys=True)+chr(10))"
control "head() raises on the repair"          G10 "$rw
import json; p=C+'/scope/run-I.json'; d=json.load(open(p)); d['head_raise_sites_bypass']['repair']={'KeyError at line 1: x': 1}; open(p,'w').write(json.dumps(d, indent=1, sort_keys=True)+chr(10))"
control "one genuine snapshot refused"         G11 "$rw
import json; p=C+'/scope/run-I.json'; d=json.load(open(p)); d['genuine']['by_revision']['repair']={'accepted; heads equal the live replica\'s': 163, 'DEFECT: genuine snapshot refused': 1}; open(p,'w').write(json.dumps(d, indent=1, sort_keys=True)+chr(10))"
control "scope runs not identical"             G12 "$rw
p=C+'/scope/run-seed0.json'; open(p,'a').write(' ')"
rm -rf "$T"
echo "$(grep -c '^CAUGHT' "$OUT")/$(wc -l <"$OUT") controls caught" >>"$OUT"
