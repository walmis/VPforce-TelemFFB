"""The DirectInput Tap Monitor (Utilities menu).

A port of the wrapper repo's console viewer into a dialog, for remote
troubleshooting.  These tests drive the pure formatting/diff functions
with fake TapShm structures and the dialog with a stubbed reader -
nothing here touches the real mapping, a game, or a device.
"""

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


from telemffb.hw import ffb_tap
from telemffb import TapMonitorDialog as tm
from tests.test_ffb_tap import add_effect, make_shm


class TestRenderSnapshot:

    def test_no_writer_says_what_to_do(self):
        text = tm.render_snapshot(None, False, 0.0)
        assert "no tapped game is publishing" in text
        assert "dinput8.ini" in text

    def test_a_device_block_names_the_device_and_ids(self):
        shm = make_shm()
        text = tm.render_snapshot(shm, True, 0.1)
        assert "VPforce Rhino FFB Monster" in text
        assert "vid=FFFF pid=2054" in text

    def test_every_used_slot_gets_a_row(self):
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, magnitude=5000)
        add_effect(shm, 2, ffb_tap.ET_SINE, magnitude=3000)
        text = tm.render_snapshot(shm, True, 0.0)
        assert "Spring" in text and "Constant" in text and "Sine" in text

    def test_parameters_are_raw_directinput_units(self):
        """A wire-level tool: translating to +-4096 would hide exactly
        the discrepancies it exists to reveal."""
        shm = make_shm()
        add_effect(shm, 1, ffb_tap.ET_CONSTANT, magnitude=9999)
        assert "mag= +9999" in tm.render_snapshot(shm, True, 0.0)

    def test_a_stopped_effect_reads_stop(self):
        shm = make_shm(playing=0)
        assert "stop" in tm.render_snapshot(shm, True, 0.0)

    def test_a_paused_device_is_flagged(self):
        shm = make_shm(paused=1)
        assert "PAUSED" in tm.render_snapshot(shm, True, 0.0)

    def test_a_zero_filled_mapping_reads_as_no_game(self):
        """TelemFFB's reader creates the mapping when absent, so an
        all-zero view IS the no-publisher state - it must show the
        friendly waiting text, not an error about stamping."""
        shm = ffb_tap.TapShm()      # all zeroes: created but never written
        text = tm.render_snapshot(shm, False, 0.0)
        assert "no tapped game is publishing" in text

    def test_a_wrong_version_is_reported_not_hidden(self):
        shm = make_shm()
        shm.version = 99
        assert "version=99" in tm.render_snapshot(shm, False, 0.0)

    def test_a_dead_writer_is_called_out(self):
        text = tm.render_snapshot(make_shm(), False, 3.0)
        assert "EXITED" in text


class TestChangeDigest:

    def test_no_change_no_lines(self):
        a = tm.state_digest(make_shm())
        b = tm.state_digest(make_shm())
        assert a == b
        assert tm.describe_changes(a, b) == []

    def test_writer_appearing_and_leaving_are_events(self):
        d = tm.state_digest(make_shm())
        assert tm.describe_changes(None, d) == ["writer appeared"]
        assert tm.describe_changes(d, None) == ["writer gone"]

    def test_a_stop_is_one_line_naming_the_slot(self):
        before = tm.state_digest(make_shm(playing=1))
        after = tm.state_digest(make_shm(playing=0))
        lines = tm.describe_changes(before, after)
        assert any("STOP" in ln and "Spring" in ln for ln in lines)

    def test_zeroed_parameters_are_their_own_event(self):
        """The tap's signature failure: an effect left 'playing' but told
        to render nothing.  Stopping and zeroing must stay separable."""
        before = tm.state_digest(make_shm())
        after = tm.state_digest(make_shm(
            spring_kwargs=dict(offset=(0, 0), coef=(0, 0))))
        lines = tm.describe_changes(before, after)
        assert any("ZEROED" in ln and "still playing" in ln for ln in lines)

    def test_a_new_effect_slot_is_an_appearance(self):
        before = tm.state_digest(make_shm())
        shm = make_shm()
        add_effect(shm, 3, ffb_tap.ET_CONSTANT)
        lines = tm.describe_changes(before, tm.state_digest(shm))
        assert any("appeared" in ln for ln in lines)

    def test_a_device_reset_is_reported(self):
        before = tm.state_digest(make_shm())
        shm = make_shm()
        shm.devices[0].resetCount = 3
        lines = tm.describe_changes(before, tm.state_digest(shm))
        assert any("resets=3" in ln for ln in lines)


