# Implementation Plan: FFB SimVar / LVAR API (rig side)

**Status:** Implemented (milestones 1-6) — see §11
**Audience:** TelemFFB developers
**Companion spec:** [MSFS_Helicopter_FFB_API.md](https://github.com/CK-AT/DIY-FFB/blob/ck_dev/Standards/MSFS_Helicopter_FFB_API.md)
**Spec revision tracked:** API_VERSION 1 (adds `L:FFB_FEATURES` capability bitfield)

---

## 1. What this document is

The companion proposal defines a small, standardized set of `L:FFB_*` LVARs that a
helicopter (aircraft addon) can expose so that any FFB rig can implement trim, feel,
and hydraulic effects without per-aircraft reverse engineering.

The proposal describes the **contract**. This document plans TelemFFB's job: the
**rig side** of that contract for the MSFS backend — discovering the API, gating it
per control, reading the aircraft-published state, and writing back the rig-owned
signals.

It also explains how the new API maps onto machinery TelemFFB already has, so the
first implementation is mostly *wiring a clean, documented contract onto existing
spring/trim/hydraulic code* rather than net-new subsystems.

### Scope

- **In scope:** MSFS (SimConnect / LVARs). All three controls: cyclic (joystick),
  collective, pedals.
- **Out of scope for v1:** DCS, IL-2, BMS. X-Plane is a natural follow-up (the same
  contract maps onto datarefs via the existing X-Plane plugin) but is not planned here.
- **Not TelemFFB's responsibility:** the aircraft-side contract (zeroing
  `ROTOR *_TRIM PCT`, yielding trim authority, publishing every frame). That lives in
  the aircraft addon. TelemFFB only consumes/produces the LVARs.

---

## 2. Why this is a good fit for TelemFFB today

The proposal is close to something TelemFFB already does per-vendor. The relevant
precedents:

