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

import random
import re
import socket
import threading
import time

from PyQt6.QtCore import QCoreApplication, QMetaObject, QObject, Qt, QSize, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QPixmap

from PyQt6 import QtCore

from ._math import to_number


__all__ = [
    "schedule_on_main_thread",
    "EffectTranslator",
    "Destroyable",
    "sanitize_dict",
    "flatten_dict",
    "insert_dict_item",
    "get_random_within_range",
    "PerformanceTracker",
    "Dispenser",
    "Teleplot",
    "teleplot",
    "ResultThread",
    "threaded",
    "HiDpiPixmap",
]


def schedule_on_main_thread(func):
    """
    Schedule a callable to execute in the main Qt thread.
    
    This is essential when calling GUI methods from worker threads (e.g., threading.Thread).
    Qt GUI objects must only be accessed from the thread they were created in (main thread).
    
    Args:
        func: A callable (lambda or function) to execute in the main thread
    
    Examples:
        # Lambda (simple and clean):
        schedule_on_main_thread(lambda: G.main_window.update_sim_indicators("dcs", True))
        schedule_on_main_thread(lambda: some_widget.setText("Hello"))
        
        # Function reference:
        def update_ui():
            G.main_window.statusBar().showMessage("Updated")
        schedule_on_main_thread(update_ui)
    """
    class CallableWrapper(QObject):
        # Keep references to wrapper objects to prevent garbage collection
        _scheduled_wrappers = []

        def __init__(self, func):
            super().__init__()
            # Keep a reference to prevent garbage collection before execution
            self._scheduled_wrappers.append(self)
            # Clean up old wrappers if list gets too long (prevent memory leak)
            if len(self._scheduled_wrappers) > 100:
                self._scheduled_wrappers[:] = self._scheduled_wrappers[-50:]

            self.func = func
            # Move to main thread
            if QCoreApplication.instance():
                self.moveToThread(QCoreApplication.instance().thread())
        
        @pyqtSlot()
        def execute(self):
            try:
                self.func()
            finally:
                # Remove from references list after execution
                try:
                    self._scheduled_wrappers.remove(self)
                except (ValueError, AttributeError):
                    pass
    
    wrapper = CallableWrapper(func)

    QMetaObject.invokeMethod(
        wrapper,
        "execute",
        Qt.ConnectionType.QueuedConnection
    )


