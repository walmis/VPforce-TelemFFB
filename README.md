
# DCS/MSFS/IL-2 FFB Telemetry Effects (for VPforce FFB)

This Python application takes telemetry input a simulator and generates augmented force feedback (FFB) effects for various aircrafts, including jets, helicopters and warbirds. 

Please note that while some effects may not be fully realistic, the main goal is to make the stick more lively and increase immersion during gameplay. 

## Quick Start
While there is a "stable release" version 1.0.0 posted here on github, the vast majority of users are on the "Work in Progress" code base, which 
is more advanced and user friendly than the current 'stable" version.  

To get the latest "WIP" version, simply navigate to https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D 
- Download the latest package, unzip and run the exe
- Don't forget to read the manual (Section 4.2) here: https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit
## Features
Note:  Not all features are listed here, not all features are supported in all simulators
- Turbulence effects that mimic the shaking and vibrations of flying through rough air
- Weapon release effects
- Engine rumble effects (If actual RPM is available) 
- Countermeasure release effects
- Rolling and touchdown effects that simulate the sensation of the wheels touching the ground
- Angle of attack buffeting, which simulates the shaking and vibrations felt when an aircraft approaches its critical angle of attack.
- Helicopter effective translational lift (ETL) effects.
- Helicopter overspeed shaking
- Shows a debug window that displays the received telemetry data.
- G-Loading - Stick force increases as the aircraft g-loading increases
- Deceleration effect - applies forward force on stick when braking
- Motion effects for gear / flaps / spoilers / speedbrake and canopy
- Drag buffeting effects for gear and spoilers
- Improved/Dynamic engine rumble effects for piston aircraft
- Engine rumble for Jet Aircraft
- Afterburner rumble
- Engine rumble for Helicopters

## FFB Device Support
As is, TelemFFB supports only the VPforce Rhino FFB Joystick base, or DIY devices which are using the kits (motor + control board) that are available from VPforce.
<br>
In addition to The Rhino joystick and DIY joystick bases, TelemFFB also offers support for DIY FFB enabled rudder pedals and DIY FFB enabled helicopter collective controls.
### FFB Enabled Joystick (VPforce Rhino or DIY Joystick with VPforce DIY Kit)
Full support for all of the above listed effects
### FFB Enabled Pedals
Pedal behavior with TelemFFB will vary based on the sim in question.  None of the supported sims have native pedal support, so everything below is implemented entirely in TelemFFB.  
In addition to most of the haptic effects listed above, the following is also supported:
- MSFS
  - Auto switching between FBW (fixed spring), Dynamic spring force w/ dynamic slip based forces, and un-sprung for helicopters (configurable dampening)
  - Rudder trim following
  - Rudder autopilot following
  - Nose-wheel shimmy effect
- DCS
  - Auto switching between pedal spring modes (mode override and spring force configuration options available per aircraft)
    - Dynamic spring force based on individual aircraft speed envelope (Warbird default)
    - Fixed spring (Jet default)
    - Un-sprung with configurable dampening (Helicopter default)
  - Rudder trim following for fixed wing aircraft
- IL2 
  - Nothing has been added for pedals in IL-2 as of yet

### FFB Enabled Collective
While the collective is not typically a control one would associate with requiring FFB, there are certain advantages as compared to a collective that relies on friction to stay put or provide dampening.
- Infinitely configurable dampening, friction and inertial FFB effects to dial in the 'feel'
- Ability to play haptic effects (such as engine rumble, ETL buffeting or gunfire) through the device
- Ability to simulate hydraulic loss (not yet implemented)

The collective control implementation in TelemFFB supports sending most of the haptic effects listed above through the collective.
<br>
- For MSFS, there is also a implementation that integrates tightly with the AFCS system on the HPG Airbus H145 and H160 helicopters.  The collective will follow the autopilot and interacts with the collective trim-release controls of those aircraft.

## Force Feedback Support for DCS
DCS has native FFB Joystick support.  As such, TelemFFB is primarily used for generating additional effects per the above list.  
There are some enhancements to the DCS FFB support available within TelemFFB, such as implementing control stick trim following on several aircraft that do not support it.


## Force Feedback Support for  MSFS

