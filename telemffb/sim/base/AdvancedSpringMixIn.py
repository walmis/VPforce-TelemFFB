import json
import logging
import math
import time
from typing import Optional, override

import telemffb.utils as utils
import telemffb.globals as G
from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.utils.conversions import FFB_UNITS

perftracker = utils.PerformanceTracker()


#: The two curves of an advanced spring setting, by the axis each one drives.
ADV_SPR_AXES = ("x", "y")

#: Seconds a curve's input source may deliver nothing, from the first frame
#: that reads it, before the log reports it absent.
ADV_SPR_INPUT_WAIT_S = 5.0


def spring_curve_input_tag(role: Optional[str], axis: str) -> str:
    """The telemetry key a subscribed curve input variable arrives under, for
    example ``SpringCurveInput_joystick_y``.

    The key must differ per device role and per curve.  The X-Plane plugin has
    a single subscription table shared by every TelemFFB instance, keyed by
    this tag, so two instances or two curves using the same tag would
    overwrite and release each other's subscription.
    """
    return f"SpringCurveInput_{role or 'joystick'}_{axis}"


class AdvancedSpringMixIn(GForceEffectMixIn, DynamicSpringMixin):
    """Mixin for the Advanced/Custom spring override (advanced spring trimming and adjuster)."""
    # Class-level default: the settings subsystem may assign adv_spr_gains
    # before __init__ has run.  Replaced, never mutated.
    # Per curve, (input source, range min, range max); None drives the curve
    # by airspeed.  The gain at range min applies until the source first arrives.
    _adv_spr_inputs: dict = {"x": None, "y": None}
    # True from an assignment with an input curve until the next frame logs it
    _adv_spr_inputs_unlogged: bool = False
    # (role, {axis: tag}) for the role the tags were built for; see _adv_spr_input_tag
    _adv_spr_tags: Optional[tuple] = None

    # user parameters
    _spring_mode : SpringModeEnum = SpringModeEnum.NONE

    adv_spr_override_enabled: bool = False   # deprecated
    adv_spr_use_hardware_trim: bool = False

    # override spring trim button bindings and settings
    override_spring_trim_reset: int = 0
    override_spring_trim_down: int = 0
    override_spring_trim_left: int = 0
    override_spring_trim_up: int = 0
    override_spring_trim_right: int = 0
    override_spring_trim_rate: int = 200
    override_spring_cp0_x: float = 0.0
    override_spring_cp0_y: float = 0.0
    # end of user parameters

    @property
    def adv_spr_gains(self) -> Optional[dict]:
        return self._adv_spr_gains

    @adv_spr_gains.setter
    def adv_spr_gains(self, value: str):
        """adv_spr_gains gets assigned with json encoded string or 'none' via settings subsystem, save it as a dict internally"""
        self._adv_spr_held = {"x": None, "y": None}
        if value == 'none':
            self._adv_spr_gains = None
            self._adv_spr_inputs = {"x": None, "y": None}
            return
        self._adv_spr_gains = json.loads(value)
        self._sort_adv_spr_points(self._adv_spr_gains)
        self._adv_spr_inputs = {axis: self._parse_adv_spr_input(self._adv_spr_gains, axis)
                                for axis in ADV_SPR_AXES}
        # Logged with the next frame, which tells an item from a variable
        self._adv_spr_inputs_unlogged = any(entry is not None for entry in self._adv_spr_inputs.values())

    @staticmethod
    def _sort_adv_spr_points(settings) -> None:
        """Order each curve's points by ascending x, as the per-frame lookup
        needs them; a curve on a reversed axis is stored right to left.

        Of points sharing an x only the first is kept: the interpolators
        refuse x values that do not strictly increase, which would raise on
        every frame.  Stored x values are rounded, so two points dragged close
        together can share one."""
        if not isinstance(settings, dict):
            return
        for axis in ADV_SPR_AXES:
            curve = settings.get(f"curve_{axis}")
            points = curve.get("points") if isinstance(curve, dict) else None
            if isinstance(points, list):
                try:
                    points.sort(key=lambda p: p["x"])
                    points[:] = [p for i, p in enumerate(points)
                                 if i == 0 or p["x"] != points[i - 1]["x"]]
                except (KeyError, TypeError):
                    logging.warning(f"Advanced spring: the {axis.upper()} curve has malformed points")

    @staticmethod
    def _parse_adv_spr_input(settings, axis: str) -> Optional[tuple]:
        """A curve's optional ``input`` block as ``(source, min, max)``; None for
        an airspeed curve.

        A curve with the block reads that source's value in the source's own
        units.  A block with an empty source still marks the curve as such: its
        points are not in airspeed units.
        """
        curve = settings.get(f"curve_{axis}") if isinstance(settings, dict) else None
        block = curve.get("input") if isinstance(curve, dict) else None
        if not isinstance(block, dict):
            return None
        source = str(block.get("source") or "").strip()
        try:
            lo = float(block.get("min", 0.0))
        except (TypeError, ValueError):
            lo = 0.0
        try:
            hi = float(block.get("max", lo))
        except (TypeError, ValueError):
            hi = lo
        return source, lo, hi

    def adv_spr_input_source(self, axis: str) -> Optional[str]:
        """The telemetry item or sim variable driving curve ``axis`` (``'x'`` or
        ``'y'``); None for an airspeed curve."""
        entry = self._adv_spr_inputs.get(axis)
        return entry[0] if entry is not None else None

    def adv_spr_input_key(self, axis: str) -> Optional[str]:
        """The telemetry key curve ``axis``'s input source is read from in the
        current frame; None for an airspeed curve."""
        return self._adv_spr_input_key_for(axis, self.telem_data)

    def _adv_spr_input_key_for(self, axis: str, telem_data: Optional[BaseTelemetryData]) -> Optional[str]:
        """A source that names an item of the frame, or one a frame of this
        aircraft has carried, is read under that name; any other source is a sim
        variable, read under this role's tag for the curve."""
        source = self.adv_spr_input_source(axis)
        if source is None:
            return None
        if source and telem_data is not None and (source in telem_data or source in self._adv_spr_items_seen):
            return source
        return self._adv_spr_input_tag(axis)

    def _adv_spr_is_frame_item(self, source: str, telem_data: BaseTelemetryData) -> bool:
        """True when ``source`` names a telemetry item: one this frame carries,
        one the previous frame carried (it also holds the items the effect chain
        writes after the subscription sync), or one any frame of this aircraft
        has carried, which covers items present only in some frames.  Such a
        name is never subscribed as a variable."""
        seen = self._adv_spr_items_seen
        if source in seen:
            return True
        if source in telem_data or source in self._last_telem_data:
            seen.add(source)
            return True
        return False

    def _adv_spr_input_tag(self, axis: str) -> str:
        """This role's :func:`spring_curve_input_tag` for curve ``axis``, built
        once per role."""
        role = getattr(G, "device_type", None)
        tags = self._adv_spr_tags
        if tags is None or tags[0] != role:
            tags = (role, {name: spring_curve_input_tag(role, name) for name in ADV_SPR_AXES})
            self._adv_spr_tags = tags
        return tags[1][axis]

    def _adv_spr_input_value(self, axis: str, telem_data: BaseTelemetryData) -> Optional[float]:
        """Curve ``axis``'s input source value in this frame, or None when the
        frame does not carry it."""
        value = telem_data.get(self._adv_spr_input_key_for(axis, telem_data), None)
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def adv_spr_curve_gain(self, axis: str, telem_data: BaseTelemetryData, airspeed_ms: float) -> float:
        """Curve ``axis``'s gain for this frame.

        An airspeed curve looks up ``airspeed_ms``.  A curve with an input source
        looks up that source's value instead; while the frame does not carry it,
        the last gain computed for the curve stays applied (until the source
        first arrives, the gain at the input range's minimum).
        """
        entry = self._adv_spr_inputs.get(axis)
        if entry is None:
            return utils.get_curve_gain(self._adv_spr_gains, axis, airspeed_ms)
        value = self._adv_spr_input_value(axis, telem_data)
        if entry[0] not in self._adv_spr_input_arrived:
            self._watch_adv_spr_input(axis, entry[0], value)
        gain = self._adv_spr_input_gain(axis, entry, value)
        self._adv_spr_held[axis] = gain
        return gain

    def _adv_spr_curve_gain_now(self, axis: str, telem_data: BaseTelemetryData, airspeed_ms: float) -> float:
        """:meth:`adv_spr_curve_gain` without storing or logging anything."""
        entry = self._adv_spr_inputs.get(axis)
        if entry is None:
            return utils.get_curve_gain(self._adv_spr_gains, axis, airspeed_ms)
        return self._adv_spr_input_gain(axis, entry, self._adv_spr_input_value(axis, telem_data))

    def _adv_spr_input_gain(self, axis: str, entry: tuple, value: Optional[float]) -> float:
        """Input curve ``axis``'s gain for its source ``value``, which is None
        while the frame does not carry it."""
        if value is not None:
            return utils.get_curve_gain(self._adv_spr_gains, axis, value)
        held = self._adv_spr_held.get(axis)
        if held is not None:
            return held
        return utils.get_curve_gain(self._adv_spr_gains, axis, entry[1])

    def _watch_adv_spr_input(self, axis: str, source: str, value: Optional[float]) -> None:
        """Log the first value of an input source, or its absence for
        :data:`ADV_SPR_INPUT_WAIT_S` from the first frame that read it; each once
        per source for this aircraft."""
        if not source:
            return
        if value is not None:
            self._adv_spr_input_arrived.add(source)
            logging.info(f"Advanced spring: {axis.upper()} curve input '{source}' first value {value:g}")
            return
        if source in self._adv_spr_input_warned:
            return
        now = time.monotonic()
        since = self._adv_spr_input_waits.setdefault(source, now)
        if now - since > ADV_SPR_INPUT_WAIT_S:
            self._adv_spr_input_warned.add(source)
            logging.warning(f"Advanced spring: {axis.upper()} curve input '{source}' has delivered no value "
                            f"in {ADV_SPR_INPUT_WAIT_S:g} s; the curve holds the gain at its range minimum "
                            f"until it does")

    def _log_adv_spr_inputs(self, telem_data: BaseTelemetryData) -> None:
        """Log each input curve's source, range and whether ``telem_data``
        makes the source an item or a variable."""
        for axis in ADV_SPR_AXES:
            entry = self._adv_spr_inputs.get(axis)
            if entry is None:
                continue
            source, lo, hi = entry
            kind = "variable" if source and self._adv_spr_source_is_variable(source, telem_data) else "item"
            logging.info(f"Advanced spring: {axis.upper()} curve follows '{source}' ({kind}, range {lo} to {hi})")

    def _adv_spr_source_is_variable(self, source: str, telem_data: BaseTelemetryData) -> bool:
        """True when ``source`` is read as a subscribed sim variable rather than
        as an item of the frame.  Only the sims whose variables can be
        subscribed at runtime override it."""
        return False

    def adv_spr_curve_gains(self, telem_data: BaseTelemetryData, airspeed_ms: float) -> dict:
        """Both curves' ``{"x": ..., "y": ...}`` gains for this frame, each against
        its own input (see :meth:`adv_spr_curve_gain`)."""
        inputs = self._adv_spr_inputs
        if inputs["x"] is None and inputs["y"] is None:
            return utils.get_gain_from_speed(self._adv_spr_gains, airspeed_ms)
        return {"x": self.adv_spr_curve_gain("x", telem_data, airspeed_ms),
                "y": self.adv_spr_curve_gain("y", telem_data, airspeed_ms)}

    def adv_spr_curve_gains_now(self, telem_data: BaseTelemetryData, airspeed_ms: float) -> dict:
        """The gains :meth:`adv_spr_curve_gains` gives for ``telem_data``, without
        storing anything: a curve whose source the frame does not carry reads the
        gain the telemetry thread holds for it.  For callers outside the telemetry
        thread."""
        inputs = self._adv_spr_inputs
        if inputs["x"] is None and inputs["y"] is None:
            return utils.get_gain_from_speed(self._adv_spr_gains, airspeed_ms)
        return {axis: self._adv_spr_curve_gain_now(axis, telem_data, airspeed_ms) for axis in ADV_SPR_AXES}

    def _adv_spr_input_var(self, axis: str, telem_data: BaseTelemetryData) -> Optional[str]:
        """The sim variable to subscribe under this role's tag for curve ``axis``:
        its input source while the advanced spring is in effect and the frame
        does not carry the source as an item of its own; otherwise None."""
        source = self.adv_spr_input_source(axis)
        if not source or not self.spring_mode_is(SpringModeEnum.ADVANCED):
            return None
        if self._adv_spr_is_frame_item(source, telem_data):
            return None
        return source

    def _sync_spring_curve_input(self, axis: str, var: Optional[str]) -> None:
        """Keep ``var`` subscribed under this role's tag for curve ``axis``; None
        releases it.

        Called whenever the variable a curve wants differs from the one last
        passed for it, which starts as None.  Only the sims whose variables can
        be subscribed at runtime override it; elsewhere a source can only be a
        telemetry item.
        """

    @property
    def spring_mode(self) -> SpringModeEnum:
        return self._spring_mode

    @spring_mode.setter
    def spring_mode(self, value):
        # Accept None, enum instances, and valid enum member names (strings).
        if value is None:
            self._spring_mode = SpringModeEnum.NONE
            return

        # Enum instance -> accept
        if isinstance(value, SpringModeEnum):
            self._spring_mode = value
            return

        # String -> map to enum if valid name
        if isinstance(value, str):
            if value in SpringModeEnum.__members__:
                self._spring_mode = SpringModeEnum[value]
                return
            # Unknown name: a config written by a build that has modes this
            # one does not.  Fall back to the least intrusive mode instead of
            # raising - the exception would abort the aircraft load, and with
            # it the settings form needed to correct the value.  The stored
            # value is left untouched so it still resolves under the build
            # that wrote it.
            logging.error(
                f"Unknown spring mode '{value}' in this aircraft's configuration - "
                f"falling back to {SpringModeEnum.NONE.name} (game managed). "
                "Choose a spring mode in the settings to replace it."
            )
            self._spring_mode = SpringModeEnum.NONE
            return

        # Any other type is invalid
        raise ValueError("Invalid type for spring_mode")

    def __init__(self, *args, **kwargs):
        # Ensure cooperative init ordering
        super().__init__(*args, **kwargs)
        self.__firmware_supported = None
        self._adv_spr_gains: Optional[dict] = None
        self._adv_spr_inputs = {"x": None, "y": None}
        self._adv_spr_inputs_unlogged = False
        # Input sources whose first value has been logged
        self._adv_spr_input_arrived: set = set()
        # Input sources whose absence has been logged
        self._adv_spr_input_warned: set = set()
        # Input source -> monotonic time of the first frame that read it without a value
        self._adv_spr_input_waits: dict = {}
        # per curve, the gain applied while its input source is absent from the frame
        self._adv_spr_held: dict = {"x": None, "y": None}
        # per curve, the variable last passed to _sync_spring_curve_input
        self._adv_spr_synced_vars: dict = {"x": None, "y": None}
        # Input sources a frame of this aircraft has carried; see _adv_spr_is_frame_item
        self._adv_spr_items_seen: set = set()

        # per-instance state used by advanced spring override
        self.adv_spr_settings_dict: dict = {}
        # condition objects and adjuster used by the advanced spring override
        self.spring_adjuster_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_adjuster_y = FFBReport_SetCondition(parameterBlockOffset=1)

    def spring_mode_is(self, mode : SpringModeEnum):
        return mode == self.spring_mode

    @staticmethod
    def _adv_spr_device_has_adjuster() -> bool:
        caps = getattr(HapticEffect.device, 'caps', None)
        return caps is None or caps.has_spring_adjuster

    def _tap_feeds_advanced_spring(self) -> bool:
        """A joystick in ADVANCED whose game spring only the tap can supply:
        the device has no spring adjuster, or the tap is capturing it."""
        if not (self.is_joystick() and self.spring_mode_is(SpringModeEnum.ADVANCED)):
            return False
        if not self._adv_spr_device_has_adjuster():
            return True
        from telemffb.hw import ffb_tap
        return ffb_tap.device_is_tapped()

    def _adv_spr_via_adjuster(self) -> bool:
        """Whether the spring adjuster renders this mode.  Otherwise a spring
        TelemFFB owns carries the curve: the tap spring on the joystick, the
        pedal spring on pedals."""
        return self._adv_spr_device_has_adjuster() and not self._tap_feeds_advanced_spring()

    def ac_modify_game_spring(self):
        if not self.spring_mode_is(SpringModeEnum.ADVANCED):
            self.effects['adv_spr'].stop()
            return
        via_adjuster = self._adv_spr_via_adjuster()
        if via_adjuster:
            if self._sim_is_il2():
                # the adjuster needs a spring on the device to act on
                if not self.adv_spr_use_hardware_trim:
                    self.il2_ffb_spring(force=True)
                else:
                    self.effects['il2_ffb_spring'].stop()
            # Verify the device firmware meets the minimum version required to execute this effect
            # Flag error and abort if not met
            if self.__firmware_supported is None:
                self.__firmware_supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
            if not self.__firmware_supported:
                self.flag_error('The Advanced/Custom Spring Override requires firmware v1.0.18 or higher.\n'
                                f'The device is currently running version {G.device_firmware_version}\n'
                                f'Please update your device firmware!')
                return
        if not self.adv_spr_gains:
            self.flag_error('Please open and configure the advanced spring gain settings')
            return

        gains = self.adv_spr_curve_gains(self.telem_data, self.telem_data.IAS or 0)

        if via_adjuster:
            self.spring_adjuster_y.set_coefficient(gains.get('y', 0))
            self.spring_adjuster_x.set_coefficient(gains.get('x', 0))

        if self.adv_spr_use_hardware_trim:
            dt = perftracker.get_time_delta('override_spring_perf')
            trim_step_size = self.override_spring_trim_rate * dt / FFB_UNITS
            self.telem_data._ovrd_spr_step = trim_step_size
            self.telem_data._ovrd_spr_dt = dt
            # evaluate UP or DOWN and then LEFT or RIGHT trims.  Allows movement on both axes simultaneously but not
            # accidental confliction of trying to move both directions on a single axis due to bad hat bindings
            report = self._get_device_report()
            x, y = self._get_device_axes()
            if self.check_button_press(self.override_spring_trim_reset, report=report):
                self.override_spring_cp0_x = 0.0
                self.override_spring_cp0_y = 0.0

            if self.check_button_press(self.override_spring_trim_down, report=report):
                self.override_spring_cp0_y -= trim_step_size
            elif self.check_button_press(self.override_spring_trim_up, report=report):
                self.override_spring_cp0_y += trim_step_size

            if self.check_button_press(self.override_spring_trim_left, report=report):
                self.override_spring_cp0_x -= trim_step_size
            elif self.check_button_press(self.override_spring_trim_right, report=report):
                self.override_spring_cp0_x += trim_step_size

            self.override_spring_cp0_x = utils.clamp(self.override_spring_cp0_x, -1.0, 1.0)
            self.override_spring_cp0_y = utils.clamp(self.override_spring_cp0_y, -1.0, 1.0)
        else:
            self.override_spring_cp0_x = 0.0
            self.override_spring_cp0_y = 0.0

        offset = super().ac_update_gforce_effect(self.telem_data, adv_spr=True)  # Returns g force spring offset if effect enabled and in offset mode
        g_y_offset = offset if offset is not None else 0.0
        self.telem_data._ovrd_spr_trim_pos = [
            self.override_spring_cp0_x,
            self.override_spring_cp0_y,
            g_y_offset,
        ]
        if not via_adjuster:
            if self.is_joystick():
                self.ffb_tap_advanced_spring(gains.get('x', 0), gains.get('y', 0),
                                             self.override_spring_cp0_x,
                                             self.override_spring_cp0_y + g_y_offset)
            # pedals: ac_override_pedal_spring carries the curve and trim
            return

        self.spring_adjuster_y.set_offset(self.override_spring_cp0_y + g_y_offset)
        self.spring_adjuster_x.set_offset(self.override_spring_cp0_x)

        adjuster = self.effects['adv_spr'].spring_adjuster()
        adjuster.setCondition(self.spring_adjuster_y)
        adjuster.setCondition(self.spring_adjuster_x)
        adjuster.start()

    @override
    def ac_update_gforce_effect(self, telem_data: BaseTelemetryData, adv_spr: bool = False) -> Optional[float]:
        """If advanced spring is enabled and in offset mode, handle gforce effect as part of advanced spring processing.
        else defer to GForceEffectMixIn implementation."""
        if not (self.gforce_effect_mode_is(GEffectModeEnum.ADVANCED) and self.g_effect_get_adv_mode() == "offset"):
            return super().ac_update_gforce_effect(telem_data, adv_spr=adv_spr)

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        if self._adv_spr_inputs_unlogged:
            self._adv_spr_inputs_unlogged = False
            self._log_adv_spr_inputs(telem_data)
        synced = self._adv_spr_synced_vars
        for axis in ADV_SPR_AXES:
            var = self._adv_spr_input_var(axis, telem_data)
            if var != synced[axis]:
                self._sync_spring_curve_input(axis, var)
                synced[axis] = var
        self.ac_modify_game_spring()