class EffectTranslator:
    """
    Effects translator is used to display "human-readable text" in the UI for effects that are active.  In the
    dictionary, the "key" is the actual effect instance name used during creation.  The "value" is a list containing
    the desired description that will be displayed in the UI, and the applicable setting variable.  The setting
    variable name is used to change the color of the slider handle in the settings page when that particular effect
    is active
    """

    effect_dict = {
        "ab_rumble_.*": ["Afterburner Rumble", "afterburner_effect_intensity"],
        'adv_spr': ["Advanced Spring Override", ""],
        'rudder_const_force': ["Rudder Constant Force", ""],
        "aoa": ["AoA Effect", "aoa_effect_gain"],
        "ap_spring": ["Autopilot Spring", ""],
        "adv_gforce_constant": ["G-Force Loading (Advanced)", ""],
        "blade_slap.*": ["Blade Slap", "blade_slap_intensity"],
        "buffeting": ["AoA/Stall Buffeting", "buffeting_intensity"],
        "bombs": ["Bomb Release", "weapon_release_intensity"],
        "canopyclunk": ["Canopy Clunk", "canopy_motion_intensity"],
        "canopymovement": ["Canopy Motion", "canopy_motion_intensity"],
        "clunk": ["Tail Hook Clunk", "tailhook_motion_intensity"],
        "collective_ap_spring": ["Collective Spring", "collective_ap_spring_gain"],
        "collective_damper": ["Collective Dampening Force", "collective_dampening_gain"],
        "collective_ft": ["Collective Force Trim", "collective_ft_ovd_spring_gain"],
        "cp_ovd_spring": ["Co-Pilot/RIO Override Spring", "cp_spr_override_spring_gain"],
        "crit_aoa": ["AoA Reduction Force", "aoa_reduction_max_force"],
        "cm": ["Countermeasure Deployment", "cm_vibration_intensity"],
        "cyclic_spring": ["Cyclic Spring Force", "cyclic_spring_gain"],
        "damage": ["Aircraft Damage Event", "damage_effect_intensity"],
        "damper": ["Damper Override", "damper_force"],
        "dcs_spr_override": ["Spring Override", ""],
        "il2_spr_override": ["Spring Override", ""],
        "decel": ["Decelleration Force", "deceleration_max_force"],
        "dynamic_spring": ["Dynamic Spring Force", ".*_spring_gain"],
        "elev_droop": ["Elevator Droop", "elevator_droop_moment"],
        "etl.*": ["ETL Shaking", "etl_effect_intensity"],
        "fbw_spring": ["Fly-by-wire Spring Force", "fbw_.*_gain"],
        "ffb_api_cyclic_spring": ["FFB API Cyclic Spring", "ffb_api_cyclic_spring_gain"],
        "ffb_api_collective_spring": ["FFB API Collective Spring", "ffb_api_collective_spring_gain"],
        "ffb_api_pedal_spring": ["FFB API Pedal Spring", "ffb_api_pedal_spring_gain"],
        "flapsmovement": ["Flap Motion", "flaps_motion_intensity"],
        "FI_vibration": ["FI Vibration", "FI_vibration_intensity"],
        "friction": ["Friction Override", "friction_force"],
        "boommovement" : ["Fuel Boom/Door","fuelboom_motion_intensity"],
        "gearbuffet.*": ["Gear Drag Buffeting", "gear_buffet_intensity"],
        "gearclunk": ["Gear Clunk", "gear_motion_intensity"],
        "gearmovement.*": ["Gear Motion", "gear_motion_intensity"],
        "gforce": ["G-Force Loading", "gforce_effect_max_intensity"],
        "new_gforce": ["G-Force Loading V2", "new_gforce_effect_max_intensity"],
        "gunfire": ["Gunfire Rumble", "gun_vibration_intensity"],
        "hit": ["Aircraft Hit Event", ""],
        "je_rumble_.*": ["Jet Engine Rumble", "jet_engine_rumble_intensity"],
        "il2_buffet.*": ["Buffeting", "il2_buffeting_factor"],
        "il2_gunfire.*": ["Gunfire Rumble", "il2_weapon_release_intensity"],
        "il2_bombs": ["Bomb Release", "il2_bomb_release_intensity"],
        "il2_rockets": ["Rocket Fire", "il2_rocket_release_intensity"],
        "il2_ffb_spring": ["FFB Telemetry Spring Override", ""],
        "ffb_tap_spring": ["Game Spring (DirectInput Tap)", ""],
        # the game's non-spring effects rendered from the tap mirror: slot-
        # keyed names 'tap_game_{slot}_{type}', matched by effect type code
        # ($-anchored so _1 does not also swallow _10/_11)
        r"tap_game_\d+_1$": ["Game Constant Force (DirectInput Tap)", "tap_effect_constant_gain"],
        r"tap_game_\d+_[34567]$": ["Game Periodic Vibration (DirectInput Tap)", "tap_effect_periodic_gain"],
        r"tap_game_\d+_9$": ["Game Damper (DirectInput Tap)", "tap_effect_damper_gain"],
        r"tap_game_\d+_10$": ["Game Inertia (DirectInput Tap)", "tap_effect_inertia_gain"],
        r"tap_game_\d+_11$": ["Game Friction (DirectInput Tap)", "tap_effect_friction_gain"],
        "il2_ffb_const": ["FFB Telemetry Constant Force", ""],
        "il2_ffb_damper": ["FFB Telemetry Damper", ""],
        "il2_eng_shk1": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk2": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk3": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_eng_shk4": ["IL2 Prop Eng Shake (Telemetry)", ""],
        "il2_jet_shk1": ["IL2 Jet Eng Shake (Telemetry)", ""],
        "il2_jet_shk2": ["IL2 Jet Eng Shake (Telemetry)", ""],
        "inertia": ["Inertia Override", "inertia_force"],
        "nw_shimmy": ["Nosewheel Shimmy", "nosewheel_shimmy_intensity"],
        "overspeed.*": ["Overspeed Shake", "overspeed_shake_intensity"],
        "payload_rel": ["Payload Release", "weapon_release_intensity"],
        "pause_spring": ["Pause/Slew Spring Force", ""],
        "pedal_spring": ["Pedal Spring", "pedal_spring_gain"],
        "pedal_ap_spring": ["Pedal AP Spring", "hpg_pedal_spring_gain"],
        "pedal_damper": ["Pedal Damper", "pedal_dampening_gain"],
        "prop_rpm.*": ["Propeller Engine Rumble", "engine_rumble_.*"],
        "rockets": ["Rocket Fire", "il2_weapon_release_intensity"],
        "rotor_rpm.*": ["Rotor RPM/Engine Rumble", "heli_engine_rumble_intensity"],
        "runway.*": ["Runway Rumble", "runway_rumble_intensity"],
        "speedbrakebuffet.*": ["Speedbrake Buffeting", "speedbrake_buffet_intensity"],
        "speedbrakemovement": ["Speedbrake Motion", "speedbrake_motion_intensity"],
        "spoilerbuffet.*": ["Spoiler Buffeting", "spoiler_buffet_intensity"],
        "spoilermovement": ["Spoiler Motion", "spoiler_motion_intensity"],
        "steering_friction": ["Steering Friction", "steering_friction_intensity"],
        "stick_shaker.*" : ["Stick Shaker","stick_shaker_intensity"],
        "hookmovement" : ["Tail Hook","tailhook_motion_intensity"],
        "touchdown": ["Touch-down Effect", "touchdown_effect_max_force"],
        "trim_cal_spring": ["Trim Calibration Spring", ""],
        "trim_spring": ["Trim Override Spring", ""],
        "trimwheel_ap_spring": ["Trimwheel AP Spring", "trimwheel_ap_spring_gain"],
        "turbulence": ["Turbulence", "turbulence_intensity"],
        "control_weight": ["Control Weight", ""],
        "vrs_buffet.*": ["Vortex Ring State Buffeting", "vrs_effect_intensity"],
        "wnd": ["Wind Effect", "wind_effect_max_intensity"],
        "wingfoldmovement.*": ["Wing Fold", "wingfold_motion_intensity"],
        "hyd_loss_damper": ["Low Hydraulic Damper", "hydraulic_loss_damper"],
        "hyd_loss_friction": ["Low Hydraulic Friction", "hydraulic_loss_friction"],
        "lock_1": ["Controls Lock Lower Bound", ""],
        "lock_2": ["Controls Lock Upper Bound", ""],
    }
    @classmethod
    def get_translation(cls, key):
        e = cls.effect_dict.get(key, None)
        if e is not None:
            return e
        else:
            for k in cls.effect_dict.keys():
                if re.match(k, key):
                    return cls.effect_dict.get(k, [f"No Lookup: {k}", ''])

        return [f"No Lookup: {key}", '']


