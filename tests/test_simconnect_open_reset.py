"""The SimConnect Open-event effect reset is best-effort.

It runs on the SimConnect reader thread the moment MSFS answers, which
can land mid-device-switch: the old device already released,
HapticEffect.device not yet repointed, and the reset writes into a
closed HID handle.  Letting that raise killed the reader thread - and
with it the sim's telemetry until the next listener restart (field
crash, 2026-08-28, a save that swapped the collective's device while
MSFS sat paused)."""
import pytest

import telemffb.globals as G

# importing SimConnectSock pulls in simconnect, which leaves a file
# handle open; the collector raises it as an unraisable warning against
# whichever test is running (same scoped filter as the gating tests)
pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class _DyingDevice:
    def reset_effects(self):
        raise OSError("hid_write/GetOverlappedResult: (0x000003E3) aborted")


class _Telem:
    def __init__(self):
        self.frames = []

    def submit_frame(self, frame):
        self.frames.append(frame)

    def set_simconnect(self, sc):
        pass


def _sock():
    from telemffb.telem.SimConnectSock import SimConnectSock
    sock = SimConnectSock.__new__(SimConnectSock)
    sock._telem = _Telem()
    return sock


def test_a_mid_switch_reset_failure_does_not_kill_the_thread(monkeypatch, caplog):
    from telemffb.hw.ffb_rhino import HapticEffect
    monkeypatch.setattr(HapticEffect, 'device', _DyingDevice(), raising=False)
    sock = _sock()
    sock.emit_event("Open")                     # must not raise
    assert sock._telem.frames                   # the event still went out
    assert any('mid-transition' in r.message for r in caplog.records)


def test_a_healthy_reset_still_runs(monkeypatch):
    from telemffb.hw.ffb_rhino import HapticEffect

    class Healthy:
        reset = False
        def reset_effects(self):
            self.reset = True
    device = Healthy()
    monkeypatch.setattr(HapticEffect, 'device', device, raising=False)
    _sock().emit_event("Open")
    assert device.reset
