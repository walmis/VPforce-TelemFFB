import time


class CenterWalk:
    """Walks a spring center from where the control sits to a target, over a fixed time.

    A control left displaced over a load or a pause has to come back to its reference
    before its position can be sent.  Engaging the spring at the reference pulls it back
    with a force set by the whole displacement; starting the center at the control and
    moving the center instead brings it back at a rate set here, with the control never
    far from the center it is following.

    The target is read every step, so a reference that moves during the walk is followed.
    """

    def __init__(self, duration_s: float = 0.75):
        self.duration_s = duration_s
        self._start = None
        self._t0 = 0.0

    def reset(self):
        self._start = None

    def step(self, physical, target):
        """The center for this frame, per axis.  The first call after a reset captures
        ``physical`` as the starting point."""
        now = time.perf_counter()
        if self._start is None:
            self._start = tuple(physical)
            self._t0 = now
        t = min(max((now - self._t0) / self.duration_s, 0.0), 1.0) if self.duration_s > 0 else 1.0
        s = t * t * (3.0 - 2.0 * t)
        return tuple(a + (b - a) * s for a, b in zip(self._start, target))
