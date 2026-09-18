"""SettingsLayout's slider caches (_rebuild_slider_caches) - the lists that
replaced on_update_telemetry's per-frame findChildren() walk.

The caches are built by walking the layout tree, because:
- a QLayout doesn't own its widgets (addWidget() parents them to the
  layout's parent widget), so layout.findChildren() finds nothing, and
- clear_layout() only deleteLater()s the old rows, so parent.findChildren()
  in the same call stack as the rebuild would also return the doomed
  sliders ("wrapped C/C++ object has been deleted" on the next repaint).

The real SettingsLayout.__init__ reads XML and settings globals, so this
drives the unbound methods on a plain QGridLayout standing in for it.
"""
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QWidget

from telemffb.ui.widgets.SettingsLayout import SettingsLayout
from telemffb.ui.widgets.custom_widgets import NoWheelSlider, NoWheelNumberSlider

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stand_in_layout():
    host = QWidget()
    layout = QGridLayout(host)
    layout.mainwindow = SimpleNamespace(N_SLIDER_LIVE_KEYS={'max_elevator_coeff': '_pct_max_e'})
    layout.repaint_active_settings = lambda: None
    layout._iter_layout_widgets = SettingsLayout._iter_layout_widgets
    layout._clear_sub_layout = lambda sub: SettingsLayout._clear_sub_layout(layout, sub)
    return host, layout


def _add_rows(layout):
    """Rows shaped like build_rows(): each slider inside a per-row
    sub-layout, plus a non-slider widget directly in the grid."""
    widgets = {}
    for row, (cls, name) in enumerate([(NoWheelSlider, 'sld_spring_gain'),
                                       (NoWheelNumberSlider, 'sld_max_elevator_coeff'),
                                       (NoWheelNumberSlider, 'sld_other_coeff')]):
        w = cls()
        w.setObjectName(name)
        sl_layout = QHBoxLayout()
        sl_layout.addWidget(w)
        layout.addLayout(sl_layout, row, 1)
        layout.addWidget(QLabel(name), row, 0)
        widgets[name] = w
    return widgets


def test_caches_bucket_sliders_from_sub_layouts(qapp):
    host, layout = _stand_in_layout()
    w = _add_rows(layout)
    SettingsLayout._rebuild_slider_caches(layout)
    assert layout.live_key_sliders == [(w['sld_max_elevator_coeff'], '_pct_max_e')]
    assert set(layout._active_setting_sliders) == {w['sld_spring_gain'], w['sld_other_coeff']}


def test_rebuild_after_clear_skips_pending_deletion_sliders(qapp):
    host, layout = _stand_in_layout()
    old = _add_rows(layout)
    SettingsLayout.clear_layout(layout, show_empty_notice=False)  # deleteLater() only
    new = _add_rows(layout)
    SettingsLayout._rebuild_slider_caches(layout)
    cached = set(layout._active_setting_sliders) | {s for s, _ in layout.live_key_sliders}
    assert cached == set(new.values())
    assert not cached & set(old.values())
