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

#: Separates the configuration errors a frame carries in ``error``:
#: ``AircraftEffectUtilsBase.flag_error`` accumulates them and
#: ``SimStatusTracker.on_frame`` holds and expires each one.  A control
#: character rather than a newline, so a message may span several lines.
ERROR_SEPARATOR = "\x1e"


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
        # Each flag_error message on its own clock: {message: monotonic time
        # it was last present in a frame}. A message is logged into the
        # exception tracker when it first appears and removed from it when it
        # has been absent for ERROR_CLEAR_HOLD_S, so fixing one of several
        # config errors clears that one alone.
        self._error_seen: dict = {}
        self._shown_msg = None  # the message currently on the status indicator
        self.telemetry_timed_out = True

    @property
    def flagged_error_msgs(self) -> set:
        """The flag_error messages currently held (and present in the
        exception tracker)."""
        return set(self._error_seen)

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
        or ``None`` when the frame is clean).

        A frame carries EVERY config error the aircraft flagged, separated by
        ERROR_SEPARATOR (``AircraftEffectUtilsBase.flag_error`` accumulates), so
        each is held, logged and expired on its own clock. Fixing one of
        several therefore drops just that one - from the status indicator,
        which moves on to the next, and from the exception tracker - while
        the rest stay up. Tracking only the first message meant the second
        never reached the tracker and the first never cleared.
        """
        now = self._clock()
        error_cond = data.get('error', None)
        messages = [m.strip() for m in str(error_cond).split(ERROR_SEPARATOR)
                    if m.strip()] if error_cond else []

        for msg in messages:
            if msg not in self._error_seen:
                # The exception tracker's source is the logging handler.
                logging.error(msg)
            self._error_seen[msg] = now

        # Hold each message for a wall-clock window after its last sighting:
        # a child instance's error arrives over IPC on whichever master
        # frames happen to catch it, so frames without it BETWEEN sightings
        # are routine. The old debounce counted 5 frames, a window that
        # shrank with sim frame rate (~30ms at 150fps) - thread timing alone
        # could flap clear->onset, and every re-onset popped the tray
        # notification again.
        for msg in [m for m, seen in self._error_seen.items()
                    if now - seen >= self.ERROR_CLEAR_HOLD_S]:
            # Rectified: drop its exception-tracker record so the tracker
            # agrees with the status indicator.
            del self._error_seen[msg]
            self._exception_tracker.remove_matching(msg)

        if not self._error_seen:
            if self.telemetry_timed_out or self.error_state:  # only set status to run if previously timed out or error status was true
                if self.error_state:
                    logging.info("App status error cleared by an error-free frame (hold window elapsed)")
                self.push_status(data.get('src'), paused=False)
                self.error_state = False
                self._shown_msg = None
                self.telemetry_timed_out = False
            return

        # One at a time on the indicator, oldest first, so a message does not
        # jump around while several are outstanding.
        current = next(iter(self._error_seen))
        if current != self._shown_msg:
            self.push_status(data.get('src'), error=True, message=current)
            self._shown_msg = current
        self.error_state = True
