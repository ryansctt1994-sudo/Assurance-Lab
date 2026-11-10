# AL-RUNTIME-001 runtime contract (E1, v1)

**Milestone:** AL-RUNTIME-001, Bounded Filesystem Authorization, E1 prototype.
**Version:** `al-runtime-001/e1/v1`.
**Status:** frozen before the gate was implemented. The machine-readable half
is `assurance_lab/runtime/contract.py`. `tests/test_runtime_contract.py` pins
the SHA-256 of both files. Changing either one needs a new version string and
a changelog entry here, never a silent edit.

## What this is, and what it is not

A **gate** performs one kind of real, externally observable effect: writing a
file into a sandbox directory. It does so only when the authority model
already verified in this repository (the replica's `explain_head`, with the
development lineage's full snapshot-integrity check) says the requester holds
the current authority.

**The sandbox directory is not a security boundary.** It is an isolated,
disposable directory. The gate confines its own writes there and notices some
outside interference. It cannot stop a process with the same filesystem
rights from writing there. Detecting such writes afterwards is the
reconciler's job. Nothing here is hardened containment, production
authorization, or a safety claim.

## Parties

| Party | Holds | Trusted for |
|---|---|---|
| Requester (principal) | its own key | nothing; every request is authenticated |
| Authority channel | — | delivering authority events. **Trusted in E1**: authority events are not authenticated (out of scope) |
| Gate | the gate key; `state/` (lock, log, snapshot); the sole right to write `effects/` | deciding by this contract |
| Witness | `commitments.jsonl`, in a directory separate from the gate's state | retaining the latest commitment independently of `state/` |
| Reconciler | the gate's verification key; read access to the log, witness and `effects/` | comparing decisions with observed effects, without using the gate's code or memory |
| Attacker (test model) | write access to `state/` and `effects/`; no keys; no write access to the witness | — |

In E1 the gate key and principal keys are HMAC-SHA256 keys. A MAC shows
integrity and origin against anyone **without** the key. It gives no
non-repudiation, and anyone who can verify can also forge. Asymmetric
signatures are the planned replacement (`scheme` field). Evidence about a run
is signed separately, by its operator's provisioned key
(`scripts/sign_evidence.sh`), or it is labelled UNSIGNED.

**Public test keys** (`contract.public_test_key`) are derived from a published
seed, so a demonstration is reproducible byte for byte. They authenticate
nothing. The gate refuses them unless the caller passes
`allow_public_test_keys=True`, and every record made with them says so.

## Valid authority state

The authority state is one `Replica`, under the resolution policy fixed in the
genesis entry. It is valid only when all of the following hold:

1. It is the fold, from an empty replica, of the authority events in a log
   whose chain and MACs verify.
2. `state/snapshot.json` is self-consistent (its stored digest is its
   content's digest), restores through `Replica.thaw` with validation, and
   has the same digest as the fold in (1).
3. The witness's latest commitment verifies and names exactly this log head
   and this snapshot digest.

Anything else is a **fail-closed state** (see below). The log is the truth;
the snapshot is a cache that must agree with it.

## Identities

All digests are SHA-256, lowercase hex, computed over `domain || "\n" ||
canonical-JSON`. Canonical JSON is `json.dumps(obj, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)`, with no floats.

| Identity | Domain tag | Over |
|---|---|---|
| Snapshot | `al-runtime-001/e1/snapshot/v1` | `snapshot_json.encode(replica.freeze())` |
| Effect | `al-runtime-001/e1/effect/v1` | `{request_id, lineage, target, content_sha256}` |
| Request MAC | `al-runtime-001/e1/request/v1` | the request's fields except `mac` |
| Log-entry MAC | `al-runtime-001/e1/log-entry/v1` | the entry's fields except `mac` |
| Commitment MAC | `al-runtime-001/e1/commitment/v1` | `{contract, seq, log_head, snapshot_digest}` |

`log_head` is the SHA-256 of the last log line's bytes, without its newline.
An empty log's head is 64 zeros. `seq` is the number of log entries.

## Requests

A request carries `request_id`, `requester`, `lineage`, `cites`, `target`,
`content_sha256` and `mac`, and travels with its content bytes.

* `request_id`: `[A-Za-z0-9_-]{8,64}`. Single use (see replay).
* `requester`: a principal name, `[a-z][a-z0-9_-]{0,31}`.
* `lineage`: `[A-Za-z0-9][A-Za-z0-9_-]{0,31}`.
* `cites`: the record id the requester relies on as the lineage's current
  head. A non-empty string of at most 64 characters.
* `target`: a relative path under the lineage's namespace `effects/<lineage>/`.
  It has 1 to 4 components joined by `/`, each matching
  `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, with at most 200 characters in total.
  So there are no absolute paths, no `.` or `..`, no hidden names and no
  empty components. Names starting with a dot are reserved for the gate's
  temporary files.
* Content: at most 65,536 bytes, whose SHA-256 is `content_sha256`.

## Decision procedure (an operational gate)

Checks run in this order. The first failure is the reason.

| # | Check | Refusal reason |
|---|---|---|
| 1 | Field types and formats; content size; content hash | `MALFORMED_REQUEST` |
| 2 | Requester is a known principal | `UNKNOWN_PRINCIPAL` |
| 3 | MAC verifies under the requester's key | `FORGED_REQUEST` |
| 4 | `request_id` not consumed | `REPLAYED_REQUEST` |
| 5 | `target` satisfies the path rules | `INVALID_TARGET` |
| 6 | `explain_head(lineage)`: `UNKNOWN` / `CONFLICT` / `NONE` | `AUTHORITY_UNKNOWN` / `AUTHORITY_CONFLICT` / `NO_AUTHORITY` |
| 7 | the head is the record `cites` names | `STALE_AUTHORITY` |
| 8 | the requester owns the head record | `NOT_AUTHORIZED` |
| 9 | every path component exists as a real directory or not at all, and the target is not a symlink or directory | `PATH_UNSAFE` |
| 10 | the target's observed state is what the gate last wrote: absent if never written; otherwise a regular file with one link and the content hash of the last completed effect | `PRESTATE_MISMATCH` |

If every check passes, the decision is **ADMIT**:

1. Append a `decision` entry (outcome ADMIT, with the effect identity) and
   publish its commitment.
2. Perform the effect. Create missing directories without following links,
   write a fresh temporary file (`O_CREAT|O_EXCL|O_NOFOLLOW`) in the target's
   directory, fsync it, rename it over the target relative to the directory
   handle, and fsync the directory.
3. Append a `complete` entry with the observed content hash, and publish its
   commitment. If step 2 fails, append an `abandoned` entry instead. The
   response is then `REFUSED` / `EFFECT_FAILED`.

Every decision on an operational gate is logged, admit or refuse, with the
head the gate saw (status, value, reason) and the snapshot digest it decided
on.

## Replay semantics

* The first logged decision on an **authenticated** request (one that passed
  checks 1–3) consumes its `request_id`, whatever the outcome.
* A request that fails checks 1–3 consumes nothing, so nobody can use up
  another principal's ids.
* A consumed id is never admitted again. The replay is refused with
  `REPLAYED_REQUEST`, which names the log index of the first decision, and it
  produces no effect. A requester that wants to retry after a refusal or a
  crash sends a new request with a new id and a new MAC.

## Refusal behaviour

* A refusal never writes to `effects/`.
* On an operational gate, a refusal is logged and committed like any other
  decision.
* In a **fail-closed state**, every request and authority event is refused
  with the state's reason, and the gate writes nothing at all: no effect, no
  log entry, no commitment. It cannot extend a log or a state it no longer
  trusts.

## Startup and fail-closed states

When the gate opens, these checks run in order. The first failure becomes its
state:

| # | Check | Fail-closed state | Recoverable by `recover()` |
|---|---|---|---|
| 1 | platform has the `dir_fd`, `O_NOFOLLOW` and `flock` primitives | `UNSUPPORTED_PLATFORM` | no |
| 2 | exclusive lock on `state/LOCK` | `LOCKED` | no |
| 3 | every log line parses and every entry's chain link and MAC verify | `LOG_TAMPERED` | no |
| 4 | the genesis entry matches the configuration, and keys are not public test keys unless allowed | `INVALID_CONFIG` | no |
| 5 | the witness's latest commitment verifies | `WITNESS_INVALID` | no |
| 6 | the witness's commitment is not ahead of, or divergent from, the log | `ROLLBACK_DETECTED` | no |
| 7 | the snapshot is self-consistent and restores with validation | `INVALID_SNAPSHOT` | no |
| 8 | the snapshot's digest equals the log fold's digest | `SNAPSHOT_MISMATCH` | yes: rebuild from the log, recorded in a `recovery` entry |
| 9 | the log is not ahead of the witness, and no ADMIT lacks a `complete` or `abandoned` entry | `UNCERTAIN_STATE` | yes: settle each unresolved effect from what the target holds (`complete` if it holds the admitted content, else `abandoned`), remove the gate's temporary files, commit |

Otherwise the gate is **OPERATIONAL**. States 1–7 need an operator: the gate
cannot repair them without trusting what it can no longer verify.

A hash chain alone does not stop rollback: a complete, older copy of
`state/` verifies internally. Check 6 rejects that copy because the witness,
retained apart from `state/`, holds a later commitment. The witness's
independence is simulated in E1 by a separate directory that the test
attacker does not touch. A real deployment would put it in another trust
domain. Trusted timestamping is a separate gate, not met here.

## Reconciliation

The reconciler reads the witness, the log and `effects/` itself. It shares only
`contract.py` with the gate. Each problem it reports is a finding:

| Finding | Meaning |
|---|---|
| `WITNESS_INVALID` | the latest commitment fails its MAC |
| `LOG_TAMPERED` | a log line fails to parse, or an entry's chain link or MAC fails |
| `LOG_BEHIND_WITNESS` | the witness holds a later or divergent commitment (rollback or truncation) |
| `LOG_AHEAD_OF_WITNESS` | log entries with no commitment (an interrupted step) |
| `UNRESOLVED_EFFECT` | an ADMIT with no `complete` or `abandoned` entry |
| `MISSING_EFFECT` | a completed effect's file is absent |
| `MODIFIED_EFFECT` | a completed effect's file holds other content |
| `UNEXPECTED_FILE` | a regular file no completed effect explains |
| `UNEXPECTED_NON_REGULAR` | a symlink, or any other non-regular, non-directory entry |
| `UNEXPECTED_DIRECTORY` | a directory that is not on the path of any admitted target |
| `MULTI_LINKED_FILE` | a file with more than one hard link (the gate only creates files by rename) |
| `TEMP_LEFTOVER` | one of the gate's temporary files remains |

It reports **CONSISTENT** when there are no findings, and **INCONSISTENT** with
the sorted findings otherwise. An effect counts as expected only when a
`complete` entry follows its ADMIT. While an effect is unresolved, its target
may hold either the previous or the admitted content without a
`MODIFIED_EFFECT` finding.

## Not claimed

* Containment or security against a process with the same filesystem rights,
  root, a compromised key, or a compromised witness.
* Authentication of authority events (trusted channel in E1).
* Concurrency beyond one gate per state directory (enforced by the lock).
* Liveness, performance, or any production use.
* Independent validation, trusted time, or a signature on any evidence unless
  one is actually attached.

## Changelog

* `al-runtime-001/e1/v1`: initial contract, frozen before the gate.
