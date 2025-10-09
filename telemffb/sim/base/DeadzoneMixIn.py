import logging

import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

def pct2dz(pct: float) -> int:
    """Convert percentage (0-100) to deadzone value (0-4096)."""
    return utils.clamp(round(pct * 4096), 0, 4096)

class DeadzoneMixIn(AircraftEffectUtilsBase):
    """Mixin for device deadzone configuration and runtime application."""
    enable_deadzone: bool = False
    deadzone_base_pct: float = 0.0

    def __init__(self):
        super().__init__()
        # instance state for deadzone handling
        self.deadzone_active: bool = False
        self.active_deadzone_pct: float = 0.0
        self.active_deadzone_pct_override: float = 0.0

    def ac_set_deadzone_override(self, value: float):
        """Set deadzone override value (0 to disable override). Range: 0.0 to 1.0 (0% to 100%)."""
        if(value != self.active_deadzone_pct_override):
            self.active_deadzone_pct_override = utils.clamp(value, 0.0, 1.0)
            HapticEffect.device.set_deadzone(pct2dz(self.active_deadzone_pct_override))
            logging.info(f"Setting Deadzone override to %{self.active_deadzone_pct_override*100}")
        if(value == 0.0 and self.active_deadzone_pct_override != 0.0):
            HapticEffect.device.set_deadzone(0)
            self.active_deadzone_pct_override = value
            logging.info("Resetting Deadzone override")

    def ac_update_deadzone(self):
        if self.active_deadzone_pct_override:
            return
        if not self.enable_deadzone and self.deadzone_active:
            if self.active_deadzone_pct != 0.0:
                HapticEffect.device.set_deadzone(0)
                self.active_deadzone_pct = 0.0
                logging.info('Disabling deadzone')
                self.deadzone_active = False
            return
        if self.active_deadzone_pct != self.deadzone_base_pct:
            HapticEffect.device.set_deadzone(pct2dz(self.deadzone_base_pct))
            self.active_deadzone_pct = self.deadzone_base_pct
            logging.info(f"Setting Deadzone to %{self.deadzone_base_pct}")
            self.deadzone_active = True

    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        self.ac_update_deadzone()


    def on_timeout(self):  # override me
        super().on_timeout()
        self.ac_set_deadzone_override(0.0)
        if self.deadzone_active:
            HapticEffect.device.set_deadzone(0)
            self.deadzone_updated = False
            self.deadzone_active = False