"""The layer_of contract, pinned after a real caller tripped on it.

`import_cell_tree(..., layer_of=build_layer_map(...)[0])` is the natural
reading of a docstring that said layer_of "maps a NAME to a pair" -- and it
died deep inside with `'dict' object is not callable`. A parameter whose
misuse surfaces as a TypeError from someone else's stack frame should refuse
at the door instead.
"""
from __future__ import annotations

import pytest

from klink.bridges.ledit import import_cell_tree


def test_a_mapping_is_refused_with_the_fix_named():
    with pytest.raises(TypeError) as exc:
        import_cell_tree(None, None, "TOP", layer_of={"M1": (1, 0)})
    msg = str(exc.value)
    assert "callable" in msg.lower()
    assert "mapping.get" in msg          # names what to pass instead
    assert "dict" in msg                 # names what was passed


def test_none_is_still_allowed():
    # None means "build one from the design's own table" -- it must get far
    # enough to touch the bridge rather than being rejected as a bad type.
    with pytest.raises(Exception) as exc:
        import_cell_tree(None, None, "TOP", layer_of=None)
    assert not isinstance(exc.value, TypeError) or \
        "callable" not in str(exc.value)
