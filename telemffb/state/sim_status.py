#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""SimStatusTracker: the app/sim status error-onset/hold/clear state
machine, moved off MainWindow (``on_update_telemetry``'s inline error
block, ``on_telemetry_timeout``, ``on_first_sim_frame`` and
``on_sim_exited``).

MainWindow used to own ``error_state`` / ``_error_last_seen`` /
``flagged_error_msgs`` / ``telemetry_timed_out`` directly and push
straight into ``header_panel.status_container`` and ``self.tray`` from
whichever of those four methods noticed a transition. This class owns
that bookkeeping instead, decides the same transitions, and reports the
result through ``AppState.set_sim_status`` - a plain (state, source,
message) value - rather than reaching into widgets itself. HeaderPanel
and TrayController each subscribe to ``sim_status_changed`` and apply it
(``telemffb.ui.panels.HeaderPanel.bind`` / ``telemffb.ui.tray.TrayController.bind``),
the same producer/subscriber split as the scope-status indicators.

The clock is injectable so the ``ERROR_CLEAR_HOLD_S`` hold window can be
tested without a real 3-second sleep.
"""

import logging
import time
from typing import Callable, Optional


class SimStatusTracker:
    #: How long the error status is held after the LAST error-bearing
    #: frame before it clears.  Wall-clock on purpose: child-instance
    #: errors reach the master over IPC on whichever frames catch them,
    #: and a frame-counted debounce shrinks with sim frame rate.
    ERROR_CLEAR_HOLD_S = 3.0

    def __init__(self, app_state, exception_tracker, clock: Callable[[], float] = time.monotonic):
        self._app_state = app_state
        self._exception_tracker = exception_tracker
        self._clock = clock
        self.error_state = False  # True='error' key found in telem_data, False=clean telem_data
        self._error_last_seen = 0.0  # monotonic time an 'error' key was last seen; the clear path holds ERROR_CLEAR_HOLD_S past it
        self.flagged_error_msgs = set()  # flag_error messages logged into the exception tracker; auto-removed from it when the error condition clears
        self.telemetry_timed_out = True

    def push_status(self, source: Optional[str], paused: bool = False,
                     error: bool = False, message: Optional[str] = None) -> None:
        """Report (source, paused, error, message) to AppState - the sole
        entry point that used to be MainWindow.update_sim_indicators. Kept
        public for callers outside the frame/timeout/exit lifecycle (e.g.
        DcsIpcThread's Ev=Start, which just wants to force "paused")."""
        if source is None:
            return
        state = 'error' if error else 'paused' if paused else 'running'
        # Called only on state transitions (error onset / clear / timeout,
        # or a caller-guarded first frame), so this is not on the
        # per-frame hot path.
        logging.info(f"App status indicator -> {state} (src={source})")
        self._app_state.set_sim_status(state, source, message)

    def on_first_frame(self, src: str) -> None:
        """first_frame_received: clear the initial 'Waiting' state by
        flipping the status to Running.

        Guarded against error_state: process_data emits telemetryReceived
        before first_frame_received, so when the very first frame is the
        one that raises a config error (common at startup), on_frame has
        already set the error indicator by the time this runs. Without
        this guard the unconditional flip to Running clobbers that error
        and, since error_state stays set, it is never re-asserted. The
        paused-in-menus case is unaffected (error_state is False there:
        Running here, then the telemetry timeout flips it to Paused).
        """
        if self.error_state:
            return
        self.push_status(src, paused=False)

    def on_timeout(self, src: Optional[str]) -> None:
        """telemetryTimeout: pause unless an error is already showing -
        an error condition takes priority over a plain timeout."""
        if not self.error_state:
            self.push_status(src, paused=True)
        self.telemetry_timed_out = True

    def on_sim_exited(self) -> None:
        """STATUS=EXIT: the next sim connection starts fresh."""
        self.telemetry_timed_out = False
        self._app_state.reset_sim_status()

    def on_frame(self, data: dict) -> None:
        """Per-frame error onset/hold/clear, from ``data['error']`` (absent
        or ``None`` when the frame is clean). Moved verbatim from
        ``MainWindow.on_update_telemetry``'s inline block."""
        error_cond = data.get('error', None)

        if error_cond is None:  # no 'error' key in telemetry
            if self.telemetry_timed_out or self.error_state:  # only set status to run if previously timed out or error status was true
                # Hold the error state for a wall-clock window after the
                # last sighting: a child instance's error arrives over
                # IPC on whichever master frames happen to catch it, so
                # error-free frames BETWEEN sightings are routine.  The
                # old debounce counted 5 frames, a window that shrank
                # with sim frame rate (~30ms at 150fps) - thread timing
                # alone could flap clear->onset, and every re-onset
                # popped the tray notification again.
                if self._clock() - self._error_last_seen >= self.ERROR_CLEAR_HOLD_S:
                    if self.error_state:
                        logging.info("App status error cleared by an error-free frame (hold window elapsed)")
                    self.push_status(data.get('src'), paused=False)
                    self.error_state = False
                    self.telemetry_timed_out = False
                    # The condition was rectified: drop the flag_error
                    # records this session logged into the exception
                    # tracker so it agrees with the (cleared) app status.
                    for msg in self.flagged_error_msgs:
                        self._exception_tracker.remove_matching(msg)
                    self.flagged_error_msgs.clear()
        else:
            self._error_last_seen = self._clock()
            if not self.error_state:  # only set error status once when there is error cond but state is not yet true
                self.push_status(data.get('src'), error=True, message=error_cond)
                logging.error(error_cond)
                self.flagged_error_msgs.add(error_cond)
                self.error_state = True
