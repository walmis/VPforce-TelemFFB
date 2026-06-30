# VPforce TelemFFB

TelemFFB is an open source, community-driven Python/Qt application that reads telemetry from a flight simulator and uses it to generate force feedback (FFB) effects. The goal is to make your stick more lively and increase immersion — not necessarily perfect realism.

> **Full documentation:** https://docs.vpforce.eu/telemffb/

## Supported Simulators

- **DCS World** — native FFB sim; TelemFFB adds supplemental haptic effects on top
- **Microsoft Flight Simulator (2020/2024)** — no native FFB; TelemFFB implements full dynamic FFB
- **X-Plane 11/12** — no native FFB; TelemFFB implements full dynamic FFB via an auto-installed plugin
- **IL-2 Sturmovik** — native FFB sim; TelemFFB adds supplemental haptic effects on top
- **Falcon BMS 4.38+** *(beta)* — limited native effects; TelemFFB adds haptic effects

For sims **with** native FFB (DCS, IL-2, BMS), TelemFFB primarily adds supplemental effects like engine rumble, gunfire, and helicopter ETL shaking while the sim manages the spring/trim system.

For sims **without** native FFB (MSFS, X-Plane), TelemFFB handles the **entire** FFB implementation, including dynamic spring forces based on calculated aerodynamic pressure throughout the aircraft's speed envelope.

## Quick Start

Download the latest release, extract the zip, and run the exe — no installer needed.

- **Stable releases:** https://github.com/walmis/VPforce-TelemFFB/releases
- **Development builds** (auto-updated): https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D

On first launch, you'll be guided through the system settings to configure your simulator(s) and device(s). See the [documentation](https://docs.vpforce.eu/telemffb/) for full setup details.

## Features

Not all features are available in all simulators.

**Haptic / Supplemental Effects**
- Engine rumble — piston, jet, and helicopter, including afterburner
- Angle of attack and stall buffeting
- Turbulence (dynamic, wind-data-driven gust simulation)
- Weapon release and countermeasure release effects
- Gunfire vibration
- Ground roll and touchdown effects
- Runway rumble and nosewheel shimmy
- Gear, flap, spoiler, speedbrake, and canopy motion effects
- Buffeting from deployed gear and spoilers
- Helicopter ETL (effective translational lift) effect
- Helicopter VRS (vortex ring state) effect
- Helicopter overspeed shaking
- Damage effect
- Uncoordinated turn effect

**Flight Control Forces (MSFS / X-Plane)**
- Dynamic spring force based on aerodynamic pressure across the aircraft's speed envelope
- Fly-By-Wire (FBW) static spring mode
- Spring-centered dynamic mode (minimum spring floor + dynamic forces)
- Advanced spring curve editor — custom per-axis gain mapping over a configurable airspeed range
- G-force effect with exponential or custom-curve configuration
- Deceleration effect (forward force when braking)
- Trim following
- Autopilot following (fixed-wing)
- Helicopter force trim emulation with trim-release button support
- Low hydraulic pressure effect (increased damper/friction/inertia on hyd loss)

**Special Implementations**
- HPG Airbus H145 / H160 (MSFS): Full AFCS integration — collective and cyclic follow the autopilot, force trim release, hands-on detection
- FlyInside helicopters (MSFS): Vibration-model integration
- FFB rudder pedals: Dynamic/fixed/unsprung spring modes, nose-wheel shimmy, trim following, autopilot following
- FFB collective: Configurable damper/friction/inertia, haptic effects playback, HPG AFCS integration

## Supported Devices

TelemFFB supports VPforce FFB devices:

- **VPforce Rhino** joystick base
- **DIY joystick bases** using VPforce motor kits
- **DIY FFB rudder pedals** using VPforce kits
- **DIY FFB collective** using VPforce kits
- **DIY FFB trim wheel** using VPforce kits (MSFS only)

Multiple devices can be run simultaneously. TelemFFB will auto-launch child instances for each additional device, all managed from a single master window.

## Configuration

TelemFFB 2.0 uses a delta-based XML configuration model. Only settings you've changed from defaults are saved, and these are stored in `%LOCALAPPDATA%\VPForce-TelemFFB`. System settings are stored in the Windows registry under `HKEY_CURRENT_USER\Software\VPforce\TelemFFB`.

Settings can be modified in real time from the Settings tab while flying. Changes take effect almost immediately.

Aircraft profiles can be created, cloned, exported, and imported via the Profile Manager. Multiple profiles per aircraft are supported.

## Antivirus / False Positives

TelemFFB is packaged with PyInstaller, which can trigger false positives in Windows Defender and other antivirus tools. The application is safe — all source code is publicly auditable and dependencies are standard PyPI packages. If flagged, allow the app manually or submit it to your AV vendor for review.

## Installing and Running from Source

**Requirements:**
- Python 3.11+
- Git

**Steps:**

```bash
git clone https://github.com/walmis/VPforce-TelemFFB.git
cd VPforce-TelemFFB
pip install -r requirements.txt
python main.py
```

On first run for DCS, TelemFFB will offer to install the required `export.lua` script into your `Saved Games/DCS` folder. For X-Plane, the telemetry plugin is installed automatically when X-Plane is enabled in settings.

**Updating from source:**
```bash
git pull
```

If local changes conflict:
```bash
git reset --hard origin/master
git pull
```

## MCP Telemetry Analysis Server (Developer)

TelemFFB includes an optional [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that exposes live telemetry, configuration state, and FFB effect state for AI-assisted analysis. This lets an LLM inspect what's happening in real time — current aircraft state, historical signal windows, active FFB effects — and reason about tuning suggestions.

The MCP server is a **developer-only** feature and is not included in production builds.

**Setup:**

```bash
pip install -r requirements-dev.txt
```

When the `mcp` package is installed, TelemFFB automatically starts the MCP server on the master instance at `http://127.0.0.1:8089/mcp` (streamable HTTP transport). Without the package, the app runs normally with no impact.

**Available MCP tools:**

| Tool | Description |
|------|-------------|
| `get_current_state` | Snapshot of sim, aircraft, device type, and latest telemetry frame |
| `get_telemetry_window` | Bounded historical window for selected signals (up to 300s) |
| `list_available_signals` | Discover which fields the current sim/aircraft provides |
| `explain_signal` | Documentation and metadata for any telemetry signal |
| `get_effect_state` | Current FFB effect state — active effects, MixIn attributes |

**Example MCP client config** (e.g. for Claude Desktop or VS Code Copilot):

```json
{
  "mcpServers": {
    "telemffb": {
      "url": "http://127.0.0.1:8089/mcp"
    }
  }
}
```

## Contributing and Development

Pull requests and issues are welcome on the [GitHub page](https://github.com/walmis/VPforce-TelemFFB).

The effects framework is designed to make it straightforward to add or customize aircraft-specific behavior. Per-aircraft logic lives in the `aircrafts_dcs.py`, `aircrafts_msfs.py`, and similar files. Effect lifecycles are managed automatically through the `effects[]` object. For example, adding a bump effect when a gun round is fired:

```python
if self.has_changed("Gun") or self.has_changed("CannonShells"):
    effects["cm"].stop()
    effects["cm"].periodic(10, self.gun_vibration_intensity, 45, duration=50).start()
```
