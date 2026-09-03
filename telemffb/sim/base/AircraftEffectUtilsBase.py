import telemffb.globals as G
import telemffb.utils as utils
from telemffb.globals import master_instance
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

import logging
import random
import time


class AircraftEffectUtilsBase(object):
    """Base class for aircraft effects and utilities."""
    def __init__(self):
        super().__init__()
        self._changes = {}
        self._change_counter = {}
        self._ipc_telem = {}
        self.stepper_dict = {}
        self.spring_mode = None
        self.gforce_effect_mode = None
        self._telem_data: BaseTelemetryData = BaseTelemetryData()
        self._last_telem_data: BaseTelemetryData = BaseTelemetryData()
        self._ipc_telem = {}

    @property
    def effects(self) -> utils.Dispenser:
        # Return global effects dispenser from globals to avoid circular imports
        return G.effects

    @property
    def telem_data(self) -> BaseTelemetryData:
        return self._telem_data

    @property
    def aircraft_type(self):
        """Return the type of aircraft based on telemetry data."""
        if self._telem_data:
            return self._telem_data.AircraftType or "Unknown"
        return "Unknown"

    def check_master_button_press(self, button):
        # print(f"Checking {button} against {master_buttons}")
        return button in G.master_buttons

    def check_button_press(self, button=0, check_master=False):
        if not button:
            #button not set
            return False
        if check_master:
            return self.check_master_button_press(button)
        else:
            input_data = self._get_device_report()
            return input_data is not None and input_data.isButtonPressed(button)

    def step_value_over_time(self, key, value, timeframe_ms, dst_val, floatpoint=False):
        '''
        This function creates an entry in the  stepper dictionary which can be used to track the progress of driving a
        value from "a to b" over a period of time across multiple passes through the effects loop.
        '''

        if value == dst_val:
            return value

        current_time_ms = time.perf_counter() * 1000  # Start time for the current step
        # current_time_end = current_time_start  # End time for the current step (initially the same as start time)

        # add a new key to the dictionary if one does not exist and initialize the tracking variables
        if key not in self.stepper_dict:
            self.stepper_dict[key] = {
                'value': value,
                'dst_val': dst_val,
                'start_time': current_time_ms,
                'end_time': current_time_ms + timeframe_ms,
                'timeframe': timeframe_ms,
                'last_iteration_ms': current_time_ms
            }
            return value
        else:
            # if it already exists, but the dst_value has changed, the condition probably changed before the timer expired, so reset the key to new condition
            if self.stepper_dict[key]['dst_val'] != dst_val:
                self.stepper_dict[key] = {
                    'value': value,
                    'dst_val': dst_val,
                    'start_time': current_time_ms,
                    'end_time': current_time_ms + timeframe_ms,
                    'timeframe': timeframe_ms,
                    'last_iteration_ms': current_time_ms
                }
                return value

        data = self.stepper_dict[key]

        iteration_ms = current_time_ms - data['last_iteration_ms']  # calculate time since last iteration

        data['last_iteration_ms'] = current_time_ms  # reset iteration timestamp to current timestamp

        delta_to_go = data['dst_val'] - data['value']  # calculate distance left to move the value
        time_to_go = data['end_time'] - current_time_ms  # calculate time left to move the value to destination

        step_size = (iteration_ms / time_to_go) * delta_to_go  # calculate step size to reach target at time

        if ((data['dst_val'] - data['value']) * (data['dst_val'] - (data['value'] + step_size))) <= 0:
            # We crossed the target
            data['value'] = data['dst_val']
            del self.stepper_dict[key]
            return data['dst_val']

        if data['value'] == data['dst_val']:  # if we have reached the dst value, delete the key and return the value
            del self.stepper_dict[key]
            return data['value']

        elapsed_time_ms = (current_time_ms - data['start_time'])

        if elapsed_time_ms >= timeframe_ms:  # if the elapsed time is greater than the given timeframe, return the destination value
            data['value'] = data['dst_val']
            del self.stepper_dict[key]
            return data['dst_val']

        val = value + step_size

        if not floatpoint:  # if floatpoint is not specified, return a rounded integer value
            val = round(val)
        data['value'] = val
        # print(f"value out = {data['value']}")
        return data['value']

    def apply_settings(self, settings_dict):
        """Apply settings from a configuration dictionary to the aircraft instance.

        Args:
            settings_dict (dict): Dictionary containing configuration key-value pairs.
                                Keys should match aircraft attribute names.

        Logs warnings for unknown parameters and info for each applied setting.
        """
        logging.info(f"Applying settings...")
        for k, v in settings_dict.items():
            if k in ["type"]: continue
            if k.endswith("_group"): continue
            if hasattr(self, k) is None and k != 'vpconf' and 'dummy' not in k and 'command_runner' not in k:
                logging.warning(f"Trying to assign unknown parameter {k} ")
                continue
            logging.info(f" [cyan]set[/cyan]: {k} = {v}")
            try:
                setattr(self, k, v)
            except Exception:
                # A setting that refuses its stored value must not take the
                # rest of the aircraft down with it: the exception would
                # escape the aircraft load, leaving a half-configured
                # aircraft and no settings form to correct the value from.
                logging.error(
                    f"Could not apply setting '{k}' = {v!r}; it keeps its "
                    "default for this session. Review this setting in the "
                    "aircraft configuration.",
                    exc_info=True,
                )

    def has_changed(self, item: str, delta_ms=0, data=None) -> bool:
        """Check if a telemetry data item has changed since last call.

        Args:
            item (str): Name of the telemetry data item to check
            delta_ms (int, optional): Time window in milliseconds to consider as "recently changed". Defaults to 0.
            data (dict, optional): Telemetry data dictionary to use. Defaults to self._telem_data.

        Returns:
            bool: True if the item changed, False otherwise.
                 If the item changed, returns a tuple (prev_val, new_val) instead.
        """
        if data == None:
            data = self._telem_data

        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = data.get(item)

        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter())

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False

    def flag_error(self, message):
        """Flag an error message for display in the UI.

        Args:
            message (str): Error message to display
        """
        dev = (self.telem_data.FFBType or 'joystick').capitalize()
        self.telem_data.error = message
        if not master_instance:
            self._ipc_telem['error'] = f"{dev}: {message}"

    def is_joystick(self):
        """Check if the current FFB device is a joystick.

        Returns:
            bool: True if device is a joystick, False otherwise
        """
        return (self._telem_data.FFBType or "joystick") == "joystick"

    def is_pedals(self):
        """Check if the current FFB device is pedals.

        Returns:
            bool: True if device is pedals, False otherwise
        """
        return self._telem_data.FFBType == "pedals"

    def is_collective(self):
        """Check if the current FFB device is a collective.

        Returns:
            bool: True if device is a collective, False otherwise
        """
        return self._telem_data.FFBType == "collective"

    def is_trimwheel(self):
        """Check if the current FFB device is a trim wheel.

        Returns:
            bool: True if device is a trim wheel, False otherwise
        """
        return self._telem_data.FFBType == "trimwheel"


    def anything_has_changed(self, item: str, value, delta_ms=0):
        """track if any parameter, given as key "item" has changed between two consecutive calls of the function
        delta_ms can be used to smooth the effects of telemetry which does not update regularly but is still "moving"
        a positive delta_ms value will allow the data to remain unchanged for that period of time before returning false"""

        prev_val, tm, changed_yet = self._changes.get(item, (None, 0, 0))
        new_val = value
        new_tm = time.perf_counter()
        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        # make sure we do not return true until the key has changed at least once (after init)
        if prev_val == None and not changed_yet:
            self._changes[item] = (new_val, tm, 0)
            prev_val = new_val

        # logging.debug(f"Prev: {prev_val}, New: {new_val}, TM: {tm}")

        if prev_val != new_val:
            self._changes[item] = (new_val, new_tm, 1)

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val, new_tm - tm)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False

    def _sim_is_msfs(self, *unused):
        """Check if the current simulator is Microsoft Flight Simulator.

        Returns:
            bool: True if MSFS, False otherwise
        """
        return self._telem_data.src == "MSFS"

    def _sim_is_xplane(self):
        """Check if the current simulator is X-Plane.

        Returns:
            bool: True if X-Plane, False otherwise
        """
        return self._telem_data.src == "XPLANE"

    def _sim_is_dcs(self, *unused):
        """Check if the current simulator is DCS World.

        Returns:
            bool: True if DCS, False otherwise
        """
        return self._telem_data.src == "DCS"

    def _sim_is_bms(self, *unused):
        """
        Check if the current simulator is BMS.

        Returns:
            bool: True if DCS, False otherwise
        """
        return self._telem_data.src == "BMS"

    def _sim_is_il2(self, *unused):
        """Check if the current simulator is IL2 Sturmovik..

                Returns:
                    bool: True if IL2, False otherwise
                """
        return self._telem_data.src == "IL2"

    def _sim_is(self, sim, *unused):
        """Check if the current simulator matches the specified name.

        Args:
            sim (str): Simulator name to check against

        Returns:
            bool: True if matches, False otherwise
        """
        return self._telem_data.src == sim

    # Helper methods for code reuse
    @staticmethod
    def _get_device_axes() -> tuple[float, float]:
        device = HapticEffect.device
        if device is None:
            return 0.0, 0.0
        input_data = device.get_input()
        if input_data is None:
            return 0.0, 0.0
        return input_data.axisXY()

    @staticmethod
    def _get_device_CP_XY() -> tuple[float, float]:
        device = HapticEffect.device
        if device is None:
            return 0.0, 0.0
        input_data = device.get_input()
        if input_data is None:
            return 0.0, 0.0
        return input_data.CP_XY()

    @staticmethod
    def _get_device_raw_axes() -> tuple[float, float]:
        device = HapticEffect.device
        if device is None:
            return 0.0, 0.0
        input_data = device.get_input()
        if input_data is None:
            return 0.0, 0.0
        if device.supports_axis_override():
            return input_data.rawAxisXY()
        return input_data.axisXY()

    @staticmethod
    def _get_device_report():
        device = HapticEffect.device
        if device is None:
            return None
        return device.get_input()
    
    @staticmethod
    def _get_device_forces() -> tuple[float, float]:
        device = HapticEffect.device
        if device is None:
            return 0.0, 0.0
        input_data = device.get_input()
        if input_data is None:
            return 0.0, 0.0
        return input_data.forceXY()

    def _device_feeding(self) -> bool:
        """Whether the FFB device is connected and delivering HID input.

        Axis overrides must never be claimed without a live device: the
        plugin pins the virtual yoke/rudder to whatever we send, so a dead
        instance would feed zeros/stale values and silence the user's real
        controller. Re-checked every frame, so a hot-unplug releases the
        override and a reconnect reclaims it.
        """
        device = HapticEffect.device
        if device is None or not device.connected:
            return False
        return device.get_input() is not None

    def _get_random_direction(self):
        """Get a random direction for weapon effects based on device type."""
        import random
        random.seed(time.perf_counter())
        if self.is_pedals():
            return random.choice([90, 270])
        return random.randint(0, 359)

    def _get_effect_direction(self, configured_direction: int) -> int:
        """Get effect direction, either configured or random based on settings."""
        if configured_direction == -1:
            return self._get_random_direction()
        return configured_direction

    def _should_skip_joystick_effect(self) -> bool:
        """Common check for joystick-only effects."""
        return not self.is_joystick()

    def _should_skip_airborne_effect(self, telem_data: BaseTelemetryData) -> bool:
        """Common check for effects that should be disabled when on ground."""
        if self._sim_is("IL2"):
            # Some IL-2 aircraft report non-zero gear pressure (WeightOnWheels) while airborne
            # after retraction (sim bug). Use gear extraction state as the authoritative gate:
            # if all gear are fully retracted, treat as airborne regardless of pressure.
            gear_state = telem_data.GearPos
            if gear_state and max(gear_state) < 0.1:
                return False
        return bool(sum(telem_data.WeightOnWheels or [0]))

    def _should_skip_no_airspeed_effect(self, telem_data: BaseTelemetryData) -> bool:
        """Common check for effects that require airspeed."""
        return not (telem_data.TAS or 0)

    def _is_telemetry_spike(self, y_gs: float, last_y_gs: float, threshold: float = 3.0) -> bool:
        """Check if telemetry shows a spike indicating crash or invalid data."""
        return abs(y_gs - last_y_gs) > threshold
    

    def is_jet_aircraft(self):
        """Check if the current aircraft is a jet.

        Returns:
            bool: True if jet, False otherwise
        """
        return (self._telem_data.AircraftClass or "") == "JetAircraft"
    
    def is_propeller_aircraft(self):
        """Check if the current aircraft is a propeller aircraft.

        Returns:
            bool: True if propeller aircraft, False otherwise
        """
        return (self._telem_data.AircraftClass or "") == "PropellerAircraft"

    def is_helicopter(self):
        """Check if the current aircraft is a helicopter.

        Returns:
            bool: True if helicopter, False otherwise
        """
        return (self._telem_data.AircraftClass or "") == "Helicopter"
    
    

    def on_timeout(self):
        """Each mixin can implement this to handle telemetry timeout events. important: super().on_timeout() must be called in subclasses."""
        pass

    def on_telemetry(self, telem_data: BaseTelemetryData):
        """Each mixin can implement this to handle telemetry events. important: super().on_telemetry() must be called in subclasses."""
        pass

    def on_event(self, event, *args):
        """Each mixin can implement this to handle input events. important: super().on_event() must be called in subclasses."""
        pass