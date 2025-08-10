#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
Falcon BMS Shared Memory Interface

This module provides Python ctypes structures and interfaces for reading
Falcon BMS shared memory areas including FlightData, FlightData2, OSBData,
StringData, DrawingData, and IVibeData.

Key Features:
- FlightData: Primary flight data (position, attitude, engine, etc.)
- FlightData2: Extended flight data (additional sensors, navigation)
- OSBData: MFD button labels and display information
- StringData: Cockpit display strings (DED, PFL, SMS, etc.)
- IVibeData: Haptic feedback data (G-forces, damage, weapons)

Usage:
    import BMSSharedMem
    
    # Connect to BMS
    BMSSharedMem.connect()
    
    # Get flight data
    state = BMSSharedMem.get_aircraft_state()
    
    # Get specific data types
    strings = BMSSharedMem.get_bms_strings()
    ivibe = BMSSharedMem.get_ivibe_data()
    
    # Disconnect
    BMSSharedMem.disconnect()
"""

import ctypes
import ctypes.wintypes
import logging
import time
from typing import Optional, Dict, Any
from threading import Lock

# Constants from FlightData.h
FLIGHTDATA_VERSION = 118
FLIGHTDATA2_VERSION = 22
STRINGDATA_VERSION = 5
DRAWINGDATA_VERSION = 1
OSB_STRING_LENGTH = 8
MAX_RWR_OBJECTS = 40
RWRINFO_SIZE = 512
CALLSIGN_LEN = 12
MAX_CALLSIGNS = 32
MAX_ECM_PROGRAMS = 5
RTT_NO_OF_AREAS = 7
STRINGDATA_AREA_SIZE_MAX = 1024 * 1024
DRAWINGDATA_AREA_SIZE_MAX = 1024 * 1024

logger = logging.getLogger(__name__)

# Windows API function prototypes
kernel32 = ctypes.windll.kernel32

# OpenFileMappingW prototype
# HANDLE OpenFileMappingW(
#   [in] DWORD   dwDesiredAccess,
#   [in] BOOL    bInheritHandle,
#   [in] LPCWSTR lpName
# );
kernel32.OpenFileMappingW.argtypes = [
    ctypes.wintypes.DWORD,    # dwDesiredAccess
    ctypes.wintypes.BOOL,     # bInheritHandle
    ctypes.wintypes.LPCWSTR   # lpName
]
kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE

# MapViewOfFile prototype
# LPVOID MapViewOfFile(
#   [in] HANDLE hFileMappingObject,
#   [in] DWORD  dwDesiredAccess,
#   [in] DWORD  dwFileOffsetHigh,
#   [in] DWORD  dwFileOffsetLow,
#   [in] SIZE_T dwNumberOfBytesToMap
# );
kernel32.MapViewOfFile.argtypes = [
    ctypes.wintypes.HANDLE,   # hFileMappingObject
    ctypes.wintypes.DWORD,    # dwDesiredAccess
    ctypes.wintypes.DWORD,    # dwFileOffsetHigh
    ctypes.wintypes.DWORD,    # dwFileOffsetLow
    ctypes.c_size_t           # dwNumberOfBytesToMap
]
kernel32.MapViewOfFile.restype = ctypes.c_void_p

# UnmapViewOfFile prototype
# BOOL UnmapViewOfFile(
#   [in] LPCVOID lpBaseAddress
# );
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL

# CloseHandle prototype
# BOOL CloseHandle(
#   [in] HANDLE hObject
# );
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL


class StringIdentifier:
    """String identifier enumeration for BMS string data"""
    # Cockpit strings
    DED_PFL_STRINGS = 1
    SMS_DISPLAY = 2
    RWR_DISPLAY = 3
    DTE_STRINGS = 4
    CMDS_STRINGS = 5
    UFC_STRINGS = 6
    # Add more identifiers as needed


class StringData(ctypes.Structure):
    """String data structure for BMS shared memory"""
    _pack_ = 4
    _fields_ = [
        ("VersionNum", ctypes.c_uint),          # Version number
        ("NoOfStrings", ctypes.c_uint),         # Number of strings in data
        ("dataSize", ctypes.c_uint),            # Size of string data
        ("data", ctypes.c_char * STRINGDATA_AREA_SIZE_MAX),  # String data buffer
    ]


class StringHeader:
    """Header structure for individual strings in StringData"""
    def __init__(self, id_val, length, offset):
        self.id = id_val
        self.length = length
        self.offset = offset


class IVibeData(ctypes.Structure):
    """IVibeData structure from IVibeData.h - Falcon Intellivibe shared memory"""
    _pack_ = 8  # 8-byte alignment
    _fields_ = [
        ("AAMissileFired", ctypes.c_ubyte),     # how many AA missiles fired
        ("AGMissileFired", ctypes.c_ubyte),     # how many maveric/rockets fired
        ("BombDropped", ctypes.c_ubyte),        # how many bombs dropped
        ("FlareDropped", ctypes.c_ubyte),       # how many flares dropped
        ("ChaffDropped", ctypes.c_ubyte),       # how many chaff dropped
        ("BulletsFired", ctypes.c_ubyte),       # how many bullets shot
        ("CollisionCounter", ctypes.c_int),     # collisions
        ("IsFiringGun", ctypes.c_bool),         # gun is firing
        ("IsEndFlight", ctypes.c_bool),         # ending the flight from 3d
        ("IsEjecting", ctypes.c_bool),          # we've ejected
        ("In3D", ctypes.c_bool),                # in 3D?
        ("IsPaused", ctypes.c_bool),            # sim paused?
        ("IsFrozen", ctypes.c_bool),            # sim frozen?
        ("IsOverG", ctypes.c_bool),             # are G limits being exceeded?
        ("IsOnGround", ctypes.c_bool),          # are we on the ground?
        ("IsExitGame", ctypes.c_bool),          # did we exit Falcon?
        ("Gforce", ctypes.c_float),             # what gforce we are feeling
        ("eyex", ctypes.c_float),               # where the eye is in relationship to the plane
        ("eyey", ctypes.c_float),               # where the eye is in relationship to the plane
        ("eyez", ctypes.c_float),               # where the eye is in relationship to the plane
        ("lastdamage", ctypes.c_int),           # 1 to 8 depending on quadrant
        ("damageforce", ctypes.c_float),        # how big the hit was
        ("whendamage", ctypes.c_uint),          # when the hit occured, game time in ms
    ]


class LightBits:
    """Light bits enumeration from FlightData.h"""
    MasterCaution = 0x1
    TF = 0x2
    OXY_BROW = 0x4
    EQUIP_HOT = 0x8
    ONGROUND = 0x10
    ENG_FIRE = 0x20
    CONFIG = 0x40
    HYD = 0x80
    Flcs_ABCD = 0x100
    FLCS = 0x200
    CAN = 0x400
    T_L_CFG = 0x800
    AOAAbove = 0x1000
    AOAOn = 0x2000
    AOABelow = 0x4000
    RefuelRDY = 0x8000
    RefuelAR = 0x10000
    RefuelDSC = 0x20000
    FltControlSys = 0x40000
    LEFlaps = 0x80000
    EngineFault = 0x100000
    Overheat = 0x200000
    FuelLow = 0x400000
    Avionics = 0x800000
    RadarAlt = 0x1000000
    IFF = 0x2000000
    ECM = 0x4000000
    Hook = 0x8000000
    NWSFail = 0x10000000
    CabinPress = 0x20000000
    AutoPilotOn = 0x40000000
    TFR_STBY = 0x80000000


class LightBits2:
    """Light bits 2 enumeration from FlightData.h"""
    HandOff = 0x1
    Launch = 0x2
    PriMode = 0x4
    Naval = 0x8
    Unk = 0x10
    TgtSep = 0x20
    Go = 0x40
    NoGo = 0x80
    Degr = 0x100
    Rdy = 0x200
    ChaffLo = 0x400
    FlareLo = 0x800
    AuxSrch = 0x1000
    AuxAct = 0x2000
    AuxLow = 0x4000
    AuxPwr = 0x8000
    EcmPwr = 0x10000
    EcmFail = 0x20000
    FwdFuelLow = 0x40000
    AftFuelLow = 0x80000
    EPUOn = 0x100000
    JFSOn = 0x200000
    SEC = 0x400000
    OXY_LOW = 0x800000
    PROBEHEAT = 0x1000000
    SEAT_ARM = 0x2000000
    BUC = 0x4000000
    FUEL_OIL_HOT = 0x8000000
    ANTI_SKID = 0x10000000
    TFR_ENGAGED = 0x20000000
    GEARHANDLE = 0x40000000
    ENGINE = 0x80000000


class LightBits3:
    """Light bits 3 enumeration from FlightData.h"""
    FlcsPmg = 0x1
    MainGen = 0x2
    StbyGen = 0x4
    EpuGen = 0x8
    EpuPmg = 0x10
    ToFlcs = 0x20
    FlcsRly = 0x40
    BatFail = 0x80
    Hydrazine = 0x100
    Air = 0x200
    Elec_Fault = 0x400
    Lef_Fault = 0x800
    OnGround = 0x1000
    FlcsBitRun = 0x2000
    FlcsBitFail = 0x4000
    DbuWarn = 0x8000
    NoseGearDown = 0x10000
    LeftGearDown = 0x20000
    RightGearDown = 0x40000
    ParkBrakeOn = 0x100000
    Power_Off = 0x200000
    cadc = 0x400000
    SpeedBrake = 0x800000
    SysTest = 0x1000000
    MCAnnounced = 0x2000000
    MLGWOW = 0x4000000
    NLGWOW = 0x8000000
    ATF_Not_Engaged = 0x10000000
    Inlet_Icing = 0x20000000


class HsiBits:
    """HSI bits enumeration from FlightData.h"""
    ToTrue = 0x01
    IlsWarning = 0x02
    CourseWarning = 0x04
    Init = 0x08
    TotalFlags = 0x10
    ADI_OFF = 0x20
    ADI_AUX = 0x40
    ADI_GS = 0x80
    ADI_LOC = 0x100
    HSI_OFF = 0x200
    BUP_ADI_OFF = 0x400
    VVI = 0x800
    AOA = 0x1000
    AVTR = 0x2000
    OuterMarker = 0x4000
    MiddleMarker = 0x8000
    FromTrue = 0x10000
    Flying = 0x80000000


class FlightData(ctypes.Structure):
    """Main FlightData structure from BMS shared memory"""
    _pack_ = 8  # 8-byte alignment as specified in header
    _fields_ = [
        # Position and velocity
        ("x", ctypes.c_float),                  # Ownship North (Ft)
        ("y", ctypes.c_float),                  # Ownship East (Ft)
        ("z", ctypes.c_float),                  # Ownship Down (Ft)
        ("xDot", ctypes.c_float),               # Ownship North Rate (ft/sec)
        ("yDot", ctypes.c_float),               # Ownship East Rate (ft/sec)
        ("zDot", ctypes.c_float),               # Ownship Down Rate (ft/sec)
        
        # Attitude
        ("alpha", ctypes.c_float),              # Ownship AOA (Degrees)
        ("beta", ctypes.c_float),               # Ownship Beta (Degrees)
        ("gamma", ctypes.c_float),              # Ownship Gamma (Radians)
        ("pitch", ctypes.c_float),              # Ownship Pitch (Radians)
        ("roll", ctypes.c_float),               # Ownship Roll (Radians)
        ("yaw", ctypes.c_float),                # Ownship Yaw (Radians)
        
        # Airspeed and performance
        ("mach", ctypes.c_float),               # Ownship Mach number
        ("kias", ctypes.c_float),               # Ownship Indicated Airspeed (Knots)
        ("vt", ctypes.c_float),                 # Ownship True Airspeed (Ft/Sec)
        ("gs", ctypes.c_float),                 # Ownship Normal Gs
        ("windOffset", ctypes.c_float),         # Wind delta to FPM (Radians)
        
        # Engine
        ("nozzlePos", ctypes.c_float),          # Engine nozzle percent open (0-100)
        ("internalFuel", ctypes.c_float),       # Internal fuel (Lbs)
        ("externalFuel", ctypes.c_float),       # External fuel (Lbs)
        ("fuelFlow", ctypes.c_float),           # Fuel flow (Lbs/Hour)
        ("rpm", ctypes.c_float),                # Engine rpm (Percent 0-103)
        ("ftit", ctypes.c_float),               # Forward Turbine Inlet Temp (Degrees C)
        
        # Gear and controls
        ("gearPos", ctypes.c_float),            # Gear position 0 = up, 1 = down
        ("speedBrake", ctypes.c_float),         # Speed brake position 0 = closed, 1 = 60 degrees
        ("epuFuel", ctypes.c_float),            # EPU fuel (Percent 0-100)
        ("oilPressure", ctypes.c_float),        # Oil Pressure (Percent 0-100)
        
        # Lights
        ("lightBits", ctypes.c_uint),           # Cockpit indicator lights
        
        # Head tracking (only work with -head command line parameter)
        ("headPitch", ctypes.c_float),          # Head pitch offset (radians)
        ("headRoll", ctypes.c_float),           # Head roll offset (radians)
        ("headYaw", ctypes.c_float),            # Head yaw offset (radians)
        
        # Additional lights
        ("lightBits2", ctypes.c_uint),          # Additional cockpit lights
        ("lightBits3", ctypes.c_uint),          # More cockpit lights
        
        # Chaff/Flare
        ("ChaffCount", ctypes.c_float),         # Number of Chaff left
        ("FlareCount", ctypes.c_float),         # Number of Flare left
        
        # Landing gear positions
        ("NoseGearPos", ctypes.c_float),        # Nose gear position
        ("LeftGearPos", ctypes.c_float),        # Left gear position
        ("RightGearPos", ctypes.c_float),       # Right gear position
        
        # ADI values
        ("AdiIlsHorPos", ctypes.c_float),       # Horizontal ILS bar position
        ("AdiIlsVerPos", ctypes.c_float),       # Vertical ILS bar position
        
        # HSI states
        ("courseState", ctypes.c_int),          # HSI course state
        ("headingState", ctypes.c_int),         # HSI heading state
        ("totalStates", ctypes.c_int),          # HSI total states
        
        # HSI values
        ("courseDeviation", ctypes.c_float),    # HSI course deviation
        ("desiredCourse", ctypes.c_float),      # HSI desired course
        ("distanceToBeacon", ctypes.c_float),   # Distance to beacon
        ("bearingToBeacon", ctypes.c_float),    # Bearing to beacon
        ("currentHeading", ctypes.c_float),     # Current heading
        ("desiredHeading", ctypes.c_float),     # Desired heading
        ("deviationLimit", ctypes.c_float),     # Deviation limit
        ("halfDeviationLimit", ctypes.c_float), # Half deviation limit
        ("localizerCourse", ctypes.c_float),    # Localizer course
        ("airbaseX", ctypes.c_float),           # Airbase X coordinate
        ("airbaseY", ctypes.c_float),           # Airbase Y coordinate
        ("totalValues", ctypes.c_float),        # Total values
        
        # Trim
        ("TrimPitch", ctypes.c_float),          # Pitch trim (-0.5 to +0.5)
        ("TrimRoll", ctypes.c_float),           # Roll trim (-0.5 to +0.5)
        ("TrimYaw", ctypes.c_float),            # Yaw trim (-0.5 to +0.5)
        
        # HSI flags
        ("hsiBits", ctypes.c_uint),             # HSI flags
        
        # DED Lines
        ("DEDLines", ctypes.c_char * 5 * 26),   # DED display lines
        ("Invert", ctypes.c_char * 5 * 26),     # DED invert flags
        
        # PFL Lines
        ("PFLLines", ctypes.c_char * 5 * 26),   # PFL display lines
        ("PFLInvert", ctypes.c_char * 5 * 26),  # PFL invert flags
        
        # TACAN Channel
        ("UFCTChan", ctypes.c_int),             # UFC TACAN channel
        ("AUXTChan", ctypes.c_int),             # AUX TACAN channel
        
        # RWR
        ("RwrObjectCount", ctypes.c_int),       # Number of RWR objects
        ("RWRsymbol", ctypes.c_int * MAX_RWR_OBJECTS),      # RWR symbols
        ("bearing", ctypes.c_float * MAX_RWR_OBJECTS),      # RWR bearings
        ("missileActivity", ctypes.c_ulong * MAX_RWR_OBJECTS),  # Missile activity
        ("missileLaunch", ctypes.c_ulong * MAX_RWR_OBJECTS),    # Missile launch
        ("selected", ctypes.c_ulong * MAX_RWR_OBJECTS),     # Selected objects
        ("lethality", ctypes.c_float * MAX_RWR_OBJECTS),    # Lethality values
        ("newDetection", ctypes.c_ulong * MAX_RWR_OBJECTS), # New detections
        
        # Fuel values
        ("fwd", ctypes.c_float),                # Forward fuel
        ("aft", ctypes.c_float),                # Aft fuel
        ("total", ctypes.c_float),              # Total fuel
        
        # Version and additional head tracking
        ("VersionNum", ctypes.c_int),           # Version number
        ("headX", ctypes.c_float),              # Head X offset (feet)
        ("headY", ctypes.c_float),              # Head Y offset (feet)
        ("headZ", ctypes.c_float),              # Head Z offset (feet)
        ("MainPower", ctypes.c_int),            # Main power switch state
    ]


class OsbLabel(ctypes.Structure):
    """OSB label structure"""
    _pack_ = 8
    _fields_ = [
        ("line1", ctypes.c_char * OSB_STRING_LENGTH),
        ("line2", ctypes.c_char * OSB_STRING_LENGTH),
        ("inverted", ctypes.c_bool),
    ]


class OSBData(ctypes.Structure):
    """OSB data structure for MFD button labels"""
    _pack_ = 8
    _fields_ = [
        ("leftMFD", OsbLabel * 20),
        ("rightMFD", OsbLabel * 20),
    ]


class FlightData2(ctypes.Structure):
    """Extended flight data structure"""
    _pack_ = 8
    _fields_ = [
        # VERSION 1
        ("nozzlePos2", ctypes.c_float),         # Engine 2 nozzle position
        ("rpm2", ctypes.c_float),               # Engine 2 RPM
        ("ftit2", ctypes.c_float),              # Engine 2 FTIT
        ("oilPressure2", ctypes.c_float),       # Engine 2 oil pressure
        ("navMode", ctypes.c_ubyte),            # Navigation mode
        ("AAUZ", ctypes.c_float),               # Barometric altitude
        ("tacanInfo", ctypes.c_char * 2),       # TACAN band/mode settings
        
        # VERSION 2/7
        ("AltCalReading", ctypes.c_int),        # Altimeter calibration
        ("altBits", ctypes.c_uint),             # Altimeter bits
        ("powerBits", ctypes.c_uint),           # Power bus states
        ("blinkBits", ctypes.c_uint),           # Blink status
        ("cmdsMode", ctypes.c_int),             # CMDS mode
        ("uhf_panel_preset", ctypes.c_int),     # UHF preset
        
        # VERSION 3
        ("uhf_panel_frequency", ctypes.c_int),  # UHF frequency
        ("cabinAlt", ctypes.c_float),           # Cabin altitude
        ("hydPressureA", ctypes.c_float),       # Hydraulic pressure A
        ("hydPressureB", ctypes.c_float),       # Hydraulic pressure B
        ("currentTime", ctypes.c_int),          # Current time
        ("vehicleACD", ctypes.c_short),         # Aircraft type
        ("VersionNum", ctypes.c_int),           # Version number
        
        # VERSION 4
        ("fuelFlow2", ctypes.c_float),          # Fuel flow 2
        
        # VERSION 5/8
        ("RwrInfo", ctypes.c_char * RWRINFO_SIZE),  # RWR info
        ("lefPos", ctypes.c_float),             # LEF position
        ("tefPos", ctypes.c_float),             # TEF position
        
        # VERSION 6
        ("vtolPos", ctypes.c_float),            # VTOL position
        
        # VERSION 9
        ("pilotsOnline", ctypes.c_char),        # Number of pilots online
        ("pilotsCallsign", ctypes.c_char * MAX_CALLSIGNS * CALLSIGN_LEN),  # Pilot callsigns
        ("pilotsStatus", ctypes.c_char * MAX_CALLSIGNS),  # Pilot status
        
        # VERSION 10
        ("bumpIntensity", ctypes.c_float),      # Bump intensity
        
        # VERSION 11
        ("latitude", ctypes.c_float),           # Latitude
        ("longitude", ctypes.c_float),          # Longitude
        
        # VERSION 12
        ("RTT_size", ctypes.c_ushort * 2),      # RTT size
        ("RTT_area", ctypes.c_ushort * RTT_NO_OF_AREAS * 4),  # RTT areas
        
        # VERSION 13
        ("iffBackupMode1Digit1", ctypes.c_char),    # IFF backup digits
        ("iffBackupMode1Digit2", ctypes.c_char),
        ("iffBackupMode3ADigit1", ctypes.c_char),
        ("iffBackupMode3ADigit2", ctypes.c_char),
        
        # VERSION 14
        ("instrLight", ctypes.c_ubyte),         # Instrument light brightness
        
        # VERSION 15
        ("bettyBits", ctypes.c_uint),           # Betty bits
        ("miscBits", ctypes.c_uint),            # Misc bits
        ("RALT", ctypes.c_float),               # Radar altitude
        ("bingoFuel", ctypes.c_float),          # Bingo fuel
        ("caraAlow", ctypes.c_float),           # Cara alow
        ("bullseyeX", ctypes.c_float),          # Bullseye X
        ("bullseyeY", ctypes.c_float),          # Bullseye Y
        ("BMSVersionMajor", ctypes.c_int),      # BMS version info
        ("BMSVersionMinor", ctypes.c_int),
        ("BMSVersionMicro", ctypes.c_int),
        ("BMSBuildNumber", ctypes.c_int),
        ("StringAreaSize", ctypes.c_uint),      # String area size
        ("StringAreaTime", ctypes.c_uint),      # String area timestamp
        ("DrawingAreaSize", ctypes.c_uint),     # Drawing area size
        
        # VERSION 16
        ("turnRate", ctypes.c_float),           # Turn rate
        
        # VERSION 18
        ("floodConsole", ctypes.c_ubyte),       # Flood console brightness
        
        # VERSION 19
        ("magDeviationSystem", ctypes.c_float), # Magnetic deviation
        ("magDeviationReal", ctypes.c_float),
        ("ecmBits", ctypes.c_uint * MAX_ECM_PROGRAMS),  # ECM bits
        ("ecmOper", ctypes.c_ubyte),            # ECM operation state
        ("RWRjammingStatus", ctypes.c_ubyte * MAX_RWR_OBJECTS),  # RWR jamming
        
        # VERSION 20
        ("radio2_preset", ctypes.c_int),        # Radio 2 preset
        ("radio2_frequency", ctypes.c_int),     # Radio 2 frequency
        ("iffTransponderActiveCode1", ctypes.c_char),   # IFF codes
        ("iffTransponderActiveCode2", ctypes.c_short),
        ("iffTransponderActiveCode3A", ctypes.c_short),
        ("iffTransponderActiveCodeC", ctypes.c_short),
        ("iffTransponderActiveCode4", ctypes.c_short),
        
        # VERSION 21
        ("tacan_ils_frequency", ctypes.c_int),  # TACAN ILS frequency
        
        # VERSION 22
        ("desired_RTT_FPS", ctypes.c_int),      # Desired RTT FPS
        ("sideSlipdeg", ctypes.c_float),        # Side slip angle
    ]


class BMSSharedMemory:
    """
    Falcon BMS Shared Memory Reader
    
    Provides access to BMS shared memory areas using Windows memory mapping.
    """
    
    def __init__(self):
        self.kernel32 = ctypes.windll.kernel32
        self.flight_data_handle = None
        self.flight_data2_handle = None
        self.osb_data_handle = None
        self.string_data_handle = None
        self.drawing_data_handle = None
        self.ivibe_data_handle = None
        
        self.flight_data_ptr = None
        self.flight_data2_ptr = None
        self.osb_data_ptr = None
        self.string_data_ptr = None
        self.drawing_data_ptr = None
        self.ivibe_data_ptr = None
        
        self._lock = Lock()
        self._connected = False
        
        # String data caching
        self._cached_string_area_time = 0
        self._cached_strings = {}
        
    def connect(self) -> bool:
        """
        Connect to BMS shared memory areas
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        with self._lock:
            try:
                # Open FlightData shared memory
                self.flight_data_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconSharedMemoryArea"
                )
                
                if self.flight_data_handle:
                    self.flight_data_ptr = self.kernel32.MapViewOfFile(
                        self.flight_data_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                    
                # Open FlightData2 shared memory
                self.flight_data2_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconSharedMemoryArea2"
                )
                
                if self.flight_data2_handle:
                    self.flight_data2_ptr = self.kernel32.MapViewOfFile(
                        self.flight_data2_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                    
                # Open OSB data shared memory
                self.osb_data_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconSharedOsbMemoryArea"
                )
                
                if self.osb_data_handle:
                    self.osb_data_ptr = self.kernel32.MapViewOfFile(
                        self.osb_data_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                    
                # Open String data shared memory
                self.string_data_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconSharedMemoryAreaString"
                )
                
                if self.string_data_handle:
                    self.string_data_ptr = self.kernel32.MapViewOfFile(
                        self.string_data_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                    
                # Open Drawing data shared memory
                self.drawing_data_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconSharedMemoryAreaDrawing"
                )
                
                if self.drawing_data_handle:
                    self.drawing_data_ptr = self.kernel32.MapViewOfFile(
                        self.drawing_data_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                
                # Open IVibeData shared memory
                self.ivibe_data_handle = self.kernel32.OpenFileMappingW(
                    0x4,  # FILE_MAP_READ
                    False,
                    "FalconIntellivibeSharedMemoryArea"
                )
                
                if self.ivibe_data_handle:
                    self.ivibe_data_ptr = self.kernel32.MapViewOfFile(
                        self.ivibe_data_handle,
                        0x4,  # FILE_MAP_READ
                        0, 0, 0
                    )
                
                # Check if we have at least FlightData
                self._connected = self.flight_data_ptr is not None
                
                if self._connected:
                    logger.info("Connected to BMS shared memory")
                else:
                    logger.warning("Failed to connect to BMS shared memory - is BMS running?")
                    
                return self._connected
                
            except Exception as e:
                logger.error(f"Error connecting to BMS shared memory: {e}")
                self.disconnect()
                return False
    
    def disconnect(self):
        """Disconnect from shared memory areas"""
        with self._lock:
            # Unmap views
            if self.flight_data_ptr:
                self.kernel32.UnmapViewOfFile(self.flight_data_ptr)
                self.flight_data_ptr = None
                
            if self.flight_data2_ptr:
                self.kernel32.UnmapViewOfFile(self.flight_data2_ptr)
                self.flight_data2_ptr = None
                
            if self.osb_data_ptr:
                self.kernel32.UnmapViewOfFile(self.osb_data_ptr)
                self.osb_data_ptr = None
                
            if self.string_data_ptr:
                self.kernel32.UnmapViewOfFile(self.string_data_ptr)
                self.string_data_ptr = None
                
            if self.drawing_data_ptr:
                self.kernel32.UnmapViewOfFile(self.drawing_data_ptr)
                self.drawing_data_ptr = None
                
            if self.ivibe_data_ptr:
                self.kernel32.UnmapViewOfFile(self.ivibe_data_ptr)
                self.ivibe_data_ptr = None
            
            # Close handles
            if self.flight_data_handle:
                self.kernel32.CloseHandle(self.flight_data_handle)
                self.flight_data_handle = None
                
            if self.flight_data2_handle:
                self.kernel32.CloseHandle(self.flight_data2_handle)
                self.flight_data2_handle = None
                
            if self.osb_data_handle:
                self.kernel32.CloseHandle(self.osb_data_handle)
                self.osb_data_handle = None
                
            if self.string_data_handle:
                self.kernel32.CloseHandle(self.string_data_handle)
                self.string_data_handle = None
                
            if self.drawing_data_handle:
                self.kernel32.CloseHandle(self.drawing_data_handle)
                self.drawing_data_handle = None
                
            if self.ivibe_data_handle:
                self.kernel32.CloseHandle(self.ivibe_data_handle)
                self.ivibe_data_handle = None
            
            # Clear caches
            self._cached_string_area_time = 0
            self._cached_strings = {}
            
            self._connected = False
            logger.info("Disconnected from BMS shared memory")
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to shared memory"""
        return self._connected
    
    def get_flight_data(self) -> Optional[FlightData]:
        """
        Get current flight data
        
        Returns:
            FlightData object or None if not connected
        """
        if not self._connected or not self.flight_data_ptr:
            return None
            
        try:
            with self._lock:
                # Create FlightData structure from memory
                return FlightData.from_address(self.flight_data_ptr)
        except Exception as e:
            logger.error(f"Error reading flight data: {e}")
            return None
    
    def get_flight_data2(self) -> Optional[FlightData2]:
        """
        Get current extended flight data
        
        Returns:
            FlightData2 object or None if not connected
        """
        if not self._connected or not self.flight_data2_ptr:
            return None
            
        try:
            with self._lock:
                return FlightData2.from_address(self.flight_data2_ptr)
        except Exception as e:
            logger.error(f"Error reading flight data 2: {e}")
            return None
    
    def get_osb_data(self) -> Optional[OSBData]:
        """
        Get current OSB data for MFD labels
        
        Returns:
            OSBData object or None if not connected
        """
        if not self._connected or not self.osb_data_ptr:
            return None
            
        try:
            with self._lock:
                return OSBData.from_address(self.osb_data_ptr)
        except Exception as e:
            logger.error(f"Error reading OSB data: {e}")
            return None
    
    def get_ivibe_data(self) -> Optional[IVibeData]:
        """
        Get current IVibeData for haptic feedback
        
        Returns:
            IVibeData object or None if not connected
        """
        if not self._connected or not self.ivibe_data_ptr:
            return None
            
        try:
            with self._lock:
                return IVibeData.from_address(self.ivibe_data_ptr)
        except Exception as e:
            logger.error(f"Error reading IVibeData: {e}")
            return None
    
    def get_string_data(self) -> Optional[StringData]:
        """
        Get current string data
        
        Returns:
            StringData object or None if not connected
        """
        if not self._connected or not self.string_data_ptr:
            return None
            
        try:
            with self._lock:
                return StringData.from_address(self.string_data_ptr)
        except Exception as e:
            logger.error(f"Error reading string data: {e}")
            return None
    
    def get_bms_strings(self) -> Dict[int, str]:
        """
        Parse and return BMS strings from string data
        Uses caching based on StringAreaTime to avoid unnecessary parsing
        
        Returns:
            Dictionary mapping string IDs to string content
        """
        # Get FlightData2 to check StringAreaTime
        flight_data2 = self.get_flight_data2()
        if not flight_data2:
            return {}
        
        # Check if we can use cached data
        current_string_area_time = flight_data2.StringAreaTime
        if (current_string_area_time == self._cached_string_area_time and 
            self._cached_strings):
            return self._cached_strings.copy()
        
        string_data = self.get_string_data()
        if not string_data:
            return {}
        
        strings = {}
        
        try:
            # Parse string headers from the data buffer
            data_offset = 0
            for i in range(string_data.NoOfStrings):
                # Each string header contains: ID (4 bytes), Length (4 bytes)
                if data_offset + 8 > string_data.dataSize:
                    break
                
                # Extract header info
                header_data = string_data.data[data_offset:data_offset + 8]
                if len(header_data) < 8:
                    break
                
                string_id = int.from_bytes(header_data[0:4], byteorder='little')
                string_length = int.from_bytes(header_data[4:8], byteorder='little')
                
                data_offset += 8
                
                # Extract string content
                if data_offset + string_length <= string_data.dataSize:
                    string_content = string_data.data[data_offset:data_offset + string_length]
                    try:
                        # Decode string, handling null termination
                        decoded_string = string_content.decode('utf-8', errors='ignore').rstrip('\x00')
                        strings[string_id] = decoded_string
                    except UnicodeDecodeError:
                        # Fallback to ascii if utf-8 fails
                        decoded_string = string_content.decode('ascii', errors='ignore').rstrip('\x00')
                        strings[string_id] = decoded_string
                    
                    data_offset += string_length
                else:
                    break
            
            # Update cache
            self._cached_string_area_time = current_string_area_time
            self._cached_strings = strings.copy()
                    
        except Exception as e:
            logger.error(f"Error parsing BMS strings: {e}")
            
        return strings
    
    def invalidate_string_cache(self):
        """
        Manually invalidate the string data cache
        Useful for debugging or forcing a refresh
        """
        with self._lock:
            self._cached_string_area_time = 0
            self._cached_strings = {}
            
    def is_flying(self) -> bool:
        """
        Check if player is currently flying (not in UI)
        
        Returns:
            bool: True if flying, False otherwise
        """
        flight_data = self.get_flight_data()
        if flight_data:
            return bool(flight_data.hsiBits & HsiBits.Flying)
        return False
    
    def get_aircraft_state(self) -> Dict[str, Any]:
        """
        Get comprehensive aircraft state data
        
        Returns:
            Dictionary containing aircraft state or empty dict if not connected
        """
        flight_data = self.get_flight_data()
        flight_data2 = self.get_flight_data2()
        ivibe_data = self.get_ivibe_data()
        bms_strings = self.get_bms_strings()
        
        if not flight_data:
            return {}
        
        state = {
            # Position and attitude
            'x': flight_data.x,
            'y': flight_data.y,
            'z': flight_data.z,
            'pitch': flight_data.pitch,
            'roll': flight_data.roll,
            'yaw': flight_data.yaw,
            'alpha': flight_data.alpha,
            'beta': flight_data.beta,
            
            # Velocity and performance
            'airspeed_kias': flight_data.kias,
            'airspeed_true': flight_data.vt,
            'mach': flight_data.mach,
            'gs': flight_data.gs,
            'vertical_speed': flight_data.zDot,
            
            # Engine
            'rpm': flight_data.rpm,
            'ftit': flight_data.ftit,
            'nozzle_pos': flight_data.nozzlePos,
            'fuel_flow': flight_data.fuelFlow,
            'oil_pressure': flight_data.oilPressure,
            
            # Fuel
            'internal_fuel': flight_data.internalFuel,
            'external_fuel': flight_data.externalFuel,
            'total_fuel': flight_data.total,
            'epu_fuel': flight_data.epuFuel,
            
            # Gear and controls
            'gear_pos': flight_data.gearPos,
            'nose_gear_pos': flight_data.NoseGearPos,
            'left_gear_pos': flight_data.LeftGearPos,
            'right_gear_pos': flight_data.RightGearPos,
            'speed_brake': flight_data.speedBrake,
            
            # Trim
            'trim_pitch': flight_data.TrimPitch,
            'trim_roll': flight_data.TrimRoll,
            'trim_yaw': flight_data.TrimYaw,
            
            # Status flags
            'on_ground': bool(flight_data.lightBits & LightBits.ONGROUND),
            'gear_down': bool(flight_data.lightBits3 & (LightBits3.NoseGearDown | LightBits3.LeftGearDown | LightBits3.RightGearDown)),
            'autopilot_on': bool(flight_data.lightBits & LightBits.AutoPilotOn),
            'master_caution': bool(flight_data.lightBits & LightBits.MasterCaution),
            'flying': bool(flight_data.hsiBits & HsiBits.Flying),
            
            # Countermeasures
            'chaff_count': flight_data.ChaffCount,
            'flare_count': flight_data.FlareCount,
            
            # Navigation
            'current_heading': flight_data.currentHeading,
            'desired_heading': flight_data.desiredHeading,
            'course_deviation': flight_data.courseDeviation,
            
            # Version info
            'version': flight_data.VersionNum,
        }
        
        # Add FlightData2 fields if available
        if flight_data2:
            state.update({
                'altitude_barometric': flight_data2.AAUZ,
                'cabin_altitude': flight_data2.cabinAlt,
                'hydraulic_pressure_a': flight_data2.hydPressureA,
                'hydraulic_pressure_b': flight_data2.hydPressureB,
                'latitude': flight_data2.latitude,
                'longitude': flight_data2.longitude,
                'bump_intensity': flight_data2.bumpIntensity,
                'turn_rate': flight_data2.turnRate,
                'radar_altitude': flight_data2.RALT,
                'bingo_fuel': flight_data2.bingoFuel,
                'lef_pos': flight_data2.lefPos,
                'tef_pos': flight_data2.tefPos,
                'side_slip': flight_data2.sideSlipdeg,
                'version2': flight_data2.VersionNum,
            })
        
        # Add IVibeData fields if available
        if ivibe_data:
            state.update({
                'aa_missiles_fired': ivibe_data.AAMissileFired,
                'ag_missiles_fired': ivibe_data.AGMissileFired,
                'bombs_dropped': ivibe_data.BombDropped,
                'flares_dropped': ivibe_data.FlareDropped,
                'chaff_dropped': ivibe_data.ChaffDropped,
                'bullets_fired': ivibe_data.BulletsFired,
                'collision_counter': ivibe_data.CollisionCounter,
                'is_firing_gun': ivibe_data.IsFiringGun,
                'is_end_flight': ivibe_data.IsEndFlight,
                'is_ejecting': ivibe_data.IsEjecting,
                'in_3d': ivibe_data.In3D,
                'is_paused_ivibe': ivibe_data.IsPaused,
                'is_frozen': ivibe_data.IsFrozen,
                'is_over_g': ivibe_data.IsOverG,
                'is_on_ground_ivibe': ivibe_data.IsOnGround,
                'is_exit_game': ivibe_data.IsExitGame,
                'g_force': ivibe_data.Gforce,
                'eye_x': ivibe_data.eyex,
                'eye_y': ivibe_data.eyey,
                'eye_z': ivibe_data.eyez,
                'last_damage': ivibe_data.lastdamage,
                'damage_force': ivibe_data.damageforce,
                'when_damage': ivibe_data.whendamage,
            })
        
        # Add BMS strings if available
        if bms_strings:
            state['bms_strings'] = bms_strings
        
        return state


# Global instance for module-level access
_bms_shared_memory = None


def get_bms_shared_memory() -> BMSSharedMemory:
    """Get the global BMS shared memory instance"""
    global _bms_shared_memory
    if _bms_shared_memory is None:
        _bms_shared_memory = BMSSharedMemory()
    return _bms_shared_memory


def connect() -> bool:
    """Connect to BMS shared memory"""
    return get_bms_shared_memory().connect()


def disconnect():
    """Disconnect from BMS shared memory"""
    get_bms_shared_memory().disconnect()


def is_connected() -> bool:
    """Check if connected to BMS shared memory"""
    return get_bms_shared_memory().is_connected


def get_aircraft_state() -> Dict[str, Any]:
    """Get current aircraft state"""
    return get_bms_shared_memory().get_aircraft_state()


def is_flying() -> bool:
    """Check if currently flying"""
    return get_bms_shared_memory().is_flying()


def get_ivibe_data() -> Optional[IVibeData]:
    """Get current IVibeData"""
    return get_bms_shared_memory().get_ivibe_data()


def get_string_data() -> Optional[StringData]:
    """Get current string data"""
    return get_bms_shared_memory().get_string_data()


def get_bms_strings() -> Dict[int, str]:
    """Get parsed BMS strings"""
    return get_bms_shared_memory().get_bms_strings()


if __name__ == "__main__":
    import sys
    import time
    import json
    from threading import Event
    import faulthandler
    faulthandler.enable()
    
    def print_flight_data(flight_data: FlightData):
        """Print formatted flight data"""
        print(f"  Position: X={flight_data.x:.1f}, Y={flight_data.y:.1f}, Z={flight_data.z:.1f}")
        print(f"  Attitude: Pitch={flight_data.pitch:.3f}, Roll={flight_data.roll:.3f}, Yaw={flight_data.yaw:.3f}")
        print(f"  Airspeed: KIAS={flight_data.kias:.1f}, Mach={flight_data.mach:.2f}, TAS={flight_data.vt:.1f}")
        print(f"  Engine: RPM={flight_data.rpm:.1f}%, FTIT={flight_data.ftit:.1f}°C")
        print(f"  Fuel: Internal={flight_data.internalFuel:.0f}, External={flight_data.externalFuel:.0f}")
        print(f"  G-Force: {flight_data.gs:.2f}G")
        print(f"  On Ground: {bool(flight_data.lightBits & LightBits.ONGROUND)}")
        print(f"  Flying: {bool(flight_data.hsiBits & HsiBits.Flying)}")
    
    def print_ivibe_data(ivibe_data: IVibeData):
        """Print formatted IVibeData"""
        print(f"  G-Force: {ivibe_data.Gforce:.2f}G")
        print(f"  In 3D: {ivibe_data.In3D}")
        print(f"  On Ground: {ivibe_data.IsOnGround}")
        print(f"  Paused: {ivibe_data.IsPaused}")
        print(f"  Over G: {ivibe_data.IsOverG}")
        print(f"  Firing Gun: {ivibe_data.IsFiringGun}")
        print(f"  Weapons: AA={ivibe_data.AAMissileFired}, AG={ivibe_data.AGMissileFired}, Bombs={ivibe_data.BombDropped}")
        if ivibe_data.lastdamage > 0:
            print(f"  Last Damage: Quadrant={ivibe_data.lastdamage}, Force={ivibe_data.damageforce:.2f}")
    
    def test_connection():
        """Test BMS shared memory connection"""
        print("Testing BMS Shared Memory Connection...")
        print("-" * 50)
        
        if connect():
            print("✓ Connected to BMS shared memory successfully!")
            
            # Test each memory area
            bms = get_bms_shared_memory()

            test_bytes = ctypes.c_ubyte * 16
            print(type(bms.flight_data_ptr))
            test_data = test_bytes.from_address(bms.flight_data_ptr)
            logger.info(f"First 16 bytes: {[test_data[i] for i in range(16)]}")

            flight_data = bms.get_flight_data()
            if flight_data:
                print(f"✓ FlightData available (Version: {flight_data.VersionNum})")
            else:
                print("✗ FlightData not available")
            
            flight_data2 = bms.get_flight_data2()
            if flight_data2:
                print(f"✓ FlightData2 available (Version: {flight_data2.VersionNum})")
            else:
                print("✗ FlightData2 not available")
            
            osb_data = bms.get_osb_data()
            if osb_data:
                print("✓ OSBData available")
            else:
                print("✗ OSBData not available")
            
            string_data = bms.get_string_data()
            if string_data:
                print(f"✓ StringData available (Version: {string_data.VersionNum})")
            else:
                print("✗ StringData not available")
            
            ivibe_data = bms.get_ivibe_data()
            if ivibe_data:
                print("✓ IVibeData available")
            else:
                print("✗ IVibeData not available")
            
            print(f"✓ Currently flying: {is_flying()}")
            
        else:
            print("✗ Failed to connect to BMS shared memory")
            print("  Make sure Falcon BMS is running and try again.")
    
    def monitor_data(update_rate: float = 1.0):
        """Monitor BMS data in real-time"""
        print(f"Monitoring BMS data (update rate: {update_rate:.1f}s)")
        print("Press Ctrl+C to stop...")
        print("-" * 50)
        
        if not is_connected():
            print("Not connected to BMS. Run connection test first.")
            return
        
        stop_event = Event()
        
        try:
            while not stop_event.is_set():
                print(f"\n[{time.strftime('%H:%M:%S')}] BMS Data Update:")
                
                flight_data = get_bms_shared_memory().get_flight_data()
                if flight_data:
                    print("FlightData:")
                    print_flight_data(flight_data)
                
                ivibe_data = get_ivibe_data()
                if ivibe_data:
                    print("\nIVibeData:")
                    print_ivibe_data(ivibe_data)
                
                bms_strings = get_bms_strings()
                if bms_strings:
                    print(f"\nBMS Strings: {len(bms_strings)} entries")
                    for string_id, content in list(bms_strings.items())[:3]:  # Show first 3
                        print(f"  ID {string_id}: {repr(content[:50])}")
                
                print("-" * 50)
                time.sleep(update_rate)
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
    
    def dump_aircraft_state():
        """Dump complete aircraft state to JSON"""
        print("Dumping complete aircraft state...")
        
        if not is_connected():
            print("Not connected to BMS. Run connection test first.")
            return
        
        state = get_aircraft_state()
        if state:
            # Convert to JSON-serializable format
            json_state = {}
            for key, value in state.items():
                try:
                    json.dumps(value)  # Test if serializable
                    json_state[key] = value
                except TypeError:
                    json_state[key] = str(value)  # Convert to string if not serializable
            
            print(json.dumps(json_state, indent=2))
        else:
            print("No aircraft state data available.")
    
    def performance_test(duration: float = 10.0):
        """Test shared memory read performance"""
        print(f"Running performance test for {duration:.1f} seconds...")
        
        if not is_connected():
            print("Not connected to BMS. Run connection test first.")
            return
        
        bms = get_bms_shared_memory()
        
        start_time = time.time()
        flight_data_reads = 0
        ivibe_reads = 0
        string_reads = 0
        
        while time.time() - start_time < duration:
            # Test FlightData reads
            flight_data = bms.get_flight_data()
            if flight_data:
                flight_data_reads += 1
            
            # Test IVibeData reads  
            ivibe_data = bms.get_ivibe_data()
            if ivibe_data:
                ivibe_reads += 1
            
            # Test String data reads
            strings = bms.get_bms_strings()
            if strings:
                string_reads += 1
        
        elapsed = time.time() - start_time
        
        print(f"Performance Results ({elapsed:.1f}s):")
        print(f"  FlightData: {flight_data_reads} reads ({flight_data_reads/elapsed:.1f} Hz)")
        print(f"  IVibeData: {ivibe_reads} reads ({ivibe_reads/elapsed:.1f} Hz)")
        print(f"  StringData: {string_reads} reads ({string_reads/elapsed:.1f} Hz)")
    
    def show_menu():
        """Show interactive menu"""
        print("\nBMS Shared Memory Test Menu:")
        print("1. Test Connection")
        print("2. Monitor Data (1s updates)")
        print("3. Monitor Data (Fast - 0.1s updates)")
        print("4. Dump Aircraft State (JSON)")
        print("5. Performance Test")
        print("6. Show String Data Details")
        print("7. Disconnect and Exit")
        print("0. Show this menu")
    
    def show_string_details():
        """Show detailed string data information"""
        print("BMS String Data Details:")
        
        string_data = get_string_data()
        if not string_data:
            print("No string data available.")
            return
        
        print(f"Version: {string_data.VersionNum}")
        print(f"Number of strings: {string_data.NoOfStrings}")
        print(f"Data size: {string_data.dataSize} bytes")
        
        strings = get_bms_strings()
        if strings:
            print(f"\nParsed strings ({len(strings)}):")
            for string_id, content in strings.items():
                print(f"  ID {string_id}: {repr(content)}")
        else:
            print("\nNo parsed strings available.")
    
    # Main interactive loop
    print("Falcon BMS Shared Memory Test Tool")
    print("==================================")
    
    show_menu()
    
    while True:
        try:
            choice = input("\nEnter choice: ").strip()
            
            if choice == '1':
                test_connection()
            elif choice == '2':
                monitor_data(1.0)
            elif choice == '3':
                monitor_data(0.1)
            elif choice == '4':
                dump_aircraft_state()
            elif choice == '5':
                performance_test()
            elif choice == '6':
                show_string_details()
            elif choice == '7':
                disconnect()
                print("Disconnected. Goodbye!")
                break
            elif choice == '0':
                show_menu()
            else:
                print("Invalid choice. Enter 0 to show menu.")
                
        except KeyboardInterrupt:
            print("\n\nExiting...")
            disconnect()
            break
        except Exception as e:
            print(f"Error: {e}")
