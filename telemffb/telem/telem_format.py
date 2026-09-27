#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
"""How telemetry and active effects read on a monitor: the desktop Monitor
tab and the in-sim panels show the same text, so it is produced here, once."""
from collections import OrderedDict

import telemffb.globals as G
from telemffb.hw.ffb_rhino import (EFFECT_CONSTANT, EFFECT_CUSTOM,
                                   EFFECT_DAMPER, EFFECT_DETENT,
                                   EFFECT_FRICTION, EFFECT_INERTIA,
                                   EFFECT_RAMP, EFFECT_SAWTOOTHDOWN,
                                   EFFECT_SAWTOOTHUP, EFFECT_SINE,
                                   EFFECT_SPRING, EFFECT_SPRING_ADJUSTER,
                                   EFFECT_SQUARE, EFFECT_TRIANGLE)

#: The keys a frame lists first, in this order; the rest follow alphabetically.
LEADING_KEYS = ('T', 'frameTimes', 'maxFrameTime', 'avgFrameTime', 'perf', 'FFBType',
                'N', 'src', 'msfs_vers', 'AircraftClass', 'SimconnectCategory')


#: Where the favorite keys live: one registry value, deliberately global
#: rather than instance-scoped (SystemSettings.setValue without an
#: `instance`), so starring a key on the joystick instance stars it for the
#: pedals and collective too - the interesting telemetry is a property of
#: the sim, not of the device watching it.  Telemetry keys carry no commas,
#: so one comma-separated value holds them all.
FAVORITES_KEY = 'monitorFavoriteKeys'


def load_favorites() -> set:
    """The starred telemetry keys."""
    raw = G.system_settings.get(FAVORITES_KEY, '')
    return {key.strip() for key in str(raw or '').split(',') if key.strip()}


def toggle_favorite(key: str) -> bool:
    """Star or unstar ``key``, and say whether it is now starred.  Re-read
    first: every instance and the in-sim panel share the one value, so
    writing back a copy read earlier would drop stars added since."""
    favorites = load_favorites()
    starred = key not in favorites
    if starred:
        favorites.add(key)
    else:
        favorites.discard(key)
    G.system_settings.setValue(FAVORITES_KEY, ','.join(sorted(favorites)))
    return starred


def ordered_telemetry(frame: dict) -> OrderedDict:
    """A frame as a monitor lists it: the identifying keys first, the rest
    alphabetical."""
    data = OrderedDict(sorted(frame.items()))
    for key in reversed(LEADING_KEYS):
        if key in data:
            data.move_to_end(key, last=False)
    return data


#: The order the active-effects list is grouped into, by PID effect type.
#: Periodics first - magnitude and shape both mean something for them -
#: then the forces that have a magnitude but no shape, then the conditions
#: with the spring-shaped ones ahead of the rest. It puts every effect that
#: reports a dash together at the foot of the list, so the intensity column
#: reads as one scale at a time instead of alternating down the page.
EFFECT_GROUPS = {
    EFFECT_SQUARE: 0, EFFECT_SINE: 0, EFFECT_TRIANGLE: 0,
    EFFECT_SAWTOOTHUP: 0, EFFECT_SAWTOOTHDOWN: 0,
    EFFECT_CONSTANT: 1, EFFECT_RAMP: 1,
    EFFECT_SPRING: 2, EFFECT_SPRING_ADJUSTER: 2, EFFECT_DETENT: 2,
    EFFECT_DAMPER: 3, EFFECT_INERTIA: 3, EFFECT_FRICTION: 3,
    EFFECT_CUSTOM: 3,
}
#: Anything unrecognised sorts last rather than silently joining a group.
UNGROUPED = max(EFFECT_GROUPS.values()) + 1


#: The badge beside an effect's name: periodic waveforms by the file each
#: is drawn from (image/wave-*.svg, and the panel's copies of them)...
BADGE_SHAPES = {
    EFFECT_SQUARE: "wave-square.svg",
    EFFECT_SINE: "wave-sine.svg",
    EFFECT_TRIANGLE: "wave-triangle.svg",
    EFFECT_SAWTOOTHUP: "wave-sawtooth-up.svg",
    EFFECT_SAWTOOTHDOWN: "wave-sawtooth-down.svg",
}

#: ...and everything else lettered. Distinct letters throughout, so Damper
#: and Detent cannot be read for one another; the hover carries the full
#: name, which is what the letter is a reminder of rather than a code to learn.
BADGE_LETTERS = {
    EFFECT_CONSTANT: "C",
    EFFECT_RAMP: "R",
    EFFECT_SPRING: "S",
    EFFECT_SPRING_ADJUSTER: "A",
    EFFECT_DETENT: "T",
    EFFECT_DAMPER: "D",
    EFFECT_INERTIA: "I",
    EFFECT_FRICTION: "F",
    EFFECT_CUSTOM: "X",
}


