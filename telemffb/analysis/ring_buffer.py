import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional


class TelemetryFrame:
    """A single captured telemetry frame."""
    __slots__ = ('timestamp', 'fields')

    def __init__(self, timestamp: float, fields: Dict[str, Any]):
        self.timestamp = timestamp
        self.fields = fields


class RingBuffer:
    """Thread-safe bounded ring buffer for telemetry frames.

    Uses a deque with maxlen for O(1) bounded append. Reads acquire a lock
    to safely iterate while the TelemManager thread appends.
    """

    def __init__(self, maxlen: int = 1800):
        self._buf: deque[TelemetryFrame] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._last_valid_ts: Optional[float] = None
        self._paused = False

    @property
    def maxlen(self) -> int:
        return self._buf.maxlen

    def __len__(self) -> int:
        return len(self._buf)

    def append(self, frame: TelemetryFrame) -> None:
        """Append a frame. Called from TelemManager thread — GIL-safe."""
        self._buf.append(frame)
        self._last_valid_ts = frame.timestamp
        self._paused = False

    def flush(self) -> None:
        """Clear all frames. Called on aircraft change."""
        with self._lock:
            self._buf.clear()
            self._last_valid_ts = None

    def mark_paused(self) -> None:
        """Mark buffer as paused (sim paused or timeout)."""
        self._paused = True

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def last_valid_timestamp(self) -> Optional[float]:
        return self._last_valid_ts

    def get_latest(self) -> Optional[TelemetryFrame]:
        """Return the most recent frame, or None if empty."""
        with self._lock:
            if self._buf:
                return self._buf[-1]
            return None

    def get_window(
        self,
        seconds: float,
        signals: Optional[List[str]] = None,
        decimate: int = 1,
    ) -> List[TelemetryFrame]:
        """Return frames within the last `seconds` seconds.

        Args:
            seconds: How many seconds of history to return.
            signals: If provided, only include these field keys in the result.
            decimate: Keep every Nth frame (1 = no decimation).

        Returns:
            List of TelemetryFrame copies with filtered fields.
        """
        with self._lock:
            if not self._buf:
                return []

            cutoff = self._buf[-1].timestamp - seconds
            result = []
            count = 0

            for frame in self._buf:
                if frame.timestamp < cutoff:
                    continue
                count += 1
                if decimate > 1 and (count % decimate) != 1:
                    continue

                if signals:
                    filtered = {k: frame.fields[k] for k in signals if k in frame.fields}
                else:
                    filtered = dict(frame.fields)

                result.append(TelemetryFrame(frame.timestamp, filtered))

            return result

    def get_all_field_names(self) -> set:
        """Return the union of all field names across recent frames."""
        with self._lock:
            if not self._buf:
                return set()
            # Sample last frame for efficiency — fields are largely stable
            return set(self._buf[-1].fields.keys())
