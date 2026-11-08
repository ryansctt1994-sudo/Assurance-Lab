"""Independent transition relation (translated from tla/AuthorityCore.tla)."""

from hashlib import sha256

import pytest

from assurance_lab.checker.explore import explore
from assurance_lab.checker.induction import check_induction, inv_states
from assurance_lab.checker.relational import Spec, compare, read, write
from assurance_lab.core.model import Config, CoreModel, init_state
from assurance_lab.core.mutants import (
    AdmitIntoOccupiedLineage, BeneficiarySelfApproval, CompactionDropsEdge, DiagnosisRevisesRoles,
    ResurrectOnRetract, SelfGrant, StaleSuccessor,
)
from assurance_lab.statedigest import (
    DuplicateEncoding, SetDigest, add_transitions, canon, canon_parts, sorted_digest, transition_line,
)

SMALL = [Config(n_records=2, n_lineages=1), Config(n_records=2, n_lineages=2),
         Config(n_records=3, n_lineages=1, max_level=1, n_sem=1)]


@pytest.mark.parametrize("cfg", SMALL, ids=lambda c: c.describe())
def test_action_labeled_successors_agree_on_every_inv_state(cfg):
    """Not just the same states: the same legal actions, with the same
    successors, from every Inv-state (reachable or not)."""
    states = list(inv_states(cfg))
    assert compare(CoreModel(cfg), Spec.for_config(cfg), states) == []


def test_oracle_codec_agrees_with_canon_and_init():
    cfg = Config()
    assert write(Spec.for_config(cfg).init()) == canon(init_state(cfg))
    for s in inv_states(Config(n_records=2, n_lineages=1)):
        assert write(read(canon(s))) == canon(s)


def test_oracle_recomputes_the_transition_digests():
    """The oracle's own successors reproduce Python's labeled-transition and
    edge digests over every Inv-state."""
    cfg = Config(n_records=2, n_lineages=1)
    py = check_induction(CoreModel(cfg))
    spec, lab, edg = Spec.for_config(cfg), SetDigest(), SetDigest()
    for s in inv_states(cfg):
        line = canon(s)
        add_transitions(lab, edg, line, sorted(spec.successors(line)))
    assert lab.label() == py.transition_digest.label()
    assert edg.label() == py.edge_digest.label()


@pytest.mark.parametrize("mutant", [SelfGrant, DiagnosisRevisesRoles, CompactionDropsEdge],
                         ids=lambda m: m.name)
def test_oracle_sees_mutants_that_state_digests_cannot(mutant):
    """These reach exactly the clean model's state set (equal state digests)
    and differ only in transitions; the transition oracle must disagree."""
    cfg = Config(n_records=2, n_lineages=1)
    states = explore(mutant(cfg), stop_on_violation=False, keep_states=True,
                     properties=frozenset()).state_set
    clean = explore(CoreModel(cfg), keep_states=True).state_set
    assert states == clean
    assert compare(mutant(cfg), Spec.for_config(cfg), states)


@pytest.mark.parametrize("mutant,name,cfg", [
    (BeneficiarySelfApproval, "beneficiary_self_approval", Config(n_records=2, n_lineages=1)),
    (AdmitIntoOccupiedLineage, "admit_into_occupied_lineage", Config(n_records=2, n_lineages=1)),
    (StaleSuccessor, "stale_successor", Config(n_records=3, n_lineages=1, max_level=1, n_sem=1)),
    (ResurrectOnRetract, "resurrect_on_retract", Config(n_records=2, n_lineages=1)),
])
def test_tla_mutant_translations_match_their_python_twins(mutant, name, cfg):
    states = explore(mutant(cfg), stop_on_violation=False, keep_states=True,
                     properties=frozenset()).state_set
    assert compare(mutant(cfg), Spec.for_config(cfg, name), states) == []
    assert compare(mutant(cfg), Spec.for_config(cfg), states)  # and differ from the clean spec


