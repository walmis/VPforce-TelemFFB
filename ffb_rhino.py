# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import time
import ctypes
import logging
from utils import DirectionModulator
import os

try:
    hidapi_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dll', 'hidapi.dll')
    ctypes.cdll.LoadLibrary(hidapi_path)
except:
    pass

import hid

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
HID_REPORT_ID_PID_STATE_REPORT = 2
HID_REPORT_ID_CREATE_EFFECT = 5
HID_REPORT_ID_PID_BLOCK_LOAD = 6
HID_REPORT_ID_PID_POOL_REPORT = 7

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

AXIS_ENABLE_X = 1
AXIS_ENABLE_Y = 2
AXIS_ENABLE_DIR = 4

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
                ("axesEnable", ctypes.c_uint8), # bit 0: x_axis_enble, bit 1: y_axis_enable, bit 2: direction_enable
                ("directionX", ctypes.c_uint8),
                ("directionY", ctypes.c_uint8),
                ("startDelay", ctypes.c_uint16)
               ]
    _defaults_ = {"reportId": HID_REPORT_ID_SET_EFFECT, "gain":4096}


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
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8), 
                ("parameterBlockOffset", ctypes.c_uint8), # bits: 0..3=parameterBlockOffset, 4..5=instance1, 6..7=instance2
                ("cpOffset", ctypes.c_int16), # -4096..4096
                ("positiveCoefficient", ctypes.c_int16), # -4096..4096
                ("negativeCoefficient", ctypes.c_int16), # -4096..4096
                ("positiveSaturation", ctypes.c_uint16), # 0..4096
                ("negativeSaturation", ctypes.c_uint16), # 0..4096
                ("deadBand", ctypes.c_uint16) # 0..4096
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_SET_CONDITION }

class FFBReport_BlockFree(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8),
                ("effectBlockIndex", ctypes.c_uint8), 
               ]
    _defaults_ = { "reportId": HID_REPORT_ID_BLOCK_FREE } 

class FFBEffectHandle:
    def __init__(self, device, effect_id, type) -> None:
        self.ffb : FFBRhino = device
        self.effect_id = effect_id
        self.type = type
    
    def start(self, loopCount=1):
        op = FFBReport_EffectOperation(effectBlockIndex=self.effect_id, operation=OP_START, loopCount=loopCount)
        self.ffb.write(bytes(op))
        return self

    def stop(self):
        op = FFBReport_EffectOperation(effectBlockIndex=self.effect_id, operation=OP_STOP)
        self.ffb.write(bytes(op))
        return self

    def destroy(self):
        if self.effect_id is not None:
            op = FFBReport_BlockFree(effectBlockIndex=self.effect_id)
            self.ffb.write(bytes(op))
            self.type = 0
            self.effect_id = None

    def setConstantForce(self, magnitude, direction, **kwargs):
        """Set constant for for effect

        :param magnitude: Magnitude [-1..1]
        :type magnitude: float
        :param direction: Direction in degrees [0..360]
        :type direction: float
        """
        assert(self.type == EFFECT_CONSTANT)
        assert(magnitude >= -1.0 and magnitude <= 1.0)
        direction %= 360

        kw = {
            "effectBlockIndex": self.effect_id,
            "effectType": self.type,
            "axesEnable": AXIS_ENABLE_DIR,
            "directionX": round((direction*255/360)),
            "gain" : 4096
        }
        kw.update(kwargs)
        op = FFBReport_SetEffect(**kw)
        self.ffb.write(bytes(op))
        op = FFBReport_SetConstantForce(magnitude=round(4096*magnitude), effectBlockIndex=self.effect_id)
        self.ffb.write(bytes(op))
        return self

    def setPeriodic(self, freq, magnitude, direction, **kwargs):
        assert(self.type in PERIODIC_EFFECTS)
        assert(magnitude >= 0 and magnitude <= 1.0)
        direction %= 360

        kw = {
            "effectBlockIndex": self.effect_id,
            "effectType": self.type,
            "axesEnable": AXIS_ENABLE_DIR,
            "directionX": round((direction*255/360)),
            "gain" : 4096
        }
        kw.update(kwargs)
        op = FFBReport_SetEffect(**kw)
        self.ffb.write(bytes(op))

        if freq == 0:
            period = 0
        else:
            period = round(1000.0/freq)
        op = FFBReport_SetPeriodic(magnitude=round(4096*magnitude), effectBlockIndex=self.effect_id, period=period, **kwargs)
        self.ffb.write(bytes(op))
        return self
    
    def __del__(self):
        self.destroy()


           
class FFBRhino:
    def __init__(self, vid = 0xFFFF, pid=0x2055) -> None:
        self.device =  hid.Device(vid, pid)

    def resetEffects(self):
        self.device.write(bytes([HID_REPORT_ID_DEVICE_CONTROL, CONTROL_RESET]))
        time.sleep(0.01)

    def createEffect(self, type) -> FFBEffectHandle:
        self.device.send_feature_report(bytes([HID_REPORT_ID_CREATE_EFFECT, type, 0, 0]))
        r = bytearray(self.device.get_feature_report(HID_REPORT_ID_PID_BLOCK_LOAD, 5))

        assert(r[0] == HID_REPORT_ID_PID_BLOCK_LOAD)
        effect_id = r[1]
        status = r[2]
        assert(status == LOAD_SUCCESS)
        return FFBEffectHandle(self, effect_id, type)
    
    def write(self, data):
        if self.device.write(data) < 0:
            raise IOError("HID Write")


class HapticEffect:
    effect : FFBEffectHandle = None
    device : FFBRhino = None
    started : bool = False
    modulator = None

    @classmethod
    def open(cls, vid = 0xFFFF, pid=0x2055) -> None:
        logging.info(f"Open Rhino HID {vid:04X}:{pid:04X}")
        cls.device = FFBRhino(vid, pid)
        return cls.device

    def periodic(self, frequency, magnitude:float, direction:float, effect_type=EFFECT_SINE, *args, **kwargs):
        if not self.effect:
            self.effect = self.device.createEffect(effect_type)
        
        if type(direction) == type and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        self.effect.setPeriodic(frequency, magnitude, direction, **kwargs)
        return self

    def constant(self, magnitude:float, direction:float, *args, **kwargs):
        """Create and manage CF FFB effect

        :param magnitude: Effect strength from 0.0 .. 1.0
        :type magnitude: float
        :param direction_deg: Angle in degrees
        :type direction_deg: float
        """
        if not self.effect:
            self.effect = self.device.createEffect(EFFECT_CONSTANT)

        if type(direction) == type and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        self.effect.setConstantForce(magnitude, direction, **kwargs)
        return self

    @property
    def status(self) -> int:
        return self.started

    def start(self):
        if self.effect and not self.started:
            logging.info(f"Start effect {self.effect.effect_id}")
            self.effect.start()
            self.started = True
    
    def stop(self):
        if self.effect and self.started:
            logging.info(f"Stop effect {self.effect.effect_id}")
            self.effect.stop() 
            self.started = False

    def destroy(self):
        if self.effect:
            logging.info(f"Destroying effect {self.effect.effect_id}")
            self.effect.destroy()
            self.effect = None

    def __del__(self):
        self.destroy()

# unit test
if __name__ == "__main__":
    d = FFBRhino(0xffff, 0x2055)
    d.resetEffects()

    #c = d.createEffect(EFFECT_CONSTANT)
    #c.setConstantForce(0.05, 90)

    c = d.createEffect(EFFECT_SINE)
    c.setPeriodic(10, 0.05, 0)
    c.start()

    time.sleep(2)
    
    #c.start()