"""
Functional tests for SettingsLayout's scroll-anchor restore (offscreen Qt).

When a settings edit triggers a form rebuild, the old behavior relied on Qt
implicitly retaining the scrollbar's *pixel* value across the rebuild. That drifts
by a few rows whenever the edit changes the height of content above the viewport
(a revealed sub-row, an expander, a greyed/disabled row, a bump merge...).

The fix anchors restoration to a *setting row* instead: before the rebuild it
records a row (the one under the mouse cursor, else the top-most visible row) and
its viewport offset; after the rebuild it scrolls that same row back to the same
offset. These tests exercise the real _capture_scroll_anchor / _restore_scroll_anchor
methods against live Qt geometry and prove the anchor row stays put even when a row
above it changes height.
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtGui import QCursor


@pytest.fixture(scope="module")
def qapp():
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as e:  # pragma: no cover - environment without a Qt platform
        pytest.skip(f"cannot create QApplication: {e}")
    return app


def _pump(app, n=3):
    for _ in range(n):
        app.processEvents()


def _build(app, rows=40, row_h=30, viewport=(400, 300)):
    """A real QScrollArea + grid of namelabel_ rows, with the actual methods under
    test bound onto the live grid layout."""
    from telemffb.SettingsLayout import SettingsLayout
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    grid = QtWidgets.QGridLayout()
    for m in ("_settings_scroll_area", "_capture_scroll_anchor", "_restore_scroll_anchor"):
        setattr(grid, m, types.MethodType(getattr(SettingsLayout, m), grid))
    grid.mainwindow = types.SimpleNamespace(settings_area=area)
    labels = {}
    for k in range(rows):
        lbl = QtWidgets.QLabel(f"setting {k}")
        lbl.setObjectName(f"namelabel_setting_{k}")
        lbl.setFixedHeight(row_h)
        grid.addWidget(lbl, k, 0)
        labels[f"setting_{k}"] = lbl
    content = QtWidgets.QWidget()
    content.setLayout(grid)
    area.setWidget(content)
    area.resize(*viewport)
    area.show()
    _pump(app)
    return area, grid, content, labels


def _viewport_y(area, w):
    return w.mapTo(area.viewport(), QtCore.QPoint(0, 0)).y()


def test_cursor_anchored_row_stays_put_when_row_above_grows(qapp, monkeypatch):
    area, grid, content, labels = _build(qapp)
    vbar = area.verticalScrollBar()
    assert vbar.maximum() > 0, "content should be scrollable"
    vbar.setValue(300)
    _pump(qapp)

    # Cursor sits over a row partway down the viewport -> that row is the anchor.
    global_pt = area.viewport().mapToGlobal(QtCore.QPoint(10, 120))
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: global_pt))
    anchor = grid._capture_scroll_anchor()
    assert anchor is not None
    name, _target_y = anchor
    before = _viewport_y(area, labels[name])

    # A row above the anchor grows by 100px -> everything below shifts down.
    labels["setting_5"].setFixedHeight(130)
    grid.activate()
    content.adjustSize()
    _pump(qapp)
    drifted = _viewport_y(area, labels[name])
    assert drifted - before >= 50, "sanity: without restore the anchor should have drifted"

    grid._restore_scroll_anchor(anchor)
    _pump(qapp)
    after = _viewport_y(area, labels[name])
    assert abs(after - before) <= 2, f"anchor not restored: before={before} after={after}"


def test_fallback_top_visible_row_preserved(qapp, monkeypatch):
    area, grid, content, labels = _build(qapp)
    area.verticalScrollBar().setValue(300)
    _pump(qapp)

    # Cursor is NOT over the form -> fall back to the top-most visible row.
    off = area.viewport().mapToGlobal(QtCore.QPoint(-50, -50))
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: off))
    anchor = grid._capture_scroll_anchor()
    assert anchor is not None
    name, target_y = anchor
    assert target_y >= 0, "fallback anchor should be a row visible in the viewport"
    before = _viewport_y(area, labels[name])

    labels["setting_2"].setFixedHeight(160)
    grid.activate()
    content.adjustSize()
    _pump(qapp)

    grid._restore_scroll_anchor(anchor)
    _pump(qapp)
    after = _viewport_y(area, labels[name])
    assert abs(after - before) <= 2, f"fallback anchor not restored: before={before} after={after}"


def test_missing_anchor_is_graceful_noop(qapp):
    area, grid, content, labels = _build(qapp)
    area.verticalScrollBar().setValue(300)
    _pump(qapp)
    before = area.verticalScrollBar().value()
    # Anchor a setting that no longer exists -> must not raise and must not scroll.
    grid._restore_scroll_anchor(("setting_does_not_exist", 100))
    _pump(qapp)
    assert area.verticalScrollBar().value() == before


def test_capture_returns_none_without_scroll_area(qapp):
    from telemffb.SettingsLayout import SettingsLayout
    grid = QtWidgets.QGridLayout()
    for m in ("_settings_scroll_area", "_capture_scroll_anchor"):
        setattr(grid, m, types.MethodType(getattr(SettingsLayout, m), grid))
    grid.mainwindow = None  # headless child / teardown
    assert grid._capture_scroll_anchor() is None
