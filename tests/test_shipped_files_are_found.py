"""The files TelemFFB ships are found by the code that looks for them, from a
source checkout - the real lookups, nothing stubbed.

Every tap test replaces ``bundled_wrapper`` with a stand-in, so when the tap
modules moved into ``telemffb/tap/`` and its file-relative path stopped
reaching the repo root, the whole suite stayed green while Install, Reinstall
and the update offer all failed from source. A lookup that walks up from its
own ``__file__`` records where that module sits in the package, and nothing
fails at import when the module moves - only here.

(Compiled builds use the executable's folder and the unpacked bundle instead,
and are not what this covers.)
"""
import os

import pytest

from telemffb.tap import tap_install
from telemffb.utils import get_resource_path

pytestmark = pytest.mark.unit

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_the_bundled_tap_wrapper_is_found():
    found = tap_install.bundled_wrapper()
    assert found is not None
    assert os.path.samefile(found, os.path.join(REPO_ROOT, "dll", "ffb_tap", tap_install.WRAPPER_NAME))


def test_the_bundled_tap_wrapper_has_a_version_to_compare_against():
    """With none, no installed wrapper is ever offered an update, silently."""
    assert tap_install.bundled_version()


@pytest.mark.parametrize("name", ["defaults.xml", "_RELEASE_NOTES.txt"])
def test_resources_at_the_repo_root_are_found(name):
    found = get_resource_path(name)
    assert os.path.isfile(found)
    assert os.path.samefile(found, os.path.join(REPO_ROOT, name))