class Destroyable:
    def destroy(self):
        raise NotImplementedError


def sanitize_dict(d):
    out = {}
    for k, v in d.items():
        out[k] = to_number(v)
    return out


def _flatten_dict_gen(d, parent_key, sep):
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, dict):
            yield from flatten_dict(v, new_key, sep=sep).items()
        else:
            yield new_key, v


def flatten_dict(d, parent_key: str = '', sep: str = '_'):
    return dict(_flatten_dict_gen(d, parent_key, sep))


def insert_dict_item(original_dict, new_key, new_value, insert_key, before=True):
    updated_dict = {}
    found = False

    for key, value in original_dict.items():
        if key == insert_key and before:
            updated_dict[new_key] = new_value
            found = True
        updated_dict[key] = value
        if key == insert_key and not before:
            updated_dict[new_key] = new_value
            found = True

    if not found:
        # Key not found, append at the end (default behavior)
        updated_dict[new_key] = new_value

    return updated_dict


def get_random_within_range(item, input_number, range_start, range_end, decimal_places=2, time_period=None):
    """ Return a random number between range_start and range_end with a precision level of decimal_places
        if time_period (in seconds) is given, the function will return the same random number during any given
        interval of time_period for 'item' """
    current_time = int(time.time())  # Get the current timestamp in seconds
    random_seed = item

    # If time_period is not provided, generate a random number on every call
    if time_period is None:
        random.seed()
    else:
        time_period_index = current_time // time_period
        random_seed += str(time_period_index)
        random.seed(random_seed)

    # Generate a random number within the specified range with the specified number of decimal places
    factor = 10 ** decimal_places
    random_number = round(random.uniform(range_start, range_end), decimal_places)
    random_number = round(random_number * factor) / factor

    return random_number


