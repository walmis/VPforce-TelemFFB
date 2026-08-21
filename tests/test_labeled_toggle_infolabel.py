"""A toggle that carries a tooltip has to advertise it.

Every checkbox in the settings dialogs is a LabeledToggle, and a fair number
of them explain themselves only through a tooltip.  A tooltip nothing points
at is a tooltip nobody hovers, so the label is an InfoLabel and the
information icon appears exactly when there is something to say.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.custom_widgets import InfoLabel, LabeledToggle

pytestmark = [pytest.mark.unit]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestInfoIcon:
    def test_no_tooltip_means_no_icon(self, app):
        toggle = LabeledToggle(label="Restore window position")
        assert isinstance(toggle.label, InfoLabel)
        assert not toggle.label.icon_label.toolTip()

    def test_a_tooltip_raises_the_icon(self, app):
        toggle = LabeledToggle(label="Prune Logs", tooltip="Auto delete archived logs")
        assert toggle.label.icon_label.toolTip() == "Auto delete archived logs"

    def test_tooltip_set_after_construction_reaches_the_icon(self, app):
        """The .ui files set tooltips on the widget after building it, which
        is the path most of the app's toggles take."""
        toggle = LabeledToggle(label="Validate DCS")
        toggle.setToolTip("Installs the export script")
        assert toggle.label.icon_label.toolTip() == "Installs the export script"

    def test_the_whole_row_answers_a_hover(self, app):
        """Hovering the switch or its text should explain it too, not only
        the icon."""
        toggle = LabeledToggle(label="Validate DCS", tooltip="Installs the export script")
        assert toggle.toggle.toolTip() == "Installs the export script"
        assert toggle.toolTip() == "Installs the export script"


class TestUnchangedBehavior:
    def test_setText_still_reaches_the_label(self, app):
        toggle = LabeledToggle(label="before")
        toggle.setText("after")
        assert toggle.label.text_label.text() == "after"

    def test_check_state_round_trips(self, app):
        toggle = LabeledToggle(label="x")
        seen = []
        toggle.stateChanged.connect(seen.append)
        toggle.setChecked(True)
        assert toggle.isChecked()
        assert seen, "stateChanged was not forwarded"
