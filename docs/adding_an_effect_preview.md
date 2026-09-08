# Adding a Preview for a New Effect

An *effect preview* plays one effect on the FFB device with synthetic telemetry, so a user
can feel "what does this effect feel like at the strength I have set" without a sim running.
In the settings form (offline editing) it is the `▶` button after the `-`/`+` pair on an
intensity slider; in the Debug menu (Alt+D → Preview Effect) it is one entry per preview.

Everything lives in [`telemffb/preview.py`](../telemffb/preview.py). A preview is a
`PreviewSpec` in the catalog at the bottom of that file. Adding one is a data entry plus a
test — no XML, no UI work. The button appears on the rows the spec names as soon as the spec
is registered.

This guide is the checklist for writing that entry for an effect you have just built.

## 1. Decide whether the effect is previewable

The rule: **periodic effects, plus the constant-force effects a user can judge on the bench**.

A preview shows the *shape* of an effect only when that shape is a one-dimensional sweep, and
it shows *intensity* only when the user can judge it with the controls in hand. Vibrations and
motions qualify. Touchdown, deceleration and runway rumble qualify too, with the guards in
[Constant-force effects](#constant-force-effects) below. These do not, and should not get a
spec:

- **G-force**. In its newer mode the magnitude depends on the deflection the pilot holds
  under load; hands-off on the bench answers the wrong question. Elevator droop on MSFS /
  X-Plane is inside the flight-controls calculation and not callable on its own.
- **The spring family** — static/dynamic/custom/advanced spring modes, FBW spring, IL-2
  native spring. The curve *is* the feature.
- **Closed loops with the sim** — trim following, the DCS stick trim workaround, trimwheel.
  Open-loop replay is meaningless.
- **Button state machines** — force trim, hardware force trim, controls lock.
- **Effects computed from real aircraft data** the preview cannot synthesise — IL-2's dynamic
  gunfire mode, for one. Preview the basic path and switch the mode off for the run.

If the effect is a periodic and its intensity is one number (or a taper between two), it is
previewable.

## 2. Read the effect method before writing anything

A preview calls the effect's own method once per frame with a synthetic `BaseTelemetryData`
frame. It never goes through `on_telemetry` — a sparse frame through the whole loop misfires
neighbours (a frame carrying only `TAS = 0` plays full elevator droop). So the spec has to
know exactly what the one method reads and what it needs to fire. Open the method and note:

| Look for | Why it matters |
|---|---|
| The enable toggle it checks (`if not self.x_enabled: dispose; return`) | That toggle is the spec's `effect_id`; the runner forces it on for the throwaway aircraft. |
| Parent gates and mode switches (`il2_shake_master`, `il2_dynamic_gunfire_mode`) | Go in `force_attrs`. |
| The telemetry fields it reads, **per sim** (`ActualRPM` on DCS, `PropRPM` on MSFS/X-Plane, `RPM` on IL-2) | Keyed per sim in `fields`. Check `_sim_is_*` branches. |
| Extra arguments the live loop passes (`ac_calc_etl_effect(telem, blade_ct=...)`) | Go in `kwargs`. Without them the effect may fall back to a hard-coded guess. |
| Whether it fires on **level** or on **change** (`anything_has_changed(...)`) | Decides hold vs ramp/edge (section 4). |
| Gates that would silence it: on-ground checks, airspeed thresholds, `AircraftClass` checks | The frame must satisfy them. |
| Whether a different method handles it per sim (DCS vs MSFS stick shaker) | `method` can be a sim-keyed dict. |
| Whether a sim overrides the method with something else entirely (IL-2 overrides `ac_update_buffeting`) | Leave that sim out of `sims`. |
| The slot names it plays (`self.effects["..."]`) and their parameters | What the test asserts on. |

Two things the runner does for you: it rotates `_last_telem_data` / `_telem_data` the way the
live loop does (so change detection works across frames), and it sets `src`, `N` and
`FFBType` on every frame (so `_sim_is_*` and `is_joystick()` work).

## 3. Write the spec

```python
FLAPS_MOTION = PreviewSpec(
    effect_id='flaps_motion_effect_enabled',   # the toggle the method checks
    rows=('flaps_motion_intensity',),          # slider row(s) that host the ▶ button
    method='ac_update_flaps',                  # called once per frame
    kind='ramp',                               # hold | ramp | edge (a label; the stimulus is the fields)
    fields={'*': {'Flaps': (0.0, 1.0)}},       # sim-keyed; '*' for every sim
)
```

Field by field:

- **`effect_id`** — the enable toggle. Forced on for the run, so a user can feel an effect they
  have not enabled yet.
- **`name`** — registry key and menu label. Defaults to `effect_id`; set it only when one
  toggle covers several separately tuned effects (IL-2's gun / bomb / rocket are three specs
  sharing `il2_enable_weapons`).
- **`rows`** — the settings rows that get the `▶` button. Always the **intensity** slider(s)
  the user adjusts while listening — never the toggle, never a threshold. A row belongs to one
  spec only; a test enforces both, and that every row exists in `defaults.xml`.
- **`reference`** — one sentence fragment saying what condition the preview represents
  ("moderate turbulence: a few m/s of vertical and lateral gusts"). The row tooltip and the
  constant-force popup slot it into a fixed template, so write it to follow "This preview
  plays …". Required; a test checks every catalog spec has one.
- **`method`** — a name, or `{'*': 'generic', 'MSFS': 'msfs_specific'}`.
- **`sims`** — restrict when a sim has no field for it, overrides the method, or the effect
  logs "unknown sim" there. Default is all five.
- **`fields`** — `{'*': {...}, 'XPLANE': {...}}`; a sim's entries merge over `'*'`.
- **`duration`**, **`tail`**, **`dwell`**, **`schedule`** — the timing, section 4.
- **`kwargs`** — extra keyword arguments for the method, resolved per frame like fields.
- **`force_attrs`** — attributes set on the throwaway aircraft alongside the toggle.

### Field values

A value in `fields` (or `kwargs`) may be:

| Form | Meaning |
|---|---|
| a constant | used as-is every frame |
| a pair `(a, b)` | `ramp`: interpolated `a → b` over stimulus progress; `edge`: `a` before the midpoint, `b` after; `hold`: `a` |
| a pair whose end is a **string** | the named aircraft attribute — `('engine_rumble_lowrpm', 'engine_rumble_highrpm')` sweeps between the profile's own thresholds |
| `Attr('name')` | the named aircraft attribute, standalone |
| a callable `(aircraft, progress) -> value` | anything else: a list that varies, a threshold plus an offset |
| `steps(count, start, step)` | a counter that changes `count` times, evenly spaced — for change-driven effects |
| `RandomHits(...)` | a counter that steps at random moments, redrawn per run — for irregular streams (damage) |

**Reference values must be profile-relative wherever a threshold exists.** "The RPM where this
profile's rumble peaks", not `650`. That way the preview tracks the user's tuning. A constant is
right only when the effect has no threshold for it (a typical turbine idle, a nominal rotor RPM).

## 4. Choose the stimulus

The stimulus is decided by how the effect fires and what the user is tuning.

### Hold — an on/off effect with one intensity

Afterburner, stick shaker, overspeed, gear buffet. A constant frame at the effect's
**full-scale point** for `HOLD_SECONDS` (5 s), `tail=0.0`.

```python
fields={'*': {'gear_value': 1.0, 'IAS': Attr('gear_buffet_speed_high')}}
```

Find the full-scale point in the code: the value at which the effect's own scaling reaches 1.0
(the top of a speed band, 100% RPM, 15 m/s past an onset). The preview answers "what does 100%
of this feel like", which is the tuning question.

### Sweep — a taper the user tunes at both ends

Prop rumble, jet rumble. No single point represents a taper, so ramp across the profile's
range and **dwell** at each end long enough to judge the two intensities the user actually
sets. A stimulus that keeps moving through a setting cannot be judged for "could I live
with this".

```python
kind='ramp',
fields={'DCS': {'ActualRPM': ('engine_rumble_lowrpm', 'engine_rumble_highrpm')}, ...},
duration=14.0, dwell=4.0, tail=0.0,      # 4 s low, 6 s sweep, 4 s high
```

### Schedule — when the ends are silent

Stall buffet is zero at onset by definition, so a dwell there is four seconds of nothing. A
`schedule` is the general form of dwell: `(seconds, from, to)` segments the stimulus progress
follows in order. Hold at the end that carries the intensity, sweep the rest, fade out.

```python
schedule=((3.0, 0.0, 1.0), (4.0, 1.0, 1.0), (1.0, 1.0, 0.0)),   # onset sweep, hold at stall, recover
```

`duration` becomes the segments' sum. `dwell` and `schedule` are mutually exclusive.

**Transients are the exception.** ETL is a few seconds of shake as the aircraft accelerates
through a band; a hold at its peak felt wrong on the bench because it never sits there in
flight. For a transient, play the event at the pace it happens — one pass, no hold.

### Ramp — a surface that moves

Gear, flaps, canopy, hook. These fire only while the value keeps **changing**, so the field
sweeps its range over the default 3 s and the default `tail` (0.5 s) repeats the final
position, which lets the effect wind down as it does live and gives an endpoint clunk time to
play. Pick the direction that ends where the clunk is (canopy travels *closed*).

### Edge — a one-shot on a change

Gunfire, weapon release, countermeasures, damage. A single step would be one event, so feed a
**train** of changes:

```python
fields={'*': {'PayloadInfo': steps(3, start=4, step=-1), 'Gun': 0, ...}},   # three releases
fields={'*': {'Gun': lambda ac, p: p, ...}},                                # changes every frame: a burst
fields={'*': {'Damage': RandomHits(), ...}},                                # irregular stream
```

Hold the *other* triggers of a shared method constant so only the previewed effect fires.

### Constant-force effects

A constant force on an unattended axis can drive it to the stops, and the user tunes these
forces against **no spring** by preference (a Rhino may still be holding its own centering
spring; a DirectInput device is not). So a constant-force spec sets `constant_force=True`,
which does two things:

- the UI asks the user to take hold of the controls before every run (the message box in
  `MainWindow.confirm_constant_force_preview`), and the row's tooltip says so;
- the runner puts up a **5% reference spring** (`REFERENCE_SPRING`) in the preview's own
  effect table for the run. It is not there to counter the force — only to take the odd
  freewheel feel off DirectInput devices that misbehave at 0% spring.

And the stimulus is always **ramped, never stepped**, so the force builds and releases:

```python
schedule=((1.5, 0.0, 1.0), (1.0, 1.0, 1.0), (1.5, 1.0, 0.0)),   # deceleration: a braking run
schedule=((0.15, 0.0, 1.0), (0.1, 1.0, 1.0), (0.15, 1.0, 0.0)), # touchdown: a defined thump
```

Two traps specific to these: an effect that skips frames whose input has not changed will
freeze its running average on a perfectly steady plateau (deceleration does; the spec
wobbles the stimulus 2%), and an effect fed through a high-pass filter needs motion, not a
level (runway rumble takes a `Jitter` on wheel compression).

**Gust-driven effects** (turbulence on MSFS/X-Plane, wind on DCS/BMS) only ever see the
frame-to-frame change in wind, so a synthesised gust field is an honest stimulus as long as
its amplitude and frequency content are stated. `Gusts(rms, steady, band)` is a per-run
superposition of sinusoids per axis; the tooltip should say what reference it represents
("moderate turbulence, a few m/s of gusts"). These effects normalise their filters by the
wall clock, so a test needs an advancing `time.perf_counter` (see `_advancing_clock`) or
the filters do nothing between microsecond-apart frames. A method that reads the bound frame
instead of taking one (`update_turbulence`) sets `frame_arg=False`.

## 5. Register it

Add the spec to the tuple that builds `PREVIEW_SPECS`, and bump the count assertion beneath
it. `PREVIEWS_BY_ROW` and the settings button follow automatically.

## 6. Write the test

Every spec has a test in [`tests/test_effect_preview.py`](../tests/test_effect_preview.py)
that drives the **real aircraft class** through the runner and reads the mock effect the slot
holds. The base test case (`BaseTelemetryEffectTestCase`) supplies the mock device and the
mock effect dispenser; `mock_effects['slot']._periodic` is `(frequency, magnitude, direction,
kwargs)`.

```python
class TestFlapsPreview(BaseTelemetryEffectTestCase):
    def test_plays_at_the_configured_intensity(self):
        ac = aircrafts_dcs.Aircraft('preview')
        ac.flaps_motion_effect_enabled = False    # the preview must force it on
        ac.flaps_motion_intensity = 0.2
        runner = PreviewRunner(ac, FLAPS_MOTION, 'DCS', frame_rate=10.0)
        for _ in range(3):
            runner.step()
        assert self.mock_effects['flapsmovement']._periodic[:3] == (180, 0.2, 0)
        while runner.step():
            pass
        assert not self.mock_effects.dict        # everything freed
```

What to pin: the slot plays, at the intensity the profile sets, with the sim's own field, on
every sim the spec offers; a sim the spec excludes raises `ValueError`; a full run leaves the
dispenser empty. Parametrise over sims with the matching aircraft class
(`aircrafts_dcs.Aircraft` for DCS and BMS, `aircrafts_msfs_xp.Aircraft` for MSFS and
X-Plane, `aircrafts_il2.Aircraft` for IL-2).

Use `frame_rate=10.0` so frame counts stay small. Inspect the **penultimate** frame of a
tail-less spec: the final frame is destroyed in the same step it plays. Slice `_periodic[:3]`
unless the waveform is the point — the fourth element is the keyword arguments.

The framework's generic `create_test_instance` stubs the change tracker without its quiet
period, so anything change-driven needs a real aircraft class.

## 7. Feel it before committing

Every preview so far has been bench-verified before its commit, and several changed on the
bench (ETL lost its hold, the buffet onset got shorter, prop rumble gained its dwells). Run it
from the settings button in offline mode, and from the Debug menu if you want it without a
model selected. Then the commit message says so.

## Pitfalls, each learned the hard way

- **The last frame is destroyed in the step it plays.** A one-shot fired on the final scripted
  frame (the gear clunk at 1.0) is never felt without a `tail`. Ramps keep the default 0.5 s;
  holds and sweeps set `tail=0.0`.
- **The change tracker primes on its first call.** `anything_has_changed` returns `False` the
  first time it sees a key, so a change-driven effect fires on the *second* change, and the
  first frame of a train is silent. Tests count from frame 1.
- **A change tracker with a quiet period reads the wall clock.** Frames are microseconds apart
  in a test, so "unchanged for 200 ms" never becomes true unless the test sleeps past it;
  endpoint clunks need `time.sleep(0.25)` before the tail step.
- **Some effects only re-issue on their own modulation ticking** (afterburner). Live, that ticks
  every frame; in a test, monkeypatch `utils.sine_point_in_time` to advance per call.
- **Zero is silence for many effects** — jet rumble at 0% RPM, spoilers at 0 deployment
  *dispose* their slots. Start a sweep at idle, not the floor.
- **Fields must be per sim when the effect reads per sim.** `EngRPM` everywhere and `EngPCT` on
  X-Plane; `Gear` and `RetractableGear` on MSFS/X-Plane where DCS reads `gear_value`. A field
  the effect reads through a `'*'` entry that references a DCS-only attribute will raise on
  MSFS — key it to the sims that have it.
- **Pass the waveform by keyword.** `periodic(freq, mag, dir, effect_type=EFFECT_SQUARE)`. The
  positional slot is for a `DirectionModulator`'s constructor and a positional waveform now
  raises. (Nine effects rendered as sine for years because of this.)
- **The preview aircraft has its own effect table.** Construction clears the dispenser it sees
  and cleanup destroys everything in it, so `build_aircraft` installs a private one before
  `__init__` runs. Never construct a preview aircraft with the plain constructor and set the
  table afterwards.
- **The framework mock swallows arguments it doesn't model.** If a new effect calls a method
  the mock lacks (`stop(destroy_after=...)`, `start(force=True)` were both added), extend the
  mock in `tests/framework/base.py` rather than working around it in the spec.
- **A spec does not make an effect previewable.** If the honest stimulus needs a picker (two
  knobs at once — buffet at a chosen speed *and* flap setting), leave it out rather than
  build the picker. That was the line drawn for the whole catalog.
