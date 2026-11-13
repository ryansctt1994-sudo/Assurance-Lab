#!/usr/bin/env bash
# AL-SNAPSHOT-001 receipt: the full suite at the repair commit, in a fresh
# clone (the provenance tests need a git repository; a `git archive` export
# has none). The clone must be clean and at the commit's tree before and
# after the run, with no untracked or ignored files left behind.
#
#   run_full_in_clone.sh REPO WORKDIR FIX_SHA
set -u
REPO=$1 W=$2 FIX=$3
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$W"
log() { printf '%s\n' "$*" >>"$W/run.log"; }
state() {  # one line: HEAD, HEAD's tree, and porcelain status including ignored files
  printf 'head=%s tree=%s status=[%s]' "$(git -C "$1" rev-parse HEAD)" "$(git -C "$1" rev-parse 'HEAD^{tree}')" \
    "$(git -C "$1" status --porcelain --ignored --untracked-files=all | tr '\n' ';')"
}
: >"$W/run.log"
log "python: $(python3 --version 2>&1); pytest: $(python3 -m pytest --version 2>&1 | head -1); git: $(git --version)"
rm -rf "$W/clone"
git clone --quiet --no-hardlinks --no-checkout "$REPO" "$W/clone" &&
  git -C "$W/clone" checkout --quiet --detach "$FIX" || { echo 2 >"$W/full-suite-clone.exit"; exit 2; }
log "expected tree=$(git -C "$REPO" rev-parse "$FIX^{tree}")"
log "[full-suite-clone] start $(date -u +%FT%TZ) $(state "$W/clone")"
( cd "$W/clone" && python3 -m pytest -p no:cacheprovider -q -rf ) >"$W/full-suite-clone.log" 2>&1
rc=$?
log "[full-suite-clone] end $(date -u +%FT%TZ) exit=$rc $(state "$W/clone")"
echo "$rc" >"$W/full-suite-clone.exit"
