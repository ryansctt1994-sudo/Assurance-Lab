"""AL-RUNTIME-001 E1 runtime contract, machine-readable half.

Frozen before the gate was implemented; ``docs/RUNTIME_CONTRACT.md`` is the
human-readable half, and ``tests/test_runtime_contract.py`` pins both files.
Everything here is pure and shared by the gate, the reference model and the
reconciler: encodings, identities, MACs, name and path rules, and the ordered
reason codes. The gate, the model and the reconciler implement the procedures
separately.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Optional

CONTRACT_VERSION = "al-runtime-001/e1/v1"
SCHEME = "hmac-sha256"

D_SNAPSHOT = b"al-runtime-001/e1/snapshot/v1"
D_EFFECT = b"al-runtime-001/e1/effect/v1"
D_REQUEST = b"al-runtime-001/e1/request/v1"
D_ENTRY = b"al-runtime-001/e1/log-entry/v1"
D_COMMIT = b"al-runtime-001/e1/commitment/v1"
_D_TEST_KEY = b"al-runtime-001 PUBLIC TEST KEY (authenticates nothing): "

GENESIS_HEAD = "0" * 64
MAX_CONTENT = 65536
MAX_TARGET_LEN = 200
MAX_DEPTH = 4
TEMP_PREFIX = ".al-tmp-"

PRINCIPAL_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
LINEAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")

ADMITTED, REFUSED = "ADMITTED", "REFUSED"
OPERATIONAL = "OPERATIONAL"

#: Request checks, in order; the first failure is the refusal reason.
REQUEST_REASONS = (
    "MALFORMED_REQUEST", "UNKNOWN_PRINCIPAL", "FORGED_REQUEST", "REPLAYED_REQUEST", "INVALID_TARGET",
    "AUTHORITY_UNKNOWN", "AUTHORITY_CONFLICT", "NO_AUTHORITY", "STALE_AUTHORITY", "NOT_AUTHORIZED",
    "PATH_UNSAFE", "PRESTATE_MISMATCH",
)
#: After ADMIT, when the effect could not be performed (an ``abandoned`` entry).
EFFECT_FAILED = "EFFECT_FAILED"
#: ``explain_head`` status -> refusal reason (status HEAD continues to the next checks).
HEAD_REFUSALS = {"UNKNOWN": "AUTHORITY_UNKNOWN", "CONFLICT": "AUTHORITY_CONFLICT", "NONE": "NO_AUTHORITY"}
#: Startup checks, in order; the first failure is the gate's fail-closed state.
STARTUP_STATES = (
    "UNSUPPORTED_PLATFORM", "LOCKED", "LOG_TAMPERED", "INVALID_CONFIG", "WITNESS_INVALID",
    "ROLLBACK_DETECTED", "INVALID_SNAPSHOT", "SNAPSHOT_MISMATCH", "UNCERTAIN_STATE",
)
RECOVERABLE_STATES = ("SNAPSHOT_MISMATCH", "UNCERTAIN_STATE")
#: Reconciliation findings.
FINDINGS = (
    "WITNESS_INVALID", "LOG_TAMPERED", "LOG_BEHIND_WITNESS", "LOG_AHEAD_OF_WITNESS", "UNRESOLVED_EFFECT",
    "MISSING_EFFECT", "MODIFIED_EFFECT", "UNEXPECTED_FILE", "UNEXPECTED_NON_REGULAR",
    "UNEXPECTED_DIRECTORY", "MULTI_LINKED_FILE", "TEMP_LEFTOVER",
)
CONSISTENT, INCONSISTENT = "CONSISTENT", "INCONSISTENT"

#: Log entry kinds.
ENTRY_KINDS = ("genesis", "authority", "decision", "complete", "abandoned", "recovery")


# ------------------------------------------------------------------ encodings
def canonical(obj) -> bytes:
    """Canonical JSON bytes. Floats are refused: they have no canonical form here."""
    def check(x):
        if isinstance(x, float):
            raise TypeError("floats are not canonical")
        if isinstance(x, dict):
            for k, v in x.items():
                if not isinstance(k, str):
                    raise TypeError("object keys must be strings")
                check(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                check(v)
    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(domain: bytes, obj) -> str:
    return sha256_hex(domain + b"\n" + canonical(obj))


def mac(key: bytes, domain: bytes, obj) -> str:
    return hmac.new(key, domain + b"\n" + canonical(obj), "sha256").hexdigest()


def mac_ok(key: bytes, domain: bytes, obj, tag) -> bool:
    return isinstance(tag, str) and hmac.compare_digest(mac(key, domain, obj), tag)


def public_test_key(name: str) -> bytes:
    """Deterministic key from a published seed: reproducible, authenticates nothing."""
    return hashlib.sha256(_D_TEST_KEY + name.encode("utf-8")).digest()


# ----------------------------------------------------------------- identities
def snapshot_digest(encoded_snapshot: dict) -> str:
    """Snapshot identity over ``snapshot_json.encode(replica.freeze())``."""
    return digest(D_SNAPSHOT, encoded_snapshot)


def effect_id(request_id: str, lineage: str, target: str, content_sha256: str) -> str:
    return digest(D_EFFECT, {"request_id": request_id, "lineage": lineage, "target": target,
                             "content_sha256": content_sha256})


def line_hash(line: bytes) -> str:
    """Hash of one log line, without its newline: the chain link and the log head."""
    return sha256_hex(line)


# ------------------------------------------------------------------- requests
@dataclass(frozen=True)
class Request:
    request_id: str
    requester: str
    lineage: str
    cites: str
    target: str
    content_sha256: str
    mac: str

    def fields(self) -> dict:
        return {"contract": CONTRACT_VERSION, "request_id": self.request_id, "requester": self.requester,
                "lineage": self.lineage, "cites": self.cites, "target": self.target,
                "content_sha256": self.content_sha256}

    def as_record(self) -> dict:
        return {**self.fields(), "mac": self.mac}


def make_request(key: bytes, request_id: str, requester: str, lineage: str, cites: str, target: str,
                 content: bytes) -> Request:
    """Client side: build and authenticate a request for ``content``."""
    unsigned = Request(request_id, requester, lineage, cites, target, sha256_hex(content), "")
    return Request(**{**unsigned.__dict__, "mac": mac(key, D_REQUEST, unsigned.fields())})


def request_problems(req, content) -> list[str]:
    """Check 1 (MALFORMED_REQUEST): types, formats, content size and hash. Pure; never raises."""
    if not isinstance(req, Request):
        return ["not a Request"]
    out = []
    for name in ("request_id", "requester", "lineage", "cites", "target", "content_sha256", "mac"):
        if not isinstance(getattr(req, name), str):
            out.append(f"{name} is not a string")
    if out:
        return out
    if not REQUEST_ID_RE.fullmatch(req.request_id):
        out.append("request_id format")
    if not PRINCIPAL_RE.fullmatch(req.requester):
        out.append("requester format")
    if not LINEAGE_RE.fullmatch(req.lineage):
        out.append("lineage format")
    if not (0 < len(req.cites) <= 64) or not req.cites.isprintable():
        out.append("cites format")
    if not (0 < len(req.target) <= MAX_TARGET_LEN) or not req.target.isascii():
        out.append("target length or charset")
    if not HEX64_RE.fullmatch(req.content_sha256):
        out.append("content_sha256 format")
    if not isinstance(content, bytes):
        out.append("content is not bytes")
    elif len(content) > MAX_CONTENT:
        out.append(f"content larger than {MAX_CONTENT} bytes")
    elif sha256_hex(content) != req.content_sha256:
        out.append("content does not match content_sha256")
    return out


def target_problems(target: str) -> list[str]:
    """Check 5 (INVALID_TARGET): the path rules, on the string alone. Pure; never raises."""
    if not isinstance(target, str) or not target:
        return ["empty target"]
    if len(target) > MAX_TARGET_LEN:
        return ["target too long"]
    parts = target.split("/")
    if len(parts) > MAX_DEPTH:
        return [f"more than {MAX_DEPTH} components"]
    return [f"component {p!r} not allowed" for p in parts if not COMPONENT_RE.fullmatch(p)]


def sandbox_components(lineage: str, target: str) -> tuple[str, ...]:
    """Path of the effect under ``effects/``: the lineage's namespace, then the target."""
    return (lineage, *target.split("/"))


# ------------------------------------------------------------------- outcomes
@dataclass(frozen=True)
class Response:
    """What the gate answers. ``reason`` is None only for ADMITTED."""

    outcome: str
    reason: Optional[str] = None
    log_index: Optional[int] = None
    effect_id: Optional[str] = None
    head_status: Optional[str] = None
    head_value: Optional[str] = None
    detail: str = ""

    def key(self) -> tuple:
        """The part of a response that conformance compares."""
        return (self.outcome, self.reason)


def commitment_fields(seq: int, log_head: str, snapshot_dig: str) -> dict:
    return {"contract": CONTRACT_VERSION, "seq": seq, "log_head": log_head, "snapshot_digest": snapshot_dig}
