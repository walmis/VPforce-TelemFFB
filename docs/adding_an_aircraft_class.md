# Adding a New Aircraft Class to TelemFFB

An *aircraft class* is the Python type that owns an aircraft's force-feedback behaviour —
`PropellerAircraft`, `JetAircraft`, `Helicopter`, and specialised subclasses like
`HPGHelicopter` or `SASHelicopter`. You add a new class when an aircraft (or family of
aircraft) needs behaviour the generic base classes can't express — typically because it
exposes a bespoke telemetry interface (an AFCS, a custom SAS, extra L-vars) that warrants
its own `on_telemetry` logic and its own set of tunable parameters.

This guide uses the helicopter subclasses `HPGHelicopter` and `SASHelicopter` (MSFS) and
`XAW109Helicopter` (X-Plane) as running examples, because bespoke classes have in practice
only been needed for MSFS/X-Plane. **The mechanics are the same for every sim, though** —
you can add a class for DCS, IL-2, or BMS the same way. Two things vary by sim, and both are
called out where they matter:

- **Which module the class lives in** (each sim family has its own — see below).
- **Telemetry remapping and class auto-detection.** MSFS and X-Plane support per-variable
  telemetry remapping (`<sc_overrides>` — SimConnect variables / X-Plane datarefs); DCS,
  IL-2, and BMS have no equivalent, so a class for those sims works with whatever its
  telemetry transport already provides. Separately, only MSFS auto-detects a *generic* class
  from SimConnect data — every other sim resolves classes purely from the `type` mapping
  (see [Runtime resolution](#runtime-resolution--how-the-pieces-meet)).

### Sim → class module

The class-name string is resolved against a module chosen by the sim. Put your class where
that sim looks:

| Sim(s) | Module | Where classes are defined |
|--------|--------|---------------------------|
| MSFS, X-Plane | `telemffb/sim/aircrafts_msfs_xp.py` | One file per class in the `telemffb/sim/msfs_xp/` subpackage, imported into the module |
| DCS, BMS | `telemffb/sim/aircrafts_dcs.py` | Inline in the module file |
| IL-2 | `telemffb/sim/aircrafts_il2.py` | Inline in the module file |

Every class in every module ultimately derives from `telemffb.sim.aircraft_base.AircraftBase`,
and each module defines its own generic bases (`Aircraft`, `PropellerAircraft`, `JetAircraft`,
`Helicopter`, …) for you to subclass.

---

## Overview — the five wiring points

A class is not "registered" in one place; it's made real by five independent edits. Miss any
one and the class either won't load, won't resolve, or won't appear in the UI. The paths
below show the MSFS/X-Plane module; substitute the module for your sim from the table above.

| # | What | Where |
|---|------|-------|
| 1 | Define the class | The sim's class module (subpackage file for MSFS/XP; inline for DCS/BMS/IL-2) |
| 2 | Ensure it's in the module namespace | Import into `aircrafts_msfs_xp.py` (MSFS/XP only; inline classes are automatic) |
| 3 | Register it as selectable | `<classes>` block in `defaults.xml` |
| 4 | Give it a friendly UI name | `NewAircraftWizard.py` |
| 5 | Map aircraft → class + class defaults | `<models>` / `<classdefaults_{sim}>` in `defaults.xml` |

---

## 1. Define the class

Subclass the nearest existing base *from your sim's module* — almost always `Helicopter`,
`PropellerAircraft`, `JetAircraft`, or `TurbopropAircraft` — so you inherit its effect
machinery and only override what differs. Each module defines its own bases, so a DCS class
subclasses the bases in `aircrafts_dcs.py`, an MSFS/X-Plane class the ones exposed through
`aircrafts_msfs_xp.py`, and so on.

**Where the file goes depends on the sim:**
- **MSFS / X-Plane** — a new file `telemffb/sim/msfs_xp/<YourClass>.py` (the classes live in
  a subpackage, one per file). This is the case the example below shows.
- **DCS / BMS** — define the class inline in `telemffb/sim/aircrafts_dcs.py` alongside the
  existing `Aircraft` / `PropellerAircraft` / `Helicopter` classes.
- **IL-2** — define the class inline in `telemffb/sim/aircrafts_il2.py`.

```python
from typing import override
from .Helicopter import Helicopter
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class SASHelicopter(Helicopter):
    # ---- user parameters ----
    # Class attributes become tunable settings when a matching <defaults>
    # entry exists in defaults.xml (see step 5). Give every one a default here.
    afcs_step_size = 2
    hands_on_deadzone = 0.1
    vrs_effect_enable: bool = True
    # ---- end user parameters ----

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        # class-specific init

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)   # keep the base effects running
        # your bespoke FFB logic here
```

Notes:
- **Always call `super()`** in `__init__`, `on_telemetry`, `on_event`, and `on_timeout`
  unless you deliberately intend to replace base behaviour. The MRO test suite
  (`tests/test_msfs_aircraft_mro.py`) enforces that every class in the chain gets called.
- Class attributes in the "user parameters" block are the class's tunable knobs. They need
  a corresponding `<defaults>` definition in `defaults.xml` (scoped by `<prereq>`/class) to
  surface in the Settings UI, but the attribute default here is the fallback.

## 2. Ensure the class is in the module namespace

At runtime `TelemManager` resolves a class-name *string* (from the aircraft's `type` setting)
to the actual class with a plain attribute lookup on the sim's module:

```python
Aircraft_Class = getattr(aircraft_info.module, cls_name, None)
```

So the class name only has to exist as an attribute of that module. What that takes depends on
where you defined the class:

- **MSFS / X-Plane** — the classes live in the `msfs_xp/` subpackage, so you must import your
  class into the module to surface it. **This is the load-bearing step for these sims:**

  ```python
  # in telemffb/sim/aircrafts_msfs_xp.py
  from .msfs_xp.SASHelicopter import SASHelicopter
  ```

  `aircraft_info.module` is `aircrafts_msfs_xp` for both MSFS and X-Plane (the two sims are
  dispatched to the same module). Omit the import and `getattr` returns `None`, so the
  aircraft silently falls back to the generic `Aircraft` base — no error is raised. The
  import exists purely to put the name in the namespace; nothing else references it.
- **DCS / BMS / IL-2** — because you defined the class inline in the module file (step 1), it
  is already a module attribute. No extra registration line is needed.

## 3. Register it as a selectable class

Add a `<classes>` entry in `defaults.xml` for each sim that should offer the class — `MSFS`,
`XPLANE`, or both. Because the two sims share the class module, the *only* thing that scopes
a class to one sim rather than both is which `<classes>` (and `type` mapping, step 5) entries
you create:

```xml
<classes>
    <sim>MSFS</sim>
    <class_name>SASHelicopter</class_name>
</classes>
<!-- X-Plane example: -->
<classes>
    <sim>XPLANE</sim>
    <class_name>XAW109Helicopter</class_name>
</classes>
```

`get_classes_for_sim()` reads these to populate the class dropdown in the New Aircraft
Wizard and the profile editor. A class with no `<classes>` entry still works if an aircraft
is mapped to it, but the user can't pick it manually.

## 4. Give it a friendly display name

In `telemffb/sim/NewAircraftWizard.py`, add the class to `friendly_class_names`:

```python
friendly_class_names = {
    ...
    "SASHelicopter": "SimFocus SAS Helicopter",
}
```

The reverse lookup (`internal_class_names`) is built automatically. If the class relies on a
shipped default profile that must be copied when a user first sets it up (as the specialised
helicopters do), also add it to `mandatory_clone_types`:

```python
mandatory_clone_types = ("HPGHelicopter", "SASHelicopter", ...)
```

## 5. Map aircraft to the class, and set class defaults

Two related edits in `defaults.xml`.

**a) Aircraft → class mapping** via a `type` model entry. When an aircraft name matches the
`<model>` regex, `get_class_for_sim_model()` returns this `<value>` as the class name:

```xml
<models>
    <name>type</name>
    <model>Airbus H145.*</model>
    <value>HPGHelicopter</value>
    <sim>MSFS</sim>
    <device>any</device>
</models>
```

**b) Class-scoped default settings** via `<classdefaults_{sim}>` — the tag embeds the sim, so
use `<classdefaults_MSFS>`, `<classdefaults_XPLANE>`, `<classdefaults_DCS>`,
`<classdefaults_IL2>`, or `<classdefaults_BMS>` as appropriate. These apply to every aircraft
of the class — spring modes, effect toggles, `validvalues` overrides, and setting
*exclusions* (the `!ClassName` form). Include the self-referential `type` default (universal
across sims) so the class survives profile cloning and inheritance:

```xml
<classdefaults_MSFS>
    <name>type</name>
    <type>HPGHelicopter</type>
    <value>HPGHelicopter</value>
    <sim>MSFS</sim>
    <device>joystick</device>
</classdefaults_MSFS>
```

For class-specific dropdown option lists (e.g. a restricted set of spring modes), use
`<validvalues_overrides>` keyed on `<class>`. To *hide* a setting from a class, add the class
to that setting's `!`-exclusion list rather than removing the row.

---

## Runtime resolution — how the pieces meet

When an aircraft loads, `TelemManager` picks its module by sim (see the
[sim → module table](#sim--class-module)), then resolves the class in this order:

1. **`type` setting** — the aircraft's resolved `type` value (from a `<models>` match, a
   user profile, or a `classdefaults` fallback) gives a class-name string.
2. **String → class** — `getattr(module, cls_name, None)`, where `module` is the sim's class
   module and the name is present because of step 2.
3. **Auto-detect fallback (MSFS only)** — if step 2 yields nothing or the bare `Aircraft`
   base, `resolve_aircraft_class_from_sc()` runs. On MSFS it reads the SimConnect
   aircraft/engine type and picks a *generic* class from it (Piston → `PropellerAircraft`,
   Jet → `JetAircraft`, Glider → `GliderAircraft`, etc.). It never selects a bespoke subclass,
   and on **every other sim** — X-Plane, DCS, IL-2, BMS — the SimConnect type fields are
   `None`, so this step just returns the bare `Aircraft` base.

Two consequences worth internalising:

- **A specialised class is only ever reached through an explicit `type` mapping (step 5a).**
  The auto-detect fallback resolves generic classes only, and only on MSFS.
- **Every sim except MSFS depends entirely on the `type` mapping.** With no auto-detect
  fallback, an X-Plane / DCS / IL-2 / BMS aircraft that isn't mapped (and has no user
  profile) lands on the bare `Aircraft` base. This is why every curated non-MSFS aircraft in
  `defaults.xml` carries an explicit `type` row, and why `XAW109Helicopter` (the X-Plane
  AW109SP) is reached purely by its `<models>` mapping. On MSFS alone, an unmapped aircraft
  still lands on a sensible generic base class by engine type.

---

## Checklist

- [ ] Class defined in the sim's module — a new file in `telemffb/sim/msfs_xp/` for MSFS/XP, or inline in `aircrafts_dcs.py` (DCS/BMS) / `aircrafts_il2.py` (IL-2); subclasses an appropriate base and calls `super()` in the lifecycle hooks
- [ ] In the module namespace — imported into `aircrafts_msfs_xp.py` for MSFS/XP (automatic for inline DCS/IL-2 classes)
- [ ] `<classes>` entry for each sim it should be offered in
- [ ] Added to `friendly_class_names` (and `mandatory_clone_types` if it needs a clone profile)
- [ ] `type` model mapping(s) for the target aircraft — **mandatory for every sim except MSFS** (no auto-detect fallback)
- [ ] `classdefaults_{sim}` entries, including the self-referential `type` default
- [ ] `defaults.xml` still parses (`ET.parse`) and the schema test passes
- [ ] Lifecycle-hook `super()` chaining verified (MSFS/XP is covered by `pytest tests/test_msfs_aircraft_mro.py`)