def grouped_effects(active_effects) -> list:
    """The active effects in display order: grouped by type, insertion order
    kept inside each group, so a surviving row never moves relative to the
    others."""
    return sorted(active_effects, key=lambda e: EFFECT_GROUPS.get(e.get('type'), UNGROUPED))


def axes_text(gains):
    """A condition's cell text: ``"X 50% Y 30%"``, a dash for an axis that
    was never written. Labelled rather than positional so a copied row still
    says which is which, and it is what IntensityBarDelegate splits on."""
    x, y = (list(gains) + [None, None])[:2]
    pct = lambda g: '-' if g is None else f"{round(g * 100)}%"
    return f"X {pct(x)} Y {pct(y)}"


#: Telemetry keys that can swing negative, keyed on the ORIGINAL telemetry
#: key rather than the debug simvar display name - the display name is
#: MSFS-only and only exists when Alt+D is toggled, so deciding sign off it
#: would make the same field flip formatting depending on a debug setting.
#: Casing matches BaseTelemetryData's own attribute names.
SIGNED_EXACT_KEYS = frozenset({
    'AoA', 'SideSlip', 'Pitch', 'Roll', 'G', 'Gaxil', 'VerticalSpeed',
    'Incidence', 'X', 'Y', 'MSL', 'AGL', 'TRIM_DELTA',
    # Control-input positions, named individually because "...Pos" is not
    # a signed suffix: the control axes run -1..1 about a neutral, but
    # CollectivePos runs 0..1 ("unlike the other control axes" - see
    # BaseTelemetryData), as do GearPos, NozzlePos and SpeedbrakePos.
    'ElevPos', 'AileronPos', 'RudderPos', 'TailRotorPos',
    'TailRotorPedalPos', 'YokeXLinearPos', 'YokeYPos',
    # Steering angle off centre, signed left/right, and named here
    # because its "Pct" spelling matches nothing else signed.
    'CenterSteerAnglePct',
})

#: Case-insensitive substrings that mark a key as signed by convention:
#: trims, deflections, positions, accelerations, velocities and the like
#: are offsets from a zero point rather than magnitudes, so they cross
#: zero routinely (this is what makes ACCs/VelWorld/StickXY jitter worst).
SIGNED_KEY_PATTERNS = (
    'trim', 'defl', 'acc', 'vel', 'wind', 'force', 'stick', 'joy',
    'phys_', 'sema', 'cp_xy', 'vib', 'target_', 'rot_',
)


def key_is_statically_signed(key: str) -> bool:
    """Layers 1-2 of the signed-key decision: an exact key known to go
    negative, or a name matching one of the signed-by-convention
    substrings (checked case-insensitively; the exact set above is not,
    since it is quoting BaseTelemetryData's own attribute spelling)."""
    if key in SIGNED_EXACT_KEYS:
        return True
    key_cf = key.lower()
    return any(pattern in key_cf for pattern in SIGNED_KEY_PATTERNS)


def value_is_negative(v) -> bool:
    """True when `v` itself, or any float element of it, is negative -
    layer 3's trigger for sticky-learning a key that layers 1-2 miss."""
    if isinstance(v, float):
        return v < 0
    if isinstance(v, list):
        return any(isinstance(x, float) and x < 0 for x in v)
    return False


def format_telemetry_value(v, signed: bool) -> str:
    """Renders one telemetry cell. A float - scalar, or a list's float
    elements - gets an explicit '+' when `signed` and non-negative, so its
    digits don't shift horizontally as the value crosses zero. Non-float list
    elements and ints are untouched either way."""
    if isinstance(v, float):
        return f"{v:+.3f}" if signed else f"{v:.3f}"
    if isinstance(v, list):
        return "[" + ", ".join(
            (f"{x:+.3f}" if signed else f"{x:.3f}") if isinstance(x, float)
            else str(x) if x is not None else "None"
            for x in v
        ) + "]"
    return str(v)


class SignedKeys:
    """Which keys render with an explicit sign: those signed by name, and any
    seen negative since the sim or aircraft last changed - a key one aircraft
    pushed negative must not stay signed for every aircraft after it."""

    def __init__(self):
        self._session = None
        self._keys = set()

    def is_signed(self, frame: dict, key: str, v) -> bool:
        session = (frame.get('src'), frame.get('N'))
        if session != self._session:
            self._session = session
            self._keys.clear()
        if key in self._keys:
            return True
        if key_is_statically_signed(key) or value_is_negative(v):
            self._keys.add(key)
            return True
        return False
