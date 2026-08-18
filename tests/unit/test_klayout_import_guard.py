"""A missing `klayout` must say what to install — including when an
empty directory is pretending to be it.

Every other optional dependency in the imaging domain already fails
instructively; klayout did not, and it is the one that is hardest to
diagnose. An empty `klayout` folder left on the path (a stale install,
a `py.typed`-only directory) is a valid NAMESPACE package: `import
klayout` succeeds and only `klayout.db` fails, so the bare error reads
"no module named klayout.db" — which says the package is installed and
merely incomplete, and sends the reader hunting in the wrong place.

A blind test on the published wheel lost real time to exactly this, and
`klink doctor` was fooled by the same shim once before.
"""
from __future__ import annotations

import builtins

import pytest

from klink.domains.imaging._util import (_klayout_missing_message,
                                         _namespace_shim_path, kdb)


class Boom(ValueError):
    pass


def _hide_klayout_db(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name == "klayout.db" or name.startswith("klayout.db."):
            raise ImportError("No module named 'klayout.db'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_the_error_names_the_interpreter_and_the_pip_command(monkeypatch):
    _hide_klayout_db(monkeypatch)
    with pytest.raises(Boom) as excinfo:
        kdb(Boom)
    msg = str(excinfo.value)
    assert "pip install klayout" in msg
    assert "THIS interpreter" in msg
    # the caller's own error type, so each exit reports in its own terms
    assert isinstance(excinfo.value, Boom)


def test_a_working_klayout_is_returned_untouched():
    pytest.importorskip("klayout.db")
    mod = kdb(Boom)
    assert hasattr(mod, "Layout")


def test_an_empty_namespace_shim_is_called_out(monkeypatch, tmp_path):
    """The hard case: `klayout` imports fine but has no db module."""
    shim = tmp_path / "klayout"
    shim.mkdir()
    (shim / "py.typed").write_text("", encoding="utf-8")

    class FakeNamespace:
        __path__ = [str(shim)]

    monkeypatch.setitem(__import__("sys").modules, "klayout",
                        FakeNamespace())
    assert _namespace_shim_path() == str(shim)

    msg = _klayout_missing_message()
    assert str(shim) in msg
    assert "namespace placeholder" in msg
    # and it explains the confusing part: why the error said klayout.db
    assert "klayout.db" in msg


def test_a_real_install_is_not_reported_as_a_shim():
    pytest.importorskip("klayout.db")
    assert _namespace_shim_path() is None
    assert "namespace placeholder" not in _klayout_missing_message()
