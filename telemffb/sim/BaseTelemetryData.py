from typing import Optional, List, Union, Dict, Any, Tuple, Iterable, Iterator
import time

class BaseTelemetryData:
    """A mapping-backed telemetry container with attribute type-hints.  

    This class intentionally exposes attributes as type hints so IDEs show  
    available members, but all storage is kept in an internal dict. It  
    behaves like a dictionary while also providing dot-access for known  
    attributes.  

    Usage:  
      d = BaseTelemetryData()  
      d['T'] = 123.0  
      d.T == 123.0  
      d.N = 'F-16'  # sets underlying storage  
      d['custom_key'] = 42  # dynamic keys also work  
    """

    # ------------------------------------------------------------------ #
    #  Attribute annotations (for IDEs / static type checkers)
    #
    #  Grouped by domain.  Every field defaults to None when not yet set.
    #
    #  Each field is documented with:
    #    Sims: which simulators provide it (DCS, MSFS, IL2, BMS, XP)
    #    Source: the sim API / variable name that populates it
    #    Units/Format: data type, units, vector component ordering
    #    Notes: special semantics, computed fields, caveats
    # ------------------------------------------------------------------ #

    # -- Identity / source --

    src: Optional[str]
    """Simulator source identifier.  
    Sims: All.  Set by each telem manager before submit_frame().  
    Values: "DCS", "MSFS", "IL2", "BMS", "XPLANE"  
    """

    N: Optional[str]
    """Aircraft name / title as reported by the simulator.  
    DCS: LoGetSelfData().Name (e.g. "F-16C_50")  
    MSFS: TITLE SimVar (e.g. "Airbus A320neo Asobo")  
    IL2: ac_name from shared memory  
    BMS: AcName from shared memory  
    """

    AircraftClass: Optional[str]
    """High-level aircraft classification used by TelemFFB.  
    Set by aircraft_base / MixIns to select effect behavior.  
    Values: "Helicopter", "GliderAircraft", "JetAircraft", "PropellerAircraft", etc.  
    """

    AircraftType: Optional[str]
    """Finer-grained aircraft type identifier.  
    Set by aircraft_base / MixIns.  Mirrors AircraftClass in most cases.  
    """

    FFBType: Optional[str]
    """Device type this telemetry frame targets.  
    Set by TelemManager from G.device_type.  
    Values: "joystick", "pedals", "collective", "trimwheel"  
    """

    ACisFBW: Optional[bool]
    """Whether the aircraft has a fly-by-wire flight control system.  
    MSFS: FLY BY WIRE FAC SWITCH SimVar (bool).  
    Other sims: not available.  
    """

    SimconnectCategory: Optional[str]
    """MSFS aircraft category string.  
    MSFS: CATEGORY SimVar (e.g. "Airplane", "Helicopter").  
    Other sims: not available.  
    """

    EngineType: Optional[int]
    """MSFS engine type enumeration.  
    MSFS: ENGINE TYPE SimVar.  
    Values: 0=Piston, 1=Jet, 2=None, 3=Helo(turbine), 5=Turboprop.  
    Other sims: not available.  
    """

    # -- Timing --

    T: Optional[float]
    """Simulation time / absolute time.  
    DCS: LoGetModelTime() — seconds since mission start.  
    MSFS: ABSOLUTE TIME SimVar — seconds since midnight Jan 1.  
    IL2: tick counter from shared memory state.  
    """

    # -- Airspeed / atmosphere --

    TAS: Optional[float]
    """True airspeed.  
    DCS: LoGetTrueAirSpeed() — m/s.  
    MSFS: AIRSPEED TRUE SimVar — m/s.  
    IL2: indicated_air_speed_metres_second (same as IAS in IL2).  
    BMS: airspeed_kias converted from knots to m/s.  
    """

    TAS_kt: Optional[float]
    """True airspeed in knots.  
    Computed: TAS / 0.514444.  Set by aircraft_base when needed.  
    """

    IAS: Optional[float]
    """Indicated airspeed.  
    DCS: LoGetIndicatedAirSpeed() — m/s.  
    MSFS: AIRSPEED INDICATED SimVar — m/s.  
    IL2: indicated_air_speed_metres_second.  
    BMS: airspeed_kias converted from knots to m/s.  
    """

    IAS_kt: Optional[float]
    """Indicated airspeed in knots.  
    Computed: IAS / 0.514444.  Set by aircraft_base when needed.  
    """

    Mach: Optional[float]
    """Mach number.  
    DCS: LoGetMachNumber().  
    MSFS: computed from TAS and air density.  
    BMS: airspeed_mach from FlightData.  
    IL2: not available.  
    """

    VerticalSpeed: Optional[float]
    """Vertical speed (rate of climb/descent).  
    DCS: LoGetVerticalVelocity() — m/s.  
    MSFS: VERTICAL SPEED SimVar — m/s.  
    IL2/BMS: not available (BMS hardcodes 0).  
    """

    GroundSpeed: Optional[float]
    """Ground speed.  
    DCS: computed from velocity vectors in TelemFFB.lua.  
    MSFS: GROUND VELOCITY SimVar — m/s.  
    IL2/BMS: not available.  
    """

    DynPressure: Optional[float]
    """Dynamic pressure (q = 0.5 * rho * V²).  
    DCS: computed in TelemFFB.lua from AirDensity and TAS — Pa.  
    MSFS: DYNAMIC PRESSURE SimVar — Pa.  
    IL2/BMS: not available.  
    """

    AirDensity: Optional[float]
    """Ambient air density.  
    DCS: barometric formula from altitude in TelemFFB.lua — kg/m³.  
    MSFS: AMBIENT DENSITY SimVar — kg/m³.  
    IL2/BMS: not available.  
    """

    # -- Speed limits --

    DesignSpeed: Optional[Tuple[float, float, float]]
    """MSFS design speeds: [Vc, Vs0, Vs1] — m/s.  
    MSFS: DESIGN SPEED VC / VS0 / VS1 SimVars.  
    Other sims: not available.  
    """

    StallAoA: Optional[float]
    """Stall angle of attack.  
    MSFS: STALL ALPHA SimVar — degrees.  
    Other sims: not available.  
    """

    StallWarning: Optional[Union[bool, int]]
    """Stall warning indicator active.  
    MSFS: STALL WARNING SimVar — bool.  
    """

    WarnAlpha: Optional[float]
    """Warning alpha (approach-to-stall AoA threshold).  
    Currently not populated by any sim.  
    """

    Vle: Optional[float]
    """Maximum landing-gear-extended speed.  
    Currently not populated by any sim.  
    """

    Vne: Optional[float]
    """Never-exceed speed.  
    MSFS: Computed from DESIGN SPEED VC × 1.4 via barometric formula in _calculate_vne_and_gains().  
    XP: Read directly from X-Plane plugin dataref.  
    Can be overridden via vne_override XML setting per aircraft.  
    """

    Vne_ms_calc: Optional[float]
    """Computed Vne in m/s (before vne_override).  
    MSFS/XP: Set by MsfsXpFlightControlsMixIn._calculate_vne_and_gains().  
    """

    Vne_kt: Optional[float]
    """Vne in knots (after vne_override applied).  
    MSFS/XP: Set by MsfsXpFlightControlsMixIn._calculate_vne_and_gains().  
    """

    Qvne: Optional[float]
    """Dynamic pressure at Vne (0.5 * rho * Vne²).  
    MSFS/XP: Computed by MsfsXpFlightControlsMixIn._calculate_vne_and_gains().  
    """

    Qvc_gain: Optional[float]
    """Reciprocal dynamic pressure gain (1 / Qvne) for force scaling.  
    MSFS/XP: Computed by MsfsXpFlightControlsMixIn._calculate_vne_and_gains().  
    """

    Vc_kt: Optional[float]
    """Cruise speed (Vc) in knots.  
    Computed from DesignSpeed[0] (MSFS only).  
    """

    Vso: Optional[float]
    """Stall speed (Vs0 / Vs1) in knots.  
    MSFS: Computed from DesignSpeed[2].  
    XP: Read directly from X-Plane plugin dataref.  
    """

    # -- Angles of attack / sideslip --

    AoA: Optional[float]
    """Angle of attack.  
    DCS: LoGetAngleOfAttack() — converted from radians to degrees in Lua.  
    MSFS: INCIDENCE ALPHA SimVar — degrees.  
    BMS: alpha_deg from FlightData — degrees.  
    IL2: not available.  
    """

    SideSlip: Optional[float]
    """Sideslip angle (beta).  
    DCS: computed from velocity vectors in TelemFFB.lua — degrees.  
    MSFS: INCIDENCE BETA SimVar — degrees.  
    IL2/BMS: not available.  
    """

    Incidence: Optional[List[float]]
    """Incidence vector (used for velocity decomposition).  
    DCS: computed from acceleration vectors in TelemFFB.lua — [x, y, z].  
    Other sims: not available.  
    """

    # -- Attitude / orientation --

    Heading: Optional[float]
    """True heading.  
    DCS: LoGetSelfData().Heading — converted from radians to degrees.  
    MSFS: PLANE HEADING DEGREES TRUE SimVar — degrees.  
    IL2/BMS: not available.  
    """

    Pitch: Optional[float]
    """Pitch angle (nose up positive).  
    DCS: LoGetADIPitchBankYaw()[0] — converted from radians to degrees.  
    MSFS: PLANE PITCH DEGREES SimVar — degrees.  
    IL2/BMS: not available.  
    """

    Roll: Optional[float]
    """Bank/roll angle (right wing down positive).  
    DCS: LoGetADIPitchBankYaw()[1] — converted from radians to degrees.  
    MSFS: PLANE BANK DEGREES SimVar — degrees.  
    IL2/BMS: not available.  
    """

    # -- Position --

    AGL: Optional[float]
    """Altitude above ground level.  
    DCS: LoGetAltitudeAboveGroundLevel() — meters.  
    MSFS: computed from altitude data — meters.  
    IL2: above_ground_level_metres — meters.  
    BMS: altitude_agl from FlightData — meters.  
    """

    MSL: Optional[float]
    """Altitude above mean sea level.  
    DCS: LoGetAltitudeAboveSeaLevel() — meters.  
    MSFS: PLANE ALTITUDE SimVar — meters.  
    BMS: altitude_msl from FlightData2 — meters.  
    IL2: not available.  
    """

    X: Optional[float]
    """World X coordinate.  
    DCS: LoGetSelfData().Position.x — meters.  
    Other sims: not available.  
    """

    Y: Optional[float]
    """World Y coordinate (altitude in DCS world space).  
    DCS: LoGetSelfData().Position.z — meters.  
    Other sims: not available.  
    """

    # -- Acceleration / G-load --

    ACCs: Optional[List[float]]
    """Acceleration vector in g-forces.  
    DCS: LoGetAccelerationUnits() — [x, y, z] in g.  
    MSFS: ACCELERATION BODY [X, Y, Z] — converted from ft/s² to g (×0.031081).  
    IL2: acceleration from shared memory [x, y, z] — converted m/s² to g.  
    BMS: [0.0, normal_g, 0.0] — only normal (Y) axis available.  
    """

    AccBody: Optional[List[float]]
    """Raw body-frame acceleration from MSFS.  
    MSFS: ACCELERATION BODY [X, Y, Z] SimVars — ft/s².  
    Other sims: not available.  
    """

    AccBody_ms: Optional[List[float]]
    """Body-frame acceleration converted to m/s².  
    Computed from AccBody (ft/s² → m/s²).  
    """

    G: Optional[float]
    """Total G-force (normal axis).  
    MSFS: G FORCE SimVar — g.  
    BMS: acceleration_Gs[1] (normal G) — g.  
    DCS/IL2: not directly available (use ACCs[1] instead).  
    """

    Gaxil: Optional[float]
    """Axial (longitudinal) G-force.  
    XP: From X-Plane plugin dataref.  
    Used in DecelerationEffectMixIn for X-Plane deceleration calculation.  
    """

    # -- Velocity vectors --

    VlctVectors: Optional[List[float]]
    """World-frame velocity vector.  
    DCS: LoGetVectorVelocity() — [x, y, z] m/s.  
    Other sims: see VelWorld.  
    """

    VelAcf: Optional[List[float]]
    """Aircraft-frame velocity vector.  
    Computed from incidence and TAS — [x, y, z] m/s.  
    """

    VelWorld: Optional[List[float]]
    """World-frame velocity vector.  
    DCS: LoGetVectorVelocity() — [x, y, z] m/s.  
    MSFS: VELOCITY WORLD [X, Y, Z] SimVars — m/s.  
    """

    # -- Wind --

    Wind: Optional[Tuple[float, float, float]]
    """Wind velocity vector.  
    DCS: LoGetVectorWindVelocity() — [x, y, z] m/s.  
    Other sims: see AmbWind.  
    """

    AmbWind: Optional[List[float]]
    """Ambient wind velocity vector.  
    MSFS: AMBIENT WIND [X, Y, Z] SimVars — m/s.  
    DCS: computed from wind vector — [x, y, z] m/s.  
    """

    RelWind: Optional[List[float]]
    """Wind velocity in body frame — [x, y, z] m/s.

    **Body-frame axis conventions differ per sim:**

    ===========  =====================  ================  =========================
    Sim          [0]                    [1]               [2]
    ===========  =====================  ================  =========================
    MSFS / XP    lateral (stbd +)       vertical (up +)   longitudinal (fwd +)
    DCS          longitudinal (fwd +)   vertical (up +)   lateral (stbd +)
    ===========  =====================  ================  =========================

    MSFS: RELATIVE WIND VELOCITY BODY [X, Y, Z] SimVars — includes aircraft
    velocity (magnitude ≈ TAS).

    DCS: ambient wind rotated to body frame in TelemFFB.lua — does **not**
    include aircraft velocity (magnitude ≈ environmental wind speed).
    """

    # -- Weight on wheels / ground state --

    WeightOnWheels: Optional[List[float]]
    """Weight-on-wheels sensor values per landing gear contact point.  
    DCS: gear position sensors — [left, nose, right] 0 or 1.  
    MSFS: CONTACT POINT COMPRESSION (0-2) — [nose, left, right] as float.  
    IL2: landing_gear_pressure — [left, nose, right].  
    BMS: estimated from gear pos & on_ground — [left, nose, right].  
    """

    SimOnGround: Optional[int]
    """Whether the aircraft is on the ground.  
    DCS: calculated from weight-on-wheels in TelemFFB.lua — 0 or 1.  
    MSFS: SIM ON GROUND SimVar — 0 or 1.  
    BMS: on_ground from FlightData — 0 or 1.  
    IL2: derived from gear pressure.  
    """

    SurfaceType: Optional[str]
    """Ground surface type string.  
    MSFS: SURFACE TYPE SimVar — mapped via tables.surface_types.  
    Other sims: not available.  
    """

    Parked: Optional[Union[bool, int]]
    """Whether the aircraft is in parking state.  
    MSFS: PLANE IN PARKING STATE SimVar.  
    Other sims: not available.  
    """

    Slew: Optional[Union[bool, int]]
    """Whether slew mode is active.  
    MSFS: IS SLEW ACTIVE SimVar.  
    Other sims: not available.  
    """

    BumpIntensity: Optional[float]
    """Ground bump intensity.  
    BMS: bumpIntensity from FlightData2 — unitless.  
    Other sims: not available.  
    """

    # -- Landing gear --

    gear_value: Optional[Union[float, List[float]]]
    """Gear deployment scalar (max of all gear positions).  
    BMS: max(gear_positions) — 0.0 to 1.0.  
    Also set by MSFS: GEAR <> POSITION via SimConnect "Gear" array.  
    """

    GearPos: Optional[Union[float, List[float]]]
    """Gear positions per strut.  
    DCS: LoGetAircraftDrawArgumentValue(1/4/6) — [nose, left, right] normalized 0-1.  
    MSFS: GEAR <> POSITION (LEFT, RIGHT) — [left, right] percent.  
    IL2: landing_gear_position — list per strut.  
    BMS: [NoseGearPos, LeftGearPos, RightGearPos] — 0.0 to 1.0.  
    """

    RetractableGear: Optional[List[float]]
    """Whether each gear strut is retractable.  
    MSFS: IS GEAR RETRACTABLE SimVar — list of bool per strut.  
    BMS: hardcoded [1, 1, 1].  
    DCS/IL2: not available.  
    """

    IsTaildragger: Optional[bool]
    """Whether the aircraft is a taildragger configuration.  
    MSFS: IS TAIL DRAGGER SimVar available — can be added via sc_overrides.  
    XP: From X-Plane plugin dataref.  
    Used by MsfsXpNosewheelShimmyMixIn to skip shimmy for taildraggers.  
    """

    Gear: Optional[Union[float, List[float]]]
    """Gear positions (may be scalar max or per-strut list).  
    MSFS: GEAR <> POSITION SimVar — via sc_overrides in some profiles.  
    See also GearPos for per-strut values.  
    """

    # -- Flaps / spoilers / aero surfaces --

    Flaps: Optional[Union[float, List[float]]]
    """Flap positions.  
    DCS: LoGetAircraftDrawArgumentValue(9/10) — [left, right] normalized 0-1.  
    MSFS: TRAILING EDGE FLAPS PERCENT (LEFT, RIGHT) — [left, right] percent.  
    IL2: flaps_position — scalar normalized.  
    BMS: [flaps_position] — list with single normalized value.  
    """

    Spoilers: Optional[Union[float, List[float]]]
    """Spoiler positions.  
    MSFS: SPOILERS POSITION (LEFT, RIGHT) — [left, right] percent.  
    DCS: aircraft-specific draw arguments.  
    """

    speedbrakes_value: Optional[float]
    """Speedbrake deployment.  
    DCS: LoGetAircraftDrawArgumentValue (aircraft-specific arg) — normalized 0-1.  
    BMS: speedbrake_position — normalized 0-1.  
    """

    Speedbrakes: Optional[Union[float, List[float]]]
    """Speedbrake position.  
    MSFS: SPOILERS POSITION SimVar — via sc_overrides.  
    """

    SpeedbrakePos: Optional[Union[float, List[float]]]
    """Speedbrake handle position.  
    MSFS: SPOILERS HANDLE POSITION SimVar — via sc_overrides.  
    """

    canopy_value: Optional[float]
    """Canopy open position.  
    Not consistently available across sims.  
    """

    Canopy: Optional[float]
    """Canopy open position.  
    MSFS: CANOPY OPEN SimVar — via sc_overrides (0-1).  
    """

    flaps_value: Optional[float]
    """Flap deployment scalar.  
    BMS: flaps_position — normalized 0-1.  
    Some aircraft compute this from Flaps list.  
    """

    TailHook: Optional[float]
    """Tailhook extension.  
    DCS: aircraft-specific draw argument — normalized 0-1.  
    """

    FuelBoom: Optional[float]
    """Air refueling fuel boom extension.  
    DCS: LoGetAircraftDrawArgumentValue(22) — normalized 0-1.  
    """

    WingFold: Optional[float]
    """Wing fold position.  
    Not consistently available across sims.  
    """

    # -- Elevator --

    ElevDefl: Optional[float]
    """Elevator deflection angle.  
    MSFS: ELEVATOR DEFLECTION SimVar — degrees.  
    DCS: not directly available.  
    """

    ElevDeflPct: Optional[float]
    """Elevator deflection as percentage of max travel.  
    MSFS: ELEVATOR DEFLECTION PCT SimVar — percent (-1.0 to 1.0).  
    """

    ElevDeflPctLR: Optional[Tuple[float, float]]
    """Elevator deflection percentage per side [left, right].  
    MSFS: ELEVATOR DEFLECTION PCT LR SimVars when available.  
    """

    ElevTrim: Optional[float]
    """Elevator trim position in degrees.  
    MSFS: ELEVATOR TRIM POSITION SimVar — degrees.  
    """

    ElevTrimMin: Optional[float]
    """Elevator trim range limits.  
    MSFS 2024: ELEVATOR TRIM MIN/MAX SimVars — degrees.  
    """
    ElevTrimMax: Optional[float]
    """Elevator trim range limits.  
    MSFS 2024: ELEVATOR TRIM MIN/MAX SimVars — degrees.  
    """

    ElevTrimUpLmt: Optional[float]
    """Elevator trim range limits (alternative names).  
    MSFS: ELEVATOR TRIM UP/DOWN LIMIT SimVars — degrees.  
    """
    ElevTrimDnLmt: Optional[float]
    """Elevator trim range limits (alternative names).  
    MSFS: ELEVATOR TRIM UP/DOWN LIMIT SimVars — degrees.  
    """

    ElevTrimPct: Optional[float]
    """Elevator trim as percentage of max travel.  
    MSFS: ELEVATOR TRIM PCT SimVar — percent (-1.0 to 1.0).  
    """

    ElevTrimNeutral: Optional[float]
    """Elevator trim neutral position.  
    MSFS: ELEVATOR TRIM NEUTRAL SimVar — degrees.  
    """

    # -- Aileron --

    AileronDefl: Optional[float]
    """Aileron average deflection angle.  
    MSFS: AILERON AVERAGE DEFLECTION SimVar — degrees.  
    """

    AileronDeflPctLR: Optional[Tuple[float, float]]
    """Aileron deflection percentage per side [left, right].  
    MSFS: AILERON <> DEFLECTION PCT (L, R) SimVars — percent.  
    """

    AileronTrim: Optional[float]
    """Aileron trim angle.  
    MSFS: AILERON TRIM SimVar — degrees.  
    """

    AileronTrimPct: Optional[float]
    """Aileron trim as percentage.  
    MSFS: AILERON TRIM PCT SimVar — percent.  
    """

    # -- Rudder --

    RudderDefl: Optional[float]
    """Rudder deflection angle.  
    MSFS: RUDDER DEFLECTION SimVar — degrees.  
    """

    RudderDeflPct: Optional[float]
    """Rudder deflection as percentage of max travel.  
    MSFS: RUDDER DEFLECTION PCT SimVar — percent (-1.0 to 1.0).  
    """

    RudderTrimPct: Optional[float]
    """Rudder trim as percentage.  
    MSFS: RUDDER TRIM PCT SimVar — percent.  
    """

    # -- Controls lock --

    ControlsLock: Optional[Union[bool, int]]
    """Whether flight controls are locked (e.g. parking/ground state).  
    MSFS: Dynamically subscribed via add_simvar() when controls_lock_enable  
    is configured — reads from user-specified SimVar (controls_lock_simvar).  
    """

    # -- Engine --

    NumEngines: Optional[int]
    """Number of engines on the aircraft.  
    MSFS: NUMBER OF ENGINES SimVar.  
    """

    EngRPM: Optional[Union[float, List[float]]]
    """Engine RPM as percentage of max.  
    DCS: LoGetEngineInfo().RPM — list per engine.  
    MSFS: GENERAL ENG PCT MAX RPM SimVar — list per engine.  
    BMS: engine_rpm + engine_rpm2 — list RPM %.  
    IL2: not available (use RPM field instead).  
    """

    ActualRPM: Optional[Union[float, List[float]]]
    """Actual engine RPM (absolute value, not percentage).  
    DCS: Exported from TelemFFB.lua for specific prop aircraft (P-51D, FW-190, Bf-109, etc.)  
    using engine_redline_reference × (EngRPM% / 100). Fallback computed in Python from  
    EngRPM × engine_max_rpm (default 2700 RPM).  
    Used by EngineRumbleMixIn for RPM-proportional vibration.  
    """

    PropRPM: Optional[Union[float, List[float]]]
    """Propeller RPM (prop aircraft only).  
    MSFS: PROP RPM SimVar — list per engine, in RPM.  
    DCS: available for some prop aircraft in TelemFFB.lua.  
    """

    RotorRPM: Optional[Union[float, List[float]]]
    """Main rotor RPM (helicopters only).  
    MSFS: ROTOR RPM:1 SimVar — RPM, scalar.  
    DCS: computed from draw arguments for specific helis (Mi-8, UH-1, Ka-50, etc.).  
    """

    EngPCT: Optional[Union[float, List[float]]]
    """Engine power percentage.  
    MSFS: GENERAL ENG PCT MAX RPM SimVar — list per engine.  
    Other sims: may overlap with EngRPM.  
    """

    RPM: Optional[Union[float, List[float]]]
    """Engine RPM (used primarily by IL2 and BMS).  
    IL2: engine_rpm from shared memory — list per engine.  
    BMS: engine_rpm from FlightData — list per engine.  
    """

    MaxRPM: Optional[Union[float, List[float]]]
    """Maximum engine RPM (IL2).  
    IL2: engine_maxrpm from shared memory — list per engine.  
    """

    Afterburner: Optional[Union[float, List[float]]]
    """Afterburner state.  
    MSFS: TURB ENG AFTERBURNER SimVar — list per engine (bool-like).  
    BMS: estimated from nozzle pos > 0.1 && fuel_flow > 8000 — scaled 0-1.  
    DCS: not directly available.  
    """

    AfterburnerPct: Optional[float]
    """Afterburner percentage active.  
    MSFS: TURB ENG AFTERBURNER PCT ACTIVE SimVar — percent.  
    """

    PropThrust: Optional[float]
    """Propeller thrust.  
    MSFS: PROP THRUST SimVar — Newtons (scaled ×10 from raw pounds).  
    """

    FTIT: Optional[Union[float, List[float]]]
    """Fan turbine inlet temperature.  
    DCS: LoGetEngineInfo().FTIT — list per engine, degrees C.  
    BMS: engine_ftit + engine_ftit2 from FlightData — list, degrees.  
    """

    NozzlePos: Optional[Union[float, List[float]]]
    """Engine exhaust nozzle position.  
    BMS: nozzle_pos + nozzle_pos2 from FlightData — 0.0 to 1.0.  
    """

    FuelFlow: Optional[Union[float, List[float]]]
    """Engine fuel flow rate.  
    BMS: fuel_flow + fuel_flow2 from FlightData.  
    """

    # -- Hydraulics --

    HydSys: Optional[Union[bool, int, float, List[Union[bool, int, float]]]]
    """Hydraulic system integrity/state.  
    MSFS: HYDRAULIC SYSTEM INTEGRITY SimVar — percent.  
    Default when absent: "n/a" (use .get("HydSys", "n/a")).  
    """

    HydPress: Optional[Union[float, List[float]]]
    """Hydraulic pressure per system.  
    DCS: LoGetEngineInfo().HydraulicPressure — [left, right] psi.  
    MSFS: HYDRAULIC PRESSURE SimVar — list per system, psi.  
    Default when absent: 1 (use .get("HydPress", 1)).  
    """

    HydSwitch: Optional[Union[bool, int]]
    """Hydraulic switch state.  
    MSFS: HYDRAULIC SWITCH SimVar — bool.  
    """

    # -- Autopilot --

    APMaster: Optional[Union[bool, int]]
    """Autopilot master switch state.  
    DCS: LoGetMCPState().AutopilotOn — bool.  
    MSFS: AUTOPILOT MASTER SimVar — bool/int.  
    """

    APEnabled: Optional[Union[bool, int]]
    """Whether autopilot is actively enabled.  
    DCS: Exported from TelemFFB.lua as MCP.AutopilotOn.  
    Used by aircrafts_dcs for AP-specific spring override.  
    """

    APServos: Optional[Union[bool, int]]
    """Whether autopilot servos are engaged.  
    XP: From X-Plane plugin dataref.  
    Used by FlightControls, HeliControls, FBW, and TrimwheelMixIns for AP following.  
    """

    APRollServo: Optional[float]
    """Autopilot roll servo authority / position.  
    XP: From X-Plane plugin dataref.  
    Used for AP aileron position following in MsfsXpFlightControlsMixIn.  
    """
    APPitchServo: Optional[float]
    """Autopilot pitch servo authority / position.  
    XP: From X-Plane plugin dataref.  
    Used for AP elevator position following in MsfsXpFBWFlightControlsMixIn.  
    """
    APYawServo: Optional[float]
    """Autopilot yaw servo authority / position.  
    XP: From X-Plane plugin dataref.  
    Used for AP rudder position following in MsfsXpFBWFlightControlsMixIn.  
    """

    # -- Weapons / payload --

    PayloadInfo: Optional[Any]
    """Weapon station payload information.  
    DCS: LoGetPayloadInfo().Stations — formatted "name-code*count" string.  
    BMS: bombs_released + missiles_fired — list.  
    """

    Gun: Optional[Any]
    """Gun/cannon shell count.  
    DCS: LoGetPayloadInfo().Cannon.shells — int.  
    IL2: guns_fired — list (count).  
    BMS: detected via IVibe — list (count).  
    """

    GunData: Optional[Any]
    """Detailed gun firing data (IL2).  
    IL2: gun_data list — structured event data.  
    """

    GunDict: Optional[Dict]
    """Gun data as dictionary (IL2).  
    IL2: gun_data_dict — structured dict.  
    """

    GunFireData: Optional[str]
    """Gun fire event data as JSON string (IL2).  
    IL2: gunfire_dict serialized as JSON.  
    """

    Guns: Optional[List[float]]
    """Guns fired count (IL2).  
    IL2: guns_fired — list per weapon group.  
    """

    Flares: Optional[Any]
    """Flare count / dispense events.  
    DCS: extracted from PayloadInfo — int.  
    BMS: [flare_count] — list with count.  
    """

    FlareCount: Optional[float]
    """Total flare count remaining.  
    BMS: flare_count from FlightData — float.  
    """

    Chaff: Optional[Any]
    """Chaff count / dispense events.  
    DCS: extracted from PayloadInfo — int.  
    BMS: [chaff_count] — list with count.  
    """

    ChaffCount: Optional[float]
    """Total chaff count remaining.  
    BMS: chaff_count from FlightData — float.  
    """

    Bombs: Optional[List[float]]
    """Bombs released event data.  
    DCS: from LoGetPayloadInfo().Stations — list.  
    IL2: bombs_released — [count, 0].  
    BMS: bombs_released — [count, 0].  
    """

    Rockets: Optional[List[float]]
    """Rockets fired event data.  
    IL2: rockets_fired — [count, 0].  
    """

    Missiles: Optional[List[float]]
    """Missiles fired event data.  
    DCS: from LoGetPayloadInfo().Stations — list.  
    BMS: missiles_fired — [count, 0].  
    """

    Hits: Optional[int]
    """Hit events counter.  
    IL2: hit_events — incremented per hit.  
    BMS: hit_events from IVibe.  
    """

    Damage: Optional[int]
    """Damage events counter.  
    IL2: damage_events — incremented per damage event.  
    BMS: damage_events from IVibe.  
    """

    Collisions: Optional[int]
    """Collision events counter.  
    BMS: collision_counter from IVibe.  
    """

    # -- Brakes / steering --

    Brakes: Optional[Union[List[float], Tuple[float, ...]]]
    """Brake positions [left, right].  
    MSFS: BRAKE LEFT/RIGHT POSITION SimVars — [left, right] 0-1.  
    Other sims: not available.  
    """

    CenterSteerAnglePct: Optional[float]
    """Nosewheel steering angle as percentage of max deflection.  
    MSFS: CONTACT POINT STEER ANGLE PCT SimVar — percent.  
    """

    WaterRudderExt: Optional[float]
    """Water rudder extension (seaplanes).  
    MSFS: WATER LEFT RUDDER EXTENDED SimVar — percent.  
    """

    # -- Joystick / physical input --
    # These fields are exchanged via IPC between master and child instances.

    ForceXY: Optional[List[float]]
    """Force feedback output vector [x, y].  
    Set by effects pipeline — normalized -1.0 to 1.0.  
    """

    JoyXY: Optional[List[float]]
    """Joystick input position [x, y].  
    From USB HID device — normalized -1.0 to 1.0.  
    """

    StickXY: Optional[List[float]]
    """Stick position feedback [x, y].  
    From USB HID device — normalized -1.0 to 1.0.  
    """

    StickXY_offset: Optional[List[float]]
    """Stick position offset [x, y].  
    Trim/center offset applied to stick — normalized.  
    """

    phys_x: Optional[float]
    """Physical stick X axis position.  
    From FFBRhino device — normalized.  
    """

    phys_y: Optional[float]
    """Physical stick Y axis position.  
    From FFBRhino device — normalized.  
    """

    phys_x_pos: Optional[float]
    """Physical position used for effect calculations (X axis).  
    May differ from phys_x due to offset/trim.  
    """

    phys_y_pos: Optional[float]
    """Physical position used for effect calculations (Y axis).  
    May differ from phys_y due to offset/trim.  
    """

    StickX: Optional[float]
    """Stick X-axis value.  
    MSFS: YOKE X POSITION SimVar — via sc_overrides.  
    """

    StickY: Optional[float]
    """Stick Y-axis value.  
    MSFS: YOKE Y POSITION SimVar — via sc_overrides.  
    """

    CP_XY: Optional[str]
    """Control position X/Y string identifier.  
    Used internally by some MixIns.  
    """

    # -- Helicopter-specific --

    CollectivePos: Optional[float]
    """Collective lever position.  
    MSFS: COLLECTIVE POSITION SimVar — percent 0-100.  
    DCS: aircraft-specific (LoGetAircraftDrawArgument or cockpit params).  
    """

    TailRotorPedalPos: Optional[float]
    """Tail rotor / anti-torque pedal position.  
    MSFS: TAIL ROTOR BLADE PITCH PCT SimVar — percent.  
    DCS: aircraft-specific.  
    """

    CyclicTrimX: Optional[float]
    """Cyclic trim position (lateral).  
    MSFS: ROTOR LATERAL TRIM PCT SimVar — percent.  
    """

    CyclicTrimY: Optional[float]
    """Cyclic trim position (longitudinal).  
    MSFS: ROTOR LONGITUDINAL TRIM PCT SimVar — percent.  
    """

    ForceTrimSW: Optional[bool]
    """Force trim switch state (helicopter force-trim / SAS release).  
    MSFS: L:TelemFFBHeliFT — custom local SimVar (bool).  
    Default when absent: True (use .get("ForceTrimSW", True)).  
    """

    SEMAx: Optional[float]
    """SAS/SEMA (Stability Enhancement/Mechanical Augmentation) authority.  
    Set by helicopter MixIns — axis-specific authority values.  
    """
    SEMAy: Optional[float]
    """SAS/SEMA (Stability Enhancement/Mechanical Augmentation) authority.  
    Set by helicopter MixIns — axis-specific authority values.  
    """

    # -- HPG Helicopters (vendor-specific) --

    hpgAfcsMaster: Optional[float]
    """HPG AFCS master switch state.  
    MSFS: L:EFB_AFCS_MASTER SimVar — via sc_overrides.  
    """

    hpgCollectiveAfcsMode: Optional[int]
    """HPG collective AFCS mode.  
    MSFS: L:EFB_AFCS_COLLECTIVE_MODE SimVar — via sc_overrides.  
    """

    hpgCollectiveRelease: Optional[Union[bool, int]]
    """HPG collective force trim release.  
    MSFS: L:EFB_COLLECTIVE_RELEASE SimVar — via sc_overrides.  
    """

    hpgFeetOnPedals: Optional[int]
    """HPG feet-on-pedals detection.  
    MSFS: L:EFB_FEET_ON_PEDALS SimVar — via sc_overrides.  
    """

    hpgFollowupTrimMode: Optional[int]
    """HPG follow-up trim mode.  
    MSFS: L:EFB_FOLLOWUP_TRIM_MODE SimVar — via sc_overrides.  
    """

    hpgHandsOnCyclic: Optional[int]
    """HPG hands-on-cyclic detection.  
    MSFS: L:EFB_HANDS_ON_CYCLIC SimVar — via sc_overrides.  
    """

    hpgSEMAx: Optional[float]
    """HPG SEMA authority X-axis.  
    MSFS: L:EFB_SEMA_X SimVar (Percent Over 100) — via sc_overrides.  
    """

    hpgSEMAy: Optional[float]
    """HPG SEMA authority Y-axis.  
    MSFS: L:EFB_SEMA_Y SimVar (Percent Over 100) — via sc_overrides.  
    """

    hpgSEMAyaw: Optional[float]
    """HPG SEMA authority yaw axis.  
    MSFS: L:EFB_SEMA_YAW SimVar (Percent Over 100) — via sc_overrides.  
    """

    hpgTrimRelease: Optional[Union[bool, int]]
    """HPG force trim release switch.  
    MSFS: L:EFB_TRIM_RELEASE SimVar — via sc_overrides.  
    """

    hpgVRSDatum: Optional[int]
    """HPG VRS datum indicator.  
    MSFS: L:EFB_VRS_DATUM SimVar — via sc_overrides.  
    """

    hpgVRSIsInVRS: Optional[int]
    """HPG VRS (Vortex Ring State) active flag.  
    MSFS: L:EFB_VRS_IS_IN_VRS SimVar — via sc_overrides.  
    """

    # -- X-Trident AW109 (vendor-specific) --

    AW109_aileron_trim_rate: Optional[float]
    """AW109 aileron trim rate.  
    MSFS: L:AW109_aileron_trim_rate SimVar — via sc_overrides.  
    """

    AW109_aileron_trim_req: Optional[float]
    """AW109 aileron trim request.  
    MSFS: L:AW109_aileron_trim_req SimVar — via sc_overrides.  
    """

    AW109_col_force_trim_release_pressed: Optional[int]
    """AW109 collective force trim release button state.  
    MSFS: L:AW109_col_force_trim_release_pressed SimVar — via sc_overrides.  
    """

    AW109_collective_axis_afcs_pos: Optional[float]
    """AW109 collective axis AFCS position.  
    MSFS: L:AW109_collective_axis_afcs_pos SimVar — via sc_overrides.  
    """

    AW109_collective_input_ratio: Optional[float]
    """AW109 collective input ratio.  
    MSFS: L:AW109_collective_input_ratio SimVar — via sc_overrides.  
    """

    AW109_collective_mode: Optional[int]
    """AW109 collective mode (manual/AFCS).  
    MSFS: L:AW109_collective_mode SimVar — via sc_overrides.  
    """

    AW109_collective_ratio: Optional[float]
    """AW109 collective position ratio.  
    MSFS: L:AW109_collective_ratio SimVar — via sc_overrides.  
    """

    AW109_collective_trim_rate: Optional[float]
    """AW109 collective trim rate.  
    MSFS: L:AW109_collective_trim_rate SimVar — via sc_overrides.  
    """

    AW109_cyc_force_trim_release_pressed: Optional[int]
    """AW109 cyclic force trim release button state.  
    MSFS: L:AW109_cyc_force_trim_release_pressed SimVar — via sc_overrides.  
    """

    AW109_elevator_trim_rate: Optional[float]
    """AW109 elevator trim rate.  
    MSFS: L:AW109_elevator_trim_rate SimVar — via sc_overrides.  
    """

    AW109_elevator_trim_req: Optional[float]
    """AW109 elevator trim request.  
    MSFS: L:AW109_elevator_trim_req SimVar — via sc_overrides.  
    """

    AW109_ped_force_trim_release_pressed: Optional[int]
    """AW109 pedal force trim release button state.  
    MSFS: L:AW109_ped_force_trim_release_pressed SimVar — via sc_overrides.  
    """

    AW109_rud_sim_trim_left: Optional[float]
    """AW109 rudder simulated trim (left).  
    MSFS: L:AW109_rud_sim_trim_left SimVar — via sc_overrides.  
    """

    AW109_rud_sim_trim_right: Optional[float]
    """AW109 rudder simulated trim (right).  
    MSFS: L:AW109_rud_sim_trim_right SimVar — via sc_overrides.  
    """

    AW109_rud_trim_coarse: Optional[float]
    """AW109 rudder trim coarse value.  
    MSFS: L:AW109_rud_trim_coarse SimVar — via sc_overrides.  
    """

    AW109_rud_trim_fine: Optional[float]
    """AW109 rudder trim fine value.  
    MSFS: L:AW109_rud_trim_fine SimVar — via sc_overrides.  
    """

    AW109_rud_trim_thresh_hi: Optional[float]
    """AW109 rudder trim threshold (high).  
    MSFS: L:AW109_rud_trim_thresh_hi SimVar — via sc_overrides.  
    """

    AW109_rud_trim_thresh_lo: Optional[float]
    """AW109 rudder trim threshold (low).  
    MSFS: L:AW109_rud_trim_thresh_lo SimVar — via sc_overrides.  
    """

    AW109_rud_trim_zero: Optional[float]
    """AW109 rudder trim zero reference.  
    MSFS: L:AW109_rud_trim_zero SimVar — via sc_overrides.  
    """

    AW109_rudder_trim_rate: Optional[float]
    """AW109 rudder trim rate.  
    MSFS: L:AW109_rudder_trim_rate SimVar — via sc_overrides.  
    """

    AW109_rudder_trim_req: Optional[float]
    """AW109 rudder trim request.  
    MSFS: L:AW109_rudder_trim_req SimVar — via sc_overrides.  
    """

    AW109_target_collective: Optional[float]
    """AW109 target collective position.  
    MSFS: L:AW109_target_collective SimVar — via sc_overrides.  
    """

    AW109_target_pitch: Optional[float]
    """AW109 target pitch (AFCS).  
    MSFS: L:AW109_target_pitch SimVar — via sc_overrides.  
    """

    AW109_target_roll: Optional[float]
    """AW109 target roll (AFCS).  
    MSFS: L:AW109_target_roll SimVar — via sc_overrides.  
    """

    AW109_target_yaw: Optional[float]
    """AW109 target yaw (AFCS).  
    MSFS: L:AW109_target_yaw SimVar — via sc_overrides.  
    """

    # -- Taog H500 (vendor-specific) --

    TaogH500CollectiveFriction: Optional[bool]
    """Taog H500 collective friction engaged.  
    MSFS: L:COLLECTIVEFRICTION SimVar — via sc_overrides.  
    """

    TaogH500CyclicFriction: Optional[bool]
    """Taog H500 cyclic friction engaged.  
    MSFS: L:CYCLICFRICTION SimVar — via sc_overrides.  
    """

    TaogH500PedalLock: Optional[bool]
    """Taog H500 pedal lock engaged.  
    MSFS: L:PEDALLOCK SimVar — via sc_overrides.  
    """

    TaogH500TRDamaged: Optional[bool]
    """Taog H500 tail rotor damaged flag.  

    MSFS: L:TRDAMAGED SimVar — via sc_overrides.  
    """

    # -- FlyInside (vendor-specific) --

    FI_VibX: Optional[float]
    """FlyInside vibration X-axis.  

    MSFS: L:FI_VibX SimVar (number) — via sc_overrides.  
    """

    FI_VibY: Optional[float]
    """FlyInside vibration Y-axis.  

    MSFS: L:FI_VibY SimVar (number) — via sc_overrides.  
    """

    FI_VibZ: Optional[float]
    """FlyInside vibration Z-axis.  

    MSFS: L:FI_VibZ SimVar (number) — via sc_overrides.  
    """

    # -- Trim wheel --

    TRIM_DELTA: Optional[float]
    """Trim wheel delta change per frame.  
    Set by MsfsXpTrimwheelMixIn — incremental trim change.  
    """

    # -- Sim state --

    SimPaused: Optional[Union[bool, int]]
    """Whether the simulator is paused.  

    DCS: detected from LoGetModelTime() stall.  
    MSFS: sim pause flag + other states (parking, slew, avatar, menus).  
    IL2: detected from window focus loss.  
    BMS: detected from IVibe data.  
    """

    Focus: Optional[Union[bool, int]]
    """Whether the sim window has input focus.  
    IL2: pygetwindow focus check — 0 or 1.  
    DCS: inferred from pause state.  
    """

    SimDisabled: Optional[Union[bool, int]]
    """Whether simulation is disabled/inactive.  
    MSFS: SIM DISABLED SimVar — bool.  
    Used to suppress effects when sim is not active.  
    """

    MPMenu: Optional[bool]
    """Whether in IL-2 multiplayer menu.  
    IL2: detected from MP menu state — bool.  
    """

    In3D: Optional[Union[bool, int]]
    """Whether the player is in 3D cockpit view.  
    BMS: ivibe.In3D — 0 or 1.  
    """

    # -- Seat / crew --

    SeatData: Optional[List[Any]]
    """Seat position/crew data.  
    IL2: seat_data from shared memory — list.  
    """

    Seat: Optional[int]
    """Current seat index.  
    IL2: seat_index — integer.  
    """

    # -- Damage / mechanics (sim-specific) --

    MechInfo: Optional[str]
    """DCS mechanical info JSON blob.  
    DCS: LoGetMechInfo() — serialized JSON string.  
    Flattened into individual telem_data keys (e.g. "Engine_left_damage")  
    then deleted from telem_data after processing in aircrafts_dcs.py.  
    """

    # -- Performance / frame timing (injected by TelemManager) --
    # These are computed by TelemManager._update_frame_timing(), not by sims.

    frameTimes: Optional[List[int]]
    """Frame timing data [current_ms, max_ms].  
    Computed from inter-frame time deltas.  
    """

    maxFrameTime: Optional[Union[str, float]]
    """Maximum frame time over recent history — formatted string."""

    avgFrameTime: Optional[Union[str, float]]
    """Average frame time over recent history — formatted string."""

    # -- Motion data --

    acc_vectors: Optional[List[float]]
    """Acceleration vectors for motion effects.  
    IL2: acceleration vector data from shared memory — list.  
    """

    rot_velocity: Optional[List[float]]
    """Rotation velocity vector.  
    IL2: rotation velocity from shared memory — [x, y, z] rad/s.  
    """

    rot_accel: Optional[List[float]]
    """Rotation acceleration vector.  
    IL2: rotation acceleration from shared memory — [x, y, z] rad/s².  
    """

    # -- Buffet --

    BuffetFrequency: Optional[float]
    """Stall buffet frequency.  
    IL2: stall_buffet_frequency from shared memory — Hz.  
    """

    BuffetAmplitude: Optional[float]
    """Stall buffet amplitude (scaled ×10 for usability).  
    IL2: stall_buffet_amplitude × 10 — normalized 0-1 range.  
    """

    # Single backing storage for all fields
    _data: Dict[str, Any]

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        object.__setattr__(self, '_data', {})
        self._data['_timestamp'] = time.perf_counter()
        self._data['FFBType'] = 'joystick'

        if initial:
            self._data.update(initial)

    # Internal helper: known attribute names (from annotations)
    @classmethod
    def _known_fields(cls) -> set:
        return {k for k, v in cls.__annotations__.items() if not k.startswith('_')}

    # --- Mapping-like interface ---
    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        src = self._data.get('src', '?')
        name = self._data.get('N', '?')
        n_keys = len(self._data)
        return f"BaseTelemetryData(src={src!r}, N={name!r}, keys={n_keys})"

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def pop(self, key: str, *args) -> Any:
        return self._data.pop(key, *args)

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self._data.setdefault(key, default)

    def keys(self) -> Iterable[str]:
        return self._data.keys()

    def items(self) -> Iterable[tuple]:
        return self._data.items()

    def values(self) -> Iterable[Any]:
        return self._data.values()

    def clear(self) -> None:
        self._data.clear()

    def update(self, other: Union[Dict[str, Any], 'BaseTelemetryData'], **kwargs) -> None:
        if isinstance(other, BaseTelemetryData):
            self._data.update(other._data)
        else:
            self._data.update(other)
        if kwargs:
            self._data.update(kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseTelemetryData':
        return cls(initial=data.copy())

    def copy(self) -> 'BaseTelemetryData':
        return BaseTelemetryData(self.to_dict())

    # --- Attribute access that maps to internal dict ---
    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        if name in self._known_fields():
            return None
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!s}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name == '_data':
            object.__setattr__(self, name, value)
            return
        # All known fields go into _data
        if name in self._known_fields():
            self._data[name] = value
            return
        # Unknown attributes also go into _data for full dict compatibility
        self._data[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self._data[name]
        except KeyError:
            raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!s}")

