"""
End-to-end regression for the settings-form scroll anchor (offscreen Qt).

Reproduces the reported "jumps exactly one row" bug: with the Axis Control
(telemffb_controls_axes) tree expanded, the form is tall enough that restoring the
scroll after expanding another section (Ground) requires a non-zero offset. The old
restore measured the freshly built geometry before Qt had laid it out (a new row's
mapTo() reports y=0 until a later event-loop pass), computed a bad offset and clamped
-- so the anchored row landed ~one row low. The convergence restore waits until the
measured position is stable before committing.

This drives the REAL SettingsLayout renderer offscreen against the repo defaults.xml
with an empty userconfig. It self-skips if Qt has no platform, the heavy construction
fails, or the expected settings are not present (so a future defaults.xml refactor
degrades this to a skip rather than a spurious failure).
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets, QtCore


@pytest.fixture(scope="module")
def qapp():
    try:
        return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no Qt platform: {e}")


def _pump(app, n=20):
    for _ in range(n):
        app.processEvents()


@pytest.fixture
def rendered(qapp, tmp_path):
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
        G.settings_mgr = types.SimpleNamespace(
            current_sim='MSFS', current_aircraft_name='Cessna 172',
            current_class='PropellerAircraft', current_pattern='',
            offline_mode=False, timed_out=True, active_profile=None)
        from telemffb import xmlutils
        import telemffb.SettingsLayout as SLmod
        SLmod.HapticEffect = lambda *a, **k: types.SimpleNamespace()
        from telemffb.SettingsLayout import SettingsLayout
        from telemffb.custom_widgets import NoKeyScrollArea
        xmlutils.update_vars('joystick', G.userconfig_path, G.defaults_path)
        xmlutils.update_roots()
        cls, pat, data = xmlutils.read_single_model('MSFS', 'Cessna 172', 'joystick')
        G.settings_mgr.current_class = cls
        G.settings_mgr.current_pattern = pat
        mw = QtWidgets.QWidget()
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
        sl.expanded_items = []
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

    names = {sl.itemAt(i).widget().objectName()
             for i in range(sl.count()) if sl.itemAt(i).widget() is not None}
    if 'namelabel_telemffb_controls_axes' not in names or 'namelabel_ground_group' not in names:
        pytest.skip("expected settings (telemffb_controls_axes / ground_group) not in defaults.xml")
    return types.SimpleNamespace(app=qapp, sl=sl, area=area, content=content, data=data, mod=SLmod)


def _vy(r, name):
    for i in range(r.sl.count()):
        w = r.sl.itemAt(i).widget()
        if w is not None and w.objectName() == f'namelabel_{name}':
            return w.mapTo(r.area.viewport(), QtCore.QPoint(0, 0)).y()


def _cy(r, name):
    for i in range(r.sl.count()):
        w = r.sl.itemAt(i).widget()
        if w is not None and w.objectName() == f'namelabel_{name}':
            return w.mapTo(r.content, QtCore.QPoint(0, 0)).y()


def test_axis_control_then_ground_expand_stays_put(rendered, monkeypatch):
    r = rendered
    vbar = r.area.verticalScrollBar()

    def cursor_on(name):
        y = _vy(r, name)
        gp = r.area.viewport().mapToGlobal(QtCore.QPoint(20, (y + 10) if y is not None else -99))
        monkeypatch.setattr(r.mod.QCursor, "pos", staticmethod(lambda gp=gp: gp))

    def scroll_to(name, at=150):
        c = _cy(r, name)
        if c is not None:
            vbar.setValue(max(0, c - at))
            _pump(r.app)

    def expand(name, ref):
        cursor_on(ref)
        before = _vy(r, ref)
        if name not in r.sl.expanded_items:
            r.sl.expanded_items.append(name)
        r.sl.reload_layout(result=r.data)
        _pump(r.app, 25)
        return before, _vy(r, ref)

    # Expand Axis Control (this alone was always fine)...
    vbar.setValue(0)
    _pump(r.app)
    b_axis, a_axis = expand('telemffb_controls_axes', 'telemffb_controls_axes')
    assert b_axis is not None and a_axis is not None
    assert abs(a_axis - b_axis) <= 2

    # ...then scroll down and expand Ground -- this is where it jumped ~one row.
    scroll_to('ground_group', 150)
    before, after = expand('ground_group', 'ground_group')
    assert before is not None and after is not None
    assert abs(after - before) <= 2, f"ground anchor drifted {after - before}px (before={before}, after={after})"