MSFS does not have native FFB support.  However, TelemFFB has a custom implementation that will simulate dynamic control forces (for applicable aircraft) in addition to many of the aforementioned effects

In addition to the above effects, the FFB implementation for MSFS supports:

Primary Flight Controls
- Dynamic Flight Control Spring Force
  - Configurable spring centering for aircraft with such systems that also have dynamic forces at the stick
- 'FBW' mode where the spring forces are static
- Force trim implementation for Helicopters
- Special implementation to support full AFCS autopilot interaction for the HPG Airbus helicopters (H145, H160)
- Trim following
- Autopilot following (for fixed-wing aircraft)

## Force Feedback support for X-Plane 11/12

As with MSFS, X-Plane does not support FFB natively.  Using the same effects library in TelemFFB, a plugin has been developed to export the necessary telemetry from X-Plane to enable all of the same effects listed in the MSFS section.  

TelemFFB will automatically install the export plugin when you configure and enable X-Plane in the TelemFFB Settings

## Force feedback support for IL-2 Sturmovik
Similar to DCS, IL-2 has native FFB joystick support.  TelemFFB implements many of the same effects listed above for IL-2.  
Note that there are several effects implemented in TelemFFB that are duplications of effects already supported by IL-2 (Gunfire, Stall/Drag buffeting and ground-roll).  If you chose to enable these effects in TelemFFB, it is recommended to disable the 'shake' force in the IL-2 FFB settings.

## Requirements
- Python 3.9+
- Git (https://git-scm.com/) to download the source from Github
- DCS World, MSFS, or IL-2
- VPforce Rhino FFB Joystick/kit, DIY Pedals or DIY Collective

## Documentation
- The latest stable version documentation can be found here:  https://vpforce.eu/downloads/VPforce_Rhino_Manual.pdf
- The latest 'work in progress' documentation is available at this Google Drive link:  https://docs.google.com/document/d/1YL5DLkiTxlaNx_zKHEYSs25PjmGtQ6_WZDk58_SGt8Y/edit  
<br>
Note that the documentation may be in-flux between 'wip' releases and may show features that are not yet released.

## Installing and running (EXE distribution)

### Downloading
If you are not git-savy or simply don't want to deal with python, git, and cloning repositories and would rather just use an all-in-one executable distribution...

- The stable (master) branch release version is available here:   https://github.com/walmis/VPforce-TelemFFB/releases

- The development ('wip', possibly unstable) branch auto-builds are available here after each commit to the repository: https://vpforcecontrols.com/downloads/TelemFFB/?C=M;O=D
<br>
### Installing and running
Simply extract the zip file into a folder on your PC and run the exe
<br>
### Updating
- There is no automated update for the stable version downloaded from GitHub releases page.  If a new version is released, it will require you to download the new version and install it on top of the previous version

- For the 'wip' development versions, there is a built-in updater as part of TelemFFB that will look to the build server for new versions when TelemFFB starts.  The update procedure will automatically update TelemFFB in-place if you choose to.


## Installing Running from source
### Installation
* Go to [releases](https://github.com/walmis/VPforce-TelemFFB/releases) page and download the latest precompiled version

* To make some changes in the scripts, you can download the repository and play around with the sources:

	1. Open a power shell/CMD window where you want to download the TelemFFB source
	2. Clone the repository to your local machine, this will create a directory `VPforce-TelemFFB`
	
		`git clone https://github.com/walmis/VPforce-TelemFFB.git`

	3. Install the required Python packages	`pip install -r requirements.txt`

	4. When running the program for the first time, it will prompt you to install the export.lua script in your `user/Saved Games/DCS` folder. This script is necessary for the program to receive telemetry data from DCS World.

### Updating
`git pull` - pulls the latest revision

If local changes conflict with remote, you can reset to the latest version:

```
git reset --hard origin/master
git pull
```

### Usage
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

### Linux 

The program can run on linux with some cavats

- IL2 is unsupported
- Selecting VPForce profiles is unsupported
- Finding a master instance is unsupported. Make sure to not start multiple instances

the `LOCALAPPDATA` environment variable needs to be set. Set it to a path where you want configurations to be stored

## Contributing and Development
If you're interested in contributing to the project, feel free to submit pull requests or open issues on the Github page. Your feedback and suggestions are always welcome!
<br>
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
