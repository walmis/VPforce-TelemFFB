import telemffb.utils as utils
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHDOWN, EFFECT_SAWTOOTHUP, EFFECT_SQUARE
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.util.conversions import kt2ms

import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MotionEffectsMixIn(AircraftEffectUtilsBase):
    """Contains motion/rumble/haptic related configuration, state and handlers.

    This mixin centralises motion-related attributes and methods such as flaps,
    canopy, landing gear, speedbrakes, spoilers, tailhook, fuelboom, wingfold,
    runway rumble and touchdown effects. Instance state used by these
    handlers is initialised in __init__ so AircraftBase can remain a thin
    delegator.
    """

    # Motion / haptic configuration (defaults moved from AircraftBase)
    # user parameters
    speedbrake_motion_intensity : float = 0.12
    speedbrake_buffet_intensity : float = 0.15
    speedbrake_speed_thresh :   float = 80 * kt2ms

    speedbrake_motion_effect_enabled: bool = False
    speedbrake_buffet_effect_enabled: bool = False

    spoiler_motion_intensity: float = 0.0
    spoiler_buffet_intensity: float = 0.15
    spoiler_spd_thresh_low: float = 80 * kt2ms
    spoiler_spd_thresh_hi: float = 140 * kt2ms
    spoiler_motion_effect_enabled: bool = False
    spoiler_buffet_effect_enabled: bool = False

    flaps_motion_intensity : float = 0.12
    flaps_buffet_intensity : float = 0.0
    flaps_motion_effect_enabled: bool = False

    canopy_motion_intensity : float = 0.12
    canopy_buffet_intensity : float = 0.0
    canopy_motion_effect_enabled: bool = False

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity: float = 0.12
    gear_buffet_effect_enabled: bool = True
    gear_buffet_intensity: float = 0.15
    gear_buffet_freq = 10
    gear_buffet_speed_low = 100
    gear_buffet_speed_high = 150

    tailhook_motion_effect_enabled: bool = False
    tailhook_motion_intensity: float = 0.12

    fuelboom_motion_effect_enabled: bool = False
    fuelboom_motion_intensity: float = 0.12

    wingfold_motion_effect_enabled: bool = False
    wingfold_motion_intensity: float = 0.10

    runway_rumble_intensity: float = 1.0
    runway_rumble_enabled: bool = False

    touchdown_effect_enabled: bool = False
    touchdown_effect_max_force: float = 0.5
    touchdown_effect_max_gs: float = 3.0
    # end of user parameters

    def __init__(self, *args, **kwargs):
        # Ensure cooperative multiple-inheritance init ordering
        super().__init__(*args, **kwargs)

        # Instance state used by motion/haptic handlers
        self.last_device_x = 0
        self.last_device_y = 0
        # smoother used by some motion effects — per-instance to avoid shared state
        self.smoother = utils.Smoother()

        self.center_wheel_hpf = utils.HighPassFilter(cutoff_freq_hz=3)
        self.side_wheels_hpf = utils.HighPassFilter(cutoff_freq_hz=3)

    def _bms_taxi_bumps(self, telem_data: BaseTelemetryData):
        """Generates a bump effect in response to bumpIntensity telemetry for Falcon BMS simulator"""
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            self.effects.dispose("runway_bump0", "runway_bump1")
            return
        bump = telem_data.BumpIntensity
        if bump and self.anything_has_changed("BumpIntensity", bump):
            intensity = utils.clamp(bump * self.runway_rumble_intensity, 0, 1)
            self.effects['runway_bump0'].periodic(15, intensity * .75, direction=0, effect_type=EFFECT_SQUARE, duration=80).start()
            self.effects['runway_bump1'].periodic(15, intensity, direction=0, effect_type=EFFECT_SQUARE, phase=180, duration=160).start()

    def ac_update_flaps(self, telem_data: BaseTelemetryData):
        """Play motion haptic when flap position changes.

        Telemetry:
            Read: Flaps       - Union[float, List[float]] (0.0–1.0); flap deployment;
                                 list max taken; primary source
                  flaps_value - float (0.0–1.0); fallback when Flaps is None
        """
        flapspos = telem_data.Flaps
        if flapspos is None:
            flapspos = telem_data.flaps_value
        if flapspos is None:
            return

        if isinstance(flapspos, list):
            flapspos = max(flapspos)
        if self.anything_has_changed("Flaps", flapspos, delta_ms=100) and self.flaps_motion_intensity > 0 and self.flaps_motion_effect_enabled:
            logging.debug(f"Flaps Pos: {flapspos}")
            direction = 90 if self.is_pedals() else 0
            self.effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, direction, 3).start()
        else:
            self.effects["flapsmovement"].stop(destroy_after=5000)

    def ac_update_runway_rumble(self, telem_data: BaseTelemetryData):
        """Add wheel-based rumble effects for immersion.
        Generates bumps/etc on touchdown, rolling, field landing etc.

        Telemetry:
            Read: WeightOnWheels - List[float] ([center, left, right], compression 0.0–1.0);
                                   [0] drives center-wheel HPF (Y axis), [1]–[2] difference
                                   drives side-wheel HPF (X axis)
            Read (BMS only): BumpIntensity - float (0.0–1.0); direct bump impulse from sim;
                                              replaces WeightOnWheels path for BMS
        """
        if self._sim_is_bms():
            # Fake it since BMS does not have weight on wheels - only has 'bumps' telemetry
            self._bms_taxi_bumps(telem_data)
            return

        if self.is_collective(): return
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            self.effects.dispose("runway0", "runway1")
            return

        WoW = telem_data.WeightOnWheels or (0, 0, 0)

        v1 = self.center_wheel_hpf.update((WoW[0])) * self.runway_rumble_intensity
        v2 = self.side_wheels_hpf.update(WoW[1] - WoW[2]) * self.runway_rumble_intensity

        v1 = utils.clamp_minmax(v1, 0.5)
        v2 = utils.clamp_minmax(v2, 0.5)

        tot_weight = sum(WoW)

        if tot_weight:
            logging.debug(f"Runway Rumble : v1 = {v1}. v2 = {v2}")
            self.effects["runway0"].constant(v1, utils.RandomDirectionModulator).envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             attackTime=1000,          # 1000ms attack
         ).start()
            self.effects["runway1"].constant(v2, utils.RandomDirectionModulator).envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             attackTime=1000,          # 1000ms attack
         ).start()
        else:
            self.effects.dispose("runway0", "runway1")

    def ac_update_touchdown_effect(self, telem_data: BaseTelemetryData):
        """Generates a G-based forward force upon landing or large bump.

        Telemetry:
            Read (MSFS/XPLANE): AccBody[1] - float (g); vertical body-frame acceleration;
                                              index [1] taken directly
            Read (DCS/IL2/BMS): ACCs[1]    - float (g); vertical G with 1 g level-flight bias;
                                              index [1] minus 1.0 gives net vertical G
            Read: SimOnGround  - int (0 or 1); effect active only when on the ground (1)
        """

        max_force = self.touchdown_effect_max_force
        max_g = self.touchdown_effect_max_gs
        if self.is_collective() or self.is_pedals():
            return
        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is('BMS'):
            gs = round((telem_data.ACCs or [0, 0])[1] - 1, 2)
        elif self._sim_is("MSFS") or self._sim_is("XPLANE"):
            gs = round((telem_data.AccBody or [0, 0])[1], 2)
        else:
            return

        if not self.touchdown_effect_enabled:
            self.effects.dispose("touchdown")
            return
        on_ground = telem_data.SimOnGround or 0
        if not on_ground:
            self.effects.dispose("touchdown")
            return
        force = round(utils.scale_clamp(gs, (0, self.touchdown_effect_max_gs), (0,self.touchdown_effect_max_force)), 2)

        logging.debug(f"Touchdown Effect: Realtime Gs: {gs}, Force:{force}")
        self.effects['touchdown'].constant(force, 180).start()

    def ac_update_canopy(self, telem_data: BaseTelemetryData):
        """Play motion haptic when canopy position changes.

        Telemetry:
            Read: Canopy       - float (0.0=closed, 1.0=open); canopy deployment; primary source
                  canopy_value - float (0.0–1.0); fallback when Canopy is None
        """
        canopypos = telem_data.Canopy
        if canopypos is None:
            canopypos = telem_data.canopy_value
        if canopypos is None:
            return
        if self.anything_has_changed("Canopy", canopypos, delta_ms=100) and self.canopy_motion_intensity > 0 and self.canopy_motion_effect_enabled:
            logging.debug(f"Canopy Pos: {canopypos}")
            direction = 90 if self.is_pedals() else 0
            self.effects["canopymovement"].periodic(120, self.canopy_motion_intensity, direction, 3).start()
        else:
            if canopypos == 0 and self.effects['canopymovement'].started:
                self.effects['canopyclunk'].periodic(10, utils.clamp((self.canopy_motion_intensity * 2), 0, 1), 180, effect_type=EFFECT_SQUARE,duration=40).start()
            self.effects["canopymovement"].stop(destroy_after=5000)

    def ac_update_landing_gear(self, telem_data: BaseTelemetryData):
        """Play gear motion haptic and gear-down buffet.

        Telemetry:
            Read: gear_value      - float (0.0=up, 1.0=down); primary gear position
                  GearPos         - float (0.0–1.0); fallback when gear_value is None
            Read (MSFS/XPLANE): RetractableGear - Union[bool, int, List] (0 or 1); if False/0
                                                   skips motion (fixed gear aircraft)
                                Gear            - List[float] (0.0–1.0 per gear); MSFS/XP gear
                                                   deployment list; max taken
            Read: IAS             - float (m/s, ≥ 0); indicated airspeed for buffet scaling;
                                     primary airspeed source
                  TAS             - float (m/s, ≥ 0); true airspeed; fallback when IAS is None
            Read (XPLANE): Vle   - float (m/s); max gear-extended speed; sets gear buffet
                                    speed thresholds (low = 0.9 × Vle, high = 1.17 × Vle)
        """
        gearpos = telem_data.gear_value if telem_data.gear_value is not None else telem_data.GearPos
        if isinstance(gearpos, list):
            gearpos = max(gearpos)

        if self._sim_is_msfs() or self._sim_is_xplane():
            retracts = telem_data.RetractableGear or 0
            if isinstance(retracts, list):
                retracts = max(retracts)
            if (self.gear_motion_intensity > 0) and (retracts):
                gearpos = max(telem_data.get("Gear", 0))

        if gearpos is None:
            return

        airspd = telem_data.IAS if telem_data.IAS is not None else telem_data.TAS
        if airspd is None:
            return

        if self._sim_is_xplane():
            self.gear_buffet_speed_low = 0.9 * (self.telem_data.Vle or 10000)
            self.gear_buffet_speed_high = self.gear_buffet_speed_low * 1.3

        rumble_freq = self.gear_buffet_freq

        if self.anything_has_changed("gear_value", gearpos, 50) and self.gear_motion_intensity > 0 and self.gear_motion_effect_enabled:
            logging.debug(f"Landing Gear Pos: {gearpos}")
            self.effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            self.effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 90, 3, phase=120).start()
            if (gearpos == 0 or gearpos == 1) and self.is_joystick():
                dir = 0 if gearpos == 0 else 180
                self.effects['gearclunk'].periodic(10, utils.clamp((self.gear_motion_intensity * 3), 0, 1), dir, effect_type=EFFECT_SQUARE,duration=40).start()
        else:
            self.effects.dispose("gearmovement", "gearmovement2")

        if (airspd > self.gear_buffet_speed_low and gearpos > .1) and self.gear_buffet_intensity > 0 and self.gear_buffet_effect_enabled:
            realtime_intensity = utils.scale_clamp(airspd, (self.gear_buffet_speed_low, self.gear_buffet_speed_high),(0, self.gear_buffet_intensity)) * gearpos
            self.effects["gearbuffet"].periodic(rumble_freq, realtime_intensity, 0, 4).start()
            self.effects["gearbuffet2"].periodic(rumble_freq, realtime_intensity, 90, 4).start()
            logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            self.effects.dispose("gearbuffet", "gearbuffet2")

    def ac_update_speed_brakes(self, telem_data: BaseTelemetryData):
        """Play speedbrake motion haptic and buffet.

        Telemetry:
            Read: AircraftClass    - str; skips effect entirely for 'Helicopter'
                  SpeedbrakePos    - float (0.0–1.0); speedbrake deployment; primary source
                  speedbrakes_value - float (0.0–1.0); fallback when SpeedbrakePos is None
                  IAS              - float (m/s, ≥ 0); indicated airspeed for buffet scaling;
                                      primary airspeed source
                  TAS              - float (m/s, ≥ 0); true airspeed; fallback when IAS is None
        """
        if (self._telem_data.AircraftClass or "GenericAircraft") == 'Helicopter':
            return

        spd_thresh = self.speedbrake_speed_thresh
        spdbrk = telem_data.SpeedbrakePos
        if spdbrk is None:
            spdbrk = telem_data.speedbrakes_value

        if spdbrk is None:
            return

        airspd = telem_data.IAS if telem_data.IAS is not None else telem_data.TAS
        if airspd is None:
            return
        if self.anything_has_changed("speedbrakes_value", spdbrk, 50) and self.speedbrake_motion_intensity > 0 and self.speedbrake_motion_effect_enabled:
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            direction = 90 if self.is_pedals() else 0
            self.effects["speedbrakemovement"].periodic(180, self.speedbrake_motion_intensity, direction, 3).start()
        else:
            self.effects.dispose("speedbrakemovement")

        config = {
            'enabled_attr': 'speedbrake_buffet_effect_enabled',
            'intensity_attr': 'speedbrake_buffet_intensity',
            'effect_names': ['speedbrakebuffet'],
            'effect_name': 'speedbrake',
            'threshold_speed': spd_thresh,
            'threshold_value': 0.1,
            'deployment_key': 'speedbrakes_value',
            'directions': [utils.RandomDirectionModulator],
            'require_airborne': False
        }
        temp_telem = dict(self._telem_data)
        temp_telem['speedbrakes_value'] = spdbrk
        temp_telem['TAS'] = airspd
        self._create_buffeting_effect(temp_telem, config)

    def ac_update_spoilers(self, telem_data: BaseTelemetryData):
        """Play spoiler motion haptic and buffet.

        Telemetry:
            Read: AircraftClass - str; skips effect entirely for 'Helicopter'
                  Spoilers      - Union[float, List[float]] (0.0–1.0); spoiler deployment;
                                   list-averaged; effect suppressed when 0 or None
                  IAS           - float (m/s, ≥ 0); indicated airspeed; primary airspeed source
                  TAS           - float (m/s, ≥ 0); true airspeed; fallback when IAS is None;
                                   drives tas_intensity scaling relative to spoiler speed thresholds
        """
        if (self._telem_data.AircraftClass or "GenericAircraft") == 'Helicopter':
            return

        spd_thresh_low = self.spoiler_spd_thresh_low
        spd_thresh_hi  = self.spoiler_spd_thresh_hi

        airspd = telem_data.IAS if telem_data.IAS is not None else telem_data.TAS
        if airspd is None:
            return

        tas_intensity = utils.clamp_minmax(utils.scale(airspd, (spd_thresh_low, spd_thresh_hi), (0.0, 1.0)), 1.0)
        spoiler = telem_data.Spoilers

        if spoiler == 0 or spoiler == None:
            self.effects.dispose("spoilermovement", "spoilermovement2")
            return
        if isinstance(spoiler, list):
            spoiler = sum(spoiler) / len(spoiler)

        if self.spoiler_motion_intensity > 0 and self.spoiler_motion_intensity > 0 and self.spoiler_motion_effect_enabled:
            if self.anything_has_changed("Spoilers", spoiler, delta_ms=50):
                logging.debug(f"Spoilers Pos: {spoiler}")
                self.effects["spoilermovement"].periodic(118, self.spoiler_motion_intensity, 0, 4).start()
                self.effects["spoilermovement2"].periodic(118, self.spoiler_motion_intensity, 90, 4).start()
            else:
                logging.debug("Destroying Spoiler Effects")
                for effect in ["spoilermovement", "spoilermovement2"]:
                    self.effects[effect].stop(1000)

        if airspd > spd_thresh_low and spoiler > .1 and self.spoiler_buffet_intensity > 0 and self.spoiler_buffet_effect_enabled:
            realtime_intensity = self.spoiler_buffet_intensity * spoiler * tas_intensity
            logging.debug(f"PLAYING SPOILER RUMBLE | intensity: {realtime_intensity}, d-factor: {spoiler}, s-factor: {tas_intensity}")
            self.effects["spoilerbuffet1-1"].periodic(15, realtime_intensity, 0, 4).start()
            self.effects["spoilerbuffet1-2"].periodic(16, realtime_intensity, 0, 4).start()
            self.effects["spoilerbuffet2-1"].periodic(14, realtime_intensity, 90, 4).start()
            self.effects["spoilerbuffet2-2"].periodic(18, realtime_intensity, 90, 4).start()
        else:
            for effect in ["spoilerbuffet1-1", "spoilerbuffet1-2", "spoilerbuffet2-1", "spoilerbuffet2-2"]:
                self.effects[effect].stop(1000)

    def ac_update_tailhook_effect(self, telem_data: BaseTelemetryData):
        """Play tailhook motion haptic when hook position changes.

        Telemetry:
            Read: TailHook - float (0.0=retracted, 1.0=extended); hook deployment position
        """
        hook = telem_data.TailHook
        if hook is None: return
        if not self.is_joystick(): return

        if not self.tailhook_motion_effect_enabled or not self.tailhook_motion_intensity:
            self.effects.dispose('hookmovement')
            return

        if self.anything_has_changed("tailhook_value", hook, delta_ms=200):
            logging.debug(f"Hook Pos: {hook}")
            direction = 90 if self.is_pedals() else 0
            self.effects["hookmovement"].periodic(160, self.tailhook_motion_intensity, direction, EFFECT_SAWTOOTHUP).start()
        else:
            if (hook == 0 or hook ==1) and self.effects['hookmovement'].started:
                dir = (1-hook) * 180
                self.effects['clunk'].periodic(10, utils.clamp((self.tailhook_motion_intensity * 2), 0, 1), dir, effect_type=EFFECT_SQUARE,duration=40).start()
            self.effects.dispose("hookmovement")

    def ac_update_fuelboom_effect(self, telem_data: BaseTelemetryData):
        """Play fuel boom motion haptic when boom position changes.

        Telemetry:
            Read: FuelBoom - float (0.0=retracted, 1.0=extended); refueling boom deployment
        """
        config = {
            'telem_key': 'FuelBoom',
            'change_key': 'fuelboom_value',
            'effect_names': ['boommovement'],
            'clunk_effects': ['clunk'],
            'enabled_attr': 'fuelboom_motion_effect_enabled',
            'intensity_attr': 'fuelboom_motion_intensity',
            'frequency': 150,
            'directions': [90 if self.is_pedals() else 0],
            'effect_type': EFFECT_SAWTOOTHDOWN,
            'delta_ms': 200,
            'clunk_duration': 40
        }
        self._create_motion_effect(telem_data, config)

    def ac_update_wingfold_effect(self, telem_data: BaseTelemetryData):
        """Play wing fold motion haptic when fold position changes (ground only).

        Telemetry:
            Read: WingFold    - float (0.0=unfolded, 1.0=folded); wing fold deployment
                  SimOnGround - int (0 or 1); effect suppressed when airborne (0)
        """
        config = {
            'telem_key': 'WingFold',
            'change_key': 'wingfold_value',
            'effect_names': ['wingfoldmovement_1', 'wingfoldmovement_2'],
            'clunk_effects': ['wingfoldclunk1', 'wingfoldclunk2'],
            'enabled_attr': 'wingfold_motion_effect_enabled',
            'intensity_attr': 'wingfold_motion_intensity',
            'frequency': 100,
            'directions': [45, 225],
            'effect_type': EFFECT_SAWTOOTHDOWN,
            'delta_ms': 200,
            'phase_offset': 90,
            'require_ground': True,
            'clunk_directions': [90, 270],
            'clunk_duration': 100
        }
        self._create_motion_effect(telem_data, config)

    def _create_motion_effect(self, telem_data: BaseTelemetryData, config: dict):
        """Generic method for motion effects (tailhook, fuelboom, wingfold, etc.)."""
        value = telem_data.get(config['telem_key'])
        if value is None:
            return

        if self._should_skip_joystick_effect():
            return

        if config.get('require_ground', False):
            on_ground = telem_data.get('SimOnGround', 0)
            if not on_ground:
                self.effects.dispose(*config['effect_names'])
                return

        enabled = getattr(self, config['enabled_attr'])
        intensity = getattr(self, config['intensity_attr'])

        if not enabled or not intensity:
            self.effects.dispose(*config['effect_names'])
            return

        delta_ms = config.get('delta_ms', 200)
        if self.anything_has_changed(config['change_key'], value, delta_ms=delta_ms):
            logging.debug(f"{config['telem_key']} Pos: {value}")

            for i, effect_name in enumerate(config['effect_names']):
                direction = config['directions'][i] if i < len(config['directions']) else config['directions'][0]
                effect_type = config.get('effect_type', 0)
                phase = config.get('phase_offset', 0) if i > 0 else 0

                self.effects[effect_name].periodic(
                    config['frequency'],
                    intensity,
                    direction,
                    effect_type,
                    phase=phase
                ).start()
        else:
            clunk_effects = config.get('clunk_effects', [])
            if clunk_effects and (value == 0 or value == 1):
                if any(self.effects[name].started for name in config['effect_names']):
                    clunk_intensity = utils.clamp(intensity * 2, 0, 1)

                    for i, clunk_name in enumerate(clunk_effects):
                        direction = config.get('clunk_directions', [180, 0])[i % 2]

            self.effects.dispose(*config['effect_names'])

    def _create_buffeting_effect(self, telem_data: BaseTelemetryData, config: dict):
        enabled = getattr(self, config['enabled_attr'])
        intensity = getattr(self, config['intensity_attr'])

        if not enabled or not intensity:
            self.effects.dispose(*config['effect_names'])
            return

        tas = telem_data.get("TAS", 0)
        if tas < config.get('threshold_speed', 0):
            self.effects.dispose(*config['effect_names'])
            return

        deployment = telem_data.get(config['deployment_key'], 0)
        if deployment <= config.get('threshold_value', 0.1):
            self.effects.dispose(*config['effect_names'])
            return

        if config.get('require_airborne', True) and self._should_skip_airborne_effect(telem_data):
            self.effects.dispose(*config['effect_names'])
            return

        speed_low, speed_high = config.get('speed_range', (0, 100))
        tas_intensity = utils.scale_clamp(tas, (speed_low, speed_high), (0.0, 1.0))

        if isinstance(deployment, list):
            if config.get('special_calc') and "F-14" in str(telem_data.N or ""):
                inner = (deployment[1], deployment[2])
                outer = (deployment[0], deployment[3])
                deployment = (0.85 * sum(inner) + 0.15 * sum(outer)) / 2
            else:
                deployment = sum(deployment) / len(deployment)

        realtime_intensity = intensity * deployment * tas_intensity
        frequency = getattr(self, config.get('frequency_attr', 'frequency'), 13)

        for i, effect_name in enumerate(config['effect_names']):
            direction = config['directions'][i] if i < len(config['directions']) else config['directions'][0]
            self.effects[effect_name].periodic(frequency, realtime_intensity, direction, 4).start()

        logging.debug(f"PLAYING {config['effect_name'].upper()} RUMBLE | intensity: {realtime_intensity}")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_tailhook_effect(telem_data)
        self.ac_update_fuelboom_effect(telem_data)
        self.ac_update_wingfold_effect(telem_data)
        self.ac_update_runway_rumble(telem_data)

        self.ac_update_speed_brakes(telem_data)
        self.ac_update_landing_gear(telem_data)
        self.ac_update_flaps(telem_data)
        self.ac_update_canopy(telem_data)
        self.ac_update_spoilers(telem_data)
        self.ac_update_touchdown_effect(telem_data)
    