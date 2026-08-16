# Weapons / Countermeasure effects mixin
from typing import override
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHUP, EFFECT_SINE, EFFECT_SQUARE
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class WeaponsEffectMixIn(AircraftEffectUtilsBase):
    """Mixin for weapon, gunfire and countermeasure effects."""

    # weapon / CM related config
    # user parameters
    gun_vibration_intensity: float = 0.12  # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity: float = 0.12  # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity: float = 0.12  # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45  # Affects direction of weapon effects (-1 for random)

    gunfire_effect_enabled: bool = False
    countermeasure_effect_enabled: bool = False
    weapon_release_effect_enabled: bool = False
    # end of user parameters

    def ac_update_cm_weapons(self, telem):
        """Apply gun, weapon release, and countermeasure haptic effects.

        Telemetry:
            Read: PayloadInfo - varies (int station count, list, or string); payload state;
                                 any change triggers the weapon release effect
                  Gun         - Union[bool, int, float] (0 or 1); gun firing state;
                                 any change triggers the gunfire effect
                  Flares      - Union[int, float] (count, ≥ 0); flare count/state;
                                 change combined with Chaff change triggers CM effect
                  Chaff       - Union[int, float] (count, ≥ 0); chaff count/state;
                                 change combined with Flares change triggers CM effect
        """
        payload = telem.PayloadInfo
        gun = telem.Gun
        flares = telem.Flares
        chaff = telem.Chaff

        # Use helper method for weapon effects
        self._create_weapon_effect(
            "payload_rel",
            "PayloadInfo",
            payload,
            self.weapon_release_effect_enabled,
            self.weapon_release_intensity,
            shape=EFFECT_SQUARE,
        )

        self._create_weapon_effect(
            "gunfire", "Gun", gun, self.gunfire_effect_enabled, self.gun_vibration_intensity, shape=EFFECT_SAWTOOTHUP
        )

        # Countermeasures effect (combined flares and chaff)
        cm_changed = self.anything_has_changed("Flares", flares) or self.anything_has_changed("Chaff", chaff)
        if cm_changed and self.countermeasure_effect_enabled:
            direction = self._get_effect_direction(self.weapon_effect_direction)
            if self.weapon_effect_direction == -1:
                logging.info(f"CM Effect Direction is randomized: {direction} deg")
            self.effects["cm"].periodic(50, self.cm_vibration_intensity, direction, duration=80).start(force=True)
        elif (
            not (
                self.anything_has_changed("Flares", flares, delta_ms=160)
                or self.anything_has_changed("Chaff", chaff, delta_ms=160)
            )
            or not self.countermeasure_effect_enabled
        ):
            self.effects["cm"].stop()

    def _create_weapon_effect(
        self,
        effect_name: str,
        telem_key: str,
        telem_value,
        enabled_flag: bool,
        intensity: float,
        frequency: int = 10,
        duration: int = 80,
        delta_ms: int = 160,
        shape: int = EFFECT_SINE,
    ):
        """Generic method for weapon-related effects (gun, payload, countermeasures)."""
        if not enabled_flag:
            self.effects[effect_name].stop()
            return

        if self.anything_has_changed(telem_key, telem_value):
            direction = self._get_effect_direction(self.weapon_effect_direction)
            if self.weapon_effect_direction == -1:
                logging.info(f"{effect_name.title()} Effect Direction is randomized: {direction} deg")
            self.effects[effect_name].periodic(
                frequency, intensity, direction, effect_type=shape, duration=duration
            ).start(force=True)
        elif not self.anything_has_changed(telem_key, telem_value, delta_ms=delta_ms):
            self.effects[effect_name].stop()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_cm_weapons(telem_data)
