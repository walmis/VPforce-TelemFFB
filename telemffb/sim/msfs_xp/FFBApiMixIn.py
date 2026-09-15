#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
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
"""Rig-side implementation of the standardized MSFS Helicopter FFB LVAR API.

Companion spec: https://github.com/CK-AT/DIY-FFB/blob/ck_dev/Standards/MSFS_Helicopter_FFB_API.md
Implementation plan: ``docs/ffb_simvar_api_implementation.md``

The aircraft publishes a small, vendor-neutral set of ``L:FFB_*`` LVARs describing
trim actuator position, trim-release state and hydraulic assist loss.  The rig writes
back a per-control mode flag and per-axis fly-through detection.  This mixin owns both
directions for the MSFS backend.

Design notes
------------
* **Mode gated.** Nothing is read or written until ``L:FFB_API_VERSION >= 1`` is
  latched, and the per-axis read values are only consumed while this control's
  ``L:FFB_<CONTROL>_ENABLED`` is 1 (spec 3.4).
* **Capability gated.** The trim spring exists only when this control's
  ``L:FFB_FEATURES`` bit is set (spec 3.1).  Hydraulics and fly-through are not
  feature-gated.
* **Inert by default.** Every entry point returns immediately when the API is not
  active, so all six vendor helicopter classes inherit this at zero cost.
"""

import logging
import time

from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.utils import clamp

# --- Controls and axes (spec 3.1 / 3.3) ------------------------------------- #

FFB_API_CYCLIC = "CYCLIC"
FFB_API_COLLECTIVE = "COLLECTIVE"
FFB_API_PEDALS = "PEDALS"

#: Bit position in ``L:FFB_FEATURES`` carrying "this control has a trim system".
FFB_API_TRIM_FEATURE_BIT = {
    FFB_API_CYCLIC: 0,
    FFB_API_COLLECTIVE: 1,
    FFB_API_PEDALS: 2,
}

#: Axis names owned by each control.  Cyclic covers two axes, the others one.
FFB_API_CONTROL_AXES = {
    FFB_API_CYCLIC: ("CYCLIC_PITCH", "CYCLIC_ROLL"),
    FFB_API_COLLECTIVE: ("COLLECTIVE",),
    FFB_API_PEDALS: ("PEDALS",),
}

# --- Subscriptions ---------------------------------------------------------- #

#: Discovery variables.  Static per aircraft: latched once, never consumed per frame.
FFB_API_DISCOVERY_VARS = (
    ("ffbApiVersion", "L:FFB_API_VERSION", "number"),
    ("ffbFeatures", "L:FFB_FEATURES", "number"),
)

#: Runtime variables.  Per frame, and only meaningful while ENABLED == 1.
FFB_API_READ_VARS = (
    ("ffbTrimCyclicPitch", "L:FFB_CYCLIC_PITCH_TRIM", "number"),
    ("ffbTrimCyclicRoll", "L:FFB_CYCLIC_ROLL_TRIM", "number"),
    ("ffbTrimCollective", "L:FFB_COLLECTIVE_TRIM", "number"),
    ("ffbTrimPedals", "L:FFB_PEDALS_TRIM", "number"),
    ("ffbHydLossCyclicPitch", "L:FFB_CYCLIC_PITCH_HYD_ASSIST_LOSS", "number"),
    ("ffbHydLossCyclicRoll", "L:FFB_CYCLIC_ROLL_HYD_ASSIST_LOSS", "number"),
    ("ffbHydLossCollective", "L:FFB_COLLECTIVE_HYD_ASSIST_LOSS", "number"),
    ("ffbHydLossPedals", "L:FFB_PEDALS_HYD_ASSIST_LOSS", "number"),
    ("ffbTrOnCyclic", "L:FFB_CYCLIC_TR_ON", "bool"),
    ("ffbTrOnCollective", "L:FFB_COLLECTIVE_TR_ON", "bool"),
    ("ffbTrOnPedals", "L:FFB_PEDALS_TR_ON", "bool"),
)

#: Telemetry field carrying each axis' trim actuator position.
FFB_API_TRIM_FIELD = {
    "CYCLIC_PITCH": "ffbTrimCyclicPitch",
    "CYCLIC_ROLL": "ffbTrimCyclicRoll",
    "COLLECTIVE": "ffbTrimCollective",
    "PEDALS": "ffbTrimPedals",
}

