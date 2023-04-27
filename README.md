
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
- Shows a debug window that displays the received telemetry data.

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
0. Open a power shell/CMD window where you want to download the TelemFFB source

1. Clone the repository to your local machine, this will create a directory `VPforce-TelemFFB`

   `git clone https://github.com/walmis/VPforce-TelemFFB.git`

2. Install the required Python packages
`pip install -r requirements.txt`

3. When running the program for the first time, it will prompt you to install the export.lua script in your `user/Saved Games/DCS` folder. This script is necessary for the program to receive telemetry data from DCS World.

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

2. Telemetry effects mainly uses Constant Force and Periodic effects. On the Rhino it was tested with **50% Periodic** effect slider, and **100% CF** effect slider setting.
3. Run DCS World
4. Enjoy enhanced FFB effects while flying various aircrafts in DCS World!

## Contributing
If you're interested in contributing to the project, feel free to submit pull requests or open issues on the Github page. Your feedback and suggestions are always welcome!
