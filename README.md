# VPforce TelemFFB

TelemFFB is an open source Python/Qt application that reads telemetry from a flight simulator and turns it into force feedback (FFB) effects on your controls. For simulators without native force feedback it provides the whole FFB implementation, including the spring forces on the stick. For simulators with native force feedback it adds the effects the game does not render itself.

> **Full documentation:** https://docs.vpforce.eu/telemffb/

## Supported Simulators

| Simulator | Native FFB | What TelemFFB does |
|---|---|---|
| Microsoft Flight Simulator 2020 / 2024 | No | Complete FFB implementation: dynamic spring forces, trim and autopilot following, all haptic effects |
| X-Plane 11 / 12 | No | Complete FFB implementation, through an auto-installed plugin |
| DCS World | Yes | Supplemental haptic effects on top of the game's spring, or the whole spring through the DirectInput Tap |
| IL-2 Sturmovik: Great Battles | Yes | Supplemental haptic effects, spring overrides, DirectInput Tap |
| IL-2 Sturmovik: Korea | Yes | Its own sim in TelemFFB, with separate profiles and an engine shake effect from the game's FFB telemetry stream |
| Falcon BMS 4.38+ (beta) | Limited | Haptic effects such as gunfire and buffeting |

For MSFS and X-Plane, spring force is computed from dynamic pressure across the aircraft's speed envelope.

For DCS, IL-2 and BMS, the default behavior is that the native game generated spring and other effects (usually just stall buffeting) are left for the game to manage via its native DirectInput implementation while TelemFFB plays its library of other effects.

For those DirectInput native sims, there are a host of spring override modes available which can override the game default spring and give you finer control.

## Supported Devices

- **VPforce Rhino** joystick base
- **DIY joystick bases, rudder pedals, collectives and trim wheels** built on VPforce motor kits
- **Third-party DirectInput force feedback devices** through [DirectLink](https://directlink.flyfrisby.com), an optional integration enabled in System Settings > Integrations. DirectLink is a separate download.

Several devices run at once. The master instance launches a child instance for each additional device, and every instance's settings are managed from the master window. More than one joystick can be configured, a stick and a yoke for example, and MSFS and X-Plane aircraft can switch between them per aircraft.

## Quick Start

Download the latest release, extract the zip and run the exe. There is no installer.

- **Releases:** https://github.com/walmis/VPforce-TelemFFB/releases

On first launch the System Settings dialog walks you through your devices and simulators. Enabling DCS installs its export module, enabling X-Plane installs the plugin, and the DirectInput Tap for DCS, IL-2 and BMS is installed from the same dialog. See the [documentation](https://docs.vpforce.eu/telemffb/) for setup details.

## Features

Not every feature applies to every simulator.

**Flight control forces (MSFS and X-Plane)**
- Dynamic spring force from aerodynamic pressure, with fly-by-wire and spring-centered modes
- Advanced spring curve editor: per-axis gain over a configurable airspeed range
- Trim following and autopilot following on joystick, pedals and trim wheel
- Automated elevator trim calibration: a short flown routine measures the aircraft's real trim response and produces a speed-aware trim-following curve
- Trimmed stick position modes: the stick follows the trim, or stays centered
- G-force loading, deceleration force, elevator droop and low hydraulic pressure effects
- Controls Lock: detent, spring and damper hold the controls when the aircraft's control lock is engaged

**Haptic effects**
- Engine rumble for piston, jet, turboprop and helicopter engines, including afterburner
- Angle of attack and stall buffeting, with a classic steady shake or a dynamic style that deepens with the stall
- Turbulence driven by the sim's wind data, decomposed into pitch, roll and yaw
- Gunfire, weapon release, rocket, bomb and countermeasure effects
- Runway rumble, touchdown, nosewheel shimmy and steering friction
- Gear, flap, spoiler, speedbrake, canopy, tail hook, fuel boom and wing fold motion and buffeting
- Aircraft damage and hit events
- Helicopters: ETL shaking, vortex ring state, overspeed shake, blade slap and rotor rumble

**Helicopters (MSFS and X-Plane)**
- Force trim emulation with trim release button support
- Collective spring, damper, friction and inertia with configurable gains
- Special implementations for the HPG H145 / H160 (AFCS integration on cyclic, pedals and collective), X-Trident AW109, Taog H500, CowanSim and FlyInside helicopters

**Profiles and settings**
- Shipped profiles for many aircraft, with notes on each aircraft's quirks
- Most specific profile match wins. When one of your profiles overlaps a shipped one, TelemFFB offers to merge them
- Fork button: split the loaded aircraft off a broader profile into one of its own
- Settings change live while flying, from the Settings tab or from an in-sim panel for MSFS and X-Plane
- Offline editor for aircraft that are not loaded, with an effect preview that plays any effect on the device from synthesized telemetry
- Telemetry Overrides editor: re-source a telemetry item from an add-on's custom variable or dataref
- Per-aircraft VPforce Configurator profiles and gain overrides
- Profile Manager for creating, cloning, exporting and importing profiles

## Configuration

There are two types of settings, each with different storage locations and formats.

**Aircraft settings:**
Aircraft settings are stored in the user configuration file  `userconfig_v2.xml` which is stored in your user folder at `%LOCALAPPDATA%\VPForce-TelemFFB`.

**System Settings:**
System settings are stored in the Windows registry under `HKEY_CURRENT_USER\Software\VPforce\TelemFFB`.

## Antivirus

TelemFFB is packaged with PyInstaller, which some antivirus tools flag. The source is public and the dependencies are standard PyPI packages. If the exe is flagged, allow it manually or submit it to your antivirus vendor for review.

## Running from Source

Requirements: Python 3.12 and Git. TelemFFB runs on Windows only.

```bash
git clone https://github.com/walmis/VPforce-TelemFFB.git
cd VPforce-TelemFFB
pip install -r requirements.txt
python main.py
```

The SimConnect client is vendored under `simconnect/`, so no separate install is needed for MSFS.

To update:

```bash
git pull
```

To discard local changes and match the default branch:

```bash
git reset --hard origin/refactor
```

Tests need the development requirements and run in parallel:

```bash
pip install -r requirements-dev.txt
pytest -n auto
```

After editing `resources.qrc`, regenerate `resources.py` with `makeresources.bat`. Release builds use PyInstaller with `VPforce-TelemFFB.spec`.

## Contributing and Development

Pull requests and issues are welcome on the [GitHub page](https://github.com/walmis/VPforce-TelemFFB).

Effects are built from MixIns under `telemffb/sim/base/`, one effect category per file, composed into aircraft classes. DCS and BMS aircraft live in `telemffb/sim/aircrafts_dcs.py`, IL-2 aircraft in `telemffb/sim/aircrafts_il2.py`, and MSFS and X-Plane aircraft one class per file under `telemffb/sim/msfs_xp/`. A MixIn reads the telemetry frame and updates the effects it owns:

```python
@override
def on_telemetry(self, telem):
    super().on_telemetry(telem)
    self._create_weapon_effect("gunfire", "Gun", telem.Gun, self.gunfire_effect_enabled,
                               self.gun_vibration_intensity, shape=EFFECT_SAWTOOTHUP)
```

Start with these before changing code:

- `AGENTS.md`: architecture, conventions and the rules the codebase follows
- `docs/adding_an_aircraft_class.md`: how to add or restructure an aircraft class
- `docs/defaults_xml_reference.md`: the shipped settings file and how settings resolve
- `docs/adding_an_effect_preview.md`: how to give a new effect a preview in the offline editor

## License

GPL v3. See `COPYING.txt`.
