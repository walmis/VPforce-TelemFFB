"""Showing a config as it is beside how it would be.

The alignment is the part worth pinning: a line missing from one side has to
hold its place, or the two panes drift apart and a change appears on
different rows in each - which is worse than no comparison at all.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.TapDiffDialog import TapDiffDialog, aligned_diff, changed

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestLiningTheVersionsUp:
    def test_unchanged_lines_appear_on_both_sides(self):
        rows = aligned_diff("a\nb\n", "a\nb\n")
        assert rows == [("equal", "a", "a"), ("equal", "b", "b")]
        assert not changed(rows)

    def test_an_added_line_holds_a_blank_place_opposite(self):
        rows = aligned_diff("a\n", "a\nb\n")
        assert rows[-1] == ("insert", None, "b")

    def test_a_removed_line_does_the_same(self):
        rows = aligned_diff("a\nb\n", "a\n")
        assert rows[-1] == ("delete", "b", None)

    def test_a_changed_line_pairs_across(self):
        rows = aligned_diff("a\nold\n", "a\nnew\n")
        assert rows[-1] == ("replace", "old", "new")

    def test_uneven_blocks_stay_level(self):
        """Two lines replaced by three still reads straight across."""
        rows = aligned_diff("x\ny\n", "1\n2\n3\n")
        assert len(rows) == 3
        assert [r[1] for r in rows] == ["x", "y", None]
        assert [r[2] for r in rows] == ["1", "2", "3"]

    def test_every_row_carries_one_line_per_side(self):
        """What keeps the panes level: the row count is the height of both,
        so neither can end up shorter than the other."""
        rows = aligned_diff("a\nb\nc\nd\n", "a\nz\n")
        assert all(len(row) == 3 for row in rows)
        assert sum(1 for _, a, _ in rows if a is not None) == 4
        assert sum(1 for _, _, b in rows if b is not None) == 2


class TestTheDialog:
    CURRENT = "[FFBDevices]\nWarthog=block\nFFFF:2054=tap\n"
    PROPOSED = "[FFBDevices]\nWarthog=block\n045E:001B=tap\n"

    def panes(self, dialog):
        split = dialog.findChild(QtWidgets.QSplitter)
        return split.widget(0), split.widget(1)

    def test_current_is_left_and_proposed_is_right(self, app):
        dialog = TapDiffDialog("t", [("f", self.CURRENT, self.PROPOSED)])
        left, right = self.panes(dialog)
        assert "FFFF:2054=tap" in left.toPlainText()
        assert "045E:001B=tap" in right.toPlainText()

    def test_neither_pane_can_be_edited(self, app):
        """A second way to change a file TelemFFB is already changing."""
        dialog = TapDiffDialog("t", [("f", self.CURRENT, self.PROPOSED)])
        assert all(p.isReadOnly() for p in self.panes(dialog))

    def test_the_panes_scroll_together(self, app):
        long_a = "\n".join(f"line {i}" for i in range(60))
        dialog = TapDiffDialog("t", [("f", long_a, long_a + "\nextra\n")])
        dialog.resize(800, 200)
        dialog.show()
        app.processEvents()
        left, right = self.panes(dialog)
        left.verticalScrollBar().setValue(7)
        assert right.verticalScrollBar().value() == 7
        right.verticalScrollBar().setValue(2)
        assert left.verticalScrollBar().value() == 2

    def test_a_sim_with_two_configs_gets_two_comparisons(self, app):
        dialog = TapDiffDialog("t", [("bin", self.CURRENT, self.PROPOSED),
                                     ("bin-mt", self.CURRENT, self.CURRENT)])
        assert len(dialog.findChildren(QtWidgets.QSplitter)) == 2


class TestNothingToShow:
    def test_identical_files_say_so_instead_of_showing_two_panes(self, app):
        dialog = TapDiffDialog("t", [("f", "a\nb\n", "a\nb\n")])
        assert dialog.findChild(QtWidgets.QSplitter) is None
        assert any("No changes" in w.text()
                   for w in dialog.findChildren(QtWidgets.QLabel))

    def test_one_changed_file_among_several_still_shows_the_comparison(self, app):
        dialog = TapDiffDialog("t", [("a", "x\n", "x\n"), ("b", "y\n", "z\n")])
        assert dialog.findChild(QtWidgets.QSplitter) is not None
