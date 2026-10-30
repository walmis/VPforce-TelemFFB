"""SettingsLayout's render/enable gates on a button setting: a bound button
counts as set, and binding a gating button rebuilds the form.

The real SettingsLayout.__init__ reads XML and settings globals, so this
drives the unbound methods on a plain stand-in.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G
from telemffb.ui.widgets.SettingsLayout import SettingsLayout

pytestmark = pytest.mark.unit


def _rows(binding):
    return [{'name': 'release', 'displayname': 'Trim Release Button', 'datatype': 'button', 'value': binding,
             'is_visible': 'true'},
            {'name': 'deadzone', 'value': '0.05', 'render_prereq': '!release', 'is_visible': 'true'},
            {'name': 'damper', 'value': '0.2', 'enable_prereq': 'release', 'is_visible': 'true'}]


def _gated(binding):
    layout = SimpleNamespace()
    rows = {r['name']: r for r in SettingsLayout.apply_conditional_gates(layout, _rows(binding))}
    return layout, rows


@pytest.mark.parametrize("binding, bound", [("0", False), ("", False), ("7", True), ("3344:0001:2", True)])
def test_a_bound_button_counts_as_set(binding, bound):
    _, rows = _gated(binding)
    assert (rows['deadzone']['is_visible'] == 'true') is not bound
    assert rows['damper']['force_disabled'] is not bound


def test_the_disabled_reason_names_the_binding_state():
    _, rows = _gated("0")
    assert rows['damper']['disabled_reason'] == "Disabled because Trim Release Button is not bound"


@pytest.mark.parametrize("button, reload", [("release", True), ("other_button", False)])
def test_binding_rebuilds_the_form_only_for_a_gating_button(monkeypatch, button, reload):
    layout, _ = _gated("0")
    layout.mainwindow = MagicMock()
    layout._show_binding = lambda *args: None
    layout.write_or_revert = MagicMock()
    monkeypatch.setattr(G, "settings_mgr", SimpleNamespace(timed_out=False), raising=False)
    SettingsLayout.update_button(layout, button, "5")
    assert layout.trigger_form_reload is reload
