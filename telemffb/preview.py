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
"""Effect preview: play one effect on the device with synthetic telemetry.

Answers "what does this effect feel like at the strength I have set"
without a sim running.  A preview drives ONE effect method on a fully
configured aircraft instance (see ``TelemManager.build_aircraft``) with a
short scripted sequence of telemetry frames, then releases everything it
created.

Why an effect method and not ``on_telemetry``: a sparse synthetic frame
through the whole loop misfires neighbours - a frame carrying only
``TAS = 0`` plays full elevator droop.  Calling the one method is the
isolation, and it costs nothing: every effect already takes a frame and
reads a handful of fields.

Why a sequence and not a frame: about a third of the effects fire on
CHANGE (``anything_has_changed``) or run through a high-pass filter, so a
constant frame produces silence.  Three stimulus kinds cover the catalog:

    hold   the same frame for the duration (rumble, buffet, shaker)
    ramp   a field swept start -> end over the duration (gear, flaps)
    edge   a field stepped before -> after at the midpoint (release, hit)

Reference values are resolved against the live instance, so a spec can
say "the RPM where this profile's rumble peaks" rather than a number,
and the preview tracks the user's tuning.

What is deliberately NOT previewable: the spring family (the curve is the
feature), anything closed-loop with the sim (trim following), and the
force-trim button state machines.  Those are status-view territory.

Safety: ``AircraftBase.__init__`` clears the SHARED effect dispenser and
the preview plays into the same device slots a live aircraft would, so a
preview must never overlap a sim session - ``preview_blockers`` is the
gate.  The runner is clock-agnostic (``step`` per frame) so the app can
drive it from a timer and tests from a loop.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import telemffb.globals as G
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

SIMS = ("DCS", "MSFS", "XPLANE", "IL2", "BMS")
KINDS = ("hold", "ramp", "edge")
FRAME_RATE_HZ = 30.0

# A field value in a spec is one of:
#   - a constant (number, list, str) used as-is
#   - a 2-tuple (a, b): for 'ramp' interpolated a -> b by progress, for
#     'edge' a before the midpoint and b after; for 'hold' a is used.
#     Either end may be the NAME of an aircraft attribute, resolved on
#     the instance, so a sweep can run between the profile's own
#     thresholds: ('engine_rumble_lowrpm', 'engine_rumble_highrpm')
#   - a callable (aircraft, progress) -> value, for anything the above
#     cannot express (a list that varies)
FieldValue = Any


@dataclass(frozen=True)
class PreviewSpec:
    """How to preview one effect.

    ``effect_id`` is the effect's enable toggle in defaults.xml; it names
    the preview and is the attribute forced on for the run.  ``method`` is
    the aircraft method called once per frame.  ``fields`` is keyed by sim
    name with ``'*'`` for every sim; a sim's entries are merged over the
    ``'*'`` entries, which is how one spec names ``EngRPM`` for most sims
    and ``EngPCT`` for X-Plane.
    """
    effect_id: str
    method: str
    kind: str
    fields: Dict[str, Dict[str, FieldValue]]
    duration: float = 3.0
    # Seconds the LAST frame is repeated before cleanup.  A one-shot fired
    # on the final scripted frame (the gear clunk at 1.0) would otherwise
    # be destroyed in the same step it was created and never felt; a
    # repeated final frame is also what the sim does when motion stops,
    # so change-driven effects wind down the way they do live.
    tail: float = 0.5
    # Seconds held at EACH end of a sweep before / after the moving part.
    # A sweep's ends are the two settings the user actually tunes (Low
    # RPM intensity, High RPM intensity); a stimulus that keeps moving
    # through them cannot be judged for "could I live with this".  The
    # holds are long enough to judge, the sweep between shows the
    # transition.  Zero for holds and edges.
    dwell: float = 0.0
    sims: Tuple[str, ...] = SIMS

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.effect_id}: unknown preview kind {self.kind!r}")
        unknown = set(self.fields) - set(SIMS) - {'*'}
        if unknown:
            raise ValueError(f"{self.effect_id}: fields keyed by unknown sim(s) {sorted(unknown)}")
        if self.dwell < 0 or (self.dwell and 2 * self.dwell >= self.duration):
            raise ValueError(f"{self.effect_id}: dwell {self.dwell}s x2 must fit inside "
                             f"the {self.duration}s duration")

    def supports(self, sim: str) -> bool:
        return sim in self.sims

    def stimulus_progress(self, progress: float) -> float:
        """Frame progress (0..1 over the whole run) -> stimulus progress:
        flat at 0 through the leading dwell, linear through the middle,
        flat at 1 through the trailing dwell."""
        if not self.dwell:
            return progress
        d = self.dwell / self.duration
        if progress <= d:
            return 0.0
        if progress >= 1.0 - d:
            return 1.0
        return (progress - d) / (1.0 - 2.0 * d)

    def resolve_fields(self, aircraft, sim: str, progress: float) -> Dict[str, Any]:
        """The telemetry fields for one frame at frame ``progress`` (0..1)."""
        if sim not in SIMS:
            raise ValueError(f"unknown sim {sim!r}")
        merged: Dict[str, FieldValue] = dict(self.fields.get('*', {}))
        merged.update(self.fields.get(sim, {}))
        progress = self.stimulus_progress(progress)
        return {name: self._resolve(value, aircraft, progress)
                for name, value in merged.items()}

    def _resolve(self, value: FieldValue, aircraft, progress: float):
        if callable(value):
            return value(aircraft, progress)
        if isinstance(value, tuple) and len(value) == 2:
            a, b = (self._endpoint(v, aircraft) for v in value)
            if self.kind == 'ramp':
                return a + (b - a) * progress
            if self.kind == 'edge':
                return a if progress < 0.5 else b
            return a
        return value

    @staticmethod
    def _endpoint(value, aircraft):
        """A pair endpoint: a number as-is, a string as the named aircraft
        attribute (a profile threshold)."""
        if isinstance(value, str):
            return getattr(aircraft, value)
        return value


class PreviewRunner:
    """Drive one ``PreviewSpec`` against an aircraft, one frame per ``step``.

    Mirrors what ``TelemManager`` does per frame - rotate ``_telem_data``
    into ``_last_telem_data``, bind the new frame, call into the aircraft -
    so change detection and per-frame state inside the effect behave as
    they do live.  ``finish`` releases every effect the run created and is
    idempotent; ``step`` calls it after the last frame.
    """

    def __init__(self, aircraft, spec: PreviewSpec, sim: str,
                 device_type: Optional[str] = None,
                 frame_rate: float = FRAME_RATE_HZ,
                 force_enable: bool = True):
        if not spec.supports(sim):
            raise ValueError(f"{spec.effect_id} is not previewable on {sim}")
        if not hasattr(aircraft, spec.method):
            raise ValueError(f"{type(aircraft).__name__} has no {spec.method}")
        self.aircraft = aircraft
        self.spec = spec
        self.sim = sim
        self.device_type = device_type or G.device_type
        self.frame_rate = frame_rate
        self.frames_total = max(2, round(spec.duration * frame_rate))   # scripted frames
        self.tail_frames = max(0, round(spec.tail * frame_rate))          # last frame repeated
        self.steps_total = self.frames_total + self.tail_frames
        self.frame_index = 0
        self.finished = False
        if force_enable:
            # The user asked to feel it; a disabled toggle would only make
            # the method dispose its slots and return.  The instance is a
            # throwaway, so nothing to restore.
            setattr(aircraft, spec.effect_id, True)

    @property
    def period(self) -> float:
        return 1.0 / self.frame_rate

    @property
    def progress(self) -> float:
        """0.0 on the first frame, 1.0 on the last scripted frame and
        throughout the tail."""
        return min(1.0, self.frame_index / (self.frames_total - 1))

    def build_frame(self, progress: float) -> BaseTelemetryData:
        frame = BaseTelemetryData()
        frame['src'] = self.sim
        frame['N'] = getattr(self.aircraft, '_name', 'preview')
        frame['FFBType'] = self.device_type
        for name, value in self.spec.resolve_fields(self.aircraft, self.sim, progress).items():
            frame[name] = value
        return frame

    def step(self) -> bool:
        """Play one frame.  Returns True while more frames remain."""
        if self.finished:
            return False
        frame = self.build_frame(self.progress)
        ac = self.aircraft
        ac._last_telem_data = ac._telem_data.copy()
        ac._telem_data = frame
        try:
            getattr(ac, self.spec.method)(frame)
        except Exception:
            logging.exception(f"Preview {self.spec.effect_id}: effect method raised; stopping")
            self.finish()
            return False
        self.frame_index += 1
        if self.frame_index >= self.steps_total:
            self.finish()
        return not self.finished

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        # Destroy, not stop: nothing outlives a preview.  Dispenser.clear
        # frees every effect block the run allocated on the device.
        try:
            self.aircraft.effects.clear()
        except Exception:
            logging.exception(f"Preview {self.spec.effect_id}: cleanup failed")

    def run(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Blocking playback at ``frame_rate`` - for scripts and bench checks."""
        while self.step():
            sleep(self.period)


