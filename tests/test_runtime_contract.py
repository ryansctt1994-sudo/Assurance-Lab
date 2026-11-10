"""AL-RUNTIME-001 E1: the runtime contract is frozen, and its two halves agree.

The contract was committed before the gate existed. Changing either file
needs a new version string and a changelog entry in the document; this test
makes such a change visible instead of silent."""

import hashlib
import re
from pathlib import Path

import pytest

from assurance_lab.distributed import snapshot_json as sj
from assurance_lab.distributed.replica import Replica, ResolutionPolicy
from assurance_lab.runtime import contract as c

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/RUNTIME_CONTRACT.md"

#: sha256 of the frozen contract files (version al-runtime-001/e1/v1).
FROZEN = {
    "docs/RUNTIME_CONTRACT.md": "c826f23710223b5419dabcb029e53b13be80a271eead39a5b69268b419be96b7",
    "assurance_lab/runtime/contract.py": "79f1526103c709c2fe1bb48a164b78164db15396e3df4f657d79861ccb41b448",
}


@pytest.mark.parametrize("path", sorted(FROZEN))
def test_contract_files_are_frozen(path):
    actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    assert actual == FROZEN[path], (
        f"{path} changed. The runtime contract is frozen: give it a new version string and a "
        "changelog entry in docs/RUNTIME_CONTRACT.md, then update this pin in the same commit.")


def test_version_is_named_in_both_halves():
    assert c.CONTRACT_VERSION == "al-runtime-001/e1/v1"
    assert f"`{c.CONTRACT_VERSION}`" in DOC.read_text(encoding="utf-8")


def _table_codes(section: str, column: int) -> list[str]:
    """Codes in one column of the section's table, in the order the document lists them."""
    text = DOC.read_text(encoding="utf-8").split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in text.splitlines() if r.startswith("| ") and not set(r) <= set("|-: ")]
    cells = [[x.strip() for x in r.strip().strip("|").split("|")] for r in rows[1:]]  # rows[0]: header
    return [code for row in cells for code in re.findall(r"`([A-Z_]+)`", row[column])]


def test_document_and_code_list_the_same_request_checks_in_the_same_order():
    assert _table_codes("Decision procedure (an operational gate)", 2) == list(c.REQUEST_REASONS)


def test_document_and_code_list_the_same_startup_states_in_the_same_order():
    assert _table_codes("Startup and fail-closed states", 2) == list(c.STARTUP_STATES)


def test_document_and_code_agree_on_which_states_recover():
    text = DOC.read_text(encoding="utf-8").split("## Startup and fail-closed states", 1)[1].split("\n## ", 1)[0]
    rows = [[x.strip() for x in r.strip().strip("|").split("|")] for r in text.splitlines()
            if r.startswith("| ") and "`" in r]
    recoverable = [re.findall(r"`([A-Z_]+)`", r[2])[0] for r in rows if r[3].startswith("yes")]
    assert recoverable == list(c.RECOVERABLE_STATES)


def test_document_and_code_list_the_same_findings():
    assert _table_codes("Reconciliation", 0) == list(c.FINDINGS)


def test_codes_are_unique_and_disjoint():
    groups = [c.REQUEST_REASONS, c.STARTUP_STATES, (c.EFFECT_FAILED,)]
    flat = [x for g in groups for x in g]
    assert len(flat) == len(set(flat))
    assert set(c.HEAD_REFUSALS.values()) <= set(c.REQUEST_REASONS)
    assert set(c.RECOVERABLE_STATES) <= set(c.STARTUP_STATES)


# ------------------------------------------------------------ identities (pinned values)
def test_identities_are_pinned():
    """Any change to an encoding, domain tag or seed changes these values."""
    assert c.public_test_key("alice").hex() == "45a2fbab451f8d8417ab64b9dad8d604d11f751130d505b84d5b6a8aea897abf"
    assert c.effect_id("req-00000001", "L", "config.txt", "0" * 64) == \
        "cbeea0b1ce3e4bea249b1e6508d6f8750a0d5e445a373abefa51ff75b618557b"
    empty = Replica(ResolutionPolicy(frozenset({"alice", "bob", "carol"}), 2))
    assert c.snapshot_digest(sj.encode(empty.freeze())) == \
        "4098c79891d3f5d7dab3d1b7a403d27d10c1e533a7047e3aa1c2c5d94a4d363e"


