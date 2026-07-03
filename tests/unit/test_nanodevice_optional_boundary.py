"""Optional-dependency boundary for klink.domains.nanodevice."""

from __future__ import annotations

import importlib.util

import pytest


NUMPY_PRESENT = importlib.util.find_spec("numpy") is not None
CV2_PRESENT = importlib.util.find_spec("cv2") is not None


def test_nanodevice_package_imports_without_calling_heavy_deps():
    import klink.domains.nanodevice as nd

    assert callable(nd.plan_writefields)
    assert callable(nd.build_hallbar)
    assert callable(nd.generate_wf_patches)


@pytest.mark.skipif(NUMPY_PRESENT, reason="numpy installed; missing-dep path not testable")
def test_load_np_names_install_command_when_missing():
    from klink.domains.nanodevice._deps import load_np

    with pytest.raises(RuntimeError) as excinfo:
        load_np()
    assert "pip install numpy" in str(excinfo.value)


@pytest.mark.skipif(CV2_PRESENT, reason="cv2 installed; missing-dep path not testable")
def test_load_cv_names_install_command_when_missing():
    from klink.domains.nanodevice._deps import load_cv

    with pytest.raises(RuntimeError) as excinfo:
        load_cv()
    assert "pip install opencv-python-headless" in str(excinfo.value)


def test_vendored_priors_are_loadable_with_stdlib_only():
    from klink.domains.nanodevice.flake import load_priors

    priors = load_priors("graphite_priors")
    assert isinstance(priors, dict)
    assert priors