#: Telemetry field carrying each axis' hydraulic assist loss.
FFB_API_HYD_FIELD = {
    "CYCLIC_PITCH": "ffbHydLossCyclicPitch",
    "CYCLIC_ROLL": "ffbHydLossCyclicRoll",
    "COLLECTIVE": "ffbHydLossCollective",
    "PEDALS": "ffbHydLossPedals",
}

#: Telemetry field carrying each control's trim-release (unclutched) state.
FFB_API_TR_FIELD = {
    FFB_API_CYCLIC: "ffbTrOnCyclic",
    FFB_API_COLLECTIVE: "ffbTrOnCollective",
    FFB_API_PEDALS: "ffbTrOnPedals",
}

#: Re-assert ENABLED at least this often even when unchanged (ms).
FFB_API_ENABLED_HEARTBEAT_MS = 1000

#: Reserved pre-API names TelemFFB writes from the legacy hands-on path.  Listed here
#: only so the exception to "write no L:FFB_* var when unsupported" is discoverable
#: from the code as well as from spec 3.6.
FFB_API_RESERVED_LEGACY_LVARS = (
    "L:FFB_HANDS_ON_CYCLIC",
    "L:FFB_HANDS_ON_CYCLICX",
    "L:FFB_HANDS_ON_CYCLICY",
    "L:FFB_FEET_ON_PEDALS",
)


