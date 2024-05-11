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

import ctypes
import inspect
import logging
import os
import time
import weakref
from dataclasses import dataclass
from typing import List, Self

import usb1
from PyQt5.QtCore import QObject, QTimer, QTimerEvent

from telemffb.utils import Destroyable, DirectionModulator, clamp, overrides, millis

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
    12: "Custom"
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

class FFBReport_Input(BaseStructure):
    _pack_ = 1
    _fields_ = [("reportId", ctypes.c_uint8), # = 1
                ("X", ctypes.c_int16),
                ("Y", ctypes.c_int16),
                ("Z", ctypes.c_int16),
                ("Rz", ctypes.c_uint8),
                ("Ry", ctypes.c_uint8),
                ("Rx", ctypes.c_uint8),
                ("Slider", ctypes.c_uint8),
                ("Button", ctypes.c_uint32),
                ("ButtonAux", ctypes.c_uint16),
                ("hats", ctypes.c_uint16),
               ]
    _defaults_ = {}

    # get if button is pressed, buttons start from 1
    def isButtonPressed(self, button_number):
        assert(button_number > 0)
        btns = self.Button | (self.ButtonAux<<32)
        return (btns & (1<<(button_number-1))) != 0

    def getPressedButtons(self):
        btns = self.Button | (self.ButtonAux << 32)
        pressed = [i + 1 for i in range(64) if (btns & (1 << i)) != 0]
        return pressed

    # get main X and Y axis in range [-1.0 .. 1.0]
    def axisXY(self):
        return (self.X/4096.0, self.Y/4096.0)

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
        cond.positiveCoefficient = clamp(cond.positiveCoefficient, -4096, 4096)
        cond.negativeCoefficient = clamp(cond.negativeCoefficient, -4096, 4096)
        data = bytes(cond)
        if self._data_changed(f"setCondition{cond.parameterBlockOffset}", data):
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
    path: str
    product_id: int
    product_string: str
    release_number: int
    serial_number: str
    usage: int
    usage_page: int
    vendor_id: int

