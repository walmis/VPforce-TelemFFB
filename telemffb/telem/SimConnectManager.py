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
SimConnectManager Module

This module provides a SimConnect interface for Microsoft Flight Simulator (MSFS) telemetry
and force feedback systems. It manages communication with MSFS via SimConnect to retrieve
aircraft telemetry data and send control inputs for force feedback devices.

The module includes:
- SimVar: Individual simulation variable representation
- SimVarArray: Array of simulation variables for multi-dimensional data
- SimConnectManager: Main class for managing SimConnect connection and data flow

Author: Valmantas Palikša, Micah Frisby
License: GPL-3.0
"""

from simconnect import *
from ctypes import byref, cast, sizeof
from telemffb.utils import dbprint
from telemffb.util.TransformExpr import TransformExpr
import time
import threading
import logging
import os
import telemffb.globals as G
from enum import IntEnum

surface_types = {
    0: "Concrete",
    1: "Grass",
    2: "Water",
    3: "Grass_bumpy",
    4: "Asphalt",
    5: "Short_grass",
    6: "Long_grass",
    7: "Hard_turf",
    8: "Snow",
    9: "Ice",
    10: "Urban",
    11: "Forest",
    12: "Dirt",
    13: "Coral",
    14: "Gravel",
    15: "Oil_treated",
    16: "Steel_mats",
    17: "Bituminus",
    18: "Brick",
    19: "Macadam",
    20: "Planks",
    21: "Sand",
    22: "Shale",
    23: "Tarmac",
    24: "Wright flyer track",
}

class SimConnectException(IntEnum):
    NONE = 0
    ERROR = 1
    SIZE_MISMATCH = 2
    UNRECOGNIZED_ID = 3
    UNOPENED = 4
    VERSION_MISMATCH = 5
    TOO_MANY_GROUPS = 6
    NAME_UNRECOGNIZED = 7
    TOO_MANY_EVENT_NAMES = 8
    EVENT_ID_DUPLICATE = 9
    TOO_MANY_MAPS = 10
    TOO_MANY_OBJECTS = 11
    TOO_MANY_REQUESTS = 12
    WEATHER_INVALID_PORT = 13
    WEATHER_INVALID_METAR = 14
    WEATHER_UNABLE_TO_GET_OBSERVATION = 15
    WEATHER_UNABLE_TO_CREATE_STATION = 16
    WEATHER_UNABLE_TO_REMOVE_STATION = 17
    INVALID_DATA_TYPE = 18
    INVALID_DATA_SIZE = 19
    DATA_ERROR = 20
    INVALID_ARRAY = 21
    CREATE_OBJECT_FAILED = 22
    LOAD_FLIGHTPLAN_FAILED = 23
    OPERATION_INVALID_FOR_OBJECT_TYPE = 24
    ILLEGAL_OPERATION = 25
    ALREADY_SUBSCRIBED = 26
    INVALID_ENUM = 27
    DEFINITION_ERROR = 28
    DUPLICATE_ID = 29
    DATUM_ID = 30
    OUT_OF_BOUNDS = 31
    ALREADY_CREATED = 32
    OBJECT_OUTSIDE_REALITY_BUBBLE = 33
    OBJECT_CONTAINER = 34
    OBJECT_AI = 35
    OBJECT_ATC = 36
    OBJECT_SCHEDULE = 37
    JETWAY_DATA = 38
    ACTION_NOT_FOUND = 39
    NOT_AN_ACTION = 40
    INCORRECT_ACTION_PARAMS = 41
    GET_INPUT_EVENT_FAILED = 42
    SET_INPUT_EVENT_FAILED = 43

class SimVar:
    """
    Represents a single simulation variable from Microsoft Flight Simulator.
    
    A SimVar encapsulates a single telemetry data point from MSFS, including
    its name, SimConnect variable reference, units, data type, and optional
    scaling or transformation functions.
    
    Attributes:
        name (str): The friendly name for this simulation variable
        var (str): The SimConnect variable name (e.g., "AIRSPEED TRUE")
        sc_unit (str): The SimConnect unit specification
        unit (str): The preferred output unit (optional)
        datatype: The SimConnect data type (DATATYPE_FLOAT64, etc.)
        scale (float): Optional scaling factor applied to the raw value
        mutator (callable): Optional function to transform the raw value
        parent (SimVarArray): Parent array if this SimVar is part of an array
        index (int): Index within parent array if applicable
    """
    
    def __init__(self, name, var, sc_unit, unit=None, datatype=DATATYPE_FLOAT64, scale=None, mutator=None):
        """
        Initialize a SimVar instance.
        
        Args:
            name (str): Friendly name for the variable
            var (str): SimConnect variable name
            sc_unit (str): SimConnect unit specification
            unit (str, optional): Preferred output unit
            datatype: SimConnect data type constant
            scale (float, optional): Scaling factor for the value
            mutator (callable, optional): Function to transform the value
        """
        self.name = name
        self.var = var
        self.scale = scale
        self.mutator = mutator
        self.sc_unit = sc_unit
        self.unit = unit
        self.datatype = datatype
        self.parent = None
        self.index = None # index for multivariable simvars
        if self.sc_unit.lower() in ["bool", "enum"]:
            self.datatype = DATATYPE_INT32
        self._scale_transform = None

    def _build_scale_transform(self):
        """
        Convert the public 'scale' value into a Transform instance.
        """
        if isinstance(self.scale, TransformExpr):
            return self.scale

        if isinstance(self.scale, (int, float, str)):
            return TransformExpr(self.scale)

        raise TypeError(
            f"Invalid scale type for SimVar '{self.name}': {type(self.scale)}"
        )

    def _calculate(self, input):
        """
        Apply scaling and mutation transformations to a raw input value.
        
        Args:
            input: The raw value from SimConnect
            
        Returns:
            The transformed value after applying mutator and scaling
        """
        if self.mutator:
            input = self.mutator(input)

        if self.scale is not None:
            if self._scale_transform is None:
                self._scale_transform = self._build_scale_transform()
            input = self._scale_transform.apply(input)

        return input

    def __repr__(self) -> str:
        """
        Return a string representation of the SimVar.
        
        Returns:
            str: String representation showing name and variable reference
        """
        return f"SimVar({self.name} '{self.var}')"

    @property
    def c_type(self):
        """
        Get the corresponding ctypes type for this SimVar's datatype.
        
        Returns:
            ctypes type: The appropriate ctypes type for SimConnect data handling
        """
        types = {
            DATATYPE_FLOAT64: c_double,
            DATATYPE_FLOAT32: c_float,
            DATATYPE_INT32: c_long,
            DATATYPE_STRING32: c_char*32,
            DATATYPE_STRING128: c_char*128
        }
        return types[self.datatype]

class SimVarArray:
    """
    Represents an array of related simulation variables from Microsoft Flight Simulator.
    
    A SimVarArray groups multiple related SimVars together, such as engine parameters
    for multi-engine aircraft or X/Y/Z components of vectors. It can handle both
    indexed variables (e.g., "ENGINE 1 RPM", "ENGINE 2 RPM") and keyword-based
    variables (e.g., "VELOCITY BODY X", "VELOCITY BODY Y").
    
    Attributes:
        name (str): The base name for this variable array
        var (str): The SimConnect variable template with placeholders
        unit (str): The unit specification for all variables in the array
        type: The SimConnect data type for all variables
        scale (float): Optional scaling factor applied to all values
        vars (list): List of individual SimVar instances in this array
        values (list): Current values for each variable in the array
        min (int): Minimum index for indexed variables
        max (int): Maximum index for indexed variables
        keywords (tuple): Keywords for keyword-based variables
    """
    
    def __init__(self, name, var, unit, type=DATATYPE_FLOAT64, scale=None, min=0, max=1, keywords=None):
        """
        Initialize a SimVarArray instance.
        
        Args:
            name (str): Base name for the variable array
            var (str): SimConnect variable template (use <> for keyword substitution)
            unit (str): Unit specification for all variables
            type: SimConnect data type constant
            scale (float, optional): Scaling factor for all values
            min (int): Minimum index for indexed variables (default: 0)
            max (int): Maximum index for indexed variables (default: 1)
            keywords (tuple, optional): Keywords for keyword-based variables
        """
        self.name = name
        self.var = var
        self.unit = unit
        self.type = type
        self.scale = scale
        self.vars = []
        self.values = []
        self.min = min
        self.max = max
        self.keywords = keywords
        if keywords is not None:
            for key in keywords:
                index = keywords.index(key)
                simvar = var.replace("<>", key)
                v = SimVar(name, simvar, unit, None, type, scale)
                v.index = index
                v.parent = self
                self.vars.append(v)
                self.values.append(0)
        else:
            for index in range(min, max+1):
                if index < min:
                    self.values.append(0)
                else:
                    v = SimVar(name, f"{var}:{index}", unit, None, type, scale)
                    v.index = index
                    v.parent = self
                    self.vars.append(v)
                    self.values.append(0)
    def clone(self):
        """
        Return new SimVarArray object with copied values from called instance.
        
        This method creates a separate copy of existing SimVarArrays for editing 
        during dynamic subscriptions without modifying the original array.
        
        Returns:
            SimVarArray: A new instance with the same configuration as this one
        """
        cloned_sv_array = SimVarArray(self.name, self.var, self.unit, type=self.type, scale=self.scale, min=self.min, max=self.max, keywords=self.keywords)
        return cloned_sv_array



EV_PAUSED = 65499 # id for paused event
EV_STARTED = 65498 # id for started event
EV_STOPPED = 65497  # id for stopped event
EV_SIMSTATE = 65496


class SimConnectManager(threading.Thread):
    """
    Manages SimConnect communication with Microsoft Flight Simulator.
    
    This class provides a threaded interface to Microsoft Flight Simulator's SimConnect
    system, handling telemetry data retrieval, event transmission, and connection management.
    It supports both MSFS 2020 and MSFS 2024.
    
    The manager maintains a comprehensive set of predefined simulation variables covering
    aircraft state, control surfaces, engines, environmental conditions, and system states.
    It also supports dynamic addition of custom variables and arrays.
    
    Key Features:
    - Automatic connection and reconnection handling
    - Comprehensive telemetry data collection
    - Event and datum transmission to MSFS
    - Support for both static and dynamic variable subscriptions
    - Pause/stop state detection and handling
    - Version detection (MSFS 2020/2024)
    
    Attributes:
        sim_vars (list): Predefined simulation variables
        sc: SimConnect instance (when connected)
        connected_version (str): Detected MSFS version
    """

    sim_vars = [
        SimVar("T", "ABSOLUTE TIME","Seconds" ),
        SimVar("N", "TITLE", "", datatype=DATATYPE_STRING128),
        SimVar("G", "G FORCE", "Number"),
        SimVarArray("AccBody", "ACCELERATION BODY <>", "feet per second squared", scale=0.031081, keywords=("X", "Y", "Z")), #scale fps/s to g
        SimVar("TAS", "AIRSPEED TRUE", "meter/second"),
        SimVar("IAS", "AIRSPEED INDICATED", "meter/second"),
        SimVar('CameraState', "CAMERA STATE", "Enum"),
        SimVar('IsInVr', "IS IN VR", "Bool"),
        SimVar("GroundSpeed", "GROUND VELOCITY", "meter/second"),
        SimVar("AirDensity", "AMBIENT DENSITY", "kilograms per cubic meter"),
        SimVar("AoA", "INCIDENCE ALPHA", "degrees"),
        SimVar("StallAoA", "STALL ALPHA", "degrees"),
        SimVar("SideSlip", "INCIDENCE BETA", "degrees"),
        SimVar("ElevDefl", "ELEVATOR DEFLECTION", "degrees"),
        SimVar("ElevDeflPct", "ELEVATOR DEFLECTION PCT", "Percent Over 100"),
        SimVar("ElevPos", "ELEVATOR POSITION", "Percent Over 100"),
        SimVar("ElevTrim", "ELEVATOR TRIM POSITION", "degrees"),
        SimVar("ElevTrimPct", "ELEVATOR TRIM PCT", "Percent Over 100"),
        SimVar("ElevTrimPct_ex1", "ELEVATOR TRIM PCT EX1", "Percent Over 100", scale="(2 * x) - 1"),
        SimVar("ElevTrimDnLmt", "ELEVATOR TRIM DOWN LIMIT", "degrees"),
        SimVar("ElevTrimUpLmt", "ELEVATOR TRIM UP LIMIT", "degrees"),
        SimVar("ElevTrimNeutral", "ELEVATOR TRIM NEUTRAL", "degrees"),
        SimVar("ElevTrimMax", "ELEVATOR TRIM MAX", "degrees"),  #2024 only (up)
        SimVar("ElevTrimMin", "ELEVATOR TRIM MIN", "degrees"),  #2024 only (down)
        SimVar("AileronDefl", "AILERON AVERAGE DEFLECTION", "degrees"),
        SimVarArray("AileronDeflPctLR", "AILERON <> DEFLECTION PCT", keywords=("LEFT", "RIGHT"), unit="Percent Over 100"),
        SimVar("AileronTrim", "AILERON TRIM", "degrees"),
        SimVar("AileronTrimPct", "AILERON TRIM PCT", "Percent Over 100"),
        SimVarArray("PropThrust", "PROP THRUST", "kilograms", min=1, max=4, scale=10),#scaled to newtons
        SimVarArray("PropRPM", "PROP RPM", "RPM", min=1, max=4),
        SimVar("RotorRPM", "ROTOR RPM:1", "RPM"),
        SimVar("DynPressure", "DYNAMIC PRESSURE", "pascal"),
        SimVar("APMaster", "AUTOPILOT MASTER", "Bool"),
        SimVar("RudderDefl", "RUDDER DEFLECTION", "degrees"),
        SimVar("RudderDeflPct", "RUDDER DEFLECTION PCT", "Percent Over 100"),
        SimVar("RudderTrimPct", "RUDDER TRIM PCT", "Percent Over 100"),
        SimVar("Pitch", "PLANE PITCH DEGREES", "degrees"),
        SimVar("Roll", "PLANE BANK DEGREES", "degrees"),
        SimVar("CyclicTrimX", "ROTOR LATERAL TRIM PCT", "Percent Over 100"),
        SimVar("CyclicTrimY", "ROTOR LONGITUDINAL TRIM PCT", "Percent Over 100"),
        SimVar("Heading", "PLANE HEADING DEGREES TRUE", "degrees"),
        SimVarArray("VelRotBody", "ROTATION VELOCITY BODY <>", "degrees per second", keywords=("X", "Y", "Z")),
        SimVarArray("AccRotBody", "ROTATION ACCELERATION BODY <>", "degrees per second squared", keywords=("X", "Y", "Z")),
        SimVarArray("DesignSpeed", "DESIGN SPEED <>", "meter/second", keywords=("VC", "VS0", "VS1")),
        SimVar("RefMaxIAS", "REFERENCE SPEED MAX IAS", "meter/second"),
        SimVar("RefMaxIAS_kt", "REFERENCE SPEED MAX IAS", "knots"),
        SimVar("VerticalSpeed", "VERTICAL SPEED", "meter/second"),
        SimVarArray("Brakes", "BRAKE <> POSITION", "Position", keywords=("LEFT", "RIGHT")),
        #SimVar("LinearCLAlpha", "LINEAR CL ALPHA", "Per Radian"),
        #SimVar("SigmaSqrt", "SIGMA SQRT", "Per Radian"),
        SimVar("SimDisabled", "SIM DISABLED", "Bool"),
        SimVar("SimOnGround", "SIM ON GROUND", "Bool"),
        SimVar("Parked", "PLANE IN PARKING STATE", "Bool"),
        SimVar("Slew", "IS SLEW ACTIVE", "Bool"),
        SimVar("SurfaceType", "SURFACE TYPE", "Enum", mutator=lambda x: surface_types.get(x, "unknown")),
        SimVar("SimconnectCategory", "CATEGORY", "", datatype=DATATYPE_STRING128),
        SimVar("EngineType", "ENGINE TYPE", "Enum"),
        SimVarArray("EngRPM", "GENERAL ENG PCT MAX RPM", "percent", min=1, max=4),
        SimVarArray("ThrottlePct", "GENERAL ENG THROTTLE LEVER POSITION", "percent", min=1, max=4),
        SimVar("NumEngines", "NUMBER OF ENGINES", "Number", datatype=DATATYPE_INT32),
        SimVarArray("AmbWind", "AMBIENT WIND <>", "meter/second", keywords= ("X", "Y", "Z")),
        SimVarArray("RelWind", "RELATIVE WIND VELOCITY BODY <>", "meter/second", keywords= ("X", "Y", "Z")),
        SimVarArray("VelWorld", "VELOCITY WORLD <>", "meter/second", keywords= ("X", "Y", "Z")),
        SimVarArray("WeightOnWheels", "CONTACT POINT COMPRESSION", "Number", min=0, max=2),
        SimVarArray("Flaps", "TRAILING EDGE FLAPS <> PERCENT", "Percent Over 100", keywords=("LEFT", "RIGHT")),
        SimVarArray("Gear", "GEAR <> POSITION", "Percent Over 100", keywords=("LEFT", "RIGHT")),
        SimVarArray("RetractableGear", "IS GEAR RETRACTABLE", "bool"),
        SimVarArray("Spoilers", "SPOILERS <> POSITION", "Percent Over 100", keywords=("LEFT", "RIGHT")),
        SimVarArray("Afterburner", "TURB ENG AFTERBURNER", "Number", min=1, max=2),
        SimVar("AfterburnerPct", "TURB ENG AFTERBURNER PCT ACTIVE", "Percent Over 100"),
        SimVar("ACisFBW", "FLY BY WIRE FAC SWITCH", "bool"),
        SimVar("StallWarning", "STALL WARNING", "bool"),
        SimVar("CollectivePos", "COLLECTIVE POSITION", "percent over 100"),
        SimVar("TailRotorPedalPos", "TAIL ROTOR BLADE PITCH PCT", "percent over 100"),
        SimVarArray("HydPress", "HYDRAULIC PRESSURE", "psi", min=1, max=2),
        SimVarArray("HydResPct", "HYDRAULIC RESERVOIR PERCENT", "Percent Over 100", min=1, max=2),
        SimVar("HydSwitch", "HYDRAULIC SWITCH", "bool"),
        SimVar("HydSys", "HYDRAULIC SYSTEM INTEGRITY", "Percent Over 100"),
        SimVar("_IS IN RTC", "IS IN RTC", "bool"),
        SimVar("_IS AVATAR", "IS AVATAR", "bool"),
        SimVar("_IS AIRCRAFT", "IS AIRCRAFT", "bool"),
        SimVar("CenterSteerAnglePct", "CONTACT POINT STEER ANGLE PCT", "percent over 100"),
        SimVar("WaterRudderExt", "WATER LEFT RUDDER EXTENDED", "percent over 100"),
        SimVar("ForceTrimSW", "L:TelemFFBHeliFT", "bool"),
    ]

    def __init__(self):
        """
        Initialize the SimConnectManager.
        
        Sets up the thread infrastructure and initializes all internal state variables
        for managing SimConnect communication, variable subscriptions, and data tracking.
        """
        threading.Thread.__init__(self, daemon=True)
        self.sc = None
        self._quit = False
        self.initial_subscribe_done = False
        self._sim_paused = False
        self._sim_started = 0
        self._sim_state = 0
        self._stop_state = 0
        self._final_frame_sent = 0
        self._events_to_send = []
        self._simdatums_to_send = []
        self.subscribed_vars = []
        self.temp_sim_vars = []
        self.temp_sv_array_element = []
        self.resubscribe = False
        self.current_simvars = []
        self.current_var_tracker = []
        self.new_var_tracker = []
        self.req_id = os.getpid()
        self.def_id = os.getpid()
        self.sv_dict = {}
        self.connected_version = None
        self._connect_attempts = 0



    def add_simvar(self, name, var, sc_unit, unit=None, datatype=DATATYPE_FLOAT64, scale=None, mutator=None):
        """
        Add or override a simulation variable for the next subscription cycle.
        
        This method allows dynamic addition of new simulation variables or overriding
        of existing ones. Variables are queued and will be applied on the next call
        to _subscribe().
        
        Args:
            name (str): Variable name. Use "name:index" format to override specific
                       array elements (e.g., "PropRPM:2" for second engine RPM)
            var (str): SimConnect variable reference
            sc_unit (str): SimConnect unit specification
            unit (str, optional): Preferred output unit
            datatype: SimConnect data type constant
            scale (float, optional): Scaling factor for the value
            mutator (callable, optional): Function to transform the value
        """
        if ":" in name:
            # We are replacing an element in a SimVarArray, create separate list
            sv = SimVar(name.split(":")[0], var, sc_unit, unit=unit, datatype=datatype, scale=scale, mutator=mutator)
            sv.index = int(name.split(":")[1])
            self.temp_sv_array_element.append(sv)

        else:
            self.temp_sim_vars.append(SimVar(name, var, sc_unit, unit=unit, datatype=datatype, scale=scale, mutator=mutator))
        
    def substitute_simvars(self):
        """
        Build the final list of simulation variables by merging predefined and custom variables.
        
        This method combines the predefined sim_vars list with any temporarily added variables
        from add_simvar(), handling both individual variable overrides and SimVarArray element
        overrides. It creates cloned copies of SimVarArrays when individual elements need to
        be modified to avoid affecting the original definitions.
        
        Returns:
            list: The final list of SimVar and SimVarArray objects to subscribe to
        """
        # build a combined list of the pre-defined simvars from __init__ and any new/updated simvars that have been set by a model
        master_list = list(self.sim_vars)
        override_list = list(self.temp_sim_vars)

        master_dict = {simvar.name: simvar for simvar in master_list}
        override_dict = {simvar.name: simvar for simvar in override_list}

        for sv_array_override in self.temp_sv_array_element:
            """clone each SimVarArray that we need to override so that we can modify its elements while leaving the 
            original in-tact.  Then add those simvars to the override dictionary for later processing
            """
            sv_array = master_dict.get(sv_array_override.name, None)  # Get Array element from master dictionary
            if sv_array is None:
                logging.error(f"Error resubscribing to SimVarArray element for '{sv_array_override.name}':  SimVarArray does not exist")
                continue
            if sv_array_override.name in override_dict:
                """if  the key already exists we have likely already cloned the original simvar array and this is 
                just another simvar to replace in the same array"""
                continue
            override_dict[sv_array_override.name] = sv_array.clone()  # create the cloned copy, add to override dictionary

        while self.temp_sv_array_element:
            """Now iterate through any overrides for SimVarArrays.  For each overridden simvar, we find the matching
            array and replace the simvar index given in the config file"""
            sv = self.temp_sv_array_element.pop(0)
            sv_array = override_dict.get(sv.name, None)  # Get cloned Array from override dictionary
            if sv_array is None:  # Check if  'name:idx' given in the config file is invalid and does not match an existing SimVarArray
                logging.error(f"Error resubscribing to SimVarArray element for '{sv.name}':  SimVarArray does not exist")
                continue

            if not 0 <= int(sv.index) < len(sv_array.vars):  # Check whether the given index to override exists in the SimVarArray
                logging.error(f"Error resubscribing to SimVarArray element for '{sv.name}':  The index '{sv.index}' doex not exist in SimVarArray '{sv.name}'")
                continue

            sv_array.vars[int(sv.index)] = sv  # Replace the defined index with the new simvar
            sv_array.vars[int(sv.index)].parent = sv_array  # set parent of newly replaced simvar array element

            override_dict[sv_array.name] = sv_array

        # Update the master dict with the override dict
        # This replaces any existing entries with the override ones and adds new ones
        master_dict.update(override_dict)

        resulting_list = list(master_dict.values())  # Convert the final dictionary back into a list

        self.temp_sim_vars.clear()
        self.temp_sv_array_element.clear()
        self.new_var_tracker.clear()
        self.sv_dict.clear()

        for sv in resulting_list:
            # build list of just the simvar / l:var for use in comparing to currently subscribed list (self.current_var_tracker)
            if isinstance(sv, SimVarArray):
                for sv in sv.vars:
                    self.new_var_tracker.append(sv.var)
                    self.sv_dict[sv.name] = sv.var
            else:
                self.new_var_tracker.append(sv.var)
                self.sv_dict[sv.name] = sv.var

        return resulting_list

    def _subscribe(self):
        """
        Subscribe to simulation variables with SimConnect.
        
        This method handles the SimConnect subscription process by:
        1. Building the final variable list via substitute_simvars()
        2. Checking if resubscription is needed (variable list changed)
        3. Clearing old subscriptions if necessary
        4. Adding all variables to a new data definition
        5. Requesting periodic data updates from MSFS
        
        The subscription only occurs if the variable list has changed since the
        last subscription to avoid unnecessary SimConnect overhead.
        """

        sim_vars = self.substitute_simvars()

        if self.current_var_tracker == self.new_var_tracker:
            # the current subscription matches the needed vars.. no need to resubscribe
            return
        else:
            logging.info("Simvar list has changed, creating new SC subscription")
            if self.initial_subscribe_done:
                self.sc.ClearDataDefinition(self.def_id)
                self.def_id += 1
                # self.REQ_ID += 1
            self.initial_subscribe_done = True

        self.subscribed_vars.clear()
        self.current_var_tracker.clear()
        self.sv_dict.clear()

        i = 0
        for sv in (sim_vars):
            if isinstance(sv, SimVarArray):
                for sv in sv.vars:
                    res = self.sc.AddToDataDefinition(self.def_id, sv.var, sv.sc_unit, sv.datatype, 0, i)
                    logging.debug(f"Result: {res} Subscribe SimVar {i} {sv}")

                    self.subscribed_vars.append(sv)
                    self.current_var_tracker.append(sv.var)
                    self.sv_dict[sv.name] = sv.var
                    i+=1
            else:
                res = self.sc.AddToDataDefinition(self.def_id, sv.var, sv.sc_unit, sv.datatype, 0, i)
                logging.debug(f"Result: {res} Subscribe SimVar {i} {sv}")

                self.subscribed_vars.append(sv)
                self.current_var_tracker.append(sv.var)
                self.sv_dict[sv.name] = sv.var
                i+=1

        self.sc.RequestDataOnSimObject(
            self.req_id,  # request identifier for response packets
            self.def_id,  # the data definition group
            OBJECT_ID_USER,
            PERIOD_SIM_FRAME,
            DATA_REQUEST_FLAG_TAGGED,# DATA_REQUEST_FLAG_CHANGED | DATA_REQUEST_FLAG_TAGGED,
            0,  # number of periods before starting events
            1,  # number of periods between events, e.g. with PERIOD_SIM_FRAME
            0,  # number of repeats, 0 is forever
        )

    # blocks and reads telemetry
    def _resubscribe(self):
        """
        Request a resubscription on the next telemetry read cycle.
        
        Sets a flag that will cause _read_telem() to call _subscribe()
        on the next iteration, allowing dynamic variable list updates.
        """
        self.resubscribe = True

    def set_simdatum_to_msfs(self, simvar, value, units=None):
        """
        Queue a simulation datum to be sent to MSFS.
        
        Args:
            simvar (str): The SimConnect variable name
            value: The value to set
            units (str, optional): Unit specification for the value
        """
        self._simdatums_to_send.append((simvar, value, units))

    def send_event_to_msfs(self, event, data: int = 0):
        """
        Queue an event to be sent to MSFS.
        
        Args:
            event (str): The event name or L:var name
            data (int): The event data/value (default: 0)
        """
        if event == "DO_NOT_SEND": return
        self._events_to_send.append((event, data))

    def tx_simdatums_to_msfs(self):
        """
        Transmit all queued simulation datums to MSFS.
        
        Processes the queue of pending datum updates and sends them
        to MSFS via SimConnect. Handles and logs any transmission errors.
        """
        while self._simdatums_to_send:
            simvar, value, units = self._simdatums_to_send.pop(0)
            try:
                self.sc.set_simdatum(simvar, value, units=units)
            except Exception as e:
                logging.error(f"Error sending {simvar} value {value} to MSFS: {e}")
                # self.telem_data['error'] = 1


    def tx_events_to_msfs(self):
        """
        Transmit all queued events to MSFS.
        
        Processes the queue of pending events and sends them to MSFS.
        Handles both standard SimConnect events and L:var (local variable)
        updates. L:vars are sent as simulation datums, while standard events
        use the SimConnect event system.
        """
        while self._events_to_send:
            event, data = self._events_to_send.pop(0)
            logging.debug(f"event {event}   data {data}")
            if event.startswith('L:'):
                self.set_simdatum_to_msfs(event, data, units="number")
            else:
                try:
                    self.sc.send_event(event, data)
                    # self.telem_data[event] = data
                except Exception as e:
                    logging.error(f"Error setting event:{event} value:{data} to MSFS: {e}")
                    # self.telem_data['error'] = 1

    def _read_telem(self) -> bool:
        """
        Main telemetry reading loop for processing SimConnect messages.
        
        This method continuously polls SimConnect for new messages and processes them:
        - Telemetry data packets are parsed and emitted via emit_packet()
        - System events (pause, start, stop) are handled and emitted via emit_event()
        - Connection state changes are detected and handled
        - Queued events and datums are transmitted to MSFS
        - Resubscription requests are processed
        
        The method handles various SimConnect message types including:
        - RECV_SIMOBJECT_DATA: Aircraft telemetry data
        - RECV_EVENT: System state changes (pause/unpause, sim start/stop)
        - RECV_OPEN: Connection established
        - RECV_QUIT: Connection closed
        - RECV_EXCEPTION: SimConnect errors
        
        Returns:
            bool: Always returns None in current implementation
        """
        pRecv = RECV_P()
        nSize = DWORD()

        while not self._quit:
            self.tx_events_to_msfs()  # tx any pending sim events that are queued
            self.tx_simdatums_to_msfs()  # tx any pending simdatum sets that are queued

            try:
                #print('Trying')
                self.sc.GetNextDispatch(byref(pRecv), byref(nSize))
            except OSError as e:
                #print(e)
                time.sleep(0.001)
                continue

            if self.resubscribe:
                self._subscribe()
                self.resubscribe = False
                continue

            recv = ReceiverInstance.cast_recv(pRecv)
            #print(f"got {recv.__class__.__name__}")
            if isinstance(recv, RECV_EXCEPTION):
                logging.warning(f"SimConnect exception [magenta]{SimConnectException(recv.dwException).name}[/magenta], sendID {recv.dwSendID}, index {recv.dwIndex}")
            elif isinstance(recv, RECV_QUIT):
                logging.info("Quit received")
                self.emit_event("Quit")
                break
            elif isinstance(recv, RECV_OPEN):
                msfs_vers = recv.szApplicationName.decode('utf-8')
                if msfs_vers == 'SunRise':
                    self.connected_version = "MSFS2024"
                elif msfs_vers == "KittyHawk":
                    self.connected_version = "MSFS2020"
                else:
                    self.connected_version = msfs_vers
                self.emit_event("Open")

            elif isinstance(recv, RECV_EVENT):
                if recv.uEventID == EV_PAUSED:
                    logging.debug(f"EVENT PAUSED,  EVENT: {recv.uEventID}, DATA: {recv.dwData}")
                    self._sim_paused = recv.dwData
                    self.emit_event("Paused", recv.dwData)
                elif recv.uEventID == EV_STARTED:
                    logging.debug(f"EVENT STARTED,  EVENT: {recv.uEventID}, DATA: {recv.dwData}")
                    self._sim_started = 1
                    self.emit_event("SimStart")
                    self._stop_state = False # clear stop state, this will cause a reload of current aircraft in telemFFB
                elif recv.uEventID == EV_STOPPED:
                    logging.debug(f"EVENT STOPPED, EVENT: {recv.uEventID}, DATA: {recv.dwData}")
                    self._sim_started = 0
                    self.emit_event("SimStop")
                elif recv.uEventID == EV_SIMSTATE:
                    logging.debug(f"EVENT SIMSTATE, EVENT: {recv.uEventID}, DATA: {recv.dwData}")
                    self._sim_state = recv.dwData
                    self.emit_event("SimState", recv.dwData)

            elif isinstance(recv, RECV_SIMOBJECT_DATA):
                logging.debug(f"Received SIMOBJECT_DATA with {recv.dwDefineCount} data elements, flags {recv.dwFlags}")
                #print(f"Received SIMOBJECT_DATA with {recv.dwDefineCount} data elements, flags {recv.dwFlags}")
                if recv.dwRequestID == self.req_id and recv.dwDefineID == self.def_id:
                    #print(f"Matched request 0x{req_id:X}")
                    data = {}
                    data["SimPaused"] = self._sim_paused
                    # data["FlightStarted"] = self._sim_state
                    offset = RECV_SIMOBJECT_DATA.dwData.offset
                    for _ in range(recv.dwDefineCount):
                        idx = cast(byref(recv, offset), POINTER(DWORD))[0]
                        offset += sizeof(DWORD)
                        # DATATYPE_FLOAT64 => c_double
                        try:
                            var : SimVar = self.subscribed_vars[idx]
                            c_type = var.c_type
                            if var.datatype == DATATYPE_STRING128: #fixme: other string types
                                val = str(cast(byref(recv, offset), POINTER(c_type))[0].value, "utf-8")
                            else:
                                val = cast(byref(recv, offset), POINTER(c_type))[0]
                            offset += sizeof(c_type)
                            val = var._calculate(val)

                            if var.parent: # var is part of array
                                var.parent.values[var.index-var.parent.min] = val
                                data[var.parent.name] = var.parent.values
                            else:
                                data[var.name] = val
                        except:
                            # dbprint("red", "**DEBUG*** Exception parsing SC FRAME")
                            continue

                    avatar = data.get("_IS AVATAR", False) # in 2024, see if user is controlling avatar
                    rtc = data.get("_IS IN RTC", False) # check if 2024 sim is running realtime cinematic (cut scene)

                    in_menus = data.get('CameraState', 0) not in (2,3,4,5)  # Check the camera state value - workaround for FS2024 telemetry at wrong times https://forums.flightsimulator.com/t/at-the-finish-of-beta-loading-if-start-is-not-click-open-upon-reaching-yosemite-during-2nd-run-of-opening-graphics-telemetry-is-sent-to-motion-platform-causiing-violent-shaking-and-movement/702082/2?u=number4815901

                    if self._sim_paused or data.get("Parked", 0) or data.get("Slew", 0) or avatar or rtc or in_menus:
                        data["STOP"] = 1
                        data['_num_simvars'] = len(data)
                        data['msfs_vers'] = self.connected_version
                        if not self._stop_state:
                            self.emit_event("STOP")
                            self.emit_packet(data) # emit last packet
                            self._stop_state = True
                    else:
                    # print(f"!#$!#$!#$!#$ EMITTING PACKET LEN: {len(data)}")
                        self._stop_state = False
                        self.emit_packet(data)
                else:
                    # dbprint("green", f"**DEBUG*** got dispatch for OLD request: {recv.dwRequestID} defID: {recv.dwDefineID} | currrent defID: {self.def_id}")
                    pass
            else:
                logging.warning(f"Received unknown simconnect message: {recv}")

    def emit_packet(self, data):
        """
        Emit a telemetry data packet.
        
        This method is intended to be overridden by subclasses to handle
        incoming telemetry data packets from MSFS.
        
        Args:
            data (dict): Dictionary containing telemetry variables and their values
        """
        pass

    def emit_event(self, event, *args):
        """
        Emit a system event notification.
        
        This method is intended to be overridden by subclasses to handle
        system events such as sim start/stop, pause/unpause, etc.
        
        Args:
            event (str): The event name
            *args: Additional event arguments
        """
        pass

    def quit(self):
        """
        Request the manager thread to quit.
        
        Sets the quit flag that will cause the main run() loop to exit
        gracefully on the next iteration.
        """
        self._quit = True

    def run(self):
        """
        Main thread execution method.
        
        This method implements the main connection and data processing loop:
        1. Attempts to establish SimConnect connection
        2. Subscribes to system events (pause, start, stop, sim state)
        3. Subscribes to telemetry variables
        4. Enters the telemetry reading loop
        5. Handles connection errors with automatic retry (10-second delay)
        
        The method will continue running until quit() is called or the thread
        is terminated. It automatically handles connection loss and reconnection.
        """
        while not self._quit:
            try:
                if self._connect_attempts % 6 == 0:
                    # only log every 6th attempt (60 seconds)
                    logging.info(f"Trying SimConnect...")


                with SimConnect(f"TelemFFB-{G.device_type}") as self.sc:
                    self.sc.SubscribeToSystemEvent(EV_PAUSED, "Pause")
                    self.sc.SubscribeToSystemEvent(EV_STARTED, "SimStart")
                    self.sc.SubscribeToSystemEvent(EV_STOPPED, "SimStop")
                    self.sc.SubscribeToSystemEvent(EV_SIMSTATE, "Sim")

                    self._subscribe()
                    self._read_telem()

            except OSError:
                if self._connect_attempts % 6 == 0:
                    # only log every 6th attempt (60 seconds)
                    logging.info(f"Failed to connect to SimConnect - is MSFS running?")
                self._connect_attempts += 1
                time.sleep(10)
                pass

    def get_var_name(self,k):
        """
        Get the SimConnect variable name for a given variable key.
        
        This method looks up the SimConnect variable reference (e.g., "AIRSPEED TRUE")
        for a given variable name key (e.g., "TAS") from the current subscription.
        
        Args:
            k (str): The variable name key to look up
            
        Returns:
            str or None: The SimConnect variable name, or None if not found
        """
        return self.sv_dict.get(k, None)
        # for sv in (self.sim_vars):
        #     if isinstance(sv, SimVarArray):
        #         for sv in sv.vars:
        #             if sv.name == k:
        #                 return sv.var
        #     else:
        #         if sv.name == k:
        #             return sv.var

# run test
if __name__ == "__main__":
    class SimConnectTest(SimConnectManager):
        def emit_packet(self, data):
            print(data)

    s = SimConnectTest()
    s.start()
    start_time = time.time()
    tst_executed = False
    while True:
        time.sleep(1)
        if not tst_executed and time.time() > 5:
            s.add_simvar("APMaster", "L:ApMode", "Enum")
            s.add_simvar("PropThrust", "L:Eng1_RPM", "number")
            s._subscribe()
            tst_executed = True