# --- golden vectors for the canonical encoding and release digests -----------------

ELEVEN = [(True, 1, 0, 1, 1, True, False, 1)] + [(False, None, None, None, None, False, False, None)] * 10


def test_golden_numeric_not_lexicographic_ordering():
    line = canon_parts(ELEVEN, {10, 2}, {(2, 10), (0, 2), (10, 11)}, (2, 0), 1)
    assert "|2,10|0>2,2>10,10>11|2,0|1" in line  # 2 before 10: integers, not strings
    assert line.startswith("1,1,0,1,1,1,0,1;0,-1,-1,-1,-1,0,0,-1;")  # NULL is -1, booleans 0/1
    assert sha256(line.encode()).hexdigest() == "55ff4de1dac898cf5da3b01e088e679afa7e9c2e42c936a4f350839ba2f5d8d7"


def test_golden_sorted_digest_and_duplicate_rejection():
    assert sorted_digest(["b", "a", "ab"]) == \
        "sorted-v1:3:dc5184d3a3844f04ab9bf2f2d7c58b46dcd65152f401a59cd0a3d46774b4d1ca"
    assert sorted_digest(["ab", "b", "a"]) == sorted_digest(["b", "a", "ab"])  # order-canonical
    # length-delimited: "a"+"b" and "ab" are different sets with different digests
    assert sorted_digest(["a", "b"]) != sorted_digest(["ab"])
    with pytest.raises(DuplicateEncoding):
        sorted_digest(["a", "b", "a"])


def test_golden_transition_line_and_edge_semantics():
    assert transition_line("S", "Admit(0,0,1,0)", "T") == "S#Admit(0,0,1,0)#T"
    lab, edg = SetDigest(), SetDigest()
    add_transitions(lab, edg, "S", [("Diagnose(0)", "S"), ("Diagnose(1)", "S"), ("Grant(1,0)", "T")])
    assert lab.count == 3  # distinct actions to the same destination counted separately
    assert edg.count == 2  # ... but each (source, destination) pair once; self-loops included


# --- partition integrity -------------------------------------------------------------


def test_partitions_are_a_disjoint_exact_cover():
    """Missing, repeated or overlapping induction partitions would change
    these: per-partition outputs concatenated contain no duplicate (the
    sorted digest raises) and equal the single-pass set, and merged
    per-partition sum digests equal the single-pass sum digest."""
    from assurance_lab.checker.induction import partitions
    from assurance_lab.statedigest import set_digest
    cfg = Config(n_records=2, n_lineages=1)
    parts = partitions(cfg)
    assert len(parts) == len(set(parts)) == (cfg.max_level + 1) ** cfg.n_principals * cfg.n_sem
    per = [[canon(s) for s in inv_states(cfg, [p])] for p in parts]
    whole = [canon(s) for s in inv_states(cfg)]
    assert sorted_digest(line for chunk in per for line in chunk) == sorted_digest(whole)
    merged = SetDigest()
    for p in parts:
        merged = merged.merge(set_digest(inv_states(cfg, [p])))
    assert merged.label() == set_digest(inv_states(cfg)).label()
    with pytest.raises(DuplicateEncoding):  # a repeated partition is caught, not absorbed
        sorted_digest(line for chunk in per + per[:1] for line in chunk)


def test_duplicate_action_labels_are_reported_not_merged():
    """A model that yields the same labeled transition twice would keep the
    labeled count equal to the transition count while the *set* is smaller;
    the oracle must flag it rather than merge."""
    class Doubled(CoreModel):
        name = "doubled_labels"

        def adjudicate(self, s):
            for lbl, t in super().adjudicate(s):
                yield lbl, t
                yield lbl, t

    cfg = Config(n_records=2, n_lineages=1)
    bad = compare(Doubled(cfg), Spec.for_config(cfg), list(inv_states(cfg)))
    assert bad and all("duplicate_labels" in b for b in bad)
