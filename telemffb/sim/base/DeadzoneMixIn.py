import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging


class DeadzoneMixIn(AircraftEffectUtilsBase):
    """Mixin for device deadzone configuration and runtime application."""
    enable_deadzone: bool = False
    deadzone_base_pct: float = 0.0

    def __init__(self):
        super().__init__()
        # instance state for deadzone handling
        self.active_deadzone_pct: float = 0.0
        self.deadzone_active: bool = False

    def ac_set_deadzone(self):
        if not self.enable_deadzone and self.deadzone_active:
            if self.active_deadzone_pct != 0.0:
                HapticEffect.device.set_deadzone(0)
                self.active_deadzone_pct = 0.0
                logging.info('Disabling deadzone')
                self.deadzone_active = False
            return
        if self.active_deadzone_pct != self.deadzone_base_pct:
            dz = utils.clamp(round((self.deadzone_base_pct / 100) * 4096), 0, 4096)
            HapticEffect.device.set_deadzone(dz)
            self.active_deadzone_pct = self.deadzone_base_pct
            logging.info(f"Setting Deadzone to %{self.deadzone_base_pct}")
            self.deadzone_active = True