class FFBApiMixIn:
    """Consume and produce the standardized ``L:FFB_*`` helicopter API."""

    # ------------------------------------------------------------------ #
    # user parameters
    # ------------------------------------------------------------------ #

    #: Master switch.  When False the rig stays in legacy mode and writes nothing.
    ffb_api_enable: bool = True

    #: Spring gain applied while the trim actuator is unclutched (TR_ON).
    ffb_api_tr_spring_gain: float = 0.0

    #: Deviation from the trim reference that latches fly-through, as a fraction of
    #: full axis travel.  ``_release_`` is the (smaller) value used to clear it, giving
    #: hysteresis so a stick resting near the threshold does not chatter.
    ffb_api_fly_through_deadzone: float = 0.1
    ffb_api_fly_through_release_deadzone: float = 0.05

    #: True = publish genuine per-axis fly-through (the spec's shape, default).
    #: False = detect per control and mirror the result onto every axis of that
    #: control, for hardware that cannot separate cyclic pitch from roll.
    ffb_api_individual_fly_through: bool = True

    #: Sign conversion from the spec's convention to device axes (spec 3.5:
    #: positive = pitch nose-up / roll right / collective up / right pedal).
    #: Collective defaults inverted because TelemFFB's device y is +1 at full down.
    #: The cyclic and pedal flags exist so a sign error is a config change rather than
    #: a code change - they need confirming against real hardware.
    ffb_api_invert_cyclic_pitch: bool = False
    ffb_api_invert_cyclic_roll: bool = False
    ffb_api_invert_collective: bool = True
    ffb_api_invert_pedals: bool = False

    # end user parameters

    def __init__(self, *args, **kwargs):
        # cooperative init for mixin ordering
        super().__init__(*args, **kwargs)
        self._ffb_api_reset()

    # ------------------------------------------------------------------ #
    # lifecycle: discovery, latching, mode gating
    # ------------------------------------------------------------------ #

    def _ffb_api_reset(self):
        """Clear all latched API state.  Called on construction and on timeout."""
        self._ffb_api_latched = False
        self._ffb_api_version = 0
        self._ffb_api_features = 0
        self._ffb_api_enabled_state = None
        self._ffb_api_enabled_last_write_ms = 0
        self._ffb_api_spring_init = 0
        self._ffb_api_tr_active = False
        self._ffb_api_fly_through_state = {}
        self._ffb_api_warned_no_features = False

    def subscribe_ffb_api_simvars(self):
        """Subscribe the discovery and runtime read variables.

        Subscription is unconditional for MSFS helicopters: discovery must work for
        any aircraft, including one with no config entry, so this deliberately does
        not ride ``sc_overrides``.  An LVAR the aircraft does not define reads as 0
        (or stays None), which is exactly the "not implemented" sentinel.

        Called every frame from :meth:`ffb_api_on_telemetry` and guarded on
        ``sv_dict``, because a SimConnectManager subscription added at runtime lives
        in ``temp_sim_vars``, which is cleared by the subscribe cycle that consumes
        it - a later ``_resubscribe()`` from anywhere rebuilds from the predefined
        list alone and drops these vars.  Re-checking is how they come back.
        """
        if not self._simconnect:
            return
        changed = False
        for name, var, unit in FFB_API_DISCOVERY_VARS + FFB_API_READ_VARS:
            if name not in self._simconnect.sv_dict:
                self._simconnect.add_simvar(name=name, var=var, sc_unit=unit)
                changed = True
        if changed:
            self._simconnect._resubscribe()

    def _ffb_api_latch_discovery(self, telem_data: BaseTelemetryData):
        """Latch API version and capability bits once per aircraft (spec 4, steps 1-2).

        Telemetry:
            Read: ffbApiVersion - Optional[float]; L:FFB_API_VERSION.  None until the
                                  subscription lands; 0 = not implemented, or not
                                  published yet - see below.
                  ffbFeatures   - Optional[float]; L:FFB_FEATURES bitfield.
        """
        if self._ffb_api_latched:
            return

        version = telem_data.get("ffbApiVersion", None)
        if version is None or int(round(version)) < 1:
            # Not implemented, or not implemented *yet*.  MSFS reads an LVAR the
            # aircraft has not created as 0, exactly like one it never creates, and
            # the subscription usually lands a few frames before the aircraft's own
            # init publishes FFB_API_VERSION.  Latching that first 0 would mark a
            # supported aircraft unsupported for the rest of the flight, so stay
            # unlatched and keep looking - an aircraft that never publishes simply
            # stays unsupported forever, which is correct.
            return

        self._ffb_api_latched = True
        self._ffb_api_version = int(round(version))

        features = telem_data.get("ffbFeatures", 0) or 0
        self._ffb_api_features = int(round(features))

        logging.info(
            f"FFB API detected: version={self._ffb_api_version} "
            f"features=0b{self._ffb_api_features:b} "
            f"(cyclic_trim={self._ffb_api_has_trim(FFB_API_CYCLIC)}, "
            f"collective_trim={self._ffb_api_has_trim(FFB_API_COLLECTIVE)}, "
            f"pedals_trim={self._ffb_api_has_trim(FFB_API_PEDALS)})"
        )
        if not self._ffb_api_features and not self._ffb_api_warned_no_features:
            # Legal per spec, but far more often a forgotten FFB_FEATURES than a
            # genuinely untrimmed helicopter.  Say so once, or it gets reported as
            # "trim doesn't work".
            logging.warning(
                "FFB API: aircraft reports API_VERSION >= 1 but FFB_FEATURES = 0 - "
                "no control advertises a trim system, so no trim spring will be "
                "applied.  If the aircraft does have trim, it is not publishing "
                "L:FFB_FEATURES."
            )
            self._ffb_api_warned_no_features = True

    def _ffb_api_control(self):
        """Return the API control name this device instance owns, or None.

        TelemFFB runs one process per device, so each instance owns exactly one
        control and there is no cross-instance coordination.
        """
        if self.is_joystick():
            return FFB_API_CYCLIC
        if self.is_collective():
            return FFB_API_COLLECTIVE
        if self.is_pedals():
            return FFB_API_PEDALS
        return None

    def _ffb_api_supported(self) -> bool:
        """True once an aircraft has been confirmed to implement the API."""
        return (
            self.ffb_api_enable
            and self._ffb_api_latched
            and self._ffb_api_version >= 1
            and self._sim_is_msfs()
            and self._simconnect is not None
        )

    def _ffb_api_active(self, control=None) -> bool:
        """True when the API owns ``control`` on this device instance.

        This is the single gate every entry point and dispatch site consults.  When
        ``control`` is omitted it defaults to the control this device owns.
        """
        if not self._ffb_api_supported():
            return False
        own = self._ffb_api_control()
        if own is None:
            return False
        return own == (control if control is not None else own)

    def _ffb_api_has_trim(self, control) -> bool:
        """True when the aircraft advertises a trim system for ``control`` (spec 3.1).

        When False the rig applies no trim spring and ignores ``_TRIM`` and ``_TR_ON``
        for that control - there is no actuator to follow or to unclutch.
        """
        bit = FFB_API_TRIM_FEATURE_BIT.get(control)
        if bit is None:
            return False
        return bool((self._ffb_api_features >> bit) & 1)

    # ------------------------------------------------------------------ #
    # rig -> aircraft writes
    # ------------------------------------------------------------------ #

    def _ffb_api_write_lvar(self, lvar, value):
        if not self._simconnect:
            return
        self._simconnect.set_simdatum_to_msfs(lvar, value, units="number")

    def _ffb_api_update_enabled_flag(self, enabled: bool):
        """Write ``L:FFB_<CONTROL>_ENABLED`` on change, with a periodic re-assert.

        Enable is keyed on the rig physically providing this control, not on whether
        the aircraft trims it: a control with no trim bit is still enabled, for
        hydraulics and fly-through (spec 4, step 3).

        Written on change plus a heartbeat rather than every frame - the SimConnect
        send queue is shared with the axis stream, and an idempotent flag does not
        need to occupy it 100+ times a second.
        """
        control = self._ffb_api_control()
        if control is None:
            return

        now_ms = time.time() * 1000
        changed = self._ffb_api_enabled_state != enabled
        stale = (now_ms - self._ffb_api_enabled_last_write_ms) > FFB_API_ENABLED_HEARTBEAT_MS
        if not changed and not stale:
            return

        self._ffb_api_write_lvar(f"L:FFB_{control}_ENABLED", int(enabled))
        self._ffb_api_enabled_last_write_ms = now_ms
        if changed:
            logging.info(f"FFB API: {control} {'enabled' if enabled else 'disabled'}")
            self._ffb_api_enabled_state = enabled

    def _ffb_api_disable(self):
        """Best-effort ``ENABLED = 0`` on timeout, sim exit, or shutdown (spec 4, step 5).

        The aircraft is required to restore normal trim/feel when the flag clears, so
        emitting the zero reliably is the rig's whole obligation here - there is no
        undo sequence to run.
        """
        if self._ffb_api_enabled_state is None or not self._ffb_api_enabled_state:
            return
        control = self._ffb_api_control()
        if control is None or not self._simconnect:
            return
        self._ffb_api_write_lvar(f"L:FFB_{control}_ENABLED", 0)
        self._ffb_api_enabled_state = False
        logging.info(f"FFB API: {control} disabled (shutdown/timeout)")

    def _ffb_api_write_fly_through(self, telem_data: BaseTelemetryData, control, results: dict):
        """Publish per-axis fly-through detection (spec 3.3).

        ``results`` maps axis name -> bool.  With ``ffb_api_individual_fly_through``
        cleared, the per-control OR is mirrored onto every axis of the control, which
        suits hardware that cannot cleanly separate cyclic pitch from roll.  The
        published variables stay per-axis either way, so the aircraft contract is
        identical in both modes.

        Telemetry:
            Written: _ffb_fly_through_<axis> (debug: int 0/1 per axis)
        """
        if not self.ffb_api_individual_fly_through:
            combined = any(results.values())
            results = {axis: combined for axis in results}

        for axis, active in results.items():
            self._ffb_api_write_lvar(f"L:FFB_{axis}_FLY_THROUGH", int(active))
            telem_data[f"_ffb_fly_through_{axis.lower()}"] = int(active)

    def _ffb_api_detect_fly_through(self, axis, phys_norm, center_norm) -> bool:
        """Position-deviation fly-through with hysteresis.

        The rig always publishes the best signal its hardware can produce; the spec
        deliberately does not ask which method was used (spec 3.7).
        """
        was_active = self._ffb_api_fly_through_state.get(axis, False)
        deadzone = (
            self.ffb_api_fly_through_release_deadzone
            if was_active
            else self.ffb_api_fly_through_deadzone
        )
        active = abs(phys_norm - center_norm) > deadzone
        self._ffb_api_fly_through_state[axis] = active
        return active

    # ------------------------------------------------------------------ #
    # aircraft -> rig reads
    # ------------------------------------------------------------------ #

    def _ffb_api_trim(self, telem_data: BaseTelemetryData, axis) -> float:
        """Trim actuator position for ``axis``, normalized -1..+1 in device sign."""
        value = telem_data.get(FFB_API_TRIM_FIELD[axis], 0) or 0
        return self._ffb_api_to_device_sign(axis, clamp(float(value), -1.0, 1.0))

    def _ffb_api_to_device_sign(self, axis, value: float) -> float:
        """Convert a spec-convention value to this rig's device axis sign.

        All sign handling for the API lives here so an inversion bug has exactly one
        place to be wrong (plan 4.4).
        """
        invert = {
            "CYCLIC_PITCH": self.ffb_api_invert_cyclic_pitch,
            "CYCLIC_ROLL": self.ffb_api_invert_cyclic_roll,
            "COLLECTIVE": self.ffb_api_invert_collective,
            "PEDALS": self.ffb_api_invert_pedals,
        }.get(axis, False)
        return -value if invert else value

    def _ffb_api_tr_on(self, telem_data: BaseTelemetryData, control) -> bool:
        """True while ``control``'s trim actuator is unclutched.

        Meaningless without a trim system, so it is gated on the feature bit: there
        is nothing to unclutch on a control the aircraft does not trim.
        """
        if not self._ffb_api_has_trim(control):
            return False
        return bool(telem_data.get(FFB_API_TR_FIELD[control], 0))

    def _ffb_api_apply_hydraulic_loss(self, telem_data: BaseTelemetryData):
        """Feed published hydraulic assist loss into the existing hydraulic effect.

        Rather than branch inside :class:`HydraulicLossMixIn`, this injects ``HydSys``
        so the existing float path, threshold and damper/inertia/friction scaling all
        run unchanged.

        Note the inversion: the spec's ``_HYD_ASSIST_LOSS`` is 0 = full boost and
        1 = unassisted, while TelemFFB's ``HydSys`` / ``hydraulic_factor`` is system
        *health*, 1 = full boost.  Not feature-gated - every axis defines the value,
        and 0 (full boost) is the correct default when absent.

        Telemetry:
            Read:    ffbHydLoss<Axis> - Optional[float] (0..1); L:FFB_<AXIS>_HYD_ASSIST_LOSS
            Written: HydSys           - float (0..1); injected hydraulic health
                     _ffb_hyd_loss    - float (0..1, debug); worst loss across the axes
        """
        control = self._ffb_api_control()
        if control is None:
            return

        losses = []
        for axis in FFB_API_CONTROL_AXES[control]:
            value = telem_data.get(FFB_API_HYD_FIELD[axis], 0) or 0
            losses.append(clamp(float(value), 0.0, 1.0))
        if not losses:
            return

        loss = max(losses)
        telem_data._ffb_hyd_loss = loss
        telem_data.HydSys = 1.0 - loss

    # ------------------------------------------------------------------ #
    # per-frame entry point
    # ------------------------------------------------------------------ #

    def ffb_api_on_telemetry(self, telem_data: BaseTelemetryData):
        """Run discovery, the ENABLED lifecycle and hydraulics for this frame.

        Called early in :meth:`Helicopter.on_telemetry`, before the effect mixins run,
        so injected ``HydSys`` is in place by the time the hydraulic effect reads it.
        The per-control spring/trim work happens later, at the dispatch sites.

        Telemetry:
            Written: _ffb_api_version, _ffb_api_features, _ffb_api_enabled (debug)
        """
        if not self._sim_is_msfs() or not self._simconnect:
            return
        if not self.ffb_api_enable:
            return

        # Per frame, not once at construction: an aircraft handler is built before
        # any telemetry is attached, so a constructor-time _sim_is_msfs() is still
        # False and a subscription made there never happens.  The sv_dict guard
        # inside makes the steady-state call a dict lookup, and re-running it also
        # re-instates the subscription if an unrelated _resubscribe() dropped it.
        self.subscribe_ffb_api_simvars()

        self._ffb_api_latch_discovery(telem_data)

        if not self._ffb_api_supported():
            # Unsupported: write no L:FFB_* variable at all (spec 4, step 1).
            return

        telem_data._ffb_api_version = self._ffb_api_version
        telem_data._ffb_api_features = self._ffb_api_features

        self._ffb_api_update_enabled_flag(True)
        telem_data._ffb_api_enabled = int(bool(self._ffb_api_enabled_state))

        self._ffb_api_apply_hydraulic_loss(telem_data)

    def ffb_api_on_timeout(self):
        """Release the aircraft on telemetry timeout / sim exit."""
        self._ffb_api_disable()
        self._ffb_api_reset()

    # ------------------------------------------------------------------ #
    # control paths - these replace the vendor/generic path when active
    # ------------------------------------------------------------------ #

    # ``ConditionEffect.set_coefficient`` dispatches on Python type: a float is scaled
    # by 4096, an int is taken as a raw 0..4096 coefficient.  Normalized user gains are
    # therefore coerced to float explicitly below, so a config value that arrives as
    # ``1`` cannot silently mean "raw coefficient 1" (i.e. no spring at all).

    def _ffb_api_spring_ready(self, phys, center_norm) -> bool:
        """Gate spring engagement until the physical axis is near the trim reference.

        Mirrors the existing ``_initialize_*_if_needed`` handshake: engaging a spring
        whose center is far from where the control physically sits would yank it.
        """
        if self._ffb_api_spring_init:
            return True
        if center_norm - 0.1 < phys < center_norm + 0.1:
            self._ffb_api_spring_init = 1
            logging.info("FFB API: spring initialized")
            return True
        return False

    def _ffb_api_update_cyclic(self, telem_data: BaseTelemetryData):
        """Drive the cyclic spring from the published trim actuator position.

        Replaces ``msfs_update_heli_controls`` while CYCLIC is enabled.  The aircraft
        has zeroed ``ROTOR *_TRIM PCT`` and owns trim, so the generic and vendor trim
        paths would be integrating state nobody maintains.

        Telemetry:
            Read:    ffbTrimCyclicPitch, ffbTrimCyclicRoll - float (-1..+1)
                     ffbTrOnCyclic                         - bool; actuator unclutched
            Written: phys_x, phys_y      (float, -1..1; physical stick position)
                     StickXY             ([float, float]; stick position)
                     StickXY_offset      ([float, float]; spring center)
        """
        phys_x, phys_y = self._get_device_raw_axes()
        telem_data.phys_x = phys_x
        telem_data.phys_y = phys_y

        tr_on = self._ffb_api_tr_on(telem_data, FFB_API_CYCLIC)
        has_trim = self._ffb_api_has_trim(FFB_API_CYCLIC)

        if not has_trim:
            # No trim system on this aircraft's cyclic: plain centering from local
            # settings, no published reference to follow.
            center_x, center_y = 0.0, 0.0
            gain = float(self.cyclic_spring_gain)
        elif tr_on:
            # Unclutched: soften and follow the stick.  On release the published
            # _TRIM has already followed to here, so the spring re-centers with no
            # snap - the discontinuity-free behaviour the API exists to provide.
            center_x, center_y = phys_x, phys_y
            gain = float(self.ffb_api_tr_spring_gain)
        else:
            center_x = self._ffb_api_trim(telem_data, "CYCLIC_ROLL")
            center_y = self._ffb_api_trim(telem_data, "CYCLIC_PITCH")
            gain = float(self.cyclic_spring_gain)

        if self._ffb_api_tr_active and not tr_on:
            logging.debug("FFB API: cyclic trim re-clutched")
        self._ffb_api_tr_active = tr_on

        self.cpO_x = round(clamp(center_x, -1.0, 1.0) * 4096)
        self.cpO_y = round(clamp(center_y, -1.0, 1.0) * 4096)
        self.cyclic_center = [center_x, center_y]

        ready = self._ffb_api_spring_ready(phys_x, center_x) and self._ffb_api_spring_ready(phys_y, center_y)

        self.spring_x.set_coefficient(gain if ready else 0, True)  # int 0 = raw zero coefficient
        self.spring_y.set_coefficient(gain if ready else 0, True)  # int 0 = raw zero coefficient
        self.spring_x.set_offset(self.cpO_x)
        self.spring_y.set_offset(self.cpO_y)
        self._spring_handle.name = "ffb_api_cyclic_spring"
        self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.setCondition(self.spring_y)
        if not self._spring_handle.started:
            self._spring_handle.start()

        telem_data.StickXY = [phys_x, phys_y]
        telem_data.StickXY_offset = self.cyclic_center

        self._ffb_api_write_fly_through(telem_data, FFB_API_CYCLIC, {
            "CYCLIC_ROLL": self._ffb_api_detect_fly_through("CYCLIC_ROLL", phys_x, center_x),
            "CYCLIC_PITCH": self._ffb_api_detect_fly_through("CYCLIC_PITCH", phys_y, center_y),
        })

        # Axis ownership is unchanged by the API (plan 10.2): if the user has TelemFFB
        # sending axes, keep doing so - but raw, with no trim contribution, since trim
        # lives in the spring center and must not be applied twice.
        self.last_device_x, self.last_device_y = phys_x, phys_y
        self._send_cyclic_axis_output(telem_data, force_trim_active=False)

    def _ffb_api_update_collective(self, telem_data: BaseTelemetryData):
        """Drive the collective spring from the published trim actuator position.

        Telemetry:
            Read:    ffbTrimCollective - float (-1..+1); ffbTrOnCollective - bool
            Written: phys_y            (float, -1..1)
        """
        _, phys_y = self._get_device_raw_axes()
        telem_data.phys_y = phys_y

        tr_on = self._ffb_api_tr_on(telem_data, FFB_API_COLLECTIVE)
        has_trim = self._ffb_api_has_trim(FFB_API_COLLECTIVE)

        if tr_on:
            # Trim actuator unclutched: soften and follow the physical lever.
            center_y = phys_y
            gain = float(self.ffb_api_tr_spring_gain)
        elif not has_trim:
            # No trim system: hold position from local settings, matching the generic
            # collective path (_update_collective_spring_mode).  collective_spring_coeff_y
            # is already a raw 0..4096 coefficient, so it stays an int.
            center_y = phys_y
            gain = int(round(self.collective_spring_coeff_y / 2))
        else:
            center_y = self._ffb_api_trim(telem_data, "COLLECTIVE")
            gain = float(self.collective_ap_spring_gain)

        self.cpO_y = round(clamp(center_y, -1.0, 1.0) * 4096)
        ready = self._ffb_api_spring_ready(phys_y, center_y)

        self.spring_y.set_coefficient(gain if ready else 0, True)  # int 0 = raw zero coefficient
        self.spring_y.set_offset(self.cpO_y)
        self._spring_handle.name = "ffb_api_collective_spring"
        self._spring_handle.setCondition(self.spring_y)
        if not self._spring_handle.started:
            self._spring_handle.start()

        self._ffb_api_write_fly_through(telem_data, FFB_API_COLLECTIVE, {
            "COLLECTIVE": self._ffb_api_detect_fly_through("COLLECTIVE", phys_y, center_y),
        })

        self.last_collective_y = phys_y
        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            y_var, y_range = self._get_msfs_collective_axis_config()
            self.collective_init = 1
            self._send_collective_outputs(telem_data, phys_y, y_var, y_range)

    def _ffb_api_update_pedals(self, telem_data: BaseTelemetryData):
        """Drive the pedal spring from the published trim actuator position.

        Telemetry:
            Read:    ffbTrimPedals - float (-1..+1); ffbTrOnPedals - bool
            Written: phys_x        (float, -1..1)
        """
        phys_x, _ = self._get_device_raw_axes()
        telem_data.phys_x = phys_x

        tr_on = self._ffb_api_tr_on(telem_data, FFB_API_PEDALS)
        has_trim = self._ffb_api_has_trim(FFB_API_PEDALS)

        if not has_trim:
            center_x = 0.0
            gain = float(self.pedal_spring_gain)
        elif tr_on:
            center_x = phys_x
            gain = float(self.ffb_api_tr_spring_gain)
        else:
            center_x = self._ffb_api_trim(telem_data, "PEDALS")
            gain = float(self.pedal_spring_gain)

        self.cpO_x = round(clamp(center_x, -1.0, 1.0) * 4096)
        ready = self._ffb_api_spring_ready(phys_x, center_x)

        self.spring_x.set_coefficient(gain if ready else 0, True)  # int 0 = raw zero coefficient
        self.spring_x.set_offset(self.cpO_x)
        self._spring_handle.name = "ffb_api_pedal_spring"
        self._spring_handle.setCondition(self.spring_x)
        if not self._spring_handle.started:
            self._spring_handle.start()

        self._ffb_api_write_fly_through(telem_data, FFB_API_PEDALS, {
            "PEDALS": self._ffb_api_detect_fly_through("PEDALS", phys_x, center_x),
        })

        self.last_pedal_x = phys_x
        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            x_scale = clamp(self.rudder_x_axis_scale, 0.0, 1.0)
            x_var, x_range = self._get_msfs_pedal_axis_config()
            self.pedals_init = 1
            self._send_pedal_outputs(telem_data, phys_x, x_scale, x_var, x_range)