| Existing mechanism | File | Relationship to the new API |
|---|---|---|
| HPG helicopter reads a rich AFCS LVAR set (`L:EFB_AFCS_MASTER`, `L:EFB_SEMA_*`, `L:EFB_TRIM_RELEASE`, `L:EFB_COLLECTIVE_RELEASE`, …) | [HPGHelicopter.py](../telemffb/sim/msfs_xp/HPGHelicopter.py) | The new API is the **vendor-neutral generalization** of this. `FFB_*_TR_ON` ≈ `hpgTrimRelease`/`hpgCollectiveRelease`; `FFB_*_TRIM` ≈ the SEMA/trim-reference idea, but published directly as a normalized actuator position instead of requiring the rig to integrate SEMA slack. |
| TelemFFB already **writes** `L:FFB_HANDS_ON_CYCLIC`, `L:FFB_HANDS_ON_CYCLICX/Y`, `L:FFB_FEET_ON_PEDALS` back to the sim | [Helicopter.py](../telemffb/sim/msfs_xp/Helicopter.py#L152-L185), [HPGHelicopter.py](../telemffb/sim/msfs_xp/HPGHelicopter.py#L520) | `FFB_*_FLY_THROUGH` is the same shape of signal (rig-detected contact/deviation written to the aircraft). The detection logic (`check_hands_on`, `check_feet_on`, force-based mode) is reusable almost verbatim. |
| Per-aircraft LVAR subscription via `sc_overrides` | [xmlutils.read_sc_overrides](../telemffb/xmlutils.py#L1420), [TelemManager._setup_simconnect_overrides](../telemffb/telem/TelemManager.py#L473) | The read side rides the same `add_simvar()` → `_resubscribe()` path. The difference: the FFB API vars should be subscribed **automatically** (for discovery), not require the user to hand-enter overrides. |
| Reading/writing arbitrary LVARs at runtime | [SimConnectManager](../telemffb/telem/SimConnectManager.py): `add_simvar()` reads `L:` vars; `send_event_to_msfs()` / `set_simdatum_to_msfs()` write them | Both directions of the API are already supported at the transport layer. No SimConnect plumbing changes are required. |
| Generic heli trim-following (`trim_following`, `CyclicTrimX/Y` → spring center) | [MsfsXpHeliControlsMixIn._update_cyclic_trim](../telemffb/sim/msfs_xp/MsfsXpHeliControlsMixIn.py#L350) | `FFB_*_TRIM` replaces the ad-hoc `ROTOR *_TRIM PCT`-derived follow with a clean, discontinuity-free actuator position — exactly the pain point the proposal calls out. |
| Hydraulic loss → damper/inertia/friction | [HydraulicLossMixIn](../telemffb/sim/base/HydraulicLossMixIn.py) | `FFB_*_HYD_ASSIST_LOSS` (0..1) replaces the heuristic that infers loss from `HydSys`/`HydPress`. **Note the inversion:** `hydraulic_factor` is system *health* (1 = full boost), while `HYD_ASSIST_LOSS` is *loss* (1 = locked). The published value is converted, not passed through. |

**Takeaway:** the transport, the trim/spring state machines, the contact-detection,
and the hydraulic-loss effect all already exist. The work is (a) a discovery +
mode-gating layer, and (b) a new aircraft class that drives the existing machinery
from the standardized vars instead of vendor-specific ones.

---

## 3. Variable inventory & direction

Recall the namespaces from the proposal:

- Controls: `CYCLIC`, `COLLECTIVE`, `PEDALS`
- Axes: `CYCLIC_PITCH`, `CYCLIC_ROLL`, `COLLECTIVE`, `PEDALS`

Two normative rules from the spec constrain everything below:

- **Static vs. per-frame.** `FFB_API_VERSION` and `FFB_FEATURES` are *discovery*
  variables: read once on connect, re-read on aircraft change, never per frame.
  Everything else is a per-frame runtime variable.
- **Read vars are only valid while enabled.** Spec §3.4: the aircraft→rig variables
  are meaningful only while that control's `FFB_<CONTROL>_ENABLED == 1`. TelemFFB
  must not consume `_TRIM` / `_HYD_ASSIST_LOSS` / `_TR_ON` for a control that is in
  normal mode, even if the aircraft happens to publish them.

### 3.1 aircraft → rig (TelemFFB reads)

| LVAR | SimConnect unit | TelemFFB field (proposed) | Consumed by |
|---|---|---|---|
| `L:FFB_API_VERSION` | `number` | `ffbApiVersion` | discovery / mode-gating (read once) |
| `L:FFB_FEATURES` | `number` (bitfield) | `ffbFeatures` | capability gating — which controls have trim (read once) |
| `L:FFB_CYCLIC_PITCH_TRIM` | `number` (−1..+1) | `ffbTrimCyclicPitch` | cyclic spring center (Y) |
| `L:FFB_CYCLIC_ROLL_TRIM` | `number` (−1..+1) | `ffbTrimCyclicRoll` | cyclic spring center (X) |
| `L:FFB_COLLECTIVE_TRIM` | `number` (−1..+1) | `ffbTrimCollective` | collective spring center |
| `L:FFB_PEDALS_TRIM` | `number` (−1..+1) | `ffbTrimPedals` | pedals spring center |
| `L:FFB_<AXIS>_HYD_ASSIST_LOSS` | `number` (0..1) | `ffbHydLoss<Axis>` | `HydraulicLossMixIn` |
| `L:FFB_CYCLIC_TR_ON` | `bool` | `ffbTrOnCyclic` | cyclic unclutch |
| `L:FFB_COLLECTIVE_TR_ON` | `bool` | `ffbTrOnCollective` | collective unclutch |
| `L:FFB_PEDALS_TR_ON` | `bool` | `ffbTrOnPedals` | pedals unclutch |

### 3.2 rig → aircraft (TelemFFB writes)

| LVAR | Written when | Source |
|---|---|---|
| `L:FFB_CYCLIC_ENABLED` | joystick instance is in FFB mode for the loaded aircraft | mode manager |
| `L:FFB_COLLECTIVE_ENABLED` | collective instance is in FFB mode | mode manager |
| `L:FFB_PEDALS_ENABLED` | pedals instance is in FFB mode | mode manager |
| `L:FFB_CYCLIC_PITCH_FLY_THROUGH` | rig detects fly-through on pitch | contact detection |
| `L:FFB_CYCLIC_ROLL_FLY_THROUGH` | rig detects fly-through on roll | contact detection |
| `L:FFB_COLLECTIVE_FLY_THROUGH` | rig detects fly-through on collective | contact detection |
| `L:FFB_PEDALS_FLY_THROUGH` | rig detects fly-through on pedals | contact detection |

### 3.3 `L:FFB_FEATURES` bit layout

Read as a double, round to nearest int, then mask: `(round(value) >> bit) & 1`.
Bit positions are frozen and append-only, so decode defensively and ignore unknown bits.

| Bit | Meaning | TelemFFB effect when `0` |
|---|---|---|
| 0 | `CYCLIC` has trim | Ignore `FFB_CYCLIC_*_TRIM`; apply **no trim spring** on cyclic |
| 1 | `COLLECTIVE` has trim | Ignore `FFB_COLLECTIVE_TRIM`; no trim spring on collective |
| 2 | `PEDALS` has trim | Ignore `FFB_PEDALS_TRIM`; no trim spring on pedals |
| 3–7 | reserved (trim / feel group) | — |
| 8+ | reserved (future capability groups, e.g. aero cues: VRS, RBS, ETL) | — |

`FFB_FEATURES` absent or `0` while `API_VERSION >= 1` is legal and means "no optional
features": the rig assumes **nothing is trimmed**. That is a meaningfully different
default from "trim value is 0" — a control with no trim system gets a plain centering
/ friction feel from local settings, not a spring driven by a published reference.
Note this makes `_TRIM` consumption conditional on *two* gates: that control's
`FEATURES` bit set **and** its `ENABLED == 1`.

---

## 4. Architecture

TelemFFB runs **one process per device** (master joystick auto-launches child
collective/pedals instances). That maps cleanly onto the per-control design of the
API: each instance owns exactly one control's `ENABLED` flag and one control's
axes. There is no cross-instance coordination required for the API itself — the
joystick instance writes `FFB_CYCLIC_ENABLED`, the collective instance writes
`FFB_COLLECTIVE_ENABLED`, etc. Each instance has its own SimConnect connection
(`SimConnect(f"TelemFFB-{G.device_type}")`), so each reads and writes independently.

Four pieces of work:

1. **Read/subscribe layer** — subscribe the `FFB_*` read vars automatically for MSFS
   heli, surface them on `BaseTelemetryData`.
2. **Discovery + mode-gating** — read `FFB_API_VERSION`; if `>= 1`, drive the
   `ENABLED` lifecycle; otherwise treat the aircraft as unsupported and fall back to
   existing behavior.
3. **`FFBApiMixIn`** on the base `Helicopter` — consumes the standardized vars to drive
   cyclic/collective/pedal springs, trim, hydraulics; writes back `FLY_THROUGH`.
   Inert until discovery succeeds, so every heli class inherits it at zero cost.
4. **Config, defaults, docs, tests.**

### 4.1 Read / subscribe layer

Two options for subscription:

- **Option A (recommended): a dedicated built-in subscription block.** Add the
  `FFB_*` read vars to a small helper that TelemFFB adds via `add_simvar()` whenever
  an MSFS aircraft loads (or specifically an MSFS helicopter). This makes discovery
  automatic — no user config needed, which the proposal explicitly wants ("The rig
  can detect whether the aircraft implements the API").
- **Option B: ride `sc_overrides`.** Zero code, ships as `defaults.xml` entries for a
  matched heli pattern. Rejected as the primary path because discovery must work for
  *any* aircraft, including ones with no config entry, and because putting them in
  overrides invites accidental user edits.

Implement Option A as a method analogous to
[`Helicopter.subscribe_simvars()`](../telemffb/sim/msfs_xp/Helicopter.py#L113):

```python
# Discovery: latched once per aircraft load, not consumed per frame.
FFB_API_DISCOVERY_VARS = [
    ("ffbApiVersion",        "L:FFB_API_VERSION",            "number"),
    ("ffbFeatures",          "L:FFB_FEATURES",               "number"),
]

# Runtime: per-frame, only valid while our control's ENABLED == 1.
FFB_API_READ_VARS = [
    ("ffbTrimCyclicPitch",   "L:FFB_CYCLIC_PITCH_TRIM",      "number"),
    ("ffbTrimCyclicRoll",    "L:FFB_CYCLIC_ROLL_TRIM",       "number"),
    ("ffbTrimCollective",    "L:FFB_COLLECTIVE_TRIM",        "number"),
    ("ffbTrimPedals",        "L:FFB_PEDALS_TRIM",            "number"),
    ("ffbHydLossCyclicPitch","L:FFB_CYCLIC_PITCH_HYD_ASSIST_LOSS", "number"),
    # ... roll / collective / pedals
    ("ffbTrOnCyclic",        "L:FFB_CYCLIC_TR_ON",           "bool"),
    ("ffbTrOnCollective",    "L:FFB_COLLECTIVE_TR_ON",       "bool"),
    ("ffbTrOnPedals",        "L:FFB_PEDALS_TR_ON",           "bool"),
]

def subscribe_ffb_api_simvars(self):
    if not self._simconnect:
        return
    changed = False
    for name, var, unit in FFB_API_DISCOVERY_VARS + FFB_API_READ_VARS:
        if name not in self._simconnect.sv_dict:
            self._simconnect.add_simvar(name=name, var=var, sc_unit=unit)
            changed = True
    if changed:
        self._simconnect._resubscribe()
```

TelemFFB's SimConnect layer has only one cadence (subscribe → per-frame telemetry), so
the discovery vars ride the same subscription. Honour the spec's "read once" by
*latching* them in the aircraft class rather than by throttling the transport: capture
`ffbApiVersion` / `ffbFeatures` on the first frame after aircraft load, cache the
decoded bits, and ignore later changes until the next aircraft change (which already
constructs a new aircraft object). This keeps behavior stable if an aircraft glitches
the value mid-flight, and costs nothing.

Notes / gotchas:
- MSFS returns `0` for an LVAR that doesn't exist, which is exactly the "not
  implemented" sentinel for `FFB_API_VERSION` — no special "missing var" handling
  needed. Confirm this against the pysimconnect fork during spike (SimConnect can
  raise on a truly-unknown LVAR name in some versions; if so, `_calculate`'s
  `try/except` in [`_read_telem`](../telemffb/telem/SimConnectManager.py#L764) already
  swallows per-var parse failures, so absence degrades to "field stays `None`" → treat
  `None` as `0`).
- `HYD_ASSIST_LOSS` defaults to `0` when absent → "full boost", which is the correct
  fallback per the proposal (an aircraft with no hydraulics reads as fully assisted).
- `FFB_FEATURES` absent → `0` → no trim bits → no trim springs. Safe, but
  *conservative*: an aircraft that implements `API_VERSION` and forgets `FFB_FEATURES`
  will feel untrimmed. Log a one-shot warning when `ffbApiVersion >= 1 and not
  ffbFeatures` so this is diagnosable in the field rather than reported as "trim
  doesn't work".
- Add each field to [`BaseTelemetryData`](../telemffb/sim/BaseTelemetryData.py) with a
  typed hint + docstring (sims, source LVAR, unit, range, default-when-absent), per
  the "new telemetry fields" convention in AGENTS.md.

### 4.2 Discovery + mode-gating lifecycle

State the API can be in, per instance:

```
UNSUPPORTED   ffbApiVersion in (None, 0)  → existing behavior; write NO L:FFB_* var
SUPPORTED     ffbApiVersion >= 1          → latch FFB_FEATURES; eligible for FFB mode
ENABLED       our control's flag written =1, aircraft yielding trim authority
              → read vars now valid; trim spring gated on this control's FEATURES bit
```

The spec's lifecycle (§4) is five steps: detect → read capabilities once → enter FFB
mode → per-frame loop → exit. Step 2 is new relative to the first draft of this plan
and must land before step 3, because the trim wiring depends on the cached bits.

**Stricter than the first draft:** while `UNSUPPORTED`, TelemFFB must not write *any*
`L:FFB_*` variable — not just `ENABLED`. That includes `FLY_THROUGH`, and it raises a
question about the existing legacy `L:FFB_HANDS_ON_*` / `L:FFB_FEET_ON_PEDALS` writes,
which share the namespace but predate the API (see §10.6). Keep them on the legacy
classes; `FFBApiMixIn` emits *only* the standardized names, and only while enabled.

Lifecycle rules (from the proposal, mapped to TelemFFB):

- **Enable:** when the instance has a live FFB device, telemetry is flowing, the
  aircraft is `SUPPORTED`, and the user has FFB enabled for this control, write
  `L:FFB_<CONTROL>_ENABLED = 1`. Per spec §4 step 3, enable is keyed on *the rig
  physically providing that control*, not on whether the aircraft trims it — a control
  with no trim bit is still enabled (for hydraulics and fly-through); it just gets no
  trim spring.
- **Disable:** write `0` on: telemetry timeout / sim exit
  ([`on_timeout`](../telemffb/sim/msfs_xp/HPGHelicopter.py#L152), `notify_sim_exited`),
  device disconnect, app shutdown (Phase 16 cleanup in `main.py`), or user disabling
  the control.
- **Non-persistence:** the proposal requires `ENABLED` must not survive aircraft
  reload and must power up `0`. TelemFFB does not need to persist it; the risk is the
  reverse — a crash leaving it `1`. Two mitigations:
  1. Write `0` in the shutdown path (Phase 16) — best effort.
  2. Rely on the aircraft-side contract ("power-up default is `0`"). TelemFFB
     re-asserts `1` every frame while enabled (cheap idempotent `set_simdatum`), so a
     stale `1` from a crash is only a risk if the *aircraft* persists it, which the
     spec forbids. Document this dependency.
- **Restore is the aircraft's job, and is required.** Spec §3.2 makes the
  `ENABLED → 0` restore path mandatory on the aircraft side, so TelemFFB's disable
  write is sufficient — no undo sequence, no re-syncing trim, no `ROTOR_TRIM_RESET`.
  TelemFFB's only obligation is to *reliably emit the zero*. Treat a missing restore
  as an aircraft bug, not something to compensate for.
- **Write cadence:** re-assert the `ENABLED` value each frame (or on change +
  heartbeat). `send_event_to_msfs`/`set_simdatum_to_msfs` are queued and flushed in
  the SC thread loop; per-frame writes of a handful of LVARs are within budget (HPG
  already writes hands-on state every frame).

Where the lifecycle lives: a small helper on the aircraft class (it has
`self._simconnect`, device type, and telemetry). Enable/disable decisions key off
`telemffb_controls_axes` + device presence + `ffbApiVersion`. The `on_timeout`
override writes the disable.

**Important interaction with `telemffb_controls_axes`:** the proposal says the rig
drives position through *normal axis inputs* ("the physical stick moves the aircraft
as usual") and the API covers only trim/feel/hydraulics. This is a real divergence
from the HPG/generic heli path, where TelemFFB itself sends
`AXIS_CYCLIC_*_SET` / `AXIS_COLLECTIVE_SET` / `ROTOR_AXIS_TAIL_ROTOR_SET`. Under the
new API the axis can just be bound normally in MSFS.

**Decision (§10.2): honour the user's existing `telemffb_controls_axes` setting.** The
API does not change axis ownership and does not impose a new default — both models are
supported and both must be validated. Consequences:

  - **`telemffb_controls_axes = False`** (user binds axes in MSFS): the spec's clean
    model. TelemFFB does spring/trim/hydraulics/fly-through only and never sends an
    axis.
  - **`telemffb_controls_axes = True`**: TelemFFB keeps sending
    `AXIS_CYCLIC_*_SET` / `AXIS_COLLECTIVE_SET` / `ROTOR_AXIS_TAIL_ROTOR_SET`. The
    axis value must stay **raw** — trim lands in the spring center and nowhere else.
    Double-applying is silent and presents as "trim feels twice as strong", so this
    needs an explicit test (§8) rather than a code comment.

Because both paths ship, the axis-sending branch is not a legacy leftover to be
tolerated — it is a supported configuration under the API and gets equal test coverage.

### 4.3 The API layer: `FFBApiMixIn`

**Decision (§10.1): a mixin on the base `Helicopter`, not a new aircraft class.**
New file `telemffb/sim/msfs_xp/FFBApiMixIn.py`, mixed into
[`Helicopter`](../telemffb/sim/msfs_xp/Helicopter.py#L31) alongside the existing
`MsfsXpHeliControlsMixIn`. It is **inert** until `ffbApiVersion >= 1` is latched, so
it costs nothing on aircraft that don't implement the API.

This avoids the two alternatives' problems: no mid-flight class swap (which would mean
tearing down and re-initialising effect state a few seconds into every flight), and no
per-aircraft `defaults.xml` entry (which would defeat the spec's discovery goal).

#### Precedence over vendor classes — three dispatch sites, zero vendor edits

Six classes inherit from `Helicopter`, but only three carry a competing trim/spring
source:

| Class | Overrides that matter | Conflicts with API? |
|---|---|---|
| [`HPGHelicopter`](../telemffb/sim/msfs_xp/HPGHelicopter.py#L34) | `msfs_update_heli_controls`, `msfs_update_pedals`, `msfs_update_collective` (SEMA trim inline) | **Yes** |
| [`XAW109Helicopter`](../telemffb/sim/msfs_xp/XAW109Helicopter.py#L34) | same three (AFCS trim inline) | **Yes** |
| [`SASHelicopter`](../telemffb/sim/msfs_xp/SASHelicopter.py#L25) | `msfs_update_heli_controls` (AFCS trim inline) | **Yes** |
| [`TaogH500Helicopter`](../telemffb/sim/msfs_xp/TaogH500Helicopter.py#L32) | `msfs_send_heli_*_pos` only | No — axis *send* path, orthogonal to trim (see §4.2) |
| [`FlyInsideHelicopter`](../telemffb/sim/msfs_xp/FlyInsideHelicopter.py#L27) | ETL / VRS / vibration effects only | No — no control path at all |
| [`CowanSimHelicopter`](../telemffb/sim/msfs_xp/CowanSimHelicopter.py#L21) | `__init__` only | No |

**Precedence rule: for a given control, the API wins whenever that control's
`ENABLED == 1`** — the vendor path for that control is replaced, not blended. The
aircraft has zeroed its `ROTOR *_TRIM PCT` per spec §3.2, so the vendor path would be
integrating AFCS/SEMA state the aircraft is no longer maintaining.

The replacement must happen at **method** granularity, not inside the trim logic: on an
API aircraft the vendor's whole `msfs_update_heli_controls` is invalid, since it drives
the spring centre from AFCS/SEMA LVARs rather than from `_TRIM`. Conveniently, all
three vendor classes are reached through exactly three polymorphic `self.` dispatch
sites, both in shared base files:

| Dispatch site | Control |
|---|---|
| [MsfsXpHeliControlsMixIn.py:389](../telemffb/sim/msfs_xp/MsfsXpHeliControlsMixIn.py#L389) | cyclic |
| [Helicopter.py:105](../telemffb/sim/msfs_xp/Helicopter.py#L105) | collective |
| [Helicopter.py:107](../telemffb/sim/msfs_xp/Helicopter.py#L107) | pedals |

Guarding those three call sites routes around every vendor override at once:

```python
if self._ffb_api_active('CYCLIC'):
    self._ffb_api_update_cyclic(telem_data)
else:
    self.msfs_update_heli_controls(telem_data)
```

**Net cost: three guards in two shared files, and no edits to any vendor class.**
Per-control granularity falls out for free — a user with an FFB cyclic but a normal
collective gets the API path for cyclic and the untouched vendor path for collective,
decided independently at each site.

Two properties worth noting:

- The existing `_update_cyclic_trim` overrides in HPG, SAS and XAW109 are already bare
  `pass` suppressors ("trimming is handled by the AFCS integration"). They need **no
  change** — the API path does not route through `_update_cyclic_trim` at all.
- This is the codebase's established idiom rather than a new pattern: "a class with its
  own trim source suppresses the generic one" is exactly what those `pass` overrides
  do. The API is simply a third trim source, arbitrated one level higher.

The per-control mapping:

All three controls share one precondition: **the trim spring exists only if that
control's `FFB_FEATURES` trim bit is set** (bit 0 cyclic, 1 collective, 2 pedals).
When the bit is clear, skip the trim-reference wiring entirely for that control and
fall back to the class's normal centering/feel from local settings — do not spring
toward a published `_TRIM`, and do not treat `_TR_ON` as an unclutch (there is no
actuator to unclutch). Implement this as a single `self._has_trim(control)` guard so
the three code paths cannot drift apart.

**Cyclic** (`msfs_update_heli_controls`, joystick instance):
- Spring center `cpO_x`/`cpO_y` ← `ffbTrimCyclicRoll` / `ffbTrimCyclicPitch` scaled
  ×4096. This is the trim actuator position — a clean, continuous reference, so it
  replaces both the SEMA-slack integration (HPG) and the `ROTOR *_TRIM PCT` follow
  (generic). Smooth toward the target to avoid steps if the aircraft publishes coarse
  values.
- `ffbTrOnCyclic == 1` (trim unclutched / TR held): soften spring to
  `trim_release_spring_gain` and let the stick move freely — reuse the existing
  force-trim-pressed branch in
  [`_update_cyclic_force_trim`](../telemffb/sim/msfs_xp/MsfsXpHeliControlsMixIn.py#L118),
  but drive it from the LVAR instead of a physical button. On release, `FFB_*_TRIM`
  has already followed to the new position (the spec guarantees TR-follow), so the
  spring re-centers there with no snap — this is the discontinuity-free behavior the
  proposal advertises.
- No `ROTOR_TRIM_RESET` events needed — the aircraft owns trim. This removes a whole
  class of "rig fighting the aircraft's trim" bugs.

**Collective** (`msfs_update_collective`):
- Spring center `cpO_y` ← `ffbTrimCollective` (respecting the inverted collective axis
  convention already documented in
  [HPGHelicopter.msfs_update_collective](../telemffb/sim/msfs_xp/HPGHelicopter.py#L620)).
- `ffbTrOnCollective` → unclutch branch (soften spring, follow stick), analogous to
  the collective force-trim-pressed path.

**Pedals** (`msfs_update_pedals`):
- Spring center ← `ffbTrimPedals`.
- `ffbTrOnPedals` → unclutch branch.

**Hydraulics (all axes):** not gated by a feature bit — `_HYD_ASSIST_LOSS` is defined
for every axis with `0` = full boost as the safe default, so it is consumed whenever
the control is enabled.

Two corrections to the original sketch, both found while implementing:

1. **The value is inverted, not pass-through.** `HydraulicLossMixIn.hydraulic_factor`
   is hydraulic *health* — `1` = full boost, and
   [the effect only relaxes below `hydraulic_loss_threshold`](../telemffb/sim/base/HydraulicLossMixIn.py).
   The spec's `_HYD_ASSIST_LOSS` is *loss* — `1` = unassisted/locked. Assigning one to
   the other directly would invert the whole effect: a healthy aircraft would feel
   locked. The conversion is `health = 1.0 - loss`.
2. **Inject `HydSys` rather than branch inside the mixin.** Setting
   `telem_data.HydSys = 1.0 - loss` before the effect mixins run lets the existing
   float path, threshold and damper/inertia/friction scaling all execute unchanged —
   no edit to `HydraulicLossMixIn` at all. For a two-axis control the worst axis wins.

Implemented in `FFBApiMixIn._ffb_api_apply_hydraulic_loss`, called from
`ffb_api_on_telemetry` early in `Helicopter.on_telemetry` so the injected value is in
place before `HydraulicLossMixIn.on_telemetry` reads it. This overrides any `HydSys`
from `sc_overrides` while the API is active, which is intended: the API is
authoritative for a control it owns.

**Fly-through (write-back):** reuse the existing detection —
[`check_hands_on`](../telemffb/sim/msfs_xp/Helicopter.py#L121),
[`check_feet_on`](../telemffb/sim/msfs_xp/HPGHelicopter.py#L171), and the force-based
mode from HPG. When the rig has the hardware to sense force, prefer force; otherwise
use position deviation from the spring center.

**Decision (§10.3): reuse the existing `send_individual_hands_on`-style toggle,
defaulting to true per-axis detection.** Users on hardware that cannot cleanly isolate
pitch from roll can fall back to per-control detection mirrored onto both cyclic axis
vars. The spec's variables stay per-axis in both modes — only the *source* of the value
changes — so the aircraft contract is unaffected either way.

Write per-axis:
`L:FFB_CYCLIC_PITCH_FLY_THROUGH`, `L:FFB_CYCLIC_ROLL_FLY_THROUGH`,
`L:FFB_COLLECTIVE_FLY_THROUGH`, `L:FFB_PEDALS_FLY_THROUGH`. This is the same code path
as the current `L:FFB_HANDS_ON_*` writes — generalize
[`_dispatch_hands_on_state`](../telemffb/sim/msfs_xp/Helicopter.py#L152) to emit the
standardized var names when the API is active.

### 4.4 Sign / unit conventions

The proposal mandates: normalized −1..+1, `0` = neutral/mid-travel; positive =
nose-up / roll-right / collective-up / right-pedal (yaw right). TelemFFB internally
uses ±4096 fixed-point for spring center/coefficient/offset. Centralize the
`normalized → ±4096` conversion (and the collective inversion) in one helper on the
class so sign bugs live in exactly one place. Cross-check the collective sign against
the existing inverted convention table in HPG before wiring.

---

## 5. Configuration & XML

- **No new aircraft class to register** (§10.1 chose a mixin). Instead, expose the
  mixin's parameters on the existing heli classes in `defaults.xml`, so they are
  available on all six vendor classes plus generic `Helicopter`. Parameters should be
  visibly grouped (e.g. an "FFB API" section) and ideally shown as inactive when the
  loaded aircraft reports `API_VERSION 0`, so users aren't tuning knobs that do
  nothing.
- Reuse existing user parameters where they map (`trim_release_spring_gain`,
  `cyclic_spring_gain`, `collective_ap_spring_gain`, `hpg_pedal_spring_gain`,
  fly-through / hands-on deadzones and force thresholds). Rename the exposed
  fly-through knobs away from "hands_on"/"feet_on" toward neutral "fly_through_*"
  labels for the new class, keeping the underlying implementation shared.
- Add a settings enum / toggle only if we expose a user-facing "FFB API mode"
  override; default behavior should be automatic based on discovery.
- No changes to `sc_overrides` are required for the built-in read vars (Option A),
  but leave `sc_overrides` working so power users can still add extra aircraft LVARs.

---

## 6. Multi-instance considerations

- Each device instance independently subscribes the read vars and writes its own
  control's `ENABLED` + `FLY_THROUGH`. No new IPC messages are needed.
- `FFB_API_VERSION` is read identically by every instance; each gates itself.
- Confirm the collective/pedals **child** instances actually establish their own
  SimConnect connections and can write LVARs (they do — each process constructs its
  own `SimConnectManager`). Verify during the spike that a child instance's
  `set_simdatum_to_msfs` reaches MSFS.
- Shutdown: the disable-write on `on_timeout` covers per-instance sim exit; add a
  best-effort `ENABLED = 0` write in the master/children shutdown path
  (`main.py` Phase 16) so a clean quit leaves the aircraft in normal mode.

---

## 7. Edge cases & failure modes

| Case | Behavior |
|---|---|
| Aircraft doesn't implement API (`FFB_API_VERSION` absent → 0) | `UNSUPPORTED`; class behaves like generic `Helicopter`. Never write **any** `L:FFB_*` var (spec §4 step 1) — not `ENABLED`, not `FLY_THROUGH`. |
| `FFB_FEATURES` absent / `0` with `API_VERSION >= 1` | Legal. No control is trimmed: no trim spring anywhere, `_TRIM` and `_TR_ON` ignored. Hydraulics and fly-through still active. Log once. |
| `FFB_FEATURES` has bits set above bit 2 | Unknown future capabilities — mask off and ignore; never treat an unknown bit as an error or as "no features". |
| Read vars published while our control is *not* enabled | Ignore them (spec §3.4). Consuming `_TRIM` in normal mode would fight the aircraft's own trim, which is exactly what the mode gate prevents. |
| Control enabled but its trim bit is `0` | Enabled is still correct (hydraulics + fly-through). Spring behavior comes from local settings only. |
| API version > TelemFFB knows | Treat as `>= 1` for the features we understand; log once. Version field is forward-compatible. |
| Rig crashes with `ENABLED = 1` | Aircraft must default to `0` on reload (spec) and should time out on stale-enable; document the dependency. Best-effort shutdown write mitigates clean exits. |
| `HYD_ASSIST_LOSS` absent | Defaults to `0` = full boost. Correct for non-hydraulic aircraft. |
| `FFB_*_TRIM` jumps (aircraft publishes coarse/stepped values) | **Consume raw (§10.4).** No rig-side filtering. A stepped `_TRIM` is an aircraft-side spec violation (§3.4: it tracks the actuator, updated every frame) and should be reported as such, not papered over. Revisit only if a shipped aircraft proves otherwise. |
| Device not connected on a child instance | Don't write `ENABLED` for that control; other controls unaffected. |
| User binds axes in MSFS vs. `telemffb_controls_axes` | Support both (§10.2), no new default. Trim is applied via spring center only, never double-applied to a sent axis. |
| Vendor class (HPG, SAS, XAW109) on an API-implementing aircraft | API wins per control while that control's `ENABLED == 1`; the vendor control method for that control is replaced at the dispatch site, not blended (§4.3). Cowan / FlyInside / Taog have no competing trim path and need nothing. |
| Mixed enablement (e.g. FFB cyclic, normal collective) on a vendor class | Falls out of per-dispatch-site gating: API cyclic path + untouched vendor collective path, no special case. |

---

## 8. Testing plan

- **Unit (pytest, `BaseTelemetryEffectTestCase`):** feed synthetic telemetry frames
  with the `FFB_*` fields set; assert spring center tracks `FFB_*_TRIM`, unclutch
  softens the spring on `TR_ON`, hydraulic factor equals `HYD_ASSIST_LOSS`, and
  `ENABLED`/`FLY_THROUGH` writes fire (assert via `MockSimConnect`). Mark
  `helicopter` + `msfs`.
- **Discovery:** version `0`/absent → no `L:FFB_*` write at all (assert `MockSimConnect`
  saw zero FFB writes, including `FLY_THROUGH`) and generic behavior; version `1` →
  enable path.
- **Capability gating:** `FFB_FEATURES` bit matrix — for `0b000`, `0b001`, `0b010`,
  `0b100`, `0b111`, and a value with a reserved high bit set, assert exactly the
  expected controls get a `_TRIM`-driven spring center and the others do not, and that
  hydraulics/fly-through are unaffected in every case. Assert `FEATURES` is latched:
  mutating it on a later frame must not change behavior until the aircraft changes.
- **Vendor precedence:** for `HPGHelicopter`, `SASHelicopter` and `XAW109Helicopter`,
  feed an API-enabled frame and assert the vendor control method is **not called** for
  that control (spy on `msfs_update_heli_controls` / `_pedals` / `_collective`) and the
  spring center comes from `_TRIM` alone. Add a mixed-enablement case: cyclic enabled,
  collective not → API cyclic path *and* vendor collective path both run. This is the
  regression that protects the mixin decision (§4.3).
- **Axis ownership:** run the spring/trim assertions under both
  `telemffb_controls_axes = True` and `False`; with `True`, additionally assert the
  sent axis value is the raw input and carries no trim contribution (§10.2).
- **Multi-instance:** verify each of joystick/collective/pedals writes only its own
  `ENABLED`.
- **Manual / in-sim:** a stub aircraft (or a modified test heli) that implements the
  aircraft side minimally — publishes a fixed `FFB_API_VERSION=1` and a movable
  `FFB_CYCLIC_PITCH_TRIM` — to validate end-to-end with real hardware. Coordinate a
  reference aircraft with the addon-dev side of the proposal.

---

## 9. Suggested milestones

1. **Spike (read-only):** subscribe `FFB_API_VERSION` + `FFB_FEATURES` + `FFB_CYCLIC_*_TRIM`, log
   values in-sim against a test aircraft. Validates transport + discovery assumptions
   (esp. missing-LVAR behavior and child-instance write capability).
2. **Read path + BaseTelemetryData fields + discovery gating**, including the
   `FFB_FEATURES` latch/decode and the "no writes while unsupported" rule.
3. **`FFBApiMixIn` cyclic:** spring center from `FFB_*_TRIM`, `TR_ON` unclutch,
   `ENABLED` lifecycle, `FLY_THROUGH` write. Validate on hardware.
3b. **Dispatch-site guards** (three call sites, two shared files) plus the vendor
   precedence tests for HPG / SAS / XAW109. Land with step 3 rather than after it: the
   guards *are* the mechanism by which the API path runs at all, not a follow-up
   cleanup.
4. **Collective + pedals** (same pattern, mind collective inversion).
5. **Hydraulics** via `HYD_ASSIST_LOSS`.
6. **Config surface in `defaults.xml`, shutdown disable-write, docs, tests.**
7. **(Later) X-Plane parity** via the existing dataref plugin, if desired.

---

## 10. Decisions

All six questions resolved 2026-09-04. Recorded with rationale so they are not
re-litigated; each links to where it is implemented above.

1. **Class wiring → mixin on base `Helicopter`.** `FFBApiMixIn`, inert until discovery
   succeeds. *Re-examined against the code after the initial decision; confirmed, and
   the cost came in well under the original estimate.*

   **Cost: three guards at three polymorphic dispatch sites in two shared files, and
   zero vendor-class edits** — not the "six mechanical edits across six classes" first
   estimated. Only three of the six subclasses have a competing trim source at all, and
   all three are reached through those same sites (§4.3).

   Rejected — **explicit `defaults.xml` class selection**: needs a config entry per
   aircraft, defeating discovery. Note that
   [`_resolve_aircraft_class`](../telemffb/telem/TelemManager.py#L411) is config-driven
   first and only falls back to SimConnect type resolution, so this would mean no
   API support at all on any aircraft without a shipped config entry.

   Rejected — **mid-flight class swap to a standalone `FFBApiHelicopter`**: the
   mechanics are cheaper than first assumed
   ([`_initialize_new_aircraft`](../telemffb/telem/TelemManager.py#L384) is a reusable
   re-entry point), but the swap is wrong on the merits. The API covers *only*
   trim/feel/hydraulics, while vendor classes carry much more — HPG alone adds
   `ac_update_vrs_effect`, `check_feet_on` and custom pedal/collective handling.
   Swapping `HPGHelicopter` out for a generic API class to gain standardized trim would
   discard vendor behaviour that stays perfectly valid. Composition is right precisely
   *because* the API is a partial concern; a swap forces an all-or-nothing choice the
   domain doesn't call for.

2. **Axis ownership → honour the user's existing `telemffb_controls_axes` setting.**
   No new default, both models supported and tested. The spec's "rig drives position
   through normal axis inputs" describes what the API *permits*, not something TelemFFB
   forces on users who have working axis-send configurations today. The obligation is
   that the sent axis stays raw so trim is never double-applied — see §4.2.

3. **Fly-through granularity → reuse the existing per-axis/per-control toggle, default
   per-axis.** The vars stay per-axis in both modes; only the detection source changes,
   so the aircraft contract is identical either way. See §4.3.

4. **Trim smoothing → consume `_TRIM` raw.** The spec says runtime vars update every
   frame and `_TRIM` tracks the actuator, so a stepped value is an aircraft-side
   violation to report, not a rig-side problem to filter. Filtering would also add a
   catch-up pull on TR release, where trim follows the stick 1:1. Revisit only with a
   concrete counter-example.

5. **Rig-side capability advertisement → not needed, and not merely deferred.** The
   rig always publishes the best fly-through signal its hardware can produce (force
   where available, position deviation otherwise), and the aircraft has no better
   alternative to fall back to. Telling it *how* the value was derived gives it nothing
   to act on — it would only invite second-guessing a signal it cannot improve. No
   `L:FFB_RIG_FEATURES` / `L:FFB_RIG_VERSION`; `API_VERSION` remains the only gate.
   **This closes the question rather than parking it** — document the reasoning in the
   spec so a future reader doesn't "fix" the asymmetry.

6. **Legacy `L:FFB_HANDS_ON_*` → document as reserved-legacy in the standard.**
   TelemFFB keeps writing `L:FFB_HANDS_ON_CYCLIC` / `L:FFB_HANDS_ON_CYCLICX/Y` /
   `L:FFB_FEET_ON_PEDALS` from the legacy path; `FFBApiMixIn` emits only the
   standardized names. The standard gains a short note listing these as pre-API and
   reserved, so no future revision reuses them and other rig implementers know to
   expect them. This makes the §4 step-1 rule ("write no `L:FFB_*` var to an
   unsupported aircraft") honest about a real, bounded exception.

### Action items outside this repo

- **Spec edit (from §10.6):** add a "reserved legacy names" note to
  [the standard](https://github.com/CK-AT/DIY-FFB/blob/ck_dev/Standards/MSFS_Helicopter_FFB_API.md) covering the three
  `L:FFB_HANDS_ON_*` / `L:FFB_FEET_ON_PEDALS` names.
- **Spec edit (from §10.5):** record why there is deliberately no rig→aircraft
  capability advertisement, so the asymmetry reads as a decision rather than an
  oversight.

---

## 11. Implementation status

Milestones 1-6 are implemented on branch `ck_standard_ffb_heli`. Milestone 7 (X-Plane
parity) is not started and remains optional.

### Files

| File | Change |
|---|---|
| [FFBApiMixIn.py](../telemffb/sim/msfs_xp/FFBApiMixIn.py) | New. Discovery/latching, `ENABLED` lifecycle, feature decode, the three control paths, fly-through, hydraulic conversion. |
| [MsfsXpHeliControlsMixIn.py](../telemffb/sim/msfs_xp/MsfsXpHeliControlsMixIn.py) | Inherits `FFBApiMixIn`; cyclic dispatch guard; `_update_cyclic_trim` suppressed under the API. |
| [Helicopter.py](../telemffb/sim/msfs_xp/Helicopter.py) | Subscribes the API vars; per-frame hook; collective and pedal dispatch guards; release on timeout. |
| [BaseTelemetryData.py](../telemffb/sim/BaseTelemetryData.py) | 13 new documented fields. |
| [defaults.xml](../defaults.xml) | 9 user parameters under an "FFB API" grouping. |
| [utils.py](../telemffb/utils.py) | `release_ffb_api_controls()`, called from `exit_application()`. |
| [tests/test_ffb_api.py](../tests/test_ffb_api.py) | 61 tests. |

### Verified

Full suite: **458 passed** (397 pre-existing + 61 new). One unrelated test,
`test_turbulence_modulator.py::TestHighPassFilter::test_constant_wind_decays_to_zero`,
flakes intermittently under load — it derives `dt` from `time.perf_counter()`, so its
decay assertion depends on wall-clock timing. Confirmed pre-existing (it also fails
with this branch's source changes reverted) and left alone.

### Not verifiable without hardware

No aircraft implements the API yet, so nothing here has been exercised end to end.
Two things specifically need a real rig plus a reference aircraft:

- **Axis sign conventions.** `ffb_api_invert_cyclic_pitch` / `_roll` / `_pedals`
  default to `False` and `ffb_api_invert_collective` to `True` (TelemFFB's device Y
  reads `+1` at full down). Only the collective default is derived from existing code;
  the others are assumptions. They are user parameters precisely so a sign error is a
  config fix, not a code change — all conversion goes through
  `_ffb_api_to_device_sign`.
- **Spring engagement feel.** `_ffb_api_spring_ready` reuses the existing
  `_initialize_*_if_needed` handshake (hold the spring off until the control is within
  0.1 of the trim reference). Whether that threshold feels right under a live
  auto-trim is untested.
