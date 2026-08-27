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

"""Game spring via the DirectInput FFB tap - sim-agnostic.

Spring mode DINPUT_TAP ('Game Managed (DirectInput Tap)'): the sim
computes its own spring but TelemFFB renders it.  Sims that render their
own force feedback (DCS, IL-2, BMS) take exclusive access of the device,
which is what has always kept companion software off it; the
TelemFFB-DInput-Tap wrapper's 'tap' device policy absorbs the sim's
output and publishes its live effect state into a shared-memory mirror
instead, leaving the device free.  This mixin reconciles the mirrored
spring onto the device each telemetry frame - trim center follow,
force-trim coefficient collapse and the rest reproduced exactly - so the
sim's own forces and TelemFFB's telemetry-driven effects share one
device.  The reader and unit translation live in telemffb.hw.ffb_tap.

Works identically on VPforce hardware (raw-HID rendering) and generic
DirectInput devices (DI bridge).  A distinct mode and effect from IL-2
Korea's native ffbdevice-records source (spring mode TELEM /
il2_ffb_spring) - the two never run together.
"""

import logging
import time

from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition


class FfbTapMixIn:
    # Axis-orientation corrections for the mirrored game spring.
    # Counterparts to DCS FFTune's swap/invert settings - those transform
    # the game's FFB output and so bake into the tap; corrections belong
    # here where the re-rendering happens, with the game-side settings
    # left at neutral.  They apply to every rendered game effect, not just
    # the spring - the game emits them all in the same orientation.
    tap_spring_swap_axes = False
    tap_spring_invert_x = False
    tap_spring_invert_y = False

    # Per-axis gain on the rendered game spring (1.0 = as the game
    # commanded; up to 2.0 because native game forces often run weak).
    # Applied AFTER the swap/invert corrections, so X and Y always mean
    # the axes as rendered on the device, whatever the game emitted.
    tap_spring_gain_x: float = 1.0
    tap_spring_gain_y: float = 1.0

    # Per-type rendering of the game's non-spring effects (the tap mirrors
    # everything the game plays; each type can be dropped independently),
    # each with its own gain on the same 0..2.0 scale.  Periodic covers the
    # five waveforms - one waveform of a vibration is not a thing a user
    # would drop while keeping the others.
    tap_effect_constant = True
    tap_effect_periodic = True
    tap_effect_damper = True
    tap_effect_inertia = True
    tap_effect_friction = True
    tap_effect_constant_gain: float = 1.0
    tap_effect_periodic_gain: float = 1.0
    tap_effect_damper_gain: float = 1.0
    tap_effect_inertia_gain: float = 1.0
    tap_effect_friction_gain: float = 1.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tap_cond_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self._tap_cond_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self._tap_slot_effects = {}     # mirror slot -> owned effect name
        self._tap_start_counts = {}     # effect name -> last seen startCount
        self._tap_transient_until = {}  # effect name -> monotonic deadline
        self._tap_effects_gen = None    # (generation, resetCount) rendered
        self._tap_unsupported_logged = False

    def ffb_tap_spring(self) -> bool:
        """Render the game-computed spring published by the DirectInput tap.

        Returns True while the tap spring is rendering; False - with this
        mode's effect stopped - when the mode is not DINPUT_TAP, no tap
        writer is publishing, the tapped device is paused, or the game's
        spring is stopped (e.g. in the menus, where the stick intentionally
        goes spring-less).
        """
        # joystick and pedals: the roles a game can render FFB to through
        # the tap (IL-2 Korea drives native FFB pedals).  Each instance
        # renders its own device's mirror block - see
        # FfbTapReader._select_device.
        if not (self.is_joystick() or self.is_pedals()):
            return False
        if not self.spring_mode_is(SpringModeEnum.DINPUT_TAP):
            self.effects['ffb_tap_spring'].stop()
            self._tap_effects_teardown()
            return False

        # the game's non-spring effects ride the same mode and mirror
        self._ffb_tap_game_effects()

        from telemffb.hw import ffb_tap
        state = ffb_tap.read_game_spring()
        self.telem_data['FFB_Tap'] = 'active' if state else 'inactive'
        if state is None:
            self.effects['ffb_tap_spring'].stop()
            return False

        # axis-orientation corrections (see the tap_spring_* attrs)
        x_state, y_state = state.x, state.y
        if self.tap_spring_swap_axes:
            x_state, y_state = y_state, x_state
        if self.tap_spring_invert_x and x_state is not None:
            x_state = x_state.inverted()
        if self.tap_spring_invert_y and y_state is not None:
            y_state = y_state.inverted()
        # per-axis gain, after the corrections: the sliders scale the axes
        # as rendered, not as the game emitted them
        if x_state is not None:
            x_state = x_state.scaled(self.tap_spring_gain_x)
        if y_state is not None:
            y_state = y_state.scaled(self.tap_spring_gain_y)
        # live slider-handle readout: the rendered force as a fraction of
        # device full scale - pinned at 1.0 = the gain is clipping
        for axis_state, key in ((x_state, '_pct_tap_x'), (y_state, '_pct_tap_y')):
            if axis_state is None:
                continue
            pct = max(abs(axis_state.positive_coefficient),
                      abs(axis_state.negative_coefficient)) / 4096
            self.telem_data[key] = pct
            self._ipc_telem[key] = pct

        spring = self.effects['ffb_tap_spring'].spring()
        for axis_state, cond, axis_name in ((x_state, self._tap_cond_x, 'X'),
                                            (y_state, self._tap_cond_y, 'Y')):
            if axis_state is None:
                continue
            cond.cpOffset = axis_state.offset
            cond.positiveCoefficient = axis_state.positive_coefficient
            cond.negativeCoefficient = axis_state.negative_coefficient
            cond.positiveSaturation = axis_state.positive_saturation
            cond.negativeSaturation = axis_state.negative_saturation
            cond.deadBand = axis_state.deadband
            spring.setCondition(cond)
            self.telem_data[f'FFB_{axis_name}_Force'] = round(
                axis_state.positive_coefficient / 4096, 4)
            self.telem_data[f'FFB_{axis_name}_Center'] = round(
                axis_state.offset / 4096, 4)
        spring.start(override=True)
        return True

    # ------------------------------------------------------------------
    # Non-spring game effects: constant forces (stick kicks, recoil),
    # periodic vibrations (rumble, buffet) and damping conditions
    # (damper/inertia/friction), reconciled from the same mirror.
    # ------------------------------------------------------------------
    def _tap_type_settings_suffix(self, effect_type: int):
        """The tap_effect_* settings name suffix for an effect type, or
        None for types with no renderer (ramp/custom)."""
        from telemffb.hw import ffb_tap
        if effect_type == ffb_tap.ET_CONSTANT:
            return 'constant'
        if effect_type in ffb_tap.PERIODIC_TYPES:
            return 'periodic'
        return {ffb_tap.ET_DAMPER: 'damper',
                ffb_tap.ET_INERTIA: 'inertia',
                ffb_tap.ET_FRICTION: 'friction'}.get(effect_type)

    def _tap_type_enabled(self, effect_type: int) -> bool:
        suffix = self._tap_type_settings_suffix(effect_type)
        if suffix is None:
            # ramp/custom are not renderable through the effect API
            if not self._tap_unsupported_logged:
                logging.info(f"DirectInput tap: game effect type "
                             f"{effect_type} is not renderable; dropping it")
                self._tap_unsupported_logged = True
            return False
        return getattr(self, f'tap_effect_{suffix}')

    def _tap_type_gain(self, effect_type: int) -> float:
        suffix = self._tap_type_settings_suffix(effect_type)
        return getattr(self, f'tap_effect_{suffix}_gain') if suffix else 1.0

    def _tap_correct_direction(self, deg: float) -> float:
        """The swap/invert corrections applied to a polar direction.
        Derived from the app convention 0 deg -> +Y, 270 deg -> +X."""
        if self.tap_spring_swap_axes:
            deg = 270.0 - deg
        if self.tap_spring_invert_x:
            deg = -deg
        if self.tap_spring_invert_y:
            deg = 180.0 - deg
        return deg % 360.0

    def _tap_effects_teardown(self):
        if self._tap_slot_effects:
            self.effects.dispose(*self._tap_slot_effects.values())
            self._tap_slot_effects.clear()
        self._tap_start_counts.clear()
        self._tap_transient_until.clear()

    def _tap_forget(self, name: str):
        self.effects.dispose(name)
        self._tap_start_counts.pop(name, None)
        self._tap_transient_until.pop(name, None)

    def _ffb_tap_game_effects(self):
        """Reconcile the mirrored non-spring effects onto the device.

        A playing slot renders; a stopped slot stops; a vacated slot, a
        type change within a slot, a device rebind or a DISFFC_RESET
        disposes.  A finite one-shot that started (and possibly finished)
        entirely between two polls is caught by its startCount delta and
        replayed for its recorded duration; a missed INFINITE-duration
        blip is unknowable (it already stopped, length < one poll) and is
        dropped.
        """
        from telemffb.hw import ffb_tap
        state = ffb_tap.read_game_effects()
        if state is None:
            self._tap_effects_teardown()
            return
        gen = (state.generation, state.reset_count)
        if gen != self._tap_effects_gen:
            # device rebound or reset: every mirrored slot is invalidated
            self._tap_effects_teardown()
            self._tap_effects_gen = gen

        now = time.monotonic()
        seen = {}
        counts = {}
        peaks = {}   # per-type telemetry key -> peak rendered force fraction
        for fx in state.effects:
            if not self._tap_type_enabled(fx.effect_type):
                continue
            name = f'tap_game_{fx.slot}_{fx.effect_type}'
            old = self._tap_slot_effects.get(fx.slot)
            if old and old != name:
                self._tap_forget(old)   # game re-created the slot as a new type
            seen[fx.slot] = name

            last_start = self._tap_start_counts.get(name)
            self._tap_start_counts[name] = fx.start_count
            missed_one_shot = (last_start is not None
                               and fx.start_count != last_start
                               and not fx.playing
                               and fx.duration_ms is not None
                               and fx.effect_type not in ffb_tap.CONDITION_TYPES)
            pct = None
            if fx.playing:
                pct = self._render_tap_effect(fx, name, duration_ms=0)
                counts[fx.effect_type] = counts.get(fx.effect_type, 0) + 1
            elif missed_one_shot:
                pct = self._render_tap_effect(fx, name,
                                              duration_ms=fx.duration_ms)
                self._tap_transient_until[name] = now + fx.duration_ms / 1000.0
                counts[fx.effect_type] = counts.get(fx.effect_type, 0) + 1
            elif self._tap_transient_until.get(name, 0) > now:
                pass   # a replayed one-shot is still sounding
            else:
                self.effects[name].stop()
                self._tap_transient_until.pop(name, None)
            if pct is not None:
                key = self._tap_type_pct_key(fx.effect_type)
                peaks[key] = max(peaks.get(key, 0.0), pct)

        for slot, name in self._tap_slot_effects.items():
            if slot not in seen:
                self._tap_forget(name)
        self._tap_slot_effects = seen

        # live slider-handle readouts (fraction of device full scale;
        # pinned at 1.0 = the type's gain is clipping)
        for key in ('_pct_tap_const', '_pct_tap_periodic', '_pct_tap_damper',
                    '_pct_tap_inertia', '_pct_tap_friction'):
            pct = peaks.get(key, 0.0)
            self.telem_data[key] = pct
            self._ipc_telem[key] = pct

        n_const = counts.get(ffb_tap.ET_CONSTANT, 0)
        n_per = sum(n for t, n in counts.items() if t in ffb_tap.PERIODIC_TYPES)
        n_damp = sum(n for t, n in counts.items() if t in ffb_tap.CONDITION_TYPES)
        parts = [f'{n}{tag}' for n, tag in
                 ((n_const, 'C'), (n_per, 'P'), (n_damp, 'D')) if n]
        self.telem_data['FFB_TapFx'] = ' '.join(parts) if parts else '-'

    def _tap_type_pct_key(self, effect_type: int) -> str:
        from telemffb.hw import ffb_tap
        if effect_type == ffb_tap.ET_CONSTANT:
            return '_pct_tap_const'
        if effect_type in ffb_tap.PERIODIC_TYPES:
            return '_pct_tap_periodic'
        return {ffb_tap.ET_DAMPER: '_pct_tap_damper',
                ffb_tap.ET_INERTIA: '_pct_tap_inertia'}.get(
                    effect_type, '_pct_tap_friction')

    def _render_tap_effect(self, fx, name: str, duration_ms: int):
        """Push one mirrored effect's parameters and start it.  Returns the
        rendered peak force as a fraction of device full scale (post-gain,
        post-clamp - 1.0 means the type's gain is clipping), for the live
        slider-handle readouts.

        ``duration_ms`` 0 keeps the device effect open-ended - the mirror's
        playing state is authoritative and reconciles the stop; a nonzero
        value is a one-shot replay of a missed transient.
        """
        from telemffb.hw import ffb_tap
        eff = self.effects[name]
        deg = self._tap_correct_direction(fx.direction_deg)
        gain = self._tap_type_gain(fx.effect_type)
        pct = 0.0
        if fx.effect_type == ffb_tap.ET_CONSTANT:
            mag = max(-1.0, min(1.0, fx.constant_magnitude * gain))
            eff.constant(mag, deg)
            pct = abs(mag)
        elif fx.effect_type in ffb_tap.PERIODIC_TYPES:
            mag = min(fx.periodic_magnitude * gain, 1.0)
            eff.periodic(fx.periodic_freq, mag, deg,
                         effect_type=fx.effect_type, duration=duration_ms,
                         offset=max(-4096, min(
                             4096, round(fx.periodic_offset * gain))),
                         phase=fx.periodic_phase)
            pct = mag
        elif fx.effect_type in ffb_tap.CONDITION_TYPES:
            method = {ffb_tap.ET_DAMPER: 'damper',
                      ffb_tap.ET_INERTIA: 'inertia',
                      ffb_tap.ET_FRICTION: 'friction'}[fx.effect_type]
            getattr(eff, method)()   # types the effect; parameters follow
            x_state, y_state = fx.x, fx.y
            if self.tap_spring_swap_axes:
                x_state, y_state = y_state, x_state
            if self.tap_spring_invert_x and x_state is not None:
                x_state = x_state.inverted()
            if self.tap_spring_invert_y and y_state is not None:
                y_state = y_state.inverted()
            for axis_state, block in ((x_state, 0), (y_state, 1)):
                if axis_state is None:
                    continue
                axis_state = axis_state.scaled(gain)
                pct = max(pct,
                          abs(axis_state.positive_coefficient) / 4096,
                          abs(axis_state.negative_coefficient) / 4096)
                eff.setCondition(FFBReport_SetCondition(
                    parameterBlockOffset=block,
                    cpOffset=axis_state.offset,
                    positiveCoefficient=axis_state.positive_coefficient,
                    negativeCoefficient=axis_state.negative_coefficient,
                    positiveSaturation=axis_state.positive_saturation,
                    negativeSaturation=axis_state.negative_saturation,
                    deadBand=axis_state.deadband))
        else:
            return 0.0
        if fx.envelope is not None:
            # levels are absolute forces: scale with the type's gain so the
            # envelope keeps its shape relative to the scaled magnitude
            eff.envelope(
                attackFromForce=min(round(fx.envelope.attack_level * gain), 4096),
                decayToForce=min(round(fx.envelope.fade_level * gain), 4096),
                attackTime=fx.envelope.attack_time_ms,
                decayTime=fx.envelope.fade_time_ms)
        eff.start(override=True)
        return pct
