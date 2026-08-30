"""The DINPUT_TAP spring mode is only offered in a sim's spring-mode
dropdown when that sim's DirectInput Tap toggle is on in System Settings
(SettingsManager.resolve_enum_list, called from the SettingsLayout
enumlist builder).

IL-2 Great Battles and Korea are indistinguishable at the telemetry and
aircraft-profile level (both arrive as sim 'IL2'), so either IL2 tap
toggle offers the mode there.
"""
import pytest

import telemffb.globals as G
from telemffb.SettingsManager import SettingsManager, SpringModeEnum

pytestmark = [pytest.mark.unit]


class FakeSettings:
    def __init__(self, **flags):
        self.flags = flags

    def get(self, name, default=None, instance=None):
        return self.flags.get(name, default)


def make_mgr(sim, monkeypatch, **tap_flags):
    monkeypatch.setattr(G, "system_settings", FakeSettings(**tap_flags),
                        raising=False)
    mgr = SettingsManager()
    mgr.current_sim = sim
    return mgr


LIST = 'DCS_IL2_JOYSTICK_SPRING_MODE'


def test_hidden_when_tap_not_configured(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch)
    d = mgr.resolve_enum_list(LIST)
    assert SpringModeEnum.DINPUT_TAP not in d
    assert SpringModeEnum.NONE in d          # the rest of the list survives


def test_offered_when_sim_tap_enabled(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch, enableTapDCS=True)
    assert SpringModeEnum.DINPUT_TAP in mgr.resolve_enum_list(LIST)


def test_other_sims_toggle_does_not_offer(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch, enableTapBMS=True)
    assert SpringModeEnum.DINPUT_TAP not in mgr.resolve_enum_list(LIST)


def test_il2_offered_by_either_toggle(monkeypatch):
    for flag in ('enableTapIL2', 'enableTapIL2_K'):
        mgr = make_mgr('IL2', monkeypatch, **{flag: True})
        d = mgr.resolve_enum_list('IL2_JOYSTICK_SPRING_MODE')
        assert SpringModeEnum.DINPUT_TAP in d, flag


def test_il2_hidden_when_both_toggles_off(monkeypatch):
    mgr = make_mgr('IL2', monkeypatch, enableTapIL2=False,
                   enableTapIL2_K=False)
    d = mgr.resolve_enum_list('IL2_JOYSTICK_SPRING_MODE')
    assert SpringModeEnum.DINPUT_TAP not in d


def test_current_value_keeps_the_entry(monkeypatch):
    """A row already saved as DINPUT_TAP must keep the option so the
    existing selection displays and can be deselected - never the
    invalid-value fallback."""
    mgr = make_mgr('DCS', monkeypatch)
    d = mgr.resolve_enum_list(LIST, current_value='DINPUT_TAP')
    assert SpringModeEnum.DINPUT_TAP in d


def test_bms_gated_on_its_own_toggle(monkeypatch):
    assert SpringModeEnum.DINPUT_TAP in make_mgr(
        'BMS', monkeypatch, enableTapBMS=True).resolve_enum_list(LIST)
    assert SpringModeEnum.DINPUT_TAP not in make_mgr(
        'BMS', monkeypatch).resolve_enum_list(LIST)


def test_class_collection_not_mutated(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch)
    mgr.resolve_enum_list(LIST)
    assert SpringModeEnum.DINPUT_TAP in getattr(SettingsManager, LIST)


def test_lists_without_the_mode_pass_through_unchanged(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch)
    original = getattr(SettingsManager, 'DCS_IL2_G_EFFECT_MODE')
    assert mgr.resolve_enum_list('DCS_IL2_G_EFFECT_MODE') is original


def test_unknown_collection_returns_none(monkeypatch):
    mgr = make_mgr('DCS', monkeypatch)
    assert mgr.resolve_enum_list('NO_SUCH_COLLECTION') is None
