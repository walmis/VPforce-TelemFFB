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
This File implements USB HID direct access to FFB system on the VPforce Rhino using hidapi
This bypasses directinput and other layers which allows to augment directInput 
with additional FFB effects.
"""

import ctypes
import functools
import inspect
import logging
import os
import time
import weakref
from dataclasses import dataclass
from typing import List, Optional, Self, override

import usb1
from PyQt6.QtCore import QObject, QTimer, QTimerEvent, pyqtSignal

from telemffb.utils import Destroyable, DirectionModulator, clamp, millis

paths = ["hidapi.dll", "dll/hidapi.dll", os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dll', 'hidapi.dll')]
for p in paths:
    try:
       ctypes.cdll.LoadLibrary(p)
       break
    except:
        pass 

import telemffb.hw.hid as hid

USB_REQTYPE_DEVICE_TO_HOST = 0x80
USB_REQTYPE_VENDOR = 0x40

USB_CTRL_REQ_GET_VERSION = 16

# RHINO specific report IDs
HID_REPORT_ID_INPUT = 1
HID_REPORT_ID_VENDOR_CMD = 0x55
HID_REPORT_ID_BUTTON_LOOPBACK = 0x40
HID_REPORT_ID_SET_EFFECT = 101
HID_REPORT_ID_SET_ENVELOPE = 102
HID_REPORT_ID_SET_CONDITION = 103
HID_REPORT_ID_SET_PERIODIC = 104
HID_REPORT_ID_SET_CONSTANT_FORCE = 105
HID_REPORT_ID_SET_RAMP_FORCE = 106
HID_REPORT_ID_SET_CUSTOM_FORCE = 107
HID_REPORT_ID_SET_DOWNLOAD_SAMPLE = 108
HID_REPORT_ID___RESERVED = 109
HID_REPORT_ID_EFFECT_OPERATION = 110
HID_REPORT_ID_BLOCK_FREE = 111
HID_REPORT_ID_DEVICE_CONTROL = 112
HID_REPORT_ID_DEVICE_GAIN = 113
HID_REPORT_ID_SET_CUSTOM_FORCE_OUTPUT_DATA = 114
HID_REPORT_ID_SET_DEADZONE = 115

HID_REPORT_ID_PID_STATE_REPORT = 2
HID_REPORT_ID_CREATE_EFFECT = 5
HID_REPORT_ID_PID_BLOCK_LOAD = 6
HID_REPORT_ID_PID_POOL_REPORT = 7

HID_REPORT_FEATURE_ID_GET_GAINS = 0x56
HID_REPORT_FEATURE_ID_SET_GAIN = 0x57

EFFECT_CONSTANT = 1
EFFECT_RAMP = 2
EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7
EFFECT_SPRING = 8
EFFECT_DAMPER = 9
EFFECT_INERTIA = 10
EFFECT_FRICTION = 11
EFFECT_CUSTOM = 12
EFFECT_DETENT = 13
EFFECT_SPRING_ADJUSTER = 14

PERIODIC_EFFECTS = [EFFECT_SQUARE,EFFECT_SINE,EFFECT_TRIANGLE,EFFECT_SAWTOOTHUP,EFFECT_SAWTOOTHDOWN]

CONTROL_DISABLE_ACTUATORS = 1
CONTROL_ENABLE_ACTUATORS = 2
CONTROL_STOP_ALL_EFFECTS = 3
CONTROL_RESET = 4 
CONTROL_PAUSE = 5
CONTROL_CONTINUE = 6

LOAD_SUCCESS = 1
LOAD_FULL = 2
LOAD_ERROR = 3

OP_START = 1
OP_START_SOLO  = 2
OP_STOP = 3
OP_START_OVERRIDE = 4

AXIS_ENABLE_X = 1
AXIS_ENABLE_Y = 2
AXIS_ENABLE_DIR = 4

# Create a pretty printable dictionary without the "Effect" prefix
effect_names = {
    0: "Invalid",
    1: "Constant",
    2: "Ramp",
    3: "Square",
    4: "Sine",
    5: "Triangle",
    6: "Sawtooth Up",
    7: "Sawtooth Down",
    8: "Spring",
    9: "Damper",
    10: "Inertia",
    11: "Friction",
    12: "Custom",
    13: "Detent",
    14: "SpringAdjuster",
}


class BaseStructure(ctypes.LittleEndianStructure):
    def __init__(self, **kwargs):
        """
        Ctypes.Structure with integrated default values.

        :param kwargs: values different to defaults
        :type kwargs: dict
        """

        values = type(self)._defaults_.copy()
        values.update(kwargs)

        super().__init__(**values)
    
    def __repr__(self):
        out = []
        for f in self._fields_:
            if f[0]: out.append(f"{f[0]}={getattr(self, f[0])}")
        return f"{self.__class__.__name__}(0x{id(self):X}): " + ",".join(out)


class FFBReport_SetEffect(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8),
                ("effectType", ctypes.c_uint8),
                ("duration", ctypes.c_uint16),
                ("triggerRepeatInterval", ctypes.c_uint16),
                ("samplePeriod", ctypes.c_uint16),
                ("gain", ctypes.c_uint16),
                ("triggerButton", ctypes.c_uint8),
                ("axesEnable", ctypes.c_uint8, 3), # bit 0: x_axis_enble, bit 1: y_axis_enable, bit 2: direction_enable
                ("effectSource", ctypes.c_uint8, 1), # bit 0: effect_source : 0 - game, 1 - telemFFB
                ("directionX", ctypes.c_uint8),
                ("directionY", ctypes.c_uint8),
                ("startDelay", ctypes.c_uint16)
               ]
    _defaults_ = {"reportId": HID_REPORT_ID_SET_EFFECT, "gain":4096, "effectSource": 1}


class FFBReport_SetDeadzone(BaseStructure):
    _pack_ = 1
    _fields_ = [
            ("reportId", ctypes.c_uint8),
            ("deadzone", ctypes.c_uint16),
        ]
    _defaults_ = {"reportId": HID_REPORT_ID_SET_DEADZONE, "deadzone": 0}


class FFBReport_EffectOperation(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8),
                ("operation", ctypes.c_uint8),
                ("loopCount", ctypes.c_uint8)
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_EFFECT_OPERATION }


class FFBReport_SetPeriodic(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8),
                ("magnitude", ctypes.c_uint16),
                ("offset", ctypes.c_int16),
                ("phase", ctypes.c_uint8),
                ("period", ctypes.c_uint16)
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_SET_PERIODIC }

class FFBReport_SetConstantForce(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8),
                ("magnitude", ctypes.c_int16), # -4096..4096
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_SET_CONSTANT_FORCE }

class FFBReport_SetCondition(BaseStructure):
    """
    Represents the structure of the SET_CONDITION HID report.

    Attributes:
        reportId (int): The report ID.
        effectBlockIndex (int): The effect block index.
        parameterBlockOffset (int): The parameter block offset. This identifies the axis
        cpOffset (int): The centerPoint offset, in the range from -4096 to 4096.
        positiveCoefficient (int): The positive coefficient, in the range from -4096 to 4096.
        negativeCoefficient (int): The negative coefficient, in the range from -4096 to 4096.
        positiveSaturation (int): The positive saturation, in the range from 0 to 4096.
        negativeSaturation (int): The negative saturation, in the range from 0 to 4096.
        deadBand (int): The dead band, in the range from 0 to 4096.
    """
    reportId: int
    effectBlockIndex: int
    parameterBlockOffset: int
    cpOffset: int
    positiveCoefficient: int
    negativeCoefficient: int
    positiveSaturation: int
    negativeSaturation: int
    deadBand: int

    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8), 
                ("parameterBlockOffset", ctypes.c_uint8), 
                ("cpOffset", ctypes.c_int16), 
                ("positiveCoefficient", ctypes.c_int16), 
                ("negativeCoefficient", ctypes.c_int16), 
                ("positiveSaturation", ctypes.c_uint16), 
                ("negativeSaturation", ctypes.c_uint16), 
                ("deadBand", ctypes.c_uint16)
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_SET_CONDITION }

    def set_offset(self, offset: (int|float)) -> Self:
        """
        Set the center position offset for the FFB device.
        
        This method sets the center position offset which adjusts the neutral position
        of the force feedback device. The offset can be provided as either an integer
        or float value.
        
        Args:
            offset (int | float): The offset value to set. If provided as a float,
                it will be scaled by 4096 and rounded to the nearest integer.
                The final value is clamped to the range [-4096, 4096].
        
        Returns:
            None
        
        Note:
            - Float values are automatically converted to integer by multiplying by 4096
            - The offset is automatically clamped to prevent values outside the valid range
            - The processed offset is stored in the cpOffset attribute
        """
        if isinstance(offset, float):
            val = round(offset * 4096)
        else:
            val = int(offset)
        val = clamp(val, -4096, 4096)
        self.cpOffset = val
        return self

    def set_saturation(self, saturation: (int|float), do_clamp: bool = True) -> Self:
        """
        Sets the positive and negative saturation based on the input saturation value.

        Args:
            saturation (int|float): The saturation value to set. If it's a float, it's converted to an integer by multiplying it by 4096.

        Returns:
            None
        """
        if isinstance(saturation, float):
            val = round(saturation * 4096)
        else:
            val = int(saturation)
        if do_clamp:
            val = clamp(val, 0, 4096)
        self.positiveSaturation = val
        self.negativeSaturation = val
        return self

    def set_coefficient(self, coefficient: (int|float), do_clamp: bool = True) -> Self:
        """
        Sets the positive and negative coefficients based on the input coefficient value.

        Args:
            coefficient (int|float): The coefficient value to set. If it's a float, it's converted to an integer by multiplying it by 4096.

        Returns:
            None
        """
        if isinstance(coefficient, float):
            val = round(coefficient * 4096)
        else:
            val = int(coefficient)

        if do_clamp:
            val = clamp(val, 0, 4096)
        self.positiveCoefficient = val
        self.negativeCoefficient = val
        return self

    def set_coefficients(self, positive, negative) -> Self:
        """
        Sets the positive and negative coefficients of the object.

        Args:
            positive (int): The positive coefficient value.
            negative (int): The negative coefficient value.

        Returns:
            None
        """
        self.positiveCoefficient = positive
        self.negativeCoefficient = negative
        return self

class FFBReport_SetEnvelope(BaseStructure):
    """
    Represents the structure of the SET_ENVELOPE HID report.

    Attributes:
        reportId (int): The report ID.
        effectBlockIndex (int): The effect block index.
        attackFromForce (int): The attack level (force to start from), relative to the baseline, in the range from 0 through 4096.
        decayToForce (int): The decay level (force to decay to), relative to the baseline, in the range from 0 through 4096.
        attackTime (int): The time, in milliseconds, to reach the sustain level.
        decayTime (int): The time, in milliseconds, to reach the decay level.
    """

    reportId: int               # uint8_t reportId
    effectBlockIndex: int       # uint8_t effectBlockIndex
    attackFromForce: int        # uint16_t attackLevel (firmware: attackFromForce)
    decayToForce: int           # uint16_t fadeLevel (firmware: decayToForce)
    attackTime: int             # uint16_t attackTime
    decayTime: int              # uint16_t fadeTime (firmware: decayTime)

    _pack_ = 1
    _fields_ = [
        ("reportId", ctypes.c_uint8),        # uint8_t reportId
        ("effectBlockIndex", ctypes.c_uint8),# uint8_t effectBlockIndex
        ("attackFromForce", ctypes.c_uint16),# uint16_t attackLevel
        ("decayToForce", ctypes.c_uint16),   # uint16_t fadeLevel
        ("attackTime", ctypes.c_uint16),     # uint16_t attackTime
        ("decayTime", ctypes.c_uint16)       # uint16_t fadeTime
    ]
    _defaults_ = { "reportId": HID_REPORT_ID_SET_ENVELOPE }

class FFBReport_BlockFree(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8), 
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_BLOCK_FREE } 

class FFBReport_Input(BaseStructure):
    X: int
    Y: int
    Z: int
    Rz: int
    Ry: int
    Rx: int
    Slider: int
    Button0_31: int
    Button32_47: int
    hats: int
    Button48_63: int
    CP_offsetX: int
    CP_offsetY: int
    ForceX: int # force output (sping + friction), for improved hands-on detection
    ForceY: int

    _pack_ = 1
    _fields_ = [
                ("reportId", ctypes.c_uint8), # = 1
                ("X", ctypes.c_int16),
                ("Y", ctypes.c_int16),
                ("Z", ctypes.c_int16),
                ("Rz", ctypes.c_uint8),
                ("Ry", ctypes.c_uint8),
                ("Rx", ctypes.c_uint8),
                ("Slider", ctypes.c_uint8),
                ("Button0_31", ctypes.c_uint32),
                ("Button32_47", ctypes.c_uint16),
                ("hats", ctypes.c_uint16),
                ("Button48_63", ctypes.c_uint16), # added in fw v1.0.17
                ("CP_offsetX", ctypes.c_int16), # added in fw v1.0.17
                ("CP_offsetY", ctypes.c_int16), # added in fw v1.0.17
                ("ForceX", ctypes.c_int16), # added in fw v1.0.17b13
                ("ForceY", ctypes.c_int16), # added in fw v1.0.17b13
               ]
    _defaults_ = {}

    # get if button is pressed, buttons start from 1
    def isButtonPressed(self, button_number) -> bool:
        """
        Checks if a button is pressed.

        Args:
            button_number (int): The button number to check. Buttons start from 1.

        Returns:
            bool: True if the button is pressed, False otherwise.

        Note: The function checks both regular buttons and hat switches. For hat
        switches, the button number is calculated as 0x80 | (i << 4) | hat_position,
        where i is the hat switch index (0-3) and hat_position is the position of
        the switch (0-8).
        """

        assert (button_number > 0)

        # Check regular buttons
        btns = self.buttons
        if (btns & (1 << (button_number - 1))) != 0:
            return True

        # Check hat switches
        for i in range(4):
            hat_position = (self.hats >> (i * 4)) & 0xF
            if hat_position != 0xF:
                b = 0x80 | (i << 4) | hat_position
                if button_number == b:
                    return True

        return False

    def getPressedButtons(self) -> List[int]:
        """
        Returns a list of pressed button numbers.

        The function returns a list of integers in the range of 1 to 80, where
        1-64 are regular buttons and 0x80-0xCF are hat switch positions. The
        hat switch positions are calculated as 0x80 | (i << 4) | hat_position,
        where i is the hat switch index (0-3) and hat_position is the position
        of the switch (0-8).

        :return: A list of pressed button numbers.
        :rtype: list
        """
        btns = self.buttons

        # Collect regular buttons
        pressed = [i + 1 for i in range(64) if (btns & (1 << i)) != 0]

        # Collect hat switch positions as button numbers
        for i in range(4):
            hat_position = (self.hats >> (i * 4)) & 0xF
            if hat_position != 0xF:
                b = 0x80 | (i << 4) | hat_position
                pressed.append(b)

        return pressed

    @property
    def buttons(self) -> int:
        """
        Returns the status of all buttons as a single 64-bit integer.

        The least significant bit corresponds to button 1, the next bit to
        button 2, and so on. A set bit indicates a pressed button.

        :return: The status of all buttons as a 64-bit integer.
        :rtype: int
        """
        return  self.Button0_31 | (self.Button32_47 << 32) | (self.Button48_63 << 48)

    # get main X and Y axis in range [-1.0 .. 1.0]
    def axisXY(self) -> tuple[float, float]:
        """
        Returns the main X and Y axis values as a tuple of two floats in the range [-1.0 .. 1.0]
        """
        return (self.X/4096.0, self.Y/4096.0)
    
    def CP_XY(self) -> tuple[float, float]:
        """
        Returns the center point offset as a tuple of two floats in the range [-1.0 .. 1.0]
        If spring coefficient = 0, returns None
        """
        cpX = self.CP_offsetX/4096.0 if self.CP_offsetX <= 4096.0 else None
        cpY = self.CP_offsetY/4096.0 if self.CP_offsetY <= 4096.0 else None
        
        return (cpX, cpY)
    
    def forceXY(self) -> tuple[float, float]:
        """
        Returns the force X and Y values as a tuple of two floats in the range [-1.0 .. 1.0]
        """
        return (self.ForceX / 4096.0, self.ForceY / 4096.0)

    def CP_scaled_axisXY(self) -> tuple[float, float]:
        """
        Returns scaled axis x/y values in the range [-1.0 .. 1.0]  using the current CP_offset as a center reference
        Output is scaled 0 to +/-1 in any direction from spring center.
        """
        X, Y = self.axisXY()
        cpX, cpY = self.CP_XY()
        # Scale X based on cpX as the zero reference
        if cpX is None:
            # Spring coefficient = 0, return 0
            scaled_x = 0
        elif X >= cpX:
            scaled_x = (X - cpX) / (1 - cpX) if cpX < 1 else (X - cpX)  # Map [cpX, 1] to [0, 1], avoid div/0
        else:
            scaled_x = (X - cpX) / (cpX + 1) if cpX > -1 else (X - cpX)  # Map [-1, cpX] to [-1, 0], avoid div/0

        # Scale Y based on cpY as the zero reference
        if cpY is None:
            # Spring coefficient = 0, return 0
            scaled_y = 0
        elif Y >= cpY:
            scaled_y = (Y - cpY) / (1 - cpY) if cpY < 1 else (Y - cpY)  # Map [cpY, 1] to [0, 1], avoid div/0
        else:
            scaled_y = (Y - cpY) / (cpY + 1) if cpY > -1 else (Y - cpY)  # Map [-1, cpY] to [-1, 0], avoid div/0

        return (scaled_x, scaled_y)


class FFBReport_PIDStatus_Input(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8), # = 2
                ("devicePaused",     ctypes.c_uint8, 1),
                ("actuatorsEnabled", ctypes.c_uint8, 1),
                ("safetySwitch",     ctypes.c_uint8, 1),
                ("actuatorOverride", ctypes.c_uint8, 1),
                ("actuatorPower",    ctypes.c_uint8, 1),
                ("deviceResetEvent", ctypes.c_uint8, 1),
                ("", ctypes.c_uint8, 2),
                ("effectPlaying", ctypes.c_uint8, 1),
                ("effectBlockIndex", ctypes.c_uint8, 7),
                ]
    _defaults_ = {}

class FFBReport_Get_Gains_Feature_Data(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8), 
                ("master_gain",     ctypes.c_uint8),
                ("periodic_gain",   ctypes.c_uint8),
                ("spring_gain",     ctypes.c_uint8),
                ("damper_gain",     ctypes.c_uint8),
                ("inertia_gain",    ctypes.c_uint8),      
                ("friction_gain",   ctypes.c_uint8),
                ("constant_gain",   ctypes.c_uint8),
                ]
    master_gain : int
    periodic_gain : int
    spring_gain : int
    damper_gain : int
    inertia_gain : int
    friction_gain : int
    constant_gain : int
    _defaults_ = {}

FFB_GAIN_MASTER = 1
FFB_GAIN_PERIODIC = 2
FFB_GAIN_SPRING = 3
FFB_GAIN_DAMPER = 4
FFB_GAIN_INERTIA = 5
FFB_GAIN_FRICTION = 6
FFB_GAIN_CONSTANT = 7
class FFBReport_Set_Gain_Feature_Data_t(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8), 
                ("gain_id",     ctypes.c_uint8), # gain slider ID
                ("gain_value",   ctypes.c_uint8), # in percent 0-100
                ]
    reportId: int
    gain_id: int
    gain_value: int
    _defaults_ = {}

input_report_handlers = {
    HID_REPORT_ID_INPUT: FFBReport_Input,
    HID_REPORT_ID_PID_STATE_REPORT: FFBReport_PIDStatus_Input
}

class FFBEffectHandle:
    def __init__(self, device, effect_id, effect_type) -> None:
        self.ffb : FFBRhino = device
        self.effect_id = effect_id
        self.type = effect_type
        self._finalizer = weakref.finalize(self, lambda ref: ref() and ref().destroy(), weakref.ref(self))
        self._cache = {}
        self._started = False

    def invalidate(self):
        self.effect_id = 0

    def _data_changed(self, key, data) -> bool:
        h = hash(data)
        if not self._cache.get(key):
            self._cache[key] = h
            return True
        changed = self._cache[key] != h
        self._cache[key] = h
        return changed  

    def __del__(self):
        self.destroy()

    def __bool__(self) -> bool:
        # effect is valid if effect id is not None/0 and type is non-zero
        return bool(self.effect_id and self.type)

    @property
    def started(self):
        return self._started

    @property
    def name(self):
        return effect_names.get(self.type)
    
    def __repr__(self):
        return f"FFBEffectHandle({self.effect_id}, {self.name})"
    
    def start(self, loopCount=1, override=False):
        op = OP_START
        if override:
            op = OP_START_OVERRIDE
        op = FFBReport_EffectOperation(effectBlockIndex=self.effect_id, operation=op, loopCount=loopCount)
        self.ffb.write(bytes(op))
        self._started = True
        return self

    def stop(self):
        op = FFBReport_EffectOperation(effectBlockIndex=self.effect_id, operation=OP_STOP)
        self.ffb.write(bytes(op))
        self._started = False
        return self

    def destroy(self):
        if self.effect_id:
            logging.debug(f"Destroying effect {self.effect_id} ({effect_names[self.type]})")
            op = FFBReport_BlockFree(effectBlockIndex=self.effect_id)
            self.ffb.write(bytes(op))
            self.type = 0
            self.effect_id = None
            self._started = False

    def setConstantForce(self, magnitude, direction, **kwargs):
        """Set constant for for effect

        :param magnitude: Magnitude [-1..1]
        :type magnitude: float
        :param direction: Direction in degrees [0..360]
        :type direction: float
        """

        if self.effect_id is None:
            logging.warn("setConstantForce on an invalidated effect")
            return

        assert(self.type == EFFECT_CONSTANT)
        assert(magnitude >= -1.0 and magnitude <= 1.0)

        direction %= 360
        direction = round((direction*255/360))

        self.setEffect(axesEnable=AXIS_ENABLE_DIR, directionX=direction)

        op = bytes(FFBReport_SetConstantForce(magnitude=round(4096*magnitude), effectBlockIndex=self.effect_id))
        if self._data_changed("SetConstantForce", op): 
            self.ffb.write(op)

        return self

    def setEffect(self, **kwargs):
        args = {
            "effectBlockIndex": self.effect_id,
            "effectType": self.type,
            "axesEnable": AXIS_ENABLE_X | AXIS_ENABLE_Y,
            "gain": 4096
        }
        args.update(kwargs)

        op = bytes(FFBReport_SetEffect(**args))
        if self._data_changed("setEffect", op):  
            self.ffb.write(op)
    
    def setCondition(self, cond : FFBReport_SetCondition):
        cond.effectBlockIndex = self.effect_id
        # spring adjuster can go full 16bits
        # maximum gain increase is 32767/4096 = 7.5
        if self.type == EFFECT_SPRING_ADJUSTER:
            cond.positiveCoefficient = clamp(cond.positiveCoefficient, -32768, 32767)
            cond.negativeCoefficient = clamp(cond.negativeCoefficient, -32768, 32767)
        else:
            cond.positiveCoefficient = clamp(cond.positiveCoefficient, -4096, 4096)
            cond.negativeCoefficient = clamp(cond.negativeCoefficient, -4096, 4096)
        data = bytes(cond)
        if self._data_changed(f"setCondition{cond.parameterBlockOffset}", data):
            self.ffb.write(data)

    def setEnvelope(self, envelope: FFBReport_SetEnvelope):
        """Set envelope parameters for the effect.
        
        Args:
            envelope: An instance of FFBReport_SetEnvelope with envelope parameters.
        """
        envelope.effectBlockIndex = self.effect_id
        data = bytes(envelope)
        if self._data_changed("setEnvelope", data):
            self.ffb.write(data)

    def setPeriodic(self, freq, magnitude, direction, duration=0, **kwargs):
        assert(self.type in PERIODIC_EFFECTS)
        assert(magnitude >= 0 and magnitude <= 1.0)
        direction %= 360
        direction = round(direction*255/360)

        self.setEffect(axesEnable=AXIS_ENABLE_DIR, directionX=direction, duration=duration, **kwargs)

        if freq == 0:
            period = 0
        else:
            period = round(1000.0/freq)
        mag = round(4096*magnitude)

        op = bytes(FFBReport_SetPeriodic(magnitude=mag, effectBlockIndex=self.effect_id, period=period, **kwargs))

        if self._data_changed("SetPeriodic", op):
            self.ffb.write(op)

        return self

@dataclass
class DeviceInfo:
    interface_number: int
    manufacturer_string: str
    path: bytes
    product_id: int
    product_string: str
    release_number: int
    serial_number: str
    usage: int
    usage_page: int
    vendor_id: int

    def vidpid(self) -> str:
        """Returns the device VID:PID string"""
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    @property
    def ident(self) -> str:
        """Returns the device name set in the VPforce Configurator"""
        return self.product_string.replace("Rhino FFB ", "").strip()

class FFBRhino(QObject):
    buttonPressed = pyqtSignal(int)
    buttonReleased = pyqtSignal(int)
    deviceConnected = pyqtSignal(bool)

    def __init__(self, vid = 0xFFFF, pid=0x2055, serial=None, path : Optional[str] = None) -> None:

        self.vid = vid
        self.pid = pid
        self.info : DeviceInfo 
        self.firmware_version : Optional[str] = None
        self._button_state : int = 0
        self._prev_hats = 0xFFFF

        if not path:
            devs = FFBRhino.enumerate(pid)
        else:
            devs = FFBRhino.enumerate()
        if serial:
            devs = list(filter(lambda x: x.serial_number == serial, devs))
        if path:
            devs = list(filter(lambda x: x.path == bytes(path, 'utf-8'), devs))
        if not devs:
            raise hid.HIDException('unable to open device')
        self.info = devs[0]

        self._in_reports = {}
        self._effect_handles : List[FFBEffectHandle] = []
        self._dev = None

        QObject.__init__(self)
        self.startTimer(1) # start Qt timer to read HID reports every 1ms

        self.reconnect()

    def reconnect(self):
        if self._dev:
            self._dev.close()
            self._dev = None

        self._dev = hid.Device(path=self.info.path)
        self._dev.nonblocking = True

    @property
    def serial(self):
        if not self._dev:
            return None
        return self._dev.serial
    @property
    def product(self):
        if not self._dev:
            return None
        return self._dev.product
    @property
    def manufacturer(self):
        if not self._dev:
            return None
        return self._dev.manufacturer
    
    @staticmethod
    def enumerate(pid=0) -> List[DeviceInfo]:
        devs = hid.enumerate(vid=0xffff, pid=pid)
        devs = [DeviceInfo(**dev) for dev in devs]
        # returns a list of valid VPforce devices
        #[{'interface_number': 0,
        # 'manufacturer_string': 'VPforce',
        # 'path': b'\\\\?\\HID#VID_FFFF&PID_2055&MI_00#9&3450694a&0&0000#{4d1e55b2-f16f'
        #         b'-11cf-88cb-001111000030}',
        # 'product_id': 8277,
        # 'product_string': 'Rhino FFB Joystick',
        # 'release_number': 516,
        # 'serial_number': '3359534E0400004C61001600',
        # 'usage': 4,
        # 'usage_page': 1,
        # 'vendor_id': 65535}]
        return list(filter(lambda x: x.interface_number == 0 and x.usage == 4, devs))

    # Get global effect slider values as seen in VPConfigurator
    def get_gains(self) -> FFBReport_Get_Gains_Feature_Data:
        assert(self._dev)
        d = self._dev.get_feature_report(HID_REPORT_FEATURE_ID_GET_GAINS, ctypes.sizeof(FFBReport_Get_Gains_Feature_Data))
        data = FFBReport_Get_Gains_Feature_Data.from_buffer_copy(d)
        return data
    
    # Set global effect class gain, same as in VPConfigurator sliders
    def set_gain(self, slider_id, value):
        assert(self._dev and value >= 0 and value <= 100)
        data = FFBReport_Set_Gain_Feature_Data_t()
        data.reportId = HID_REPORT_FEATURE_ID_SET_GAIN
        data.gain_id = slider_id
        data.gain_value = value
        self._dev.send_feature_report(bytes(data))

    def set_deadzone(self, deadzone: int):
        """
        Set the deadzone for the device.

        :param deadzone: Deadzone value in the range 0-4096.
        """
        assert(self._dev and 0 <= deadzone <= 4096)
        data = FFBReport_SetDeadzone(deadzone=deadzone)
        self._dev.write(bytes(data))

    # runs on mainThread
    @override
    def timerEvent(self, a0: QTimerEvent) -> None:
        try:
            self.read_reports()
        except Exception:
            logging.exception("Exception")
            self._dev.close()
            self._dev = None

            logging.warn("Reconnecting HID device in 1s")
            def do_reconnect():
                try:
                    self.reconnect()
                    logging.info("HID connected!")
                    self.deviceConnected.emit(True)
                except Exception:
                    self.deviceConnected.emit(False)
                    logging.warn("Reconnecting HID device in 1s")
                    QTimer.singleShot(1000, do_reconnect)

            QTimer.singleShot(1000, do_reconnect)
            
    def _process_hats(self, hats):
        if hats != self._prev_hats:
            hats_changed = hats ^ self._prev_hats
            for i in range(4):
                mask = 0xF << (i * 4)
                if hats_changed & mask:
                    val = (hats >> (i * 4)) & 0xF
                    prev_val = (self._prev_hats >> (i * 4)) & 0xF

                    if val != 0xF:
                        b = 0x80 | (i << 4) | val
                        self.buttonPressed.emit(b)
                    else:
                        b = 0x80 | (i << 4) | prev_val
                        self.buttonReleased.emit(b)
            self._prev_hats = hats

    def on_hid_report_received(self, report_id):
        if report_id == HID_REPORT_ID_INPUT:
            report: FFBReport_Input = self.get_input()

            btns: int = report.buttons

            prev = self._button_state
            self._button_state = btns
            
            diff = btns ^ prev # xor to get differences
            i = 0
            while diff: # iterate and shift out all changed bits
                if diff & 1:
                    if (~prev & btns)&1: # do some bitwise magic to check presses/releases
                        self.buttonPressed.emit(i)
                    if (prev & ~btns)&1:
                        self.buttonReleased.emit(i)
                i+=1
                diff = diff >> 1
                btns = btns >> 1
                prev = prev >> 1

            self._process_hats(report.hats)

        elif report_id == HID_REPORT_ID_PID_STATE_REPORT:
            report = self.get_report(HID_REPORT_ID_PID_STATE_REPORT)
            #print(report)
            if report.deviceResetEvent:
                logging.info("Device FFB state was reset, telemFFB will recreate all effects")
                for ref in self._effect_handles:
                    effect : FFBEffectHandle = ref()
                    effect.invalidate()

            if report.effectPlaying == 0:
                for ref in self._effect_handles:
                    effect : FFBEffectHandle = ref()
                    if effect.effect_id == report.effectBlockIndex:
                        effect._started = False

    def get_firmware_version(self, cached=True):
        if self.firmware_version and cached:
            return self.firmware_version
        
        try:
            with usb1.USBContext() as context:
                handle = context.openByVendorIDAndProductID(
                    self.vid,
                    self.pid,
                    skip_on_error=True,
                )
                #if handle is None:
                    # Device not present, or user is not allowed to access device.
                ##request_type, request, value, index, length

                self.firmware_version = handle.controlRead(USB_REQTYPE_DEVICE_TO_HOST|USB_REQTYPE_VENDOR, 
                                        USB_CTRL_REQ_GET_VERSION, 0, 0, 64).decode("utf-8")
                return self.firmware_version
        except Exception:
            logging.exception("Unable to read Firmware Version")
        
        return None

    def reset_effects(self):
        assert(self._dev)
        logging.info("FFB: Reset device effects")
        self._dev.write(bytes([HID_REPORT_ID_DEVICE_CONTROL, CONTROL_RESET]))
        time.sleep(0.01)

    def create_effect(self, type) -> FFBEffectHandle:
        assert(self._dev)
        self._dev.send_feature_report(bytes([HID_REPORT_ID_CREATE_EFFECT, type, 0, 0]))
        r = bytearray(self._dev.get_feature_report(HID_REPORT_ID_PID_BLOCK_LOAD, 5))

        assert(r[0] == HID_REPORT_ID_PID_BLOCK_LOAD)
        effect_id = r[1]
        status = r[2]

        if(status != LOAD_SUCCESS):
            logging.warn("Effects pool full, cannot create new effect")
            return None

        handle = FFBEffectHandle(self, effect_id, type)
        self._effect_handles.append(weakref.ref(handle, lambda x: self._effect_handles.remove(x)))
        return handle
    
    def write(self, data):
        assert(self._dev)
        if self._dev.write(data) < 0:
            raise IOError("HID Write")
        
    def read_reports(self):
        if not self._dev:
            return
        # read all input reports from the operating system buffer
        # we only care about the latest ones, otherwise there will be latency!
        # this function is non-blocking
        while True:
            tmp = self._dev.read(64)
            if tmp:
                report_id = tmp[0]
                self._in_reports[report_id] = tmp
                self.on_hid_report_received(report_id)
            else: break
        
    def get_report(self, report_id):   
        #self.readReports()

        data = self._in_reports.get(report_id, None)
        if data:
            try:
                return input_report_handlers[report_id].from_buffer_copy(data)
            except KeyError as err:
                logging.exception(f'ERROR GETTING HID REPORT: {err}')
                return data

        return data
        
    def get_input(self) -> FFBReport_Input:
        return self.get_report(HID_REPORT_ID_INPUT)


# Higher level effect interface
class HapticEffect(Destroyable):
    """High-level wrapper for a single haptic effect on a Rhino device.

    This class provides a lazy, convenient API to create, configure and
    control Force Feedback (FFB) effects on the `FFBRhino` device. Effects
    may be created lazily and configured before they are actually allocated
    on the hardware; pending creation and pending condition updates are
    stored and applied when the effect is first used.

    Attributes:
        device: Class-level reference to the opened `FFBRhino` device.
    """

    device : Optional[FFBRhino] = None

    def __init__(self):
        """Create a new HapticEffect controller.

        The effect is not allocated on the device immediately. Callers can
        chain configuration methods (for example `.spring(...)` or
        `.constant(...)`) and then call `.start()` to ensure allocation and
        playback.
        """
        self.name : Optional[str] = None
        self._stopped_time : int = 0
        self._h_effect : Optional[FFBEffectHandle] = None
        self.modulator : Optional[FFBReport_SetCondition] = None
        self.effect_type : Optional[int] = None
        # Lazy initialization state
        self._pending_create = None  # function for creating the effect (lazy initialization)
        self._pending_conditions = {} # functions for setting condition (lazy initialization)
        self._pending_envelope : Optional[FFBReport_SetEnvelope] = None  # envelope to apply once on creation
        self._envelope_applied : bool = False  # track if envelope has been applied
        self._envelope_once : bool = False  # track if envelope should be cleared after first use  # track if envelope was explicitly set via .envelope()  # track if envelope has been applied

    def __repr__(self):
        """Return a short representation including the underlying handle."""
        return f"HapticEffect({self._h_effect}) at {hex(id(self))}"
    
    @property
    def id(self):
        """Return the numeric device effect id if allocated, otherwise None."""
        return self._h_effect.effect_id if self._h_effect else None

    @classmethod
    def open(cls, vid = 0xFFFF, pid=0x2055, serial=None, path=None) -> FFBRhino:
        """Open and attach a `FFBRhino` device for all HapticEffect instances.

        This is a convenience to set the class-level `device` reference used
        when effects are created. The arguments are forwarded to the
        `FFBRhino` constructor/selector.

        Args:
            vid: USB vendor id (default 0xFFFF for VPforce devices).
            pid: USB product id (default 0x2055 for Rhino).
            serial: Optional device serial to select a specific unit.
            path: Optional device path to select a specific unit.

        Returns:
            The opened `FFBRhino` instance.
        """
        logging.info(f"Open Rhino HID {vid:04X}:{pid:04X}")
        cls.device = FFBRhino(vid, pid, serial, path)
        logging.info(f"Successfully opened HID '{cls.device.info.path.decode('utf-8')}'")

        return cls.device

    def _ensure_effect_created(self):
        """Allocate the underlying effect on the device if it hasn't been yet.

        If this object was configured before allocation, the pending create
        function and any pending condition setters will be executed. Raises
        an AssertionError if the effect was previously destroyed.
        """
        if not self._h_effect:
            assert self._pending_create is not None
            # Execute the pending create function
            self._pending_create()
            # If there are pending conditions to set, do it now
            for val in self._pending_conditions.values():
                val()
            self._pending_conditions.clear()
            # Apply envelope if pending and not yet applied
            if self._h_effect and self._pending_envelope and not self._envelope_applied:
                self._h_effect.setEnvelope(self._pending_envelope)
                self._envelope_applied = True

    def setCondition(self, cond : FFBReport_SetCondition) -> Self:
        """Set condition parameters for condition-style effects.

        Condition effects include spring, damper, inertia, friction and the
        spring adjuster. If the underlying device effect is not yet
        allocated the condition will be queued and applied after allocation.

        Args:
            cond: An instance of `FFBReport_SetCondition` describing the
                parameter block to apply.

        Returns:
            Self to allow method chaining.
        """
        assert self.effect_type in [
            EFFECT_SPRING,
            EFFECT_DAMPER,
            EFFECT_INERTIA,
            EFFECT_FRICTION,
            EFFECT_SPRING_ADJUSTER,
            EFFECT_DETENT,
        ]

        if self._h_effect:
            self._h_effect.setCondition(cond)
        else:
            # Queue the condition for application once effect is created
            self._pending_conditions[cond.effectBlockIndex] = lambda: self._h_effect.setCondition(cond)

        return self

    def _conditional_effect(self, effect_type, coef_x = None, coef_y= None, sat_x = None, sat_y = None) -> Self:
        """Common helper for condition-style effects.

        Configures positive/negative coefficients and saturation for X and Y
        parameter blocks. If the effect is not yet allocated the creation
        function will be stored for lazy allocation.

        Keyword Args:
            coef_x (int|float|None): Positive/negative coefficient for the X axis.
                If a float is provided it is scaled internally where appropriate.
            coef_y (int|float|None): Positive/negative coefficient for the Y axis.
            sat_x (int|float|None): Positive/negative saturation for the X axis.
            sat_y (int|float|None): Positive/negative saturation for the Y axis.

        Returns:
            Self for chaining.
        """
        self.effect_type = effect_type

        def set_conditions():
            assert self._h_effect is not None, "Effect must be created before setting condition"

            if coef_x is not None or sat_x is not None:
                cond_x = FFBReport_SetCondition(parameterBlockOffset=0, 
                                                positiveCoefficient=int(coef_x or 0),
                                                negativeCoefficient=int(coef_x or 0),
                                                positiveSaturation=int(sat_x or 0),
                                                negativeSaturation=int(sat_x or 0))
                self._h_effect.setCondition(cond_x)

            if coef_y is not None or sat_y is not None:
                cond_y = FFBReport_SetCondition(parameterBlockOffset=1, 
                                                positiveCoefficient=int(coef_y or 0),
                                                negativeCoefficient=int(coef_y or 0),
                                                positiveSaturation=int(sat_y or 0),
                                                negativeSaturation=int(sat_y or 0))
                self._h_effect.setCondition(cond_y)

        if not self._h_effect:
            # Store the creation function
            def create_and_setup():
                self._h_effect = self.device.create_effect(effect_type)
                if not self._h_effect:
                    return False
                self._h_effect.setEffect() # initialize defaults

                set_conditions()
                return True

            self._pending_create = create_and_setup
        else:
            # Effect already exists, update coefficients directly
            set_conditions()

        return self

    def inertia(self, coef_x = None, coef_y = None, **kwargs) -> Self:
        """Configure this object as an inertia effect.

        Accepts keyword arguments passed through to `_conditional_effect`.
        Typical keyword arguments:
          - coef_x, coef_y: coefficients for X/Y parameter blocks
          - sat_x, sat_y: saturation for X/Y parameter blocks

        Returns:
            Self for chaining.
        """
        return self._conditional_effect(EFFECT_INERTIA, coef_x=coef_x, coef_y=coef_y, **kwargs)

    def damper(self, coef_x = None, coef_y = None, **kwargs) -> Self:
        """Configure this object as a damper effect.

        Accepts keyword arguments passed through to `_conditional_effect`.
        Typical keyword arguments:
          - coef_x, coef_y: coefficients for X/Y parameter blocks
          - sat_x, sat_y: saturation for X/Y parameter blocks

        Returns:
            Self for chaining.
        """
        return self._conditional_effect(EFFECT_DAMPER, coef_x=coef_x, coef_y=coef_y, **kwargs)

    def friction(self, coef_x = None, coef_y = None, **kwargs) -> Self:
        """Configure this object as a friction effect.

        Accepts keyword arguments passed through to `_conditional_effect`.
        Typical keyword arguments:
          - coef_x, coef_y: coefficients for X/Y parameter blocks
          - sat_x, sat_y: saturation for X/Y parameter blocks

        Returns:
            Self for chaining.
        """
        return self._conditional_effect(EFFECT_FRICTION, coef_x=coef_x, coef_y=coef_y, **kwargs)

    def spring(self, coef_x = None, coef_y = None, **kwargs) -> Self:
        """Configure this object as a spring effect.

        Accepts keyword arguments passed through to `_conditional_effect`.
        Typical keyword arguments:
          - coef_x, coef_y: coefficients for X/Y parameter blocks
          - sat_x, sat_y: saturation for X/Y parameter blocks

        Returns:
            Self for chaining.
        """
        return self._conditional_effect(EFFECT_SPRING, coef_x=coef_x, coef_y=coef_y, **kwargs)

    def spring_adjuster(self, coef_x = None, coef_y = None, **kwargs) -> Self:
        """Configure this object as a spring adjuster effect.

        Accepts keyword arguments passed through to `_conditional_effect`.
        Typical keyword arguments:
          - coef_x, coef_y: coefficients for X/Y parameter blocks
          - sat_x, sat_y: saturation for X/Y parameter blocks

        Returns:
            Self for chaining.
        """
        return self._conditional_effect(EFFECT_SPRING_ADJUSTER, coef_x=coef_x, coef_y=coef_y, **kwargs)

    def detent(self, position_x=0, position_y=0, peak_x=None, peak_y=None, 
               range_x=None, range_y=None, gate_pos_x=None, gate_neg_x=None,
               gate_pos_y=None, gate_neg_y=None, deadband_x=None, deadband_y=None) -> Self:
        """Configure this object as a detent effect.
        
        A detent effect creates a "notch" or "valley" at a specified position,
        providing tactile feedback when the control passes through that position.
        The effect automatically includes 400-unit edge smoothing and -1000 damping.
        
        Firmware Parameter Mapping:
        ---------------------------
        Python Parameter    | Firmware Code         | Description
        --------------------|----------------------|----------------------------------
        position_x, _y      | e->cp.x, e->cp.y     | Center position of detent
        peak_x, peak_y      | e->coef.pos.x, .y    | Detent size/peak strength
        range_x, range_y    | e->coef.neg.x, .y    | Detent range/width
        gate_pos_x, _y      | e->saturation.pos.x/y| Upper gate/bound position
        gate_neg_x, _y      | e->saturation.neg.x/y| Lower gate/bound position
        deadband_x, _y      | e->deadband.x, .y    | Deadband size on X/Y axis
        
        Args:
            position_x (int, optional): Center position of detent on X-axis (default: 0).
                Firmware: e->cp.x
            position_y (int, optional): Center position of detent on Y-axis (default: 0).
                Firmware: e->cp.y
            peak_x (int, optional): Detent size/peak strength on X-axis.
                Firmware: e->coef.pos.x (e.g., groove_detent_size = 2500)
            peak_y (int, optional): Detent size/peak strength on Y-axis.
                Firmware: e->coef.pos.y (e.g., latch_detent_size = 1500)
            range_x (int, optional): Detent range/width on X-axis.
                Firmware: e->coef.neg.x (e.g., groove_detent_range = 3000)
            range_y (int, optional): Detent range/width on Y-axis.
                Firmware: e->coef.neg.y (e.g., latch_detent_range = 2000)
            gate_pos_x (int, optional): Upper gate/bound position on X-axis.
                Firmware: e->saturation.pos.x
            gate_neg_x (int, optional): Lower gate/bound position on X-axis.
                Firmware: e->saturation.neg.x
            gate_pos_y (int, optional): Upper gate/bound position on Y-axis.
                Firmware: e->saturation.pos.y (e.g., -400 for shifter groove)
            gate_neg_y (int, optional): Lower gate/bound position on Y-axis.
                Firmware: e->saturation.neg.y (e.g., -10000 for shifter groove)
            deadband_x (int, optional): Deadband size on X-axis (default: None).
                Firmware: e->deadband.x
            deadband_y (int, optional): Deadband size on Y-axis (default: None).
                Firmware: e->deadband.y
        
        Returns:
            Self for chaining.
            
        Note:
            The firmware implementation includes automatic features:
            - 400-unit smoothing range at the edges of the saturation bounds
            - -1000 damping coefficient applied when force is active
            - Cross-axis gain modulation based on position within smoothing range
            
        Example - Vertical groove detent (from firmware init_hshifter):
            >>> # Vertical groove at x=2000
            >>> effect = HapticEffect().detent(
            ...     position_x=2000,          # e->cp.x = 2000
            ...     peak_x=2500,              # e->coef.pos.x = groove_detent_size
            ...     range_x=3000,             # e->coef.neg.x = groove_detent_range
            ...     gate_pos_y=-400,          # e->saturation.pos.y = -400
            ...     gate_neg_y=-10000         # e->saturation.neg.y = -10000
            ... )
            >>> effect.start()
            
        Example - Latch detent (from firmware):
            >>> effect = HapticEffect().detent(
            ...     position_y=-3000,         # e->cp.y = -3000
            ...     peak_y=1500,              # e->coef.pos.y = latch_detent_size
            ...     range_y=2000,             # e->coef.neg.y = latch_detent_range
            ...     position_x=2000,          # e->cp.x = 2000
            ...     peak_x=2500,              # e->coef.pos.x = groove_detent_size
            ...     range_x=3000,             # e->coef.neg.x = groove_detent_range
            ...     gate_pos_y=-400,
            ...     gate_neg_y=-10000
            ... )
            >>> effect.start()
        """
        self.effect_type = EFFECT_DETENT

        def set_conditions():
            assert self._h_effect is not None, "Effect must be created before setting condition"

            # X-axis configuration
            if peak_x is not None or range_x is not None or gate_pos_x is not None or gate_neg_x is not None or deadband_x is not None:
                cond_x = FFBReport_SetCondition(
                    parameterBlockOffset=0,
                    cpOffset=int(position_x),               # e->cp.x
                    positiveCoefficient=int(peak_x or 0),   # e->coef.pos.x (detent size/peak)
                    negativeCoefficient=int(range_x or 0),  # e->coef.neg.x (detent range/width)
                    positiveSaturation=int(gate_pos_x or 0),  # e->saturation.pos.x
                    negativeSaturation=int(gate_neg_x or 0),   # e->saturation.neg.x
                    deadBand=int(deadband_x or 0)           # e->deadband.x
                )
                self._h_effect.setCondition(cond_x)

            # Y-axis configuration
            if peak_y is not None or range_y is not None or gate_pos_y is not None or gate_neg_y is not None or deadband_y is not None:
                cond_y = FFBReport_SetCondition(
                    parameterBlockOffset=1,
                    cpOffset=int(position_y),               # e->cp.y
                    positiveCoefficient=int(peak_y or 0),   # e->coef.pos.y
                    negativeCoefficient=int(range_y or 0),  # e->coef.neg.y
                    positiveSaturation=int(gate_pos_y or 0),  # e->saturation.pos.y
                    negativeSaturation=int(gate_neg_y or 0),   # e->saturation.neg.y
                    deadBand=int(deadband_y or 0)           # e->deadband.y
                )
                self._h_effect.setCondition(cond_y)

        if not self._h_effect:
            # Store the creation function for lazy initialization
            def create_and_setup():
                self._h_effect = self.device.create_effect(EFFECT_DETENT)
                if not self._h_effect:
                    return False
                self._h_effect.setEffect()  # Initialize defaults
                set_conditions()
                return True

            self._pending_create = create_and_setup
        else:
            # Effect already exists, update parameters directly
            set_conditions()

        return self

    def periodic(self, frequency, magnitude:float, direction:float, *args, effect_type=EFFECT_SINE, duration=0, **kwargs):
        """Create or update a periodic effect (sine/square/triangle/etc.).

        Direction may be a numeric angle in degrees or a DirectionModulator
        class; when a modulator class is given it will be instantiated and
        queried for an updated direction value.

        Args:
            frequency: Frequency in Hz.
            magnitude: Magnitude in range [0.0, 1.0].
            direction: Direction in degrees or a DirectionModulator subclass.
            effect_type: One of PERIODIC_EFFECTS (defaults to sine).
            duration: Duration in milliseconds (0 for infinite/continuous).

        Returns:
            Self for chaining.
        """
        # Handle direction modulator
        if isinstance(direction, type) and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        if not self._h_effect:
            # Store the creation and setup function
            def create_and_setup():
                self._h_effect = self.device.create_effect(effect_type)
                self.effect_type = effect_type
                if not self._h_effect: 
                    return False
                self._h_effect.setPeriodic(frequency, magnitude, direction, duration=duration, **kwargs)
                return True
            
            self._pending_create = create_and_setup
        else:
            # Effect exists, update it directly
            self._h_effect.setPeriodic(frequency, magnitude, direction, duration=duration, **kwargs)

        return self

    def constant(self, magnitude:float, direction:float, *args, **kwargs):
        """Create or update a constant (constant force) effect.

        Direction may be a numeric angle in degrees or a DirectionModulator
        class. If the underlying effect is not yet allocated the creation is
        queued for lazy allocation.

        Args:
            magnitude: Float in range [0.0, 1.0] representing effect strength.
            direction: Angle in degrees or a DirectionModulator subclass.

        Returns:
            Self for chaining.
        """
        # Handle direction modulator
        if isinstance(direction, type) and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        if not self._h_effect:
            # Store the creation and setup function
            def create_and_setup():
                self._h_effect = self.device.create_effect(EFFECT_CONSTANT)
                self.effect_type = EFFECT_CONSTANT
                if not self._h_effect: 
                    return False
                self._h_effect.setConstantForce(magnitude, direction, **kwargs)
                return True
            
            self._pending_create = create_and_setup
        else:
            # Effect exists, update it directly
            self._h_effect.setConstantForce(magnitude, direction, **kwargs)

        return self

    def envelope(self, attackFromForce=None, decayToForce=None, attackTime=None, decayTime=None, once=False, **kwargs) -> Self:
        """Set or update envelope parameters for the effect.
        
        The envelope can be specified either as individual parameters or as a dict/FFBReport_SetEnvelope.
        By default, explicitly set envelopes persist across start/stop cycles. Set once=True for one-time use.
        
        Args:
            attackFromForce (int, optional): Force to start from (0-4096). Firmware: attackFromForce
            decayToForce (int, optional): Force to decay to (0-4096). Firmware: decayToForce
            attackTime (int, optional): Time in milliseconds to reach sustain level
            decayTime (int, optional): Time in milliseconds to reach decay level
            once (bool, optional): If True, envelope is cleared after first use (on stop). Default False.
            **kwargs: Can include a dict or FFBReport_SetEnvelope instance as 'envelope' key
        
        Returns:
            Self for chaining.
            
        Example - Persistent envelope:
            >>> effect = HapticEffect()
            >>> effect.constant(magnitude=0.5, direction=90).envelope(
            ...     attackFromForce=2048, 
            ...     decayToForce=512,
            ...     attackTime=500,
            ...     decayTime=300
            ... ).start()
            
        Example - One-time envelope:
            >>> effect.constant(magnitude=0.5, direction=90).envelope(
            ...     attackFromForce=2048,
            ...     decayToForce=512,
            ...     attackTime=500,
            ...     decayTime=300,
            ...     once=True
            ... ).start()
        """
        # Handle if full envelope object passed via kwargs
        if 'envelope' in kwargs:
            env = kwargs['envelope']
            if isinstance(env, dict):
                self._pending_envelope = FFBReport_SetEnvelope(**env)
            elif isinstance(env, FFBReport_SetEnvelope):
                self._pending_envelope = env
        else:
            # Build from individual parameters
            params = {}
            if attackFromForce is not None:
                params['attackFromForce'] = attackFromForce
            if decayToForce is not None:
                params['decayToForce'] = decayToForce
            if attackTime is not None:
                params['attackTime'] = attackTime
            if decayTime is not None:
                params['decayTime'] = decayTime
            
            if params:
                self._pending_envelope = FFBReport_SetEnvelope(**params)
        
        # Track if envelope should be cleared after first use
        self._envelope_once = once
        
        # If effect already exists and envelope is set, apply envelope immediately
        if self._h_effect and self._pending_envelope:
            self._h_effect.setEnvelope(self._pending_envelope)
            self._envelope_applied = True
        
        return self

    @property
    def started(self) -> bool:
        """Return True when the underlying effect is currently playing."""
        return bool(self._h_effect and self._h_effect.started)

    def start(self, force=False, **kw):
        """Ensure effect is allocated and start playback.

        Args:
            force: If True, restart the effect even if it is already playing.
            **kw: Forwarded to the underlying effect start call (for loopCount
                or override behavior).

        Returns:
            Self for chaining.
        """
        # Ensure effect is created before starting
        self._ensure_effect_created()

        if self._h_effect and (not self.started or force):
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                caller_frame = inspect.currentframe().f_back
                caller_name = caller_frame.f_code.co_name
                logging.debug(f"The function {caller_name} is starting effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""
            logging.info(f"Start effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.start(**kw)
            self._stopped_time = 0

        return self

    def stop(self, destroy_after : int = 10000):
        """Stop playback of the effect and optionally destroy it after a timeout.

        Args:
            destroy_after: If non-zero, the effect will be destroyed after this
                many milliseconds of being stopped. Default is 10000 (10s).

        Returns:
            Self for chaining.
        """
        if self._h_effect and self._h_effect.started:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                caller_frame = inspect.currentframe().f_back
                caller_name = caller_frame.f_code.co_name
                logging.debug(f"The function {caller_name} is stopping effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""  
            logging.info(f"Stop effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.stop()
            
            # Clear envelope if it was marked as one-time use
            if self._envelope_once and self._pending_envelope:
                clear_envelope = FFBReport_SetEnvelope(
                    attackFromForce=0,
                    decayToForce=0,
                    attackTime=0,
                    decayTime=0
                )
                self._h_effect.setEnvelope(clear_envelope)
                self._pending_envelope = None
                self._envelope_applied = False
                self._envelope_once = False
            
            if destroy_after:
                if not self._stopped_time:
                    self._stopped_time = millis()

        if self._stopped_time and destroy_after:
            if millis() - self._stopped_time > destroy_after:
                self._stopped_time = 0
                self.destroy()
        return self

    def destroy(self):
        """Deallocate the underlying effect from the device immediately.

        After calling this the effect handle is cleared and subsequent calls
        that require an allocated effect will recreate it lazily.
        """
        if self._h_effect:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                caller_frame = inspect.currentframe().f_back
                caller_name = caller_frame.f_code.co_name
                logging.debug(f"The function {caller_name} is destroying effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""  
            logging.info(f"Destroying effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.destroy()
            self._h_effect = None

    def __del__(self):
        """Destructor helper to ensure resources are freed."""
        self.destroy()

# unit test
if __name__ == "__main__":
    import random

    import telemffb.utils as utils

    devs = FFBRhino.enumerate()
    print()
    for dev in devs:
        print(f"Found {dev.product_string} with serial {dev.serial_number}")
        print(f"-- path {dev.path}")
        print()

    print(hid.enumerate(vid=0xffff))


    d = FFBRhino(0xffff, 0x2055)

    print("Example getGains()")
    gains = d.get_gains()
    print("Result:", gains)
    print(f"Master Gain: {gains.master_gain}")
    print(f"Periodic Gain: {gains.periodic_gain}")
    
    print("Set constant gain to 90")
    d.set_gain(FFB_GAIN_CONSTANT, 90)
    gains = d.get_gains()
    print(f"Constant Gain(Should be 90): {gains.constant_gain}")
    d.set_gain(FFB_GAIN_CONSTANT, 100)


    exit()


    #c = d.createEffect(EFFECT_CONSTANT)
    #c.setConstantForce(0.05, 90)

    #s = d.
    
    #c = d.createEffect(EFFECT_SINE)
    #c.setPeriodic(10, 0.05, 0)
    #c.start()
    e = HapticEffect()
    while True:
        d = random.randrange(0, 359)
        e.periodic(frequency=20, magnitude=0.3, duration=80, phase=45, direction=d)
        e.start(force=True)
        time.sleep(0.01)
    
    #c.start()