def resolve_preview_target(settings_mgr, default_sim: str = 'DCS',
                           default_model: str = 'Preview') -> Tuple[str, str]:
    """The (sim, model) a preview should build its aircraft for.

    The settings tab's current selection when it names a real sim (the
    user is looking at a model, so they get that model's tuning);
    otherwise a generic aircraft on ``default_sim``, whose sim-level
    defaults are what a fresh install would feel.
    """
    sim = getattr(settings_mgr, 'current_sim', None)
    model = getattr(settings_mgr, 'current_aircraft_name', None)
    if sim not in SIMS:
        return default_sim, default_model
    return sim, (model or default_model)


class TimedPreview:
    """Drive a ``PreviewRunner`` from the Qt event loop at its frame rate.

    The runner is clock-agnostic; this is the app-side clock.  ``stop``
    ends a run early and still frees the effects.  ``on_finished`` fires
    exactly once, whether the run completed or was stopped.
    """

    def __init__(self, runner: PreviewRunner, on_finished: Optional[Callable[[], None]] = None):
        from PyQt6.QtCore import QTimer, Qt
        self.runner = runner
        self.on_finished = on_finished
        self._notified = False
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, round(1000.0 / runner.frame_rate)))
        self._timer.timeout.connect(self._tick)

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.runner.finish()
        self._notify()

    def _tick(self) -> None:
        if not self.runner.step():
            self._timer.stop()
            self._notify()

    def _notify(self) -> None:
        if self._notified:
            return
        self._notified = True
        if self.on_finished is not None:
            self.on_finished()


