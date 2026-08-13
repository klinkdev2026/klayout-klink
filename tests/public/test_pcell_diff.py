"""PUBLIC test: the byte-exact differential acceptance harness (klink
mechanism; canonical home ``klink.domains.structdevice.pcell_diff``).

Moved from tests/unit/test_ledit_bridge.py when the harness moved out of
the L-Edit bridge; the bridge re-export is covered there.
"""

from klink.domains.structdevice.pcell_diff import verify_differential


def test_verify_differential_byte_exact_and_mismatch():
    truth = lambda p: {"A": [[0, 0, int(p["W"] * 1000), 1000]]}
    good = lambda p: {"A": [[0, 0, int(p["W"] * 1000), 1000]]}
    off_by_one = lambda p: {"A": [[0, 0, int(p["W"] * 1000) + 1, 1000]]}

    r = verify_differential(good, truth, [{"W": 1}, {"W": 2.5}])
    assert r.all_ok and "ALL BYTE-EXACT" in r.summary()

    r = verify_differential(off_by_one, truth, [{"W": 1}])
    assert not r.all_ok
    assert "first diff" in r.summary()


def test_missing_and_extra_layers_are_diffs():
    truth = lambda p: {"A": [[0, 0, 10, 10]], "B": [[5, 5, 6, 6]]}
    drops_b = lambda p: {"A": [[0, 0, 10, 10]]}
    adds_c = lambda p: {"A": [[0, 0, 10, 10]], "B": [[5, 5, 6, 6]],
                        "C": [[1, 1, 2, 2]]}

    assert not verify_differential(drops_b, truth, [{}]).all_ok
    assert not verify_differential(adds_c, truth, [{}]).all_ok