class PerformanceTracker:
    def __init__(self):
        self.trackers = {}

    def get_time_delta(self, name: str):
        now = time.perf_counter()
        if name not in self.trackers:
            self.trackers[name] = now
            return 0.0
        last_time = self.trackers[name]
        delta = now - last_time
        self.trackers[name] = now
        return delta

    def remove_tracker(self, name: str):
        if name in self.trackers:
            del self.trackers[name]

    def clear_trackers(self):
        self.trackers.clear()

class Dispenser:
    def __init__(self, cls) -> None:
        self.cls = cls
        self.dict = {}

    def get(self, name, *args, **kwargs):
        v = self.dict.get(name)
        if not v:
            v = self.cls(*args, **kwargs)
            v.name = name
            self.dict[name] = v
        return v

    def remove(self, name):
        self.dispose(name)

    def __contains__(self, name):
        return name in self.dict

    def __getitem__(self, name):
        return self.get(name)

    def __iter__(self):
        return self.dict.__iter__()

    def __delitem__(self, name):
        v = self.dict[name]
        if isinstance(v, Destroyable):
            v.destroy()
        del self.dict[name]

    def clear(self):
        for k,v in self.dict.items():
            if isinstance(v, Destroyable):
                v.destroy()
        self.dict.clear()

    def values(self):
        return self.dict.values()

    def dispose(self, *names):
        for name in names:
            if name in self.dict:
                v = self.dict[name]
                if isinstance(v, Destroyable):
                    v.destroy()
                del self.dict[name]

    def foreach(self, func):
        for i in self.values():
            func(i)


class Teleplot:
    def __init__(self):
        self.sock = None
        self.enabled = False

    def configure(self, address: str):
        try:
            address = address.split(":")
            address[1] = int(address[1])
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.connect(tuple(address))
        except Exception:
            pass

    def sendTelemetry(self, name, value, instance=None):
        if instance is not None:
            name = f"{instance}_{name}"
        try:
            if self.sock:
                now = time.time() * 1000

                if type(value) == list:
                    msg = "\n".join([f"{name}_{i}:{now}:{value[i]}" for i in range(len(value))])
                else:
                    msg = f"{name}:{now}:{value}"
                self.sock.send(msg.encode())
        except Exception:
            pass


teleplot = Teleplot()


class ResultThread(threading.Thread):
    """
    A custom Thread that can return the value of the function 
    runned inside it
    """
    fx_output = None
    error = None

    def run(self, *args, **kwargs):
        try:
            if self._target:
                self.fx_output = self._target(*self._args, **self._kwargs)
        except Exception as e:
            self.error = e
        finally:
            # Avoid a refcycle if the thread is running a function with
            # an argument that has a member that points to the thread.
            del self._target, self._args, self._kwargs

    def await_output(self):
        """
        Wait for the thread to finish and return the return value of fx
        """
        self.join()
        return self.fx_output

    def get_error(self):
        """
        Return error if any, return None if no error occured
        """
        self.join()
        return self.error


def threaded(daemon=False):
    """
    A decorator to run a function in a separate thread, this is useful
    when you want to do any IO operations (network request, prints, etc...)
    and want to do something else while waiting for it to finish.
    :param fx: the function to run in a separate thread
    :param daemon: boolean whether or not to run as a daemon thread
    :return: whatever fx returns
    """
    def _threaded(fx):
        def wrapper(*args, **kwargs):
            thread = ResultThread(target=fx, daemon=daemon,
                                  args=args, kwargs=kwargs)
            thread.start()
            return thread
        return wrapper

    return _threaded


class HiDpiPixmap(QPixmap):
    def __init__(self, arg):
        ratio = QGuiApplication.instance().devicePixelRatio()

        if isinstance(arg, QSize):  # If arg is QSize, create a pixmap with a specific size
            super().__init__(QSize(round(arg.width() * ratio), round(arg.height() * ratio)))
        elif isinstance(arg, str):  # If arg is a filename, create a pixmap from a file
            super().__init__(arg)
        else:  # If no arg is provided, create an empty pixmap
            super().__init__()

        self.setDevicePixelRatio(ratio)

    def _scaled(self, width, height, aspectRatioMode=QtCore.Qt.AspectRatioMode.KeepAspectRatio, transformMode=QtCore.Qt.TransformationMode.SmoothTransformation):
        ratio = self.devicePixelRatio()
        scaled_pixmap = super().scaled(int(width * ratio), int(height * ratio), aspectRatioMode, transformMode)
        scaled_pixmap.setDevicePixelRatio(ratio)
        return scaled_pixmap
