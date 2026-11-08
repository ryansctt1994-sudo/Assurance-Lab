# Provenance: binding results to the code that produced them

A verification report is only as good as the link between its numbers and
the code that computed them. This file states what that link guarantees,
how it is enforced, where it stops, and the evidence for each part.

## Threat model

In scope: **accidental or careless mismatch** between the code that ran and
the code a report names, e.g. an editor autosave, a mid-run fix, an
uncommitted change, an installed copy of the package shadowing the
checkout, or worker processes importing different bytes than the parent.

Out of scope: an adversary with write access to the machine while the run
is in progress (as root, it can modify anything, including the checker). The
mechanisms below *detect* some such tampering but don't prevent it.

## Mechanism (with `--out`)

1. **Pre-execution refusal (exit 2).** The launcher refuses if
   `assurance_lab/` has uncommitted changes, since the launcher itself runs
   from the working tree.
2. **Snapshot.** `git archive HEAD assurance_lab` is extracted into a fresh
   `mkdtemp` directory and made read-only.
3. **Origin of bytes.** The snapshot's digest is recomputed independently
   from the git object store (`git ls-tree` plus `git cat-file blob`), and
   must match. This catches `export-subst`/`export-ignore` rewrites and
   extraction faults.
4. **Isolated execution.** The campaign runs as
   `python -I -S -B -c <boot> <snapshot>`: no `PYTHON*` variables, no user
   or system site-packages (so no editable-install finder can redirect the
   import), the current directory off `sys.path`, and no bytecode written
   into the snapshot. multiprocessing passes these flags and `sys.path` on
   to spawn and forkserver workers.
5. **Start receipt.** Captured before enumeration: commit, tree id, source
   digest, git-object digest, domains, workers, start method, command. It is
   never recomputed. In the snapshot, the run refuses unless its own package
   directory *is* the snapshot and its digest equals the launcher's.
6. **Import-origin verification.** The parent, and every pool worker that
   executes a job, report the files they imported `assurance_lab` from
   (keyed by module spec, so a worker's `__mp_main__` counts too). Any origin
   outside the executed tree means exit 3.
7. **End re-hash.** The executed tree is hashed again at the end; any change
   means exit 3. With snapshot execution this is a second, independent check.
8. **In-place reports are UNBOUND** and rejected (exit 3); see the evidence below.

## Evidence

| Check | How it is shown |
|---|---|
| Snapshot bytes = git objects = working tree (when clean) | `tests/test_provenance.py::test_snapshot_matches_git_objects_and_working_tree` |
| Digest algorithm is the same across implementations | git-object digest of `fb69e01` equals the filesystem digest recorded by that run (`393ce1b9…`) |
| Isolated interpreter and fork/spawn/forkserver workers import only from the snapshot; no site-packages, no checkout on `sys.path` | `test_snapshot_interpreter_and_workers_import_only_from_snapshot[fork,spawn,forkserver]` |
| Each refusal branch fires | `test_snapshot_preflight_refusals`, `test_dirty_tree_refused_unless_allowed` |
| Net change during the run is caught | development drift demo: an edit to `reliance.py` gave exit 3 |
| **Edit-and-revert reaches in-place runs** and **not snapshot runs** | `scripts/demo_edit_and_revert.sh`, results below |
| End to end in CI | quick campaign from a snapshot, platform default and `--start-method spawn`, with asserted mode and origin lines |

### `scripts/demo_edit_and_revert.sh` (quick profile, spawn workers, `ffce8f9`)

The script swaps in an induction checker that silently skips `Diagnose`
transitions after the start receipt is taken, waits for the spawn workers to
import it, then reverts the file before the run ends.

| | Transitions checked | Tree at start | Foreign origins | End re-hash | Verdict | Exit |
|---|---|---|---|---|---|---|
| in-place | **537,282** (sabotaged) | clean | none | unchanged | **UNBOUND** | 3 |
| snapshot | **872,610** (correct) | clean | none | unchanged | **BOUND** | 0 |

In-place, every per-line provenance check passed while 38% of the inductive
check was skipped. That is why in-place reports are rejected outright, not
just flagged.

## Limits

- **Root bypasses read-only permissions.** The runs above were as uid 0.
  The snapshot's protection is that nothing else knows its random path;
  anything that does can modify it, and that is detected only as a *net*
  change by the end re-hash.
- **The launcher, the interpreter, and the standard library are trusted.**
  None of them is hashed; the Python version is recorded in run metadata.
- **The snapshot is of the commit, not of the repository's other files.**
  Only `assurance_lab/` is exported and executed. Tests, scripts and docs
  are not part of a report's binding.
- **Idle pool processes are not checked.** A process that never ran a job
  never imported campaign code, so it has no origins to verify.
