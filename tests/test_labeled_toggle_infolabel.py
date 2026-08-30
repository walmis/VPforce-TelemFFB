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


class TestTheHandleFollowsTheState:
    """The switch is drawn from two things: the track colour, which comes
    from isChecked(), and the handle position, which the slide animation
    drives.  They have to agree - a track drawn off with the handle
    sitting on reads as neither state (field report, 2026-08-29: the
    DirectInput toggle after the missing-DLL revert)."""

    def test_setting_it_off_moves_the_handle(self, app):
        from telemffb.custom_widgets import Toggle
        toggle = Toggle()
        toggle.setChecked(True)
        assert toggle.handle_position == 1.0
        toggle.setChecked(False)
        assert toggle.handle_position == 0.0

    def test_it_holds_with_signals_blocked(self, app):
        """How the settings dialog reverts the DirectInput toggle: the
        stateChanged slot never runs, so the handle has to be moved by
        setChecked itself."""
        from telemffb.custom_widgets import Toggle
        toggle = Toggle()
        toggle.setChecked(True)
        toggle.blockSignals(True)
        toggle.setChecked(False)
        toggle.blockSignals(False)
        assert toggle.isChecked() is False
        assert toggle.handle_position == 0.0

    def test_a_revert_during_the_users_own_click_lands_off(self, app):
        """The real sequence: the click slides the handle on, a modal
        runs while it does, and the handler then reverts."""
        from PyQt6 import QtCore
        from telemffb.custom_widgets import LabeledToggle
        widget = LabeledToggle(label="Enable DirectInput Devices")
        inner = widget.toggle

        def revert(_state):
            if widget.isChecked():
                for _ in range(20):        # the modal's nested loop
                    QtWidgets.QApplication.processEvents()
                    QtCore.QThread.msleep(5)
                widget.setChecked(False)
        widget.stateChanged.connect(revert)

        inner.nextCheckState()
        QtWidgets.QApplication.processEvents()     # the revert is deferred
        assert inner.isChecked() is False
        assert inner.handle_position == 0.0, "handle left in the on position"


class TestARefusalCanBeRepeated:
    """A slot that answers stateChanged by setting the state back must keep
    working on the second click, and the third.

    QCheckBox emits stateChanged only when the new state differs from the
    last state it published.  Setting it from inside that emission leaves
    those two out of step, and the next click then flips the switch with no
    signal at all - the track lights up, nothing is told, and the handle
    does not move.  Toggle defers a re-entrant set to keep them in step.
    """

    def _refusing(self):
        from telemffb.custom_widgets import LabeledToggle
        widget = LabeledToggle(label="Enable DirectInput Devices")
        seen = []

        def refuse(_state):
            seen.append(_state)
            if widget.isChecked():
                widget.setChecked(False)
        widget.stateChanged.connect(refuse)
        return widget, seen

    def test_every_click_is_answered(self, app):
        widget, seen = self._refusing()
        for attempt in range(1, 4):
            before = len(seen)
            widget.toggle.nextCheckState()
            QtWidgets.QApplication.processEvents()
            assert len(seen) > before, f"click {attempt} raised no signal"
            assert widget.isChecked() is False, \
                f"the switch stayed on after click {attempt}"
            assert widget.toggle.handle_position == 0.0, \
                f"handle left on after click {attempt}"

    def test_an_ordinary_set_is_not_deferred(self, app):
        """Only the re-entrant case waits; everything else stays immediate,
        because callers read the state straight back."""
        from telemffb.custom_widgets import Toggle
        toggle = Toggle()
        toggle.setChecked(True)
        assert toggle.isChecked() is True
        assert toggle.handle_position == 1.0
        toggle.setChecked(False)
        assert toggle.isChecked() is False
        assert toggle.handle_position == 0.0
