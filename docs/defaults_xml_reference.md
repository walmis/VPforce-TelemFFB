# TelemFFB `defaults.xml` Reference

`defaults.xml` is the master configuration schema for TelemFFB. It defines every configurable
setting — its default value, display metadata, UI widget type, simulator/device scope, and
conditional visibility rules. It also contains aircraft-model-specific default overrides and
SimConnect variable mappings.

The file is consumed primarily by `telemffb/xmlutils.py`. The UI is built from the resolved
setting dicts by `telemffb/SettingsLayout.py`. Enum option lists referenced in the file are
defined as class-level dicts in `telemffb/SettingsManager.py`.

> **Recommended editor:** Due to the file's repetitive structure and large size, it is best
> edited with **[XiMpLe](https://www.ximple.cz/)**, which renders the XML in a spreadsheet-like
> grid format and makes bulk editing across many entries practical.

---

## File Structure

```
<TelemFFB>
  <defaults>           ...  (repeated; one per setting definition)
  <models>             ...  (repeated; model-specific defaults in defaults.xml)
  <sc_overrides>       ...  (repeated; SimConnect variable overrides)
  <classdefaults_DCS>  ...  (repeated)
  <classdefaults_BMS>  ...  (repeated)
  <classdefaults_IL2>  ...  (repeated)
  <classdefaults_MSFS> ...  (repeated)
  <classdefaults_XPLANE> ... (repeated)
  <validvalues_overrides> ... (repeated)
</TelemFFB>
```

---

## Setting Resolution — 6-Layer Override Hierarchy

When TelemFFB resolves the effective value of a setting for a given aircraft, sim, and device,
it applies overrides from lowest to highest priority:

| Priority | Source | Scope |
|----------|--------|-------|
| 1 (lowest) | `<defaults>` in `defaults.xml` | All aircraft for the matching sim+device |
| 2 | `<classdefaults_{sim}>` in `defaults.xml` | All aircraft of a given class in a sim |
| 3 | `<simSettings>` in `userconfig.xml` | User override for all aircraft in a sim |
| 4 | `<classSettings>` in `userconfig.xml` | User override for a class within a sim |
| 5 | `<models>` in `defaults.xml` | Pattern-matched aircraft-specific defaults |
| 6 (highest) | `<models>` in `userconfig.xml` | User changes for a specific aircraft |

A higher-priority layer wins for any setting it specifies; settings not present in a higher
layer fall back down the chain.

---

## Section: `<defaults>`

Each `<defaults>` block defines a single configurable setting. The same `name` may appear in
multiple `<defaults>` blocks with **different sim or device filter flags** — the correct one for
the active sim/device combination is selected at runtime. This is how per-sim default values
and per-sim `validvalues` lists are handled.

### Child Elements

| Element | Required | Description |
|---------|----------|-------------|
| `name` | Yes | Internal setting key. Used in code, stored in `userconfig.xml`. Must be unique per sim+device combination. |
| `displayname` | Yes | Human-readable label shown in the settings UI. |
| `datatype` | Yes | Determines the UI widget and value semantics. See [Datatypes](#datatypes) below. |
| `value` | No | Default value for this setting. If omitted, the setting has no baseline default and will **not appear in the UI** for any aircraft unless a matching `<classdefaults_{sim}>` entry supplies the value for that aircraft's class. |
| `validvalues` | Varies | Meaning depends on `datatype`. Comma-separated option list, slider range, or enum dict name. See [Datatypes](#datatypes). |
| `info` | No | Tooltip text. May contain HTML entities (e.g., `&lt;br&gt;`). |
| `grouping` | No | Label of the UI group under which this setting appears. |
| `parentgroup` | No | Organizational label used for XML-internal tracking. Does not affect UI rendering. |
| `prereq` | No | Conditional visibility rule tied to tree parentage. See [The `prereq` Field](#the-prereq-field). |
| `render_prereq` | No | Cross-tree gate: **hide** this setting when the condition (based on other bool settings' values) fails. See [The `render_prereq` and `enable_prereq` Fields](#the-render_prereq-and-enable_prereq-fields). |
| `enable_prereq` | No | Cross-tree gate: render but **disable** this setting (with an auto-generated explanatory tooltip) when the condition fails. See [The `render_prereq` and `enable_prereq` Fields](#the-render_prereq-and-enable_prereq-fields). |
| `debug_only` | No | If `true`, the setting is only parsed/shown when the app is in debug mode (`G.system_settings.get('debug', False)`). See [The `debug_only` Field](#the-debug_only-field). |
| `order` | Yes | Float; controls row position and layout behavior. See [The `order` Field](#the-order-field). |
| `unit` | No | If present, adds a unit dropdown next to the value field. Supported unit groups: speed (`m/s`, `ft/s`, `km/h`, `kts`, `mph`) and length (`m`, `ft`, `km`, `mi`, `nm`). Switching units auto-converts the stored value. |
| `sliderfactor` | No | Scaling factor between slider position and stored value. Required for slider-based datatypes. See [Datatypes](#datatypes). |
| `exclusive_with` | No | Comma-separated list of `bool` setting names. If this setting is enabled (`true`), all listed settings are forced to `false`. |
| `effecttype` | No | The FFB effect category this setting affects (e.g., `spring`, `damper`). Informational only. |

### Simulator Filter Elements

When a `<defaults>` block includes one or more of these, the entry is included only when the
active simulator matches:

| Element | Simulator |
|---------|-----------|
| `<any>true</any>` | All simulators |
| `<DCS>true</DCS>` | DCS World |
| `<BMS>true</BMS>` | Falcon BMS |
| `<IL2>true</IL2>` | IL-2 Sturmovik |
| `<MSFS>true</MSFS>` | Microsoft Flight Simulator (2020/2024) |
| `<XPLANE>true</XPLANE>` | X-Plane |

### Device Filter Elements

Controls which device type sees this setting:

| Element | Device |
|---------|--------|
| `<joystick>true</joystick>` | Joystick / cyclic |
| `<pedals>true</pedals>` | Rudder pedals |
| `<collective>true</collective>` | Collective |
| `<trimwheel>true</trimwheel>` | Trim wheel |

---

## Section: `<models>`

Each `<models>` block is an aircraft-specific default override stored in `defaults.xml`
(priority layer 5). The aircraft name is matched against the `model` regex pattern using
`re.match()`.

### Child Elements

| Element | Description |
|---------|-------------|
| `name` | Setting name — must match a `name` defined in `<defaults>`. |
| `model` | Python regex pattern matched against the full aircraft name string. |
| `value` | The override value for this setting. |
| `sim` | Simulator this entry applies to (`DCS`, `BMS`, `IL2`, `MSFS`, `XPLANE`). |
| `device` | Device type (`joystick`, `pedals`, `collective`, `trimwheel`, `any`). |
| `unit` | Optional unit string, stored alongside the value. |

> **Note:** Aircraft name matching uses `re.match()`, which anchors at the start of the string.
> Use `.*` at the beginning of patterns intended to match anywhere in the name.

---

## Section: `<sc_overrides>`

SimConnect variable override entries. Each block maps a setting name + aircraft pattern to a
specific SimConnect variable or L:var, allowing per-aircraft telemetry source customization.

### Child Elements

| Element | Description |
|---------|-------------|
| `name` | The setting identifier this override applies to. |
| `model` | Python regex pattern matching the aircraft name. |
| `var` | The SimConnect variable name or L:var (`L:VarName`) to use as the data source. |
| `sc_unit` | SimConnect unit string (e.g., `"percent"`, `"feet per second"`). |
| `scale` | Numeric scale factor applied to the raw SimConnect value. |

---

## Section: `<classdefaults_{sim}>`

Class-level default overrides for a specific simulator (priority layer 2). There is one table
per supported simulator:

- `<classdefaults_DCS>`
- `<classdefaults_BMS>`
- `<classdefaults_IL2>`
- `<classdefaults_MSFS>`
- `<classdefaults_XPLANE>`

> `<classdefaults_any>` is a legacy element and should not be used in new entries.

Each block applies a default value for a specific aircraft class + device combination within
a simulator. This allows, for example, the default spring mode to differ between
`PropellerAircraft` and `Helicopter` without creating individual model entries.

### Child Elements

| Element | Description |
|---------|-------------|
| `name` | Setting name — must match a `name` defined in `<defaults>`. |
| `type` | Aircraft class this entry applies to (e.g., `Helicopter`, `JetAircraft`). Prefix with `!` to **prohibit** — `!Helicopter` means the setting is hidden and will not appear in the UI for Helicopter class aircraft. |
| `value` | The default value for this class+sim+device combination. |
| `sim` | Simulator (should match the enclosing element tag). |
| `device` | Device type (`joystick`, `pedals`, `collective`, `trimwheel`, `any`). |
| `unit` | Optional unit string. |

---

## Section: `<validvalues_overrides>`

Overrides the `validvalues` for a specific setting for a given sim + aircraft class + device
combination. The primary use case is providing a different enum list for `enumlist`-typed
settings (such as `spring_mode`) per aircraft class, so that incompatible modes are hidden.

### Child Elements

| Element | Description |
|---------|-------------|
| `name` | Setting name whose `validvalues` are being overridden. |
| `sim` | Simulator filter. |
| `class` | Aircraft class filter (e.g., `Helicopter`, `GliderAircraft`). |
| `validvalues` | Replacement value — for `enumlist` settings, this is the name of an enum dict attribute on `SettingsManager`. |
| `device` | Device type filter. |

---

## Datatypes

The `datatype` field controls both the UI widget rendered for a setting and the relationship
between the stored string value and what the user sees.

### `bool`

Toggle switch.

- Stored value: `"true"` or `"false"`
- `validvalues`: not used
- `sliderfactor`: not used
- When `false`, the setting row is displayed as disabled and any child settings (via `prereq`)
  are hidden.

---

### `float`

Horizontal percentage slider.

- Stored value: decimal float string (e.g., `"0.75"`)
- `validvalues`: `"min,max"` — integer percent range for the slider (e.g., `"0,100"`)
- `sliderfactor`: conversion factor between slider percent position and stored value:
  ```
  stored_value = (slider_pct / 100) × sliderfactor
  slider_pct   = (stored_value / sliderfactor) × 100
  ```
  Example: `sliderfactor=2.0`, slider at 50% → stored `1.0`
- UI label shows the slider position as a percentage.

---

### `n_float`

Identical behavior to `float` but uses a different slider widget (`NoWheelNumberSlider`).

---

### `d_float`

Direct-value horizontal slider (no percentage conversion).

- Stored value: decimal float string
- `validvalues`: `"min,max"` — direct slider position range
- `sliderfactor`: stored value = `round(slider_pos × factor, 2)`
- UI label shows the actual stored value (not a percentage).

---

### `pct_float`

Percent-value slider stored as a decimal fraction.

- Stored value: decimal fraction (e.g., `"0.043"` represents 4.3%)
- `validvalues`: `"min,max"` — slider range in the percent domain
- `sliderfactor`: each slider step = `factor` percent:
  ```
  percent_value  = slider_pos × sliderfactor
  stored_value   = percent_value / 100
  ```
  Example: `sliderfactor=0.1`, slider at 43 → `4.3%` displayed, `0.043` stored.

---

### `cfgfloat`

Percentage slider stored as a decimal fraction; `sliderfactor` does not affect the save path.

- Stored value: `slider_pct / 100` (e.g., slider at 75 → stored `"0.75"`)
- `validvalues`: `"min,max"` — slider range

---

### `d_int`

Direct-value integer slider.

- Stored value: integer string
- `validvalues`: `"min,max"` — slider position range
- `sliderfactor`: stored value = `round(slider_pos × factor)` as integer

---

### `int`

Free-text input field for integer values.

- Stored value: integer string
- `validvalues`: not used for display

---

### `anyfloat`

Free-text input field for float values. Uses the same widget as `int`.

- Stored value: float string

---

### `text`

Plain text input field.

- Stored value: arbitrary string

---

### `spin_int`

Spin box with increment/decrement arrows.

- Stored value: integer string
- `validvalues`: `"min,max"` — spin box minimum and maximum values

---

### `list`

Fixed-choice dropdown.

- Stored value: one of the listed option strings
- `validvalues`: comma-separated list of allowed string options

---

### `anylist`

Editable dropdown (user may also type a custom value).

- Stored value: any string
- `validvalues`: comma-separated list of suggested options shown in the dropdown

---

### `enumlist`

Dropdown populated from a named Python enum dict in `SettingsManager`.

- Stored value: the enum member **name** (e.g., `"BASIC"`, `"FORCETRIM"`)
- `validvalues`: the **attribute name** of a dict on `SettingsManager`
  (e.g., `MSFS_XP_JOYSTICK_SPRING_MODE`)
- The UI shows the human-readable label (dict value); the stored key is the enum member name.
- Available dict names are defined as class-level attributes on `SettingsManager`:

  | Dict name | Applies to |
  |-----------|------------|
  | `MSFS_XP_JOYSTICK_SPRING_MODE` | MSFS/XP joystick (fixed-wing) |
  | `MSFS_XP_PEDAL_SPRING_MODE` | MSFS/XP pedals (fixed-wing) |
  | `MSFS_XP_GILDER_JOYSTICK_SPRING_MODE` | MSFS/XP joystick (gliders) |
  | `MSFS_XP_HELI_JOYSTICK_SPRING_MODE` | MSFS/XP joystick (helicopter) |
  | `MSFS_XP_HELI_PEDAL_SPRING_MODE` | MSFS/XP pedals (helicopter) |
  | `MSFS_XP_HELI_COLLECTIVE_SPRING_MODE` | MSFS/XP collective (helicopter) |
  | `MSFS_XP_FT_ONLY_JOYSTICK_SPRING_MODE` | MSFS/XP joystick (force-trim only) |
  | `MSFS_XP_FT_ONLY_COLLECTIVE_SPRING_MODE` | MSFS/XP collective (force-trim only) |
  | `MSFS_XP_FT_ONLY_PEDAL_SPRING_MODE` | MSFS/XP pedals (force-trim only) |
  | `DCS_IL2_PEDAL_SPRING_MODE` | DCS/IL-2 pedals |
  | `DCS_IL2_JOYSTICK_SPRING_MODE` | DCS/IL-2 joystick (fixed-wing) |
  | `DCS_HELI_JOYSTICK_SPRING_MODE` | DCS joystick (helicopter) |
  | `DCS_HELI_PEDAL_SPRING_MODE` | DCS pedals (helicopter) |
  | `DCS_HELI_COLLECTIVE_SPRING_MODE` | DCS collective (helicopter) |
  | `MSFS_XP_G_EFFECT_MODE` | MSFS/XP G-effect mode |
  | `DCS_IL2_G_EFFECT_MODE` | DCS/IL-2 G-effect mode |

> When a `<validvalues_overrides>` entry applies, its `validvalues` replaces the one from
> `<defaults>` for that sim+class+device combination.

---

### `devicelist`

Dropdown of the joystick role's configured devices (the per-aircraft device selection).

- Stored value: the devpath of a configured joystick slot, or `"primary"` (the default) for
  whichever device is marked active in System Settings.
- `validvalues`: not used — the options are built at render time from the configured slots
  (`utils.joystick_device_choices`), labeled by stored ident with USB ids appended.
- A stored devpath that no longer matches a configured slot renders as
  "(no longer configured)" and stays deselectable; at aircraft load such a reference falls
  back to the primary device.
- Used by exactly one setting: `joystick_device`. When a slot's device is replaced in System
  Settings, the save offers to rewrite user-config references to the outgoing devpath
  (`xmlutils.update_joystick_device_references`).

---

### `button`

USB HID button capture. Clicking the button prompts the user to press a physical button.

- Stored value: integer button number (`"0"` = unassigned, renders as "Click to Configure")

---

### `advspr`

Button that opens the Advanced Spring Gain dialog (speed-vs-force curve editor).

- Stored value: JSON string with curve and gain configuration, or `"none"` if unconfigured

---

### `advgs`

Button that opens the Advanced G-Effect dialog (g-force curve editor).

- Stored value: JSON string with g-effect curve configuration, or `"none"` if unconfigured

---

### `configurator`

Button that opens the Gain Override Configurator dialog.

- Stored value: JSON string with a gain override dict, or `"none"` if unconfigured

---

### `path`

File browse button for selecting a `.vpconf` VPforce Configurator profile.

- Stored value: absolute filesystem path string (`"-"` if not set)

---

### `group`

Section header. Renders as a bold, clickable label with no value entry widget.
Child settings reference this group's `name` via their `prereq` field.

- Stored value: **must be** `"true"` — children are only retained (`eliminate_no_prereq`)
  and revealed (`is_visible`) when their parent group's value is `true`.
- A `group` item with no children pointing to it via `prereq` is automatically hidden.

**Top-level vs nested groups.** A group with no `prereq` is a top-level section header
(Aerodynamics, Inertial, …): it is pinned to column 0 and its children start at indent 0.
A group that carries its own `prereq` is a *nested* sub-header — it renders as an ordinary
indented row and its children indent one level further. Nested groups let a set of related
settings collapse together without inventing a dummy `bool` toggle to hang them under.
Example: `tap_axis_group` (`prereq=spring_mode.DINPUT_TAP`) collecting the three tap axis
correction toggles.

**Whether a group collapses is decided by `order`:**

| Group `order` | Behavior |
|---------------|----------|
| Contains `.0` (e.g. `50.0`) | **Locked open** — no arrow; children always shown while the parent chain is satisfied (`basic_group`) |
| Anything else (e.g. `10000`, `700.2`) | **Collapsible** — label gains a ►/▼ arrow that toggles its children |

A nested group's children must dodge both magic suffixes themselves: no `.0` (that would
make the child a locked group container) and no trailing `1` (bump-up). A workable set is
group `700.2` with children `700.22`, `700.23`, `700.24`.

---

### `convert`

Internal migration/conversion type. Always invisible; never rendered in the UI.

---

## The `prereq` Field

The `prereq` field controls conditional visibility. A setting is only shown in the UI when
its `prereq` condition is satisfied **and** all ancestor conditions in the chain are also
satisfied.

### Syntax

```
prereq = setting_name
prereq = setting_name.VALUE1.VALUE2...
```

- `prereq=basic_group` — visible when `basic_group` value is `"true"`
- `prereq=spring_mode.BASIC.CENTER` — visible when `spring_mode` is `"BASIC"` or `"CENTER"`
- No `prereq` — always shown (top-level setting)

### Evaluation

Visibility is resolved recursively up the ancestor chain. A setting is visible only if:
1. Its own `prereq` condition is satisfied, **and**
2. Its parent (the setting named in `prereq`) is itself visible and expanded in the UI.

> **Name-collision hazard:** the parent lookup is a *substring* match
> (`parent_name in child_prereq`), not an equality test. A new setting whose name is a
> substring of another setting's name — or of a value-qualified prereq string — can
> resolve to the wrong parent, and a name that is a substring of its *own* prereq can
> make a row its own parent (guarded against, but it renders as hidden). Keep new names
> distinctive; `tests/test_settings_nested_groups.py` demonstrates the check.

---

## The `debug_only` Field

The `debug_only` field gates a setting behind the app's debug mode, independent of `prereq`.

### Syntax

```
debug_only = true
```

- `debug_only=true` — the entry is dropped entirely while parsing `defaults.xml`
  (`read_xml_file()` in `xmlutils.py`) unless `G.system_settings.get('debug', False)` is true.
  When debug mode is off, the setting never reaches `SettingsLayout.py` — it behaves as if it
  doesn't exist in the XML at all, rather than being shown disabled.
- Omitted (or anything other than `true`) — the setting is evaluated normally, with no
  dependency on debug mode.

`debug_only` can be combined with `prereq` on the same entry; both conditions must be
satisfied for the setting to appear.

---

## The `render_prereq` and `enable_prereq` Fields

Where `prereq` couples a setting's visibility to its **tree parent**, these two fields gate a
setting on the current value of **any other bool setting(s)**, regardless of where they sit in
the hierarchy. Use them when a setting is made irrelevant or conflicting by a control elsewhere
in the tree — e.g. two legacy trim knobs that become inert once the auto-calibrated trim curve
is enabled.

| Field | Effect when the condition **fails** |
|-------|-------------------------------------|
| `render_prereq` | The setting is **hidden** (removed from the form entirely). |
| `enable_prereq` | The setting still **renders but is disabled/greyed**, and its tooltip is prefixed with an auto-generated reason (e.g. *"Disabled because Use Calibrated Trim Curve is enabled"*). |

### Syntax (both fields)

```
render_prereq = name
enable_prereq = !name
render_prereq = name1,!name2
```

- Comma-separated list of tokens. `name` requires that setting be `true`; `!name` requires it
  be `false`. **All** tokens must pass for the condition to hold.
- Each referenced name must be an existing `bool` setting (enforced by the schema tests).
- A referenced setting absent from the resolved set is treated as `false`.

### Evaluation

Unlike `debug_only` (a parse-time gate against a global), these depend on another setting's
**resolved value**, so they are evaluated in the view layer (`SettingsLayout.apply_conditional_gates`),
after normal `prereq` visibility and before invisible rows are dropped. Evaluation is against the
referenced setting's **value**, not its visibility — so the gate still works when the controlling
toggle is itself collapsed or hidden.

### Precedence

`render_prereq` (hide) wins over `enable_prereq` (disable) if both are present and both fail — a
hidden row cannot be disabled. The two may target different controllers (e.g. hide when a feature
is inapplicable, disable when a conflicting setting is active).

### Example

```xml
<!-- Inert once the calibrated trim curve is in use -->
<enable_prereq>!joystick_trim_follow_use_curve_y</enable_prereq>
```

on `joystick_trim_follow_gain_virtual_y` and `joystick_ap_y_follow_axis`: both stay visible but
grey out with an explanatory tooltip whenever `joystick_trim_follow_use_curve_y` is `true`.

---

## The `order` Field

A float that controls row position and layout behavior within the settings list.

The integer part establishes sort order among all settings. The decimal suffix controls
special layout behaviors:

| Pattern | Behavior |
|---------|----------|
| Integer only (e.g., `100`) | Normal row, sorted by this position |
| Ends in `.0` (e.g., `50.0`, `200.0`) | Group container — always shown when parent is expanded; no expansion arrow |
| Ends in `1` with a decimal point (e.g., `200.1`, `200.31`) | **Bump-up** — rendered on the same row as its `prereq` parent when enabled, replacing the parent's own entry widget |
| Other decimal suffix (e.g., `200.2`, `200.32`) | Child setting — rendered in a collapsible section below the parent, which gains an expand/collapse arrow |

### Bump-up rule (important)

The bump-up behavior is triggered by the **last character** of the order string being `'1'`
and the string containing a decimal point. This is not strictly about the tenths digit:

- `200.1` → bumps up ✓ (ends in `1`)
- `200.31` → bumps up ✓ (ends in `1`)
- `200.2` → collapsible section (ends in `2`)
- `200.32` → collapsible section (ends in `2`)

The parent's own decimal suffix has no effect on how its children are rendered — only the
child's order string is evaluated.

### Expander arrow logic

A parent setting shows an expand/collapse arrow (collapsible section) when:
- It has **2 or more** child settings (any decimal suffix), **or**
- It has exactly **1** child setting whose order does **not** trigger bump-up

A parent with exactly 1 bump-up child shows **no** expander arrow — the child simply
appears on the same row as the parent toggle when enabled.

> **Note:** The `.0` group-container check uses string containment (`'.0' in order`), so an
> order value like `200.0` suppresses the expander, as expected. The value `200.01` would
> also match this check, so that suffix should be avoided.

---

## `userconfig.xml` Structure

The `userconfig.xml` file stores all user-authored changes. It is separate from `defaults.xml`
and is never shipped with the application.

| Section | Priority Layer | Description |
|---------|----------------|-------------|
| `<simSettings>` | 3 | User overrides for all aircraft in a given simulator |
| `<classSettings>` | 4 | User overrides for an aircraft class within a simulator |
| `<models>` | 6 | User overrides for a specific aircraft (matched by regex) |
| `<sc_overrides>` | — | User-defined SimConnect variable overrides |
| `<profileMappings>` | — | Maps aircraft name patterns to named user profiles |

`xmlutils.py` reads and writes all sections of `userconfig.xml`. The function
`consolidate_sort_and_write_userconfig()` deduplicates and canonically sorts the file before
writing to prevent drift.

---

## Adding a New Setting

1. Add one or more `<defaults>` blocks with the appropriate sim and device filter flags.
2. Set `datatype`, `value`, `validvalues`, and `sliderfactor` appropriately (see
   [Datatypes](#datatypes)).
3. Set `prereq` to the name of a parent `bool` or `group` setting if the new setting should
   only be visible when that parent is enabled. If visibility or enablement should instead
   depend on a setting **elsewhere** in the tree (not its parent), use `render_prereq` (hide)
   or `enable_prereq` (disable) — see
   [The `render_prereq` and `enable_prereq` Fields](#the-render_prereq-and-enable_prereq-fields).
4. Set `order` to a value that places the row logically within its group.
5. Add `<classdefaults_{sim}>` entries if the default value should differ by aircraft class.
6. Add `<validvalues_overrides>` entries if the dropdown options should differ by class.
7. Add the corresponding Python attribute to the effect mixin class with the same name as the
   `name` field so TelemFFB reads the value at runtime.

---

## Aircraft Class Names

The following class names are used in `type`, `prereq`, and `<classdefaults_{sim}>` entries:

| Class | Used in |
|-------|---------|
| `PropellerAircraft` | DCS, MSFS, XPLANE |
| `TurbopropAircraft` | MSFS, XPLANE |
| `JetAircraft` | DCS, BMS, IL2, MSFS, XPLANE |
| `GliderAircraft` | MSFS, XPLANE |
| `Helicopter` | DCS, IL2, MSFS, XPLANE |
| `HPGHelicopter` | MSFS (HPG addon helicopters) |
| `SASHelicopter` | MSFS |
| `FlyInsideHelicopter` | MSFS |
| `CowanSimHelicopter` | MSFS |
| `TaogH500Helicopter` | MSFS |
| `XAW109Helicopter` | XPLANE |
