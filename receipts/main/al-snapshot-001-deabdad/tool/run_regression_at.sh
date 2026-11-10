#!/usr/bin/env bash
# Run tests/test_al_snapshot_001.py as committed at COMMIT, against the code
# at COMMIT, from a fresh `git archive` export; the export's tree is recorded
# before and after and must equal COMMIT's tree.
#
#   run_regression_at.sh REPO WORKDIR NAME COMMIT
set -u
REPO=$1 W=$2 NAME=$3 C=$4
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$W"
tree_of() {
  local idx; idx=$(mktemp "$W/idx.XXXXXX"); rm -f "$idx"
  GIT_DIR="$REPO/.git" GIT_INDEX_FILE="$idx" GIT_WORK_TREE="$1" git add -A >/dev/null 2>&1 &&
    GIT_DIR="$REPO/.git" GIT_INDEX_FILE="$idx" GIT_WORK_TREE="$1" git write-tree
  rm -f "$idx"
}
D="$W/$NAME-export"
rm -rf "$D" && mkdir -p "$D" && git -C "$REPO" archive "$C" | tar -x -C "$D"
want=$(git -C "$REPO" rev-parse "$C^{tree}") before=$(tree_of "$D")
start=$(date -u +%FT%TZ)
( cd "$D" && python3 -m pytest -p no:cacheprovider -q -rA tests/test_al_snapshot_001.py ) >"$W/$NAME.log" 2>&1
rc=$?
after=$(tree_of "$D")
echo "$rc" >"$W/$NAME.exit"
printf '[%s] commit=%s start=%s end=%s exit=%s expected-tree=%s tree-before=%s tree-after=%s\n' \
  "$NAME" "$(git -C "$REPO" rev-parse "$C")" "$start" "$(date -u +%FT%TZ)" "$rc" "$want" "$before" "$after" >>"$W/run.log"
