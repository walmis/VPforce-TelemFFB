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

"""ChildTelemView: the master's end of a child instance's telemetry view.

When the master's config scope is a child's device, that child sends its
whole telemetry frame - not just the few keys merged into the master's own
(``IPCNetworkThread._ipc_telem``) - so the Monitor tab can show what the
child sees. The frames are state, not events: only the newest matters and a
missed one is replaced 50 ms later. So ``accept`` keeps the newest frame per
device *undecoded* and ``frame`` decodes it when the display asks, at most
once per frame - the IPC thread never parses a frame nobody looks at, and a
backlog drained in one pass costs one decode, not one per datagram.

Every frame carries the child's send counter, which is what ``accept`` counts
gaps from: a datagram the kernel dropped shows up as a jump in the sequence.
The numbers are the evidence for whether UDP is good enough for this, or the
view should move to shared memory - in which case only ``accept``'s caller
changes; the display reads ``frame`` and ``stats`` either way.

``accept`` runs on the IPC thread, ``frame``/``stats``/``end`` on the UI
thread. Each device's state is swapped in as a whole, so a reader never sees
half of an update.
"""

import json
import logging
import time
from dataclasses import dataclass, replace
from typing import Dict, Optional

# A frame older than this is not the child's view any more - the child has
# stopped, or has not answered the request yet - and the caller falls back.
STALE_SEC = 1.0


@dataclass(frozen=True)
class ViewStats:
    received: int = 0
    lost: int = 0
    rate: float = 0.0      # frames/s over the last full second


@dataclass(frozen=True)
class _View:
    seq: int = 0
    payload: str = ''
    arrived: float = 0.0
    stats: ViewStats = ViewStats()
    window_start: float = 0.0
    window_count: int = 0


class ChildTelemView:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._views: Dict[str, _View] = {}
        self._decoded = (None, 0, None)    # (device, seq, frame)

    def accept(self, device: str, seq: int, payload: str) -> None:
        """One frame off the wire: ``payload`` is its JSON, kept as text."""
        now = self._clock()
        # -1: the frame that opens the first window marks its start, as the
        # frame that closes a window marks the start of the next.
        view = self._views.get(device) or _View(window_start=now, window_count=-1)
        stats = view.stats
        if view.seq and seq > view.seq:
            stats = replace(stats, lost=stats.lost + seq - view.seq - 1)
        # seq <= the last one: the child restarted and is counting from the
        # top again. Nothing was lost, there is just no gap to measure.
        stats = replace(stats, received=stats.received + 1)

        window_start, window_count = view.window_start, view.window_count + 1
        if now - window_start >= 1.0:
            stats = replace(stats, rate=window_count / (now - window_start))
            window_start, window_count = now, 0

        self._views[device] = _View(seq, payload, now, stats, window_start, window_count)

    def frame(self, device: str) -> Optional[dict]:
        """The newest frame from ``device``, or None when there is none
        fresh enough to call its current view."""
        view = self._views.get(device)
        if view is None or self._clock() - view.arrived > STALE_SEC:
            return None
        if self._decoded[:2] != (device, view.seq):
            try:
                self._decoded = (device, view.seq, json.loads(view.payload))
            except json.JSONDecodeError:
                return None
        return self._decoded[2]

    def stats(self, device: str) -> ViewStats:
        view = self._views.get(device)
        if view is None:
            return ViewStats()
        if self._clock() - view.arrived > STALE_SEC:
            return replace(view.stats, rate=0.0)
        return view.stats

    def end(self, device: str) -> None:
        """The master stopped watching ``device``: put the tally in the log,
        and start the next watch from zero."""
        view = self._views.pop(device, None)
        if view is None or not view.stats.received:
            return
        stats = view.stats
        sent = stats.received + stats.lost
        logging.info(f"Telemetry view of {device}: {stats.received} frames received, "
                     f"{stats.lost} lost ({stats.lost / sent:.2%})")