def set_gate_refusal(shm, count=1, vid=0xFFFF, pid=0x2054):
    shm.reserved[0] |= ffb_tap.TAP_DIAG_GATE_CLOSED
    shm.reserved[1] = count
    shm.reserved[2] = (vid << 16) | pid
    return shm


class TestGateRefusal:
    """Wrapper 0.9.3+ stamps WHY nothing was captured - the start-order
    trap, answered by the one party that was there at bind time."""

    def test_the_live_table_shouts_the_diagnosis(self):
        shm = set_gate_refusal(make_shm())
        text = tm.render_snapshot(shm, True, 0.0)
        assert "GAME STARTED BEFORE TelemFFB" in text
        assert "FFFF:2054" in text
        assert "Restart the game" in text

    def test_a_refusal_without_ids_still_diagnoses(self):
        shm = set_gate_refusal(make_shm(), vid=0, pid=0)
        text = tm.render_snapshot(shm, True, 0.0)
        assert "GAME STARTED BEFORE TelemFFB" in text

    def test_no_flag_no_diagnosis(self):
        assert "GAME STARTED" not in tm.render_snapshot(
            make_shm(), True, 0.0)

    def test_the_change_log_records_the_refusal_once(self):
        before = tm.state_digest(make_shm())
        after = tm.state_digest(set_gate_refusal(make_shm()))
        lines = tm.describe_changes(before, after)
        assert any("IGNORED at bind" in ln for ln in lines)
        # and an unchanged refusal is not re-logged
        assert tm.describe_changes(after, after) == []

    def test_a_refusal_that_predates_the_monitor_reaches_the_log(self):
        """The normal remote case: the wrapper stamped the refusal long
        before the monitor opened.  The saved log is the artifact support
        reads, so first sight must carry the diagnosis - while the other
        slots keep the one-line 'writer appeared' of a first sight."""
        lines = tm.describe_changes(None,
                                    tm.state_digest(set_gate_refusal(make_shm())))
        assert lines[0] == "writer appeared"
        assert any("IGNORED at bind" in ln for ln in lines)
        assert not any("appeared" in ln for ln in lines[1:])

    def test_a_second_refusal_is_a_new_event(self):
        a = tm.state_digest(set_gate_refusal(make_shm(), count=1))
        b = tm.state_digest(set_gate_refusal(make_shm(), count=2))
        assert any("refusals=2" in ln
                   for ln in tm.describe_changes(a, b))


class _StubReader:
    """Stands in for FfbTapReader: yields whatever the test queued."""

    def __init__(self):
        self.shm = None
        self.alive = True
        self.closed = False

    def snapshot(self):
        return self.shm

    def writer_alive(self, shm):
        return self.alive

    def close(self):
        self.closed = True


@pytest.fixture
def dialog(app, monkeypatch):
    monkeypatch.setattr(tm, "FfbTapReader", _StubReader)
    monkeypatch.setattr(tm, "_tick_ms", lambda: 0)
    dlg = tm.TapMonitorDialog()
    yield dlg
    dlg.close()


class TestDialog:

    def test_live_view_follows_the_snapshot(self, dialog):
        dialog._reader.shm = make_shm()
        dialog.poll()
        assert "VPforce Rhino FFB Monster" in dialog.live_view.toPlainText()

    def test_the_log_records_transitions_not_frames(self, dialog):
        dialog._reader.shm = make_shm()
        dialog.poll()
        dialog.poll()
        dialog.poll()
        log = dialog.log_view.toPlainText()
        assert log.count("writer appeared") == 1

    def test_a_stop_lands_in_the_log_with_a_timestamp(self, dialog):
        dialog._reader.shm = make_shm(playing=1)
        dialog.poll()
        dialog._reader.shm = make_shm(playing=0)
        dialog.poll()
        log = dialog.log_view.toPlainText()
        assert "STOP" in log
        assert "[" in log      # timestamped lines

    def test_no_writer_shows_the_waiting_status(self, dialog):
        dialog.poll()
        assert "waiting" in dialog.status_label.text()

    def test_save_log_writes_the_pane(self, dialog, tmp_path, monkeypatch):
        dialog._reader.shm = make_shm()
        dialog.poll()
        out = tmp_path / "log.txt"
        monkeypatch.setattr(tm.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        dialog.save_log()
        assert "writer appeared" in out.read_text(encoding="utf-8")

    def test_close_stops_polling_and_releases_the_reader(self, dialog):
        dialog.close()
        assert not dialog._timer.isActive()
        assert dialog._reader.closed

    def test_a_cancelled_save_writes_nothing(self, dialog, tmp_path,
                                             monkeypatch):
        monkeypatch.setattr(tm.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        dialog.save_log()          # must simply not raise
        assert list(tmp_path.iterdir()) == []
