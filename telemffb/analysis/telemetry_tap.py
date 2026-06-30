import logging
import time
from typing import Any, Dict, Optional

from telemffb.analysis.ring_buffer import RingBuffer, TelemetryFrame

log = logging.getLogger(__name__)


class TelemetryTap:
    """Lightweight telemetry tap placed in TelemManager.process_data().

    Captures fully-enriched BaseTelemetryData frames into a ring buffer
    after aircraft.on_telemetry() completes. Skips paused/stale frames.
    """

    def __init__(self, high_rate_size: int = 1800, low_rate_size: int = 6000, low_rate_decimate: int = 4):
        self.high_rate = RingBuffer(maxlen=high_rate_size)
        self.low_rate = RingBuffer(maxlen=low_rate_size)
        self._low_rate_counter = 0
        self._low_rate_decimate = low_rate_decimate
        self._enabled = True
        self._current_aircraft: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def capture(self, telem_data) -> None:
        """Capture a telemetry frame. Called from TelemManager thread.

        Args:
            telem_data: BaseTelemetryData instance (dict-like).
        """
        if not self._enabled:
            return

        # Skip paused frames
        if telem_data.get('SimPaused'):
            return

        ts = time.time()
        fields = telem_data.to_dict()

        frame = TelemetryFrame(ts, fields)

        # High-rate buffer: every frame
        self.high_rate.append(frame)

        # Low-rate buffer: every Nth frame
        self._low_rate_counter += 1
        if self._low_rate_counter >= self._low_rate_decimate:
            self._low_rate_counter = 0
            self.low_rate.append(frame)

    def on_aircraft_change(self, aircraft_name: Optional[str]) -> None:
        """Flush buffers when the aircraft changes."""
        if aircraft_name != self._current_aircraft:
            log.info(f"TelemetryTap: aircraft changed to '{aircraft_name}', flushing buffers")
            self._current_aircraft = aircraft_name
            self.high_rate.flush()
            self.low_rate.flush()
            self._low_rate_counter = 0

    def on_timeout(self) -> None:
        """Mark buffers as paused on telemetry timeout."""
        self.high_rate.mark_paused()
        self.low_rate.mark_paused()

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """Return the latest frame's fields, or None."""
        frame = self.high_rate.get_latest()
        if frame:
            return dict(frame.fields)
        return None
