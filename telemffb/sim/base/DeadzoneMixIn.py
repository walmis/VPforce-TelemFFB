import logging

import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

def pct2dz(pct: float) -> int:
    """Convert a deadzone fraction (0.0-1.0) to device value (0-4096)."""
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
        self._dz_defer_logged: bool = False  # rate-limit the deferred-write debug log

    def _write_device_deadzone(self, dz: int) -> bool:
        """Write the device deadzone if the device is alive.

        Returns True when the write was issued.  A missing (zombie
        startup) or hot-unplugged device is a silent no-op (one debug line
        per episode) so the 60-120 Hz path never sees an exception.
        """
        dev = HapticEffect.device
        if dev is None or not dev.connected:
            if not self._dz_defer_logged:
                self._dz_defer_logged = True
                logging.debug("DeadzoneMixIn: device not connected, deadzone write deferred")
            return False
        self._dz_defer_logged = False
        dev.set_deadzone(dz)
        return True

    def ac_set_deadzone_override(self, value: float):
        """Set deadzone override value (0 to disable override). Range: 0.0 to 1.0 (0% to 100%)."""
        if(value == 0.0 and self.active_deadzone_pct_override != 0.0):
            self.active_deadzone_pct_override = value
            self._write_device_deadzone(0)
            logging.info("Resetting Deadzone override")
        elif(value != self.active_deadzone_pct_override):
            self.active_deadzone_pct_override = value
            self._write_device_deadzone(pct2dz(self.active_deadzone_pct_override))
            logging.info(f"Setting Deadzone override to %{self.active_deadzone_pct_override*100}")


    def ac_update_deadzone(self, force: bool = False):
        if self.active_deadzone_pct_override:
            return
        if not self.enable_deadzone and self.deadzone_active:
            if self.active_deadzone_pct != 0.0:
                # Only commit the transition when the write actually went
                # out; while the device is down the pending state stays so
                # the same frame logic retries it once the device returns.
                if self._write_device_deadzone(0):
                    self.active_deadzone_pct = 0.0
                    logging.info('Disabling deadzone')
                    self.deadzone_active = False
            return
        if force:
            # Recovery replay: the firmware lost its deadzone on the
            # power cycle, so the transition gate below would never
            # re-send a steady value - push the current one, with the
            # same fraction-to-device conversion as the normal path.
            if self._write_device_deadzone(pct2dz(self.active_deadzone_pct)):
                if self.active_deadzone_pct:
                    self.deadzone_active = True
            return
        if self.active_deadzone_pct != self.deadzone_base_pct:
            if self._write_device_deadzone(pct2dz(self.deadzone_base_pct)):
                self.active_deadzone_pct = self.deadzone_base_pct
                logging.info(f"Setting Deadzone to %{self.deadzone_base_pct}")
                self.deadzone_active = True

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_deadzone()


    def on_timeout(self):  # override me
        super().on_timeout()
        self.ac_set_deadzone_override(0.0)
        if self.deadzone_active:
            # Best-effort device clear; the in-memory state is reset
            # regardless, which is all a timeout path may do to a device
            # that is absent or hot-unplugged (the recovery setup replay
            # re-applies the configured state when it returns).
            self._write_device_deadzone(0)
            self.deadzone_updated = False
            self.deadzone_active = False
