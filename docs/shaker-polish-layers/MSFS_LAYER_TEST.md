# MSFS Layer Smoke-Test Log

_Manual test log. Scaffolded by Claude on STEP_05; awaiting user fill-in._

- **Scaffolded:** 2026-05-02
- **Scaffold commit:** `112b2be96fd76c302239373d72a7a2af9426ccd3`
- **Status:** template only — no results yet

---

## Test conditions

Fill in before the run:

| Field | Value |
|-------|-------|
| Aircraft | _<e.g. Asobo F/A-18E, DA62, TBM930>_ |
| Mission / scenario | _<e.g. free-flight, approach, combat pattern>_ |
| Gear / load / fuel state | _<e.g. clean config, 50% fuel, no stores>_ |
| Shaker output device | _<e.g. Dayton DAEX25VT-4, internal amp model>_ |
| Master shaker gain | _<0.0–1.0 value from System Settings>_ |
| Channel mode | _<mono / stereo>_ |
| Pan | _<0 = centre, −1 = full left, +1 = full right>_ |
| Rhino device + serial | _<optional, e.g. VPforce Rhino MkII SN:XXXX>_ |
| Simulator version | _<e.g. MSFS 2020 SU15 / MSFS 2024 build XXXXX>_ |

---

## Test matrix

Minimum 5 effects from the spec; extras included for completeness. For each
effect, toggle `shakerGain` to 0 (or kill the shaker child) to get an
A/B baseline before filling in the note block.

| # | Effect key | Scenario | Spec expectation |
|---|-----------|---------|-----------------|
| 1 | `je_rumble_*` / `prop_rpm*` | Cruise + power sweep low-to-high | Body-coupled bass on shaker, fundamental on stick; continuous feel, not phasey |
| 2 | `touchdown` | Land at low Vs, then heavier sink rate | Single low body-thump on shaker; haptic crack on stick; one event, no double-hit |
| 3 | `gunfire` (DCS) or `gearmovement` (MSFS) | Fire burst / cycle gear | Snap on stick, low rumble on shaker; each gear-end should also pop on shaker |
| 4 | `buffeting` | Approach stall, hold in buffet | Continuous low rumble on shaker, mid texture on stick; no audible beating |
| 5 | `runway0` | Take-off roll | Shaker rumble grows with speed; stick texture overlaid; no band-doubling with chair |
| 6 | `gearclunk` | Gear doors open/close | Short body thump per clunk event |
| 7 | `payload_release` | Stores release (if aircraft supports) | Sharp thump on shaker with simultaneous haptic on stick |
| 8 | `ab_rumble` / `afterburner` | Light and hold afterburner | Deep sustained rumble on shaker; note if overlapping buffeting causes clipping |
| 9 | `etl` | Helicopter ETL transition | Body-coupled low rumble growing through ETL band; smooth with rotor texture on stick |

---

## Per-effect note blocks

### 1. Engine / prop rumble — power sweep (cruise)

_Scenario: set cruise power, then sweep throttle from ~30 % to 100 % slowly._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes (if worse, hypothesis: gain too low? freq_factor wrong direction? routing wrong band?):

---

### 2. Touchdown — low sink, then heavy

_Scenario: two landings — greased on-speed, then deliberately firm sink rate._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 3. Gunfire / gearmovement — burst or gear cycle

_Scenario (MSFS): retract then extend gear; (DCS): fire short burst._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 4. Buffeting — pre-stall hold

_Scenario: slow to stall warning, hold in sustained buffet for ~5 s._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 5. Runway rumble — take-off roll

_Scenario: full-power take-off roll from brake release to rotate._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 6. Gear clunk (extra)

_Scenario: lower gear at pattern altitude; listen/feel for each door event._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 7. Payload release (extra)

_Scenario: release a store or jettison fuel tank (if aircraft supports it)._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

### 8. Afterburner rumble (extra)

_Scenario: light AB from idle, hold for ~10 s, then note if simultaneous
buffeting causes any amp clipping or layer beating._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes (watch for: shaker amp clipping when buffeting + ab_rumble overlap):

---

### 9. ETL transition (extra, helicopters)

_Scenario: hover, then accelerate through ETL (approx 15–20 kt)._

- Audibly different from pre-layer behaviour? _yes / no_
- Shaker more / same / less prominent vs Rhino-via-chair? _<fill in>_
- Subjective immersion: _better / same / worse / off_
- Notes:

---

## Cross-cutting findings

_Fill in after all effects if anything spans multiple effects (e.g. "gain
feels globally low across all effects" or "shaker amp clips whenever two
effects overlap"):_

- _<finding 1>_
- _<finding 2>_

---

## Verdict

_One paragraph summary written by the user after the run. Describe overall
impression, whether the layered routing felt worth the complexity, and the top
one or two things to tune next._

_<user writes verdict here>_