class FFBRhino(QObject):
    def __init__(self, vid = 0xFFFF, pid=0x2055, serial=None, path=None) -> None:

        self.vid = vid
        self.pid = pid
        self.info : DeviceInfo = None
        self.firmware_version : str = None

        if not path:
            devs = FFBRhino.enumerate(pid)
            if serial:
                devs = list(filter(lambda x: x.serial_number == serial, devs))
            if path:
                devs = list(filter(lambda x: x.path == path, devs))
            if not devs:
                raise hid.HIDException('unable to open device')
            self.info = devs[0]

        self._in_reports = {}
        self._effectHandles : List[FFBEffectHandle] = []
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
        return self._dev.serial
    @property
    def product(self):
        return self._dev.product
    @property
    def manufacturer(self):
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
        d = self._dev.get_feature_report(HID_REPORT_FEATURE_ID_GET_GAINS, ctypes.sizeof(FFBReport_Get_Gains_Feature_Data))
        data = FFBReport_Get_Gains_Feature_Data.from_buffer_copy(d)
        return data
    
    # Set global effect class gain, same as in VPConfigurator sliders
    def set_gain(self, slider_id, value):
        assert(value >= 0 and value <= 100)
        data = FFBReport_Set_Gain_Feature_Data_t()
        data.reportId = HID_REPORT_FEATURE_ID_SET_GAIN
        data.gain_id = slider_id
        data.gain_value = value
        self._dev.send_feature_report(bytes(data))

    # runs on mainThread
    @overrides(QObject)
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
                except Exception:
                    logging.warn("Reconnecting HID device in 1s")
                    QTimer.singleShot(1000, do_reconnect)

            QTimer.singleShot(1000, do_reconnect)
            

    def on_hid_report_received(self, report_id):
        if report_id == HID_REPORT_ID_PID_STATE_REPORT:
            report = self.get_report(HID_REPORT_ID_PID_STATE_REPORT)
            #print(report)
            if report.deviceResetEvent:
                logging.info("Device FFB reset event: Invalidating all effects")
                for ref in self._effectHandles:
                    effect : FFBEffectHandle = ref()
                    effect.invalidate()

            if report.effectPlaying == 0:
                for ref in self._effectHandles:
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
        logging.info("FFB: Reset device effects")
        self._dev.write(bytes([HID_REPORT_ID_DEVICE_CONTROL, CONTROL_RESET]))
        time.sleep(0.01)

    def create_effect(self, type) -> FFBEffectHandle:
        self._dev.send_feature_report(bytes([HID_REPORT_ID_CREATE_EFFECT, type, 0, 0]))
        r = bytearray(self._dev.get_feature_report(HID_REPORT_ID_PID_BLOCK_LOAD, 5))

        assert(r[0] == HID_REPORT_ID_PID_BLOCK_LOAD)
        effect_id = r[1]
        status = r[2]

        if(status != LOAD_SUCCESS):
            logging.warn("Effects pool full, cannot create new effect")
            return None

        handle = FFBEffectHandle(self, effect_id, type)
        self._effectHandles.append(weakref.ref(handle, lambda x: self._effectHandles.remove(x)))
        return handle
    
    def write(self, data):
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
    device : FFBRhino = None

    def __init__(self):
       self.name = None
       self._stopped_time : int = 0
       self._h_effect : FFBEffectHandle = None
       self.modulator = None
       self.effect_type = None
       self._conds = {}

    def __repr__(self):
        return f"HapticEffect({self._h_effect})"

    # Open defaut Rhino device, specific devices can be specified using serial or path arguments
    # path example: \\\\?\\HID#VID_FFFF&PID_2055&MI_00#9&3450694a&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}
    # path can be obtained using FFBRhino.enumerate function
    @classmethod
    def open(cls, vid = 0xFFFF, pid=0x2055, serial=None, path=None) -> FFBRhino:
        logging.info(f"Open Rhino HID {vid:04X}:{pid:04X}")
        cls.device = FFBRhino(vid, pid, serial, path)
        logging.info(f"Successfully opened HID '{cls.device.info.path.decode('utf-8')}'")

        return cls.device
    
    def setCondition(self, cond : FFBReport_SetCondition) -> Self:
        assert(self.effect_type in [EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION])

        if not self._h_effect:
            self._conditional_effect(self.effect_type)
        
        self._h_effect.setCondition(cond)

        return self
    
    def _conditional_effect(self, effect_type, coef_x = None, coef_y= None) -> Self:
        if not self._h_effect:
            self._h_effect = self.device.create_effect(effect_type)
            self.effect_type = effect_type
            if not self._h_effect: 
                return self
            self._h_effect.setEffect() # initialize defaults

        if coef_x is not None:
            cond_x = FFBReport_SetCondition(parameterBlockOffset=0, 
                                            positiveCoefficient=int(coef_x),
                                            negativeCoefficient=int(coef_x))
            self._h_effect.setCondition(cond_x)

        if coef_y is not None:
            cond_y = FFBReport_SetCondition(parameterBlockOffset=1, 
                                            positiveCoefficient=int(coef_y),
                                            negativeCoefficient=int(coef_y))
            self._h_effect.setCondition(cond_y)

        return self
    
    def inertia(self, coef_x = None, coef_y = None):
        return self._conditional_effect(EFFECT_INERTIA, coef_x, coef_y)
    
    def damper(self, coef_x = None, coef_y = None):
        return self._conditional_effect(EFFECT_DAMPER, coef_x, coef_y)
    
    def friction(self, coef_x = None, coef_y = None):
        return self._conditional_effect(EFFECT_FRICTION, coef_x, coef_y) 
    
    def spring(self, coef_x = None, coef_y = None):
        return self._conditional_effect(EFFECT_SPRING, coef_x, coef_y) 

    def periodic(self, frequency, magnitude:float, direction:float, *args, effect_type=EFFECT_SINE, duration=0, **kwargs):
        if not self._h_effect:
            self._h_effect = self.device.create_effect(effect_type)
            self.effect_type = effect_type
            if not self._h_effect: 
                return self
        
        if type(direction) == type and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        self._h_effect.setPeriodic(frequency, magnitude, direction, duration=duration, **kwargs)
        return self

    def constant(self, magnitude:float, direction:float, *args, **kwargs):
        """Create and manage CF FFB effect

        :param magnitude: Effect strength from 0.0 .. 1.0
        :type magnitude: float
        :param direction_deg: Angle in degrees
        :type direction_deg: float
        """
        if not self._h_effect:
            self._h_effect = self.device.create_effect(EFFECT_CONSTANT)
            self.effect_type = EFFECT_CONSTANT
            if not self._h_effect: return self

        if type(direction) == type and issubclass(direction, DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()

        self._h_effect.setConstantForce(magnitude, direction, **kwargs)
        return self

    @property
    def started(self) -> bool:
        return self._h_effect and self._h_effect.started

    def start(self, force=False, **kw):

        if self._h_effect and (not self.started or force):
            caller_frame = inspect.currentframe().f_back
            caller_name = caller_frame.f_code.co_name
            logging.debug(f"The function {caller_name} is starting effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""
            logging.info(f"Start effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.start(**kw)
            self._stopped_time = 0

        return self
    
    def stop(self, destroy_after : int = 10000):
        """Stop active effect

        :param destroy_after: Cleanup (destroy) effect if unused for x milliseconds
        :type destroy_after: int, optional
        """
        if self._h_effect and self._h_effect.started:
            caller_frame = inspect.currentframe().f_back
            caller_name = caller_frame.f_code.co_name
            logging.debug(f"The function {caller_name} is stopping effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""  
            logging.info(f"Stop effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.stop()
            if destroy_after:
                if not self._stopped_time:
                    self._stopped_time = millis()

        if self._stopped_time and destroy_after:
            if millis() - self._stopped_time > destroy_after:
                self._stopped_time = 0
                self.destroy()
        return self

    def destroy(self):
        if self._h_effect:
            caller_frame = inspect.currentframe().f_back
            caller_name = caller_frame.f_code.co_name
            logging.debug(f"The function {caller_name} is destroying effect {self._h_effect.effect_id}")
            name = f" (\"{self.name}\")" if self.name else ""  
            logging.info(f"Destroying effect {self._h_effect.effect_id} ({self._h_effect.name}){name}")
            self._h_effect.destroy()
            self._h_effect = None

    def __del__(self):
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
