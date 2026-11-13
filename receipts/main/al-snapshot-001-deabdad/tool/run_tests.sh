#!/usr/bin/env bash
# AL-SNAPSHOT-001 receipt: before/after regression runs and a clean full-suite
# run, each from a fresh `git archive` export, with the tested tree recorded
# before and after every run (a temporary index over the export; the export
# must equal the commit's tree both times).
#
#   run_tests.sh REPO WORKDIR BASE_SHA FIX_SHA
set -u
REPO=$1 W=$2 BASE=$3 FIX=$4
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$W"
log() { printf '%s\n' "$*" >>"$W/run.log"; }

tree_of() {  # tree id of directory $1, as git would record it
  local idx; idx=$(mktemp "$W/idx.XXXXXX"); rm -f "$idx"
  GIT_DIR="$REPO/.git" GIT_INDEX_FILE="$idx" GIT_WORK_TREE="$1" git add -A >/dev/null 2>&1 &&
    GIT_DIR="$REPO/.git" GIT_INDEX_FILE="$idx" GIT_WORK_TREE="$1" git write-tree
  rm -f "$idx"
}

export_tree() {  # export_tree SHA DIR
  rm -rf "$2" && mkdir -p "$2" && git -C "$REPO" archive "$1" | tar -x -C "$2"
}

run_pytest() {  # run_pytest NAME DIR EXPECTED_TREE ARGS...
  local name=$1 dir=$2 want=$3; shift 3
  local before after rc
  before=$(tree_of "$dir")
  log "[$name] start $(date -u +%FT%TZ) tree-before=$before expected=$want"
  ( cd "$dir" && python3 -m pytest -p no:cacheprovider "$@" ) >"$W/$name.log" 2>&1
  rc=$?
  after=$(tree_of "$dir")
  echo "$rc" >"$W/$name.exit"
  log "[$name] end $(date -u +%FT%TZ) exit=$rc tree-after=$after"
  printf '%s %s %s %s\n' "$name" "$want" "$before" "$after" >>"$W/trees.txt"
}

: >"$W/trees.txt"; : >"$W/run.log"
log "python: $(python3 --version 2>&1); pytest: $(python3 -m pytest --version 2>&1 | head -1)"
log "base=$BASE fix=$FIX"

# 1. before: the published code with the repair's regression file
export_tree "$BASE" "$W/before"
git -C "$REPO" show "$FIX:tests/test_al_snapshot_001.py" >"$W/before/tests/test_al_snapshot_001.py"
# expected: the base tree plus exactly that one file (computed from git objects, not from the export)
idx=$(mktemp "$W/idx.XXXXXX"); rm -f "$idx"
want_before=$(GIT_DIR="$REPO/.git" GIT_INDEX_FILE="$idx" sh -c 'git read-tree "$1" &&
  git update-index --add --cacheinfo "100644,$(git rev-parse "$2:tests/test_al_snapshot_001.py"),tests/test_al_snapshot_001.py" &&
  git write-tree' _ "$BASE" "$FIX")
rm -f "$idx"
run_pytest before-regression "$W/before" "$want_before" -q -rA tests/test_al_snapshot_001.py

# 2. after: the repair commit, regression file only
export_tree "$FIX" "$W/after"
run_pytest after-regression "$W/after" "$(git -C "$REPO" rev-parse "$FIX^{tree}")" -q -rA tests/test_al_snapshot_001.py

# 3. test inventories (collect only) at the base and at the repair
export_tree "$BASE" "$W/base-inventory"
run_pytest inventory-base "$W/base-inventory" "$(git -C "$REPO" rev-parse "$BASE^{tree}")" --collect-only -q
run_pytest inventory-fix "$W/after" "$(git -C "$REPO" rev-parse "$FIX^{tree}")" --collect-only -q

# 4. the full suite at the repair, from a fresh export
export_tree "$FIX" "$W/full"
run_pytest full-suite "$W/full" "$(git -C "$REPO" rev-parse "$FIX^{tree}")" -q -rf

log "done $(date -u +%FT%TZ)"
echo done >"$W/ALL.exit"
