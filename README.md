
# DCS/MSFS FFB Telemetry Effects (for VPforce FFB)

This Python application takes input from DCS telemetry via UDP or MSFS Telemetry via SimConnect and generates augmented force feedback (FFB) effects for various aircrafts in DCS World or MSFS, including jets, helicopters and warbirds. 

Please note that while some effects may not be fully realistic, the main goal is to make the stick more lively and increase immersion during gameplay. 
Also this early version is very bare-bones and community-driven development is very welcome!

## Features
- Turbulence effects that mimic the shaking and vibrations of flying through rough air
- Weapon release effects
- Engine rumble effects (If actual RPM is available) 
- Countermeasure release effects
- Rolling and touchdown effects that simulate the sensation of the wheels touching the ground
- Angle of attack buffeting, which simulates the shaking and vibrations felt when an aircraft approaches its critical angle of attack.
- Helicopter effective translational lift (ETL) effects.
- Helicopter overspeed shaking
- Shows a debug window that displays the received telemetry data.
## Work in progress / new features

Many new features have been recently added including:

- G-Loading - Stick force increases as the aircraft g-loading increases
- Deceleration effect - applies forward force on stick when braking
- Motion effects for gear / flaps / spoilers / speedbrake and canopy
- Drag buffeting effects for gear and spoilers
- Improved/Dynamic engine rumble effects for piston aircraft
- Engine rumble for Jet Aircraft
- Afterburner rumble
- Engine rumble for Helicopters
- Customizable weapons release effect direction (0-359) or random
- AoA reduction force to simulate such features as in the F/A-18
## Improved MSFS Support

MSFS support in TelemFFB has been significantly extended.  A Majority of the effects listed above are supported (where applicable).

Primary Flight Controls
- Dynamic Flight Control Spring Force (existing TelemFFB Feature)
  - Recommend to use VPForce Configurator Stick Spring + Hardware Force Trim for Helicopters as TelemFFB
does not yet support native force trim with MSFS.
- FBW Flight Controls (static spring forces)
- "Spring Centered" flight controls (such as in modern gliders)

  
New Effects
 - G Loading Effect
 - Deceleration Force Effect
 - Aoa/Stall Buffeting
 - Weight on Wheels (runway rumble, touchdown effects)
 - Gear Motion
 - Gear Turbulence
 - Flaps Motion
 - Speedbrake/Spoiler Motion
 - Speedbrake/Spoiler Turbulence
 - Piston engine rumble
 - Afterburner Effect (see notes in config.ini)
 - Helicopter Engine Rumble
 - Helicopter ETL Effect

## Enabling support for MSFS

The DCS telemetry listener is always active, however the MSFS listener must be explicitly enabled.

There are 2 way to enable support for MSFS in TelemFFB
- Edit the config file and set the `msfs_enabled` flag to "1"
- use the '-s' command line argument `-s MSFS`
## Executable Versions
If you are not git-savy or simply dont want to deal with python, git, and cloning repositories and would rather just use an all-in-one executable distribution...

- The main (stable) branch release version is available here:   https://github.com/walmis/VPforce-TelemFFB/releases
- The development (possibly unstable) branch auto-builds are available here after each commit to the repository: https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=A
## Development

- This framework is made to be very easy to manage FFB effect lifecycles and develop additional effects or specialize specific aircraft effects.
Main action is going on in `aircrafts_dcs.py` or `aircrafts_msfs.py`. 
For example to create an effect that plays a "bump" when a gun round is fired:
```python
        if self.has_changed("Gun") or self.has_changed("CannonShells"): 
            effects["cm"].stop() # stop if effect was previously running
            #start Sine effect with frequency 10Hz, preconfigured intensity, 45 deg angle and total duration of 50ms
            effects["cm"].periodic(10, self.gun_vibration_intensity, 45, duration=50).start()
```
There is no need to create and manage the effect objects. All is done automatically when accessing the effects[] object.

## Caveats

- The initial version of this framework has a very general implementation for all DCS aircrafts. Each aircraft would need to have it's own tailored FFB effect settings and aircraft specific features.
- When initially developing this framework with SDL2 Haptic API, I've quickly encountered a problem that two DirectInput apps cannot manage effects at the same time. So a raw solution using `hidapi` was developed. It talks directly to the `VPforce Rhino FFB` device via HID commands and manages effects without interfering with DCSs own FFB effects.

## Requirements
- Python 3
- Git (https://git-scm.com/) to download the source from Github
- DCS World and a VPforce Rhino FFB Joystick/kit

## Installation

* Go to [releases](https://github.com/walmis/VPforce-TelemFFB/releases) page and download the latest precompiled version

* To make some changes in the scripts, you can download the repository and play around with the sources:

	1. Open a power shell/CMD window where you want to download the TelemFFB source
	2. Clone the repository to your local machine, this will create a directory `VPforce-TelemFFB`
	
		`git clone https://github.com/walmis/VPforce-TelemFFB.git`

	3. Install the required Python packages	`pip install -r requirements.txt`

	4. When running the program for the first time, it will prompt you to install the export.lua script in your `user/Saved Games/DCS` folder. This script is necessary for the program to receive telemetry data from DCS World.

## Updating
`git pull` - pulls the latest revision

If local changes conflict with remote, you can reset to the latest version:

```
git reset --hard origin/master
git pull
```

## Usage
1. Run the application

	`python main.py`

optional arguments: 
- `--help` show the available parameters
- `--teleplot` is the destination IP:port address for teleplot.fr telemetry plotting service (default is "127.0.0.1:47269"). This service allows you to plot telemetry data over time.
- `-p` or `--plot` is used to specify telemetry item names to send to teleplot, separated by spaces
- `-D` or `--device` is used to specify the Rhino device USB VID:PID (default is "ffff:2055")
- `-c` or `--configfile` is used to specify a config file to load (default is "config.ini")

2. Telemetry effects mainly uses Constant Force and Periodic effects. On the Rhino it was tested with **50% Periodic** effect slider, and **100% CF** effect slider setting.
3. Run DCS World
4. Enjoy enhanced FFB effects while flying various aircrafts in DCS World!

Note: If you have multiple VPForce FFB Enabled devices (Rhino, DIY Pedals, etc) it is possible to run multiple instances of TelemFFB if you specify the VID:PID when launching.  You can use the config file option to tune the effects for each peripheral in separate config files

## Contributing
If you're interested in contributing to the project, feel free to submit pull requests or open issues on the Github page. Your feedback and suggestions are always welcome!
