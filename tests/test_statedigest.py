"""State-set digests: canonical, injective, order-independent, deterministic."""

import multiprocessing
import random

import pytest

from assurance_lab.checker.explore import explore
from assurance_lab.checker.induction import check_induction, inv_states
from assurance_lab.core.model import Config, CoreModel, init_state
from assurance_lab.statedigest import ENCODING, SetDigest, canon, set_digest

CFG = Config(n_records=2, n_lineages=1)

# Golden values. If the encoding changes, previously recorded digests are no
# longer comparable: bump ENCODING and update these deliberately.
INIT_LINE = "0,-1,-1,-1,-1,0,0,-1;0,-1,-1,-1,-1,0,0,-1;0,-1,-1,-1,-1,0,0,-1|||2,0|0"
GOLDEN_INV = "ac-state-v1:12994:c80c9f474b4099f536684070a957d8eb8f5fe055eafd857cb3fd39fffb6c1055"
GOLDEN_REACH = "ac-state-v1:5034:65c960939c53aa166a719c17051250f7b528af8be90029bd1533ba34ab97c024"


def test_encoding_is_pinned():
    assert ENCODING == "ac-state-v1"
    assert canon(init_state(Config())) == INIT_LINE
    assert set_digest(inv_states(CFG)).label() == GOLDEN_INV
    assert set_digest(explore(CoreModel(CFG), keep_states=True).state_set).label() == GOLDEN_REACH


def test_canonical_encoding_is_injective_on_inv_states():
    states = list(inv_states(Config(n_records=2, n_lineages=2)))
    assert len({canon(s) for s in states}) == len(states) == len(set(states))


def test_digest_is_order_independent_and_mergeable():
    states = list(inv_states(CFG))
    shuffled = states[:]
    random.Random(7).shuffle(shuffled)
    whole = set_digest(states)
    assert set_digest(shuffled).label() == whole.label()
    half = len(states) // 2
    assert set_digest(states[:half]).merge(set_digest(states[half:])).label() == whole.label()


def test_digest_detects_a_single_changed_membership():
    states = list(inv_states(CFG))
    a = set_digest(states)
    b = set_digest(states[1:])
    c = SetDigest()
    for s in states[1:]:
        c.add(s)
    c.add(states[0]._replace(sem_now=states[0].sem_now))  # same state back in
    assert a.label() != b.label() and c.label() == a.label()


@pytest.mark.parametrize("method", [m for m in ("fork", "spawn", "forkserver")
                                    if m in multiprocessing.get_all_start_methods()])
def test_induction_digest_is_invariant_across_workers_and_start_methods(method):
    seq = check_induction(CoreModel(CFG), workers=1)
    pool = check_induction(CoreModel(CFG), workers=2, mp_context=multiprocessing.get_context(method))
    assert seq.digest.label() == pool.digest.label() == GOLDEN_INV


def test_reachable_and_inv_digests_differ_where_sets_differ():
    reach = set_digest(explore(CoreModel(CFG), keep_states=True).state_set)
    inv = check_induction(CoreModel(CFG)).digest
    assert reach.count < inv.count and reach.hexdigest() != inv.hexdigest()