def test_canonical_json_is_deterministic_and_refuses_floats():
    assert c.canonical({"b": [1, "x"], "a": None}) == b'{"a":null,"b":[1,"x"]}'
    assert c.canonical({"a": 1, "b": 2}) == c.canonical({"b": 2, "a": 1})
    for bad in ({"x": 1.0}, [0.5], {1: "non-string key"}):
        with pytest.raises(TypeError):
            c.canonical(bad)


# ----------------------------------------------------------------------- requests
KEY = c.public_test_key("alice")


def _req(content=b"hello", **over):
    base = dict(request_id="req-00000001", requester="alice", lineage="L", cites="r0", target="config.txt")
    base.update(over)
    return c.make_request(KEY, content=content, **base), content


def test_a_well_formed_request_has_no_problems_and_its_mac_verifies():
    req, content = _req()
    assert c.request_problems(req, content) == []
    assert c.mac_ok(KEY, c.D_REQUEST, req.fields(), req.mac)
    assert not c.mac_ok(c.public_test_key("bob"), c.D_REQUEST, req.fields(), req.mac)


@pytest.mark.parametrize("field,value", [
    ("request_id", "replayed-id-but-changed"), ("requester", "bob"), ("lineage", "M"), ("cites", "r1"),
    ("target", "other.txt"), ("content_sha256", "1" * 64)])
def test_changing_any_field_after_authentication_breaks_the_mac(field, value):
    req, _ = _req()
    changed = c.Request(**{**req.__dict__, field: value})
    assert not c.mac_ok(KEY, c.D_REQUEST, changed.fields(), changed.mac)


@pytest.mark.parametrize("over,problem", [
    ({"request_id": "short"}, "request_id format"),
    ({"request_id": "has space 123"}, "request_id format"),
    ({"requester": "Alice"}, "requester format"),
    ({"lineage": "-L"}, "lineage format"),
    ({"cites": ""}, "cites format"),
    ({"target": ""}, "target length or charset"),
    ({"target": "café"}, "target length or charset"),
    ({"content": b"x" * (c.MAX_CONTENT + 1)}, f"content larger than {c.MAX_CONTENT} bytes"),
])
def test_malformed_requests_are_named(over, problem):
    req, content = _req(**over)
    assert problem in c.request_problems(req, content)


def test_content_must_match_its_hash():
    req, _ = _req()
    assert "content does not match content_sha256" in c.request_problems(req, b"other bytes")
    assert c.request_problems("not a request", b"") == ["not a Request"]


# -------------------------------------------------------------------- path rules
@pytest.mark.parametrize("target", ["a", "config.txt", "a/b/c/d", "A-1_b.c", "x" * 64, "v1.2/notes"])
def test_valid_targets(target):
    assert c.target_problems(target) == []


@pytest.mark.parametrize("target", [
    "", "/abs", "../x", "a/../b", "./a", ".hidden", "a/.al-tmp-x", "a//b", "a/", "/", "a/b/c/d/e",
    "x" * 65, "a b", "-x", "a\\b", "nul\x00", "~", "a/~b", ".", "..", ("y" * 60 + "/") * 3 + "y" * 60,
])
def test_invalid_targets(target):
    assert c.target_problems(target), target


def test_sandbox_path_is_the_lineage_namespace_then_the_target():
    assert c.sandbox_components("L", "a/b.txt") == ("L", "a", "b.txt")


def test_temporary_names_can_never_be_valid_targets():
    assert c.TEMP_PREFIX.startswith(".")
    assert c.target_problems(c.TEMP_PREFIX + "abc")
