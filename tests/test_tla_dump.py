"""TLC dump parsing. NOTE: validated against hand-written samples and a
printer round-trip only, not yet against output from a real TLC run."""

import pytest

from assurance_lab.checker.induction import inv_states
from assurance_lab.core.model import Config
from assurance_lab.statedigest import canon, set_digest
from assurance_lab.tla import (
    TLAParseError, dump_digest, parse_dump, parse_state, parse_value, state_line, to_tla,
)

CFG = Config(n_records=2, n_lineages=1)


def test_value_syntax():
    assert parse_value("{}") == frozenset()
    assert parse_value("{<<0, 1>>, <<1, 2>>}") == frozenset({(0, 1), (1, 2)})
    assert parse_value("(0 :> 2 @@ 1 :> -1)") == {0: 2, 1: -1}
    assert parse_value("[b |-> TRUE, a |-> \"x\"]") == {"a": "x", "b": True}
    assert parse_value("<<>>") == ()
    with pytest.raises(TLAParseError):
        parse_value("{0, 1")
    with pytest.raises(TLAParseError):
        parse_value("(0 :> 1 1 :> 2)")


def test_handwritten_state_with_wrapping_and_field_order():
    text = """/\\ current = {1}
/\\ edges = {<<0, 1>>}
/\\ levels = (0 :> 2 @@ 1 :> 1)
/\\ recs = ( 0 :> [ owner |-> 1, admitted |-> TRUE, lineage |-> 0, approver |-> 0,
             sem |-> 0, invalidated |-> FALSE, retracted |-> FALSE, adj |-> -1 ] @@
     1 :> [ admitted |-> TRUE, lineage |-> 0, owner |-> 0, approver |-> 1, sem |-> 0,
             invalidated |-> FALSE, retracted |-> FALSE, adj |-> 0 ] )
/\\ semNow = 0
"""
    line = state_line(parse_state(text), 2, 2)
    assert line == "1,0,1,0,0,0,0,-1;1,0,0,1,0,0,0,0|1|0>1|2,1|0"


def test_round_trip_every_inv_state():
    for s in inv_states(CFG):
        assert state_line(parse_state(to_tla(s)), CFG.n_records, CFG.n_principals) == canon(s)


def test_dump_digest_equals_python_digest():
    states = list(inv_states(CFG))
    dump = "Some TLC preamble\n" + "".join(f"State {i + 1}:\n{to_tla(s)}\n" for i, s in enumerate(states))
    assert len(list(parse_dump(dump))) == len(states)
    assert dump_digest(dump, CFG.n_records, CFG.n_principals).label() == set_digest(states).label()


def test_shape_errors_are_reported_not_guessed():
    s = next(iter(inv_states(CFG)))
    bad_fields = to_tla(s).replace("adj |->", "adjudicated |->")
    with pytest.raises(TLAParseError):
        state_line(parse_state(bad_fields), 2, 2)
    a, b = s.levels
    fn = f"(0 :> {a} @@ 1 :> {b})"
    assert fn in to_tla(s)
    one_based = to_tla(s).replace(fn, f"<<{a}, {b}>>")  # a 1-indexed sequence instead
    with pytest.raises(TLAParseError):
        state_line(parse_state(one_based), 2, 2)


def test_cli_compares_against_expected_label(tmp_path, capsys):
    from assurance_lab.tla import main
    states = list(inv_states(CFG))
    dump = tmp_path / "x.dump"
    dump.write_text("".join(f"State {i + 1}:\n{to_tla(s)}\n" for i, s in enumerate(states)))
    want = set_digest(states).label()
    assert main([str(dump), "--records", "2", "--principals", "2", "--expect", want]) == 0
    assert main([str(dump), "--records", "2", "--principals", "2", "--expect", want[:-1] + "0"]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_two_independent_serializers_agree_and_disagreement_is_caught(monkeypatch):
    from assurance_lab import tla
    states = list(inv_states(CFG))
    dump = "".join(f"State {i + 1}:\n{to_tla(s)}\n" for i, s in enumerate(states))
    for s in states:
        st = parse_state(to_tla(s))
        assert tla.state_line_independent(st, 2, 2) == state_line(st, 2, 2) == canon(s)
    # a defect in the shared canonical serializer no longer goes unnoticed
    monkeypatch.setattr(tla, "canon_parts",
                        lambda recs, cur, edges, levels, sem: "|".join(["x", str(sorted(cur)), "", "", str(sem)]))
    with pytest.raises(tla.SerializerDisagreement):
        tla.dump_digest(dump, 2, 2)
