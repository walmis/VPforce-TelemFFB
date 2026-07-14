# TelemFFB Shaker MVP — Architecture

## High-level data flow

```
                  +----------------------------+
                  |  Simulator (MSFS first)    |
                  |  SimConnect / export.lua / |
                  |  IPC bridge                |
                  +--------------+-------------+
                                 |
                                 |  telemetry frames
                                 v
              +-----------------------------------+
              |  TelemFFB master process          |
              |  (--type joystick, no --child)    |
              |  - reads telemetry from sim       |
              |  - publishes telem frames over    |
              |    ZeroMQ to all child instances  |
              +------+----------------------+-----+
                     |                      |
       ZeroMQ telem  |                      |  ZeroMQ telem
                     v                      v
        +--------------------+   +--------------------------+
        | Joystick child /   |   | Shaker child             |
        | self (Rhino HID)   |   | --type shaker --child    |
        |                    |   |                          |
        | aircraft_base ->   |   | aircraft_base ->         |
        |   effects[...]     |   |   effects[...]           |
        |   .periodic().start|   |   .periodic().start      |
        |        |           |   |        |                 |
        |        v           |   |        v                 |
        | HapticEffect       |   | HapticEffect (facade)    |
        | (ffb_rhino)        |   | (ffb_shaker)             |
        |        |           |   |        |                 |
        |        v           |   |        v                 |
        |  USB HID -> Rhino  |   |   ShakerSynth            |
        +--------------------+   |   (numpy + sounddevice)  |
                                 |        |                 |
                                 |        v                 |
                                 |  PortAudio output stream |
                                 |  -> selected soundcard   |
                                 |  -> tactile transducer   |
                                 +--------------------------+
```

The shaker child is launched the same way pedals/collective/trimwheel children are launched today. The only thing that's different about it is the back-end: instead of opening a Rhino HID device, it opens an audio output stream via `sounddevice` and synthesises sine tones.

## Why a 5th device type, not dual-output in the joystick instance?

Three reasons, in order of importance:

1. **Process isolation matches the existing model.** Each TelemFFB device already runs in its own OS process, subscribed to the master's telemetry feed over ZeroMQ. The shaker is conceptually another physical effector — it needs its own copy of `aircraft_base` evaluating telemetry and dispensing effects, exactly like pedals does today. Forcing it into the joystick process would mean special-casing dual-effects routing inside `HapticEffect.start`, which has knock-on consequences for spring/damper handling, log scoping, and per-device profile selection.
2. **Profile / settings reuse for free.** TelemFFB stores per-device-type configuration (`SettingsManager` keyed by device type, see `main.py:443` and `:469`). A shaker device type gets its own profile slot automatically — gain, output device, and any future shaker-specific tuning live there without touching the joystick profile.
3. **Failure isolation.** A blocked PortAudio callback or a crashed audio backend cannot stall the Rhino force-feedback loop on the joystick. The processes are independent; one dying does not take the other with it.

The cost is that the user runs an extra TelemFFB instance. That cost is already paid by anyone running pedals + stick today, so it's not a new burden.

## The HapticEffect facade pattern

`telemffb/sim/aircraft_base.py` calls into `HapticEffect` with chained-builder syntax:

```python
effects["runway0"].periodic(15, intensity * .75, direction=0,
                            effect_type=EFFECT_SQUARE, duration=80).start()
```

`ffb_rhino.HapticEffect` translates each chain into a USB HID PID/FFB report. We add `ffb_shaker.HapticEffect` exposing **the same method surface**, but its implementation routes parameters into a `ShakerSynth` oscillator instead. The two modules are interchangeable from the perspective of `aircraft_base`.

The actual switch happens in one place — the `import` line at `aircraft_base.py:29-31`. STEP_03 changes that to conditionally import from `ffb_shaker` when `G.device_type == 'shaker'`. The 90 % of `aircraft_base` that constructs effects via `effects[...]` does not change, because the binding `effects = Dispenser(HapticEffect)` at line 38 picks up whichever `HapticEffect` the conditional import resolved to.

Force-only methods (`spring`, `damper`, `friction`, `inertia`, `setCondition`, `spring_adjuster`) on the shaker facade are **chainable no-ops** that log at debug level. They have to exist (otherwise calls like `effects['x'].spring().start()` raise `AttributeError`), but they must not produce sound.

## The effect whitelist rationale

`aircraft_base.py` references roughly 54 distinct effect names. They divide cleanly:

- **Rumble / periodic effects** (≈30 names): runway bumps, gunfire, countermeasures, buffeting, gear/flap movement, prop and rotor RPM, afterburner / jet rumble, ETL, overspeed, AOA warnings, wind. All of these correspond to a vibration the pilot feels — they translate well to a shaker.
- **Force-only effects**: `spring`, `damper`, `friction`, `inertia`, `spring_adjuster`, `setCondition`. These describe a static centring or damping force on the stick. There's no useful audio analogue; they should be silently dropped.
- **Constant-force "shake" effects**: `runway0`, `runway1`, `gforce`, etc. use `.constant()` rather than `.periodic()`. They produce a sustained sensation. The facade maps them to a fixed low frequency (25 Hz default) and uses the magnitude as oscillator amplitude.

Rather than driving routing off the call chain (`.periodic` vs `.constant` vs `.spring`), STEP_04 makes routing **explicit** via `SHAKER_EFFECT_WHITELIST` — a hardcoded set of effect names known to be useful on a shaker. Anything not in the set is dropped at `start()` with a debug log. Three benefits:

1. New effects added to `aircraft_base.py` upstream don't accidentally produce noise on the shaker — they need an explicit opt-in.
2. The whitelist is the single source of truth for "what does this device do?"; tuning each effect's amplitude / frequency in the future has a natural home next to the whitelist.
3. Debugging routing is straightforward: log mismatches at debug level, walk the whitelist.

The trade-off is that adding new effects requires editing the set. For an MVP that's fine; STEP_07's `KNOWN_LIMITATIONS.md` calls this out explicitly.
