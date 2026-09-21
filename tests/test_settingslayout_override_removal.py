"""Removing a class- or sim-level user override from a model's settings page
(SettingsLayout.do_remove_override, reached by right-clicking the override
icon).

The XML erasers underneath have tests of their own (test_xmlutils). What is
new here is the handler choosing between them and asking first - and it
deletes the user's settings, so picking the wrong scope, or acting on a "No",
would lose something silently.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QMessageBox

import telemffb.globals as G
from telemffb import xmlutils
from telemffb.ui.widgets import SettingsLayout as module
from telemffb.ui.widgets.SettingsLayout import SettingsLayout
from tests.test_xmlutils import xml_tmpdir  # noqa: F401  the userconfig with one entry at each level

pytestmark = pytest.mark.unit

CLASS_ENTRY = './/classSettings[sim="MSFS"][type="PropellerAircraft"][name="aoa_effect_gain"]'
SIM_ENTRY = './/simSettings[sim="MSFS"][name="global_gain"]'
MODEL_ENTRY = './/models[sim="MSFS"][name="aileron_expo"]'


def _present(xpath) -> bool:
    xmlutils.update_roots()
    return xmlutils.auto_user_root.find(xpath) is not None


def _remove(monkeypatch, setting, scope, answer=QMessageBox.StandardButton.Yes):
    """Call the handler as the right-click menu does, the prompt answered."""
    monkeypatch.setattr(module.QMessageBox, 'question', lambda *a, **k: answer)
    G.settings_mgr.timed_out = True
    layout = SimpleNamespace(trigger_form_reload=False, reload_caller=Mock())
    SettingsLayout.do_remove_override(layout, 'MSFS', 'PropellerAircraft', setting, scope)
    return layout


def test_a_class_override_is_removed_and_nothing_else(xml_tmpdir, monkeypatch):
    assert _present(CLASS_ENTRY)
    layout = _remove(monkeypatch, 'aoa_effect_gain', 'CLASS')
    assert not _present(CLASS_ENTRY)
    assert _present(SIM_ENTRY) and _present(MODEL_ENTRY)
    layout.reload_caller.assert_called_once()  # the page redraws with the value beneath


def test_a_sim_override_is_removed_and_nothing_else(xml_tmpdir, monkeypatch):
    assert _present(SIM_ENTRY)
    _remove(monkeypatch, 'global_gain', 'SIM')
    assert not _present(SIM_ENTRY)
    assert _present(CLASS_ENTRY) and _present(MODEL_ENTRY)


def test_the_scope_asked_for_is_the_only_one_touched(xml_tmpdir, monkeypatch):
    """The class-level setting, asked to be removed at the SIM level: there
    is nothing of that name there, and the class entry must survive it."""
    _remove(monkeypatch, 'aoa_effect_gain', 'SIM')
    assert _present(CLASS_ENTRY)


def test_answering_no_removes_nothing(xml_tmpdir, monkeypatch):
    layout = _remove(monkeypatch, 'aoa_effect_gain', 'CLASS', answer=QMessageBox.StandardButton.No)
    assert _present(CLASS_ENTRY)
    layout.reload_caller.assert_not_called()