def preview_blockers(current_aircraft=None, device_alive: bool = True) -> List[str]:
    """Reasons a preview must not run now; empty means go.

    A live aircraft owns effect slots on the device and its constructor
    would be re-run by ``build_aircraft`` (clearing them); a dead device
    has nowhere to play.  Pure so it is testable; the caller passes
    ``G.telem_manager.currentAircraft`` and ``HapticEffect.device_alive()``.
    """
    reasons = []
    if current_aircraft is not None:
        reasons.append("a sim session is active - previews run only with no aircraft loaded")
    if not device_alive:
        reasons.append("no FFB device connected")
    return reasons


# ---------------------------------------------------------------------------
# Specs.  Two to prove the two stimulus kinds end to end; the catalog grows
# here until it moves to defaults.xml.
# ---------------------------------------------------------------------------

JET_ENGINE_RUMBLE = PreviewSpec(
    effect_id='engine_jet_rumble_enabled',
    method='ac_update_jet_engine_rumble',
    kind='hold',
    # 100% RPM is the full-scale point: intensity = jet_engine_rumble_intensity * rpm/100
    fields={'*': {'EngRPM': 100},
            'XPLANE': {'EngPCT': 100}},
    tail=0.0,   # a hold has nothing to settle
)

GEAR_MOTION = PreviewSpec(
    effect_id='gear_motion_effect_enabled',
    method='ac_update_landing_gear',
    kind='ramp',
    # gear_value swept up -> down keeps the motion effect alive (it plays
    # while the value keeps changing) and lands exactly on 1.0, where the
    # clunk fires.  IAS = 0 keeps the gear-down buffet out of the picture.
    fields={'*': {'gear_value': (0.0, 1.0), 'IAS': 0.0},
            'MSFS': {'RetractableGear': 1, 'Gear': lambda ac, p: [p]},
            'XPLANE': {'RetractableGear': 1, 'Gear': lambda ac, p: [p]}},
)

_PROP_RPM_SWEEP = ('engine_rumble_lowrpm', 'engine_rumble_highrpm')

PROP_ENGINE_RUMBLE = PreviewSpec(
    effect_id='engine_prop_rumble_enabled',
    method='ac_update_piston_engine_rumble',
    kind='ramp',
    # The effect is a TAPER, not a level: intensity falls from the Low RPM
    # setting to the High RPM setting while the frequency climbs with RPM,
    # so no single point represents it.  Sweep the profile's own range and
    # the whole taper is felt in one press - idle chunk, rising pitch,
    # settling to the cruise hum - dwelling at each end long enough to
    # judge the two intensities the user tunes.  Each sim names its RPM
    # field differently.
    fields={'DCS': {'ActualRPM': _PROP_RPM_SWEEP},
            'MSFS': {'PropRPM': _PROP_RPM_SWEEP},
            'XPLANE': {'PropRPM': _PROP_RPM_SWEEP},
            'IL2': {'RPM': _PROP_RPM_SWEEP}},
    duration=14.0,   # 4 s at Low RPM, 6 s sweep, 4 s at High RPM
    dwell=4.0,
    tail=0.0,
    # BMS is not handled by the effect (it reads no BMS RPM field)
    sims=('DCS', 'MSFS', 'XPLANE', 'IL2'),
)

PREVIEW_SPECS: Dict[str, PreviewSpec] = {
    spec.effect_id: spec for spec in (PROP_ENGINE_RUMBLE, JET_ENGINE_RUMBLE, GEAR_MOTION)
}
