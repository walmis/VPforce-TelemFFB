"""Long plain tooltips are shown as rich text, which is what Qt wraps."""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtWidgets import QApplication, QLabel

from telemffb.ui.panels.MonitorTableModel import KeyValueTableModel
from telemffb.ui.widgets import TooltipWrapFilter as module

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def app():
    # held, or it is collected and widgets made after it crash
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shown(app, monkeypatch):
    texts = []
    monkeypatch.setattr(module.QToolTip, "showText", lambda pos, text, *a: texts.append(text))
    return texts


def hover(text):
    label = QLabel()
    label.setToolTip(text)
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(1, 1), QPoint(1, 1))
    return module.TooltipWrapFilter().eventFilter(label, event)


def test_a_long_plain_tooltip_is_shown_as_rich_text(shown):
    assert hover("x" * 70 + " & <more>\nsecond line")
    (text,) = shown
    assert Qt.mightBeRichText(text)
    assert "&amp;" in text and "&lt;more&gt;" in text and "<br>" in text


def test_a_short_one_is_left_to_qt(shown):
    assert not hover("Reset to default")
    assert shown == []


def test_one_already_rich_is_left_to_qt(shown):
    assert not hover("<qt>" + "x" * 90 + "</qt>")
    assert shown == []


def test_the_monitors_tooltips_wrap_too(app):
    """A view's tooltips come from its model, which the filter never sees."""
    model = KeyValueTableModel(["Active Effects", "Intensity"])
    long_tip = "No meaningful intensity: this is a condition effect, and the force it produces depends on stick position."
    model.set_rows([("ID:1 Spring", ("ID:1 Spring", "-"))], {"ID:1 Spring": (None, long_tip)})
    tip = model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)
    assert Qt.mightBeRichText(tip)
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == "ID:1 Spring"

