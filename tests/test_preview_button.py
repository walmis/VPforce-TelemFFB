"""The effect-preview play button on settings slider rows (offscreen Qt).

Drives the REAL SettingsLayout against the repo defaults.xml, the way the
scroll-anchor e2e test does, and checks the button's placement rules:

* offline editing only - no button at all otherwise
* only on rows a preview names (the button is the availability cue)
* disabled with the reason as tooltip when the main window reports a
  blocker; enabled and wired to the main window's toggle when not
* reads as a stop while its own preview plays

Self-skips if Qt has no platform or the layout cannot be built.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import types

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets


@pytest.fixture(scope="module")
def qapp():
    try:
        return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no Qt platform: {e}")


def _pump(app, n=10):
    for _ in range(n):
        app.processEvents()


class FakeMainWindow(QtWidgets.QWidget):
    """What the layout asks of the main window for the button."""

    def __init__(self):
        super().__init__()
        self.blockers = []
        self.running_spec = None
        self.running_devices = ['joystick']
        self.toggled = []

    def effect_preview_blockers(self):
        return list(self.blockers)

    def effect_preview_running(self, spec):
        return spec is self.running_spec

    def effect_preview_running_devices(self):
        return list(self.running_devices)

    def toggle_effect_preview(self, spec, button=None, devices=None):
        self.toggled.append((spec, button, devices))


def _render(qapp, tmp_path, *, offline, blockers=(), running=None,
            sim='DCS', model='F-16C_50', running_devices=('joystick',)):
    try:
        import telemffb.globals as G
        uc = tmp_path / "userconfig.xml"
        uc.write_text('<?xml version="1.0"?>\n<TelemFFB></TelemFFB>\n')
        G.userconfig_path = str(uc)
        G.defaults_path = os.path.abspath("defaults.xml")
        G.system_settings = {}
        G.device_type = 'joystick'
        G.master_instance = True
        G.current_device_config_scope = 'joystick'
        G.launched_instances = {}
        G.ipc_instance = None
        from telemffb.SettingsManager import SettingsManager
        G.settings_mgr = types.SimpleNamespace(
            current_sim=sim, current_aircraft_name=model,
            current_class='', current_pattern='',
            offline_mode=offline, offline_scope='MODEL', timed_out=True, active_profile=None,
            resolve_enum_list=lambda name, value='': (
                SettingsManager.resolve_enum_list(G.settings_mgr, name, value)),
            _tap_mode_offered=lambda: (
                SettingsManager._tap_mode_offered(G.settings_mgr)),
            TAP_SIM_KEYS=SettingsManager.TAP_SIM_KEYS)
        from telemffb import xmlutils
        import telemffb.SettingsLayout as SLmod
        SLmod.HapticEffect = lambda *a, **k: types.SimpleNamespace()
        from telemffb.SettingsLayout import SettingsLayout
        from telemffb.custom_widgets import NoKeyScrollArea
        xmlutils.update_vars('joystick', G.userconfig_path, G.defaults_path)
        xmlutils.update_roots()
        cls, pat, data = xmlutils.read_single_model(sim, model, '', 'joystick')
        G.settings_mgr.current_class = cls
        G.settings_mgr.current_pattern = pat
        mw = FakeMainWindow()
        mw.blockers = list(blockers)
        mw.running_spec = running
        mw.running_devices = list(running_devices)
        mwl = QtWidgets.QVBoxLayout(mw)
        area = NoKeyScrollArea()
        area.setWidgetResizable(True)
        mwl.addWidget(area)
        mw.settings_area = area
        content = QtWidgets.QWidget()
        sl = SettingsLayout.__new__(SettingsLayout)
        QtWidgets.QGridLayout.__init__(sl, content)
        sl.exclusive_list = []
        sl.parent_expander_dict = {}
        sl.revert_targets = {}
        sl.unit_previous_values = {}
        # expand every group so the intensity rows get built
        sl.expanded_items = [d['name'] for d in data if d.get('datatype') == 'group']
        sl.mainwindow = mw
        sl.device = types.SimpleNamespace()
        sl.trigger_form_reload = True
        area.setWidget(content)
        mw.resize(520, 640)
        mw.show()
        _pump(qapp)
        sl.build_rows(data)
        sl.activate()
        _pump(qapp)
    except Exception as e:  # pragma: no cover - environment/data dependent
        pytest.skip(f"could not build real SettingsLayout offscreen: {e}")
    buttons = {b.objectName()[3:]: b for b in content.findChildren(QtWidgets.QPushButton)
               if b.objectName().startswith('pv_')}
    all_buttons = {b.objectName()[6:]: b for b in content.findChildren(QtWidgets.QPushButton)
                   if b.objectName().startswith('pvall_')}
    pads = {w.objectName()[6:]: w for w in content.findChildren(QtWidgets.QWidget)
            if w.objectName().startswith('pvpad_')}
    all_pads = {w.objectName()[9:]: w for w in content.findChildren(QtWidgets.QWidget)
                if w.objectName().startswith('pvpadall_')}
    sliders = {w.objectName()[4:]: w for w in content.findChildren(QtWidgets.QSlider)
               if w.objectName().startswith('sld_')}
    return types.SimpleNamespace(mw=mw, buttons=buttons, all_buttons=all_buttons, pads=pads,
                                 all_pads=all_pads, sliders=sliders, sl=sl, content=content)


def test_no_buttons_or_pads_outside_offline_mode(qapp, tmp_path):
    r = _render(qapp, tmp_path, offline=False)
    assert r.buttons == {} and r.pads == {}


def test_rows_without_a_preview_get_a_matching_pad(qapp, tmp_path):
    """Every slider row is the same length: a button or a same-size pad."""
    from telemffb.SettingsLayout import PREVIEW_BUTTON_SIZE
    # a propeller model: its form mixes preview rows (rumble, buffet...)
    # with plain sliders (expo, droop moment) - the F-16's visible sliders
    # all happen to host previews now
    r = _render(qapp, tmp_path, offline=True, sim='MSFS', model='Cessna 172')
    if not r.buttons or not r.pads:
        pytest.skip("need both kinds of row rendered for this model")
    assert not (set(r.buttons) & set(r.pads))
    every_slider = {w.objectName().split('_', 1)[1]
                    for w in r.content.findChildren(QtWidgets.QSlider)}
    assert set(r.buttons) | set(r.pads) >= every_slider          # every slider row has one
    # and the second slot too: a play-all button or its pad, on every row
    assert set(r.all_buttons) | set(r.all_pads) >= every_slider
    for b in r.buttons.values():
        assert (b.width(), b.height()) == (PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE)
    for pad in r.pads.values():
        assert (pad.width(), pad.height()) == (PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE)


def test_playing_preview_paints_its_rows_handles_green(qapp, tmp_path):
    from telemffb.custom_widgets import vpf_purple
    from telemffb.preview import JET_ENGINE_RUMBLE, AFTERBURNER
    from telemffb.SettingsLayout import mark_preview_sliders, PREVIEW_ACTIVE_HANDLE
    r = _render(qapp, tmp_path, offline=True)
    jet, ab = 'jet_engine_rumble_intensity', 'afterburner_effect_intensity'
    if jet not in r.sliders or ab not in r.sliders:
        pytest.skip("jet rumble and afterburner intensity rows not both rendered for this model")
    mark_preview_sliders(r.content, JET_ENGINE_RUMBLE, True)
    assert r.sliders[jet].handle_color == PREVIEW_ACTIVE_HANDLE
    assert r.sliders[ab].handle_color == vpf_purple             # only its own rows
    mark_preview_sliders(r.content, JET_ENGINE_RUMBLE, False)
    assert r.sliders[jet].handle_color == vpf_purple
    mark_preview_sliders(r.content, AFTERBURNER, True)
    assert r.sliders[ab].handle_color == PREVIEW_ACTIVE_HANDLE


def test_buttons_only_on_rows_that_host_a_preview(qapp, tmp_path):
    from telemffb.preview import PREVIEWS_BY_ROW
    r = _render(qapp, tmp_path, offline=True)
    if 'jet_engine_rumble_intensity' not in r.sliders:
        pytest.skip("jet rumble intensity row not rendered for this model")
    assert 'jet_engine_rumble_intensity' in r.buttons
    assert set(r.buttons) <= set(PREVIEWS_BY_ROW)
    # and only previews offered on this sim
    assert all(PREVIEWS_BY_ROW[name].supports('DCS') for name in r.buttons)
    assert 'il2_weapon_release_intensity' not in r.buttons


def test_enabled_button_toggles_the_main_windows_preview(qapp, tmp_path):
    from telemffb.preview import PREVIEWS_BY_ROW
    r = _render(qapp, tmp_path, offline=True)
    name = 'jet_engine_rumble_intensity'
    if name not in r.buttons:
        pytest.skip("row not rendered for this model")
    b = r.buttons[name]
    assert b.isEnabled() and b.text() == '▶'
    assert 'Click again to stop' in b.toolTip()
    assert PREVIEWS_BY_ROW[name].reference in b.toolTip()     # says what it represents
    assert b.toolTip().startswith("<p")                       # rich text: Qt word-wraps it
    b.click()
    assert r.mw.toggled == [(PREVIEWS_BY_ROW[name], b, None)]


def test_play_all_only_where_two_or_more_running_devices_offer_the_row(qapp, tmp_path):
    """stall buffet intensity is offered on every device, touchdown on the
    joystick only: with joystick and pedals running the first gets a
    play-all naming both, the second gets a pad in that slot."""
    from telemffb.preview import PREVIEWS_BY_ROW
    r = _render(qapp, tmp_path, offline=True, sim='MSFS', model='Cessna 172',
                running_devices=('joystick', 'pedals'))
    buffet, touch = 'buffeting_intensity', 'touchdown_effect_max_force'
    if buffet not in r.buttons or touch not in r.buttons:
        pytest.skip("buffet and touchdown rows not both rendered for this model")
    assert buffet in r.all_buttons and buffet not in r.all_pads
    assert touch in r.all_pads and touch not in r.all_buttons
    b = r.all_buttons[buffet]
    assert b.text() == '▶▶'
    assert "Play on joystick and pedals together." in b.toolTip()
    assert b.toolTip().startswith("<p")                       # rich text: Qt word-wraps it
    b.click()
    assert r.mw.toggled == [(PREVIEWS_BY_ROW[buffet], b, ('joystick', 'pedals'))]


def test_no_play_all_with_a_single_running_device(qapp, tmp_path):
    r = _render(qapp, tmp_path, offline=True, sim='MSFS', model='Cessna 172',
                running_devices=('joystick',))
    assert r.all_buttons == {}
    assert set(r.all_pads) >= set(r.buttons)                  # every preview row padded instead


def test_blocked_button_is_disabled_with_the_reason(qapp, tmp_path):
    r = _render(qapp, tmp_path, offline=True, blockers=['no FFB device connected'])
    name = 'jet_engine_rumble_intensity'
    if name not in r.buttons:
        pytest.skip("row not rendered for this model")
    b = r.buttons[name]
    assert not b.isEnabled()
    assert 'no FFB device connected' in b.toolTip()


def test_the_playing_rows_button_reads_as_stop(qapp, tmp_path):
    from telemffb.preview import PREVIEWS_BY_ROW
    name = 'jet_engine_rumble_intensity'
    r = _render(qapp, tmp_path, offline=True, running=PREVIEWS_BY_ROW[name])
    if name not in r.buttons:
        pytest.skip("row not rendered for this model")
    assert r.buttons[name].text() == '■'
    others = [b for n, b in r.buttons.items() if n != name]
    assert all(b.text() == '▶' for b in others)
