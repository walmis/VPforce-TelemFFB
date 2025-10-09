import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn

class HydraulicLossMixIn(FFBForcesMixIn):
    """Mixin to handle hydraulic-loss related configuration, runtime state and effects."""
    enable_hydraulic_loss_effect: bool = False
    hydraulic_loss_threshold: float = 0.95
    hydraulic_loss_damper: float = 1
    hydraulic_loss_inertia: float = 1
    hydraulic_loss_friction: float = 1

    def __init__(self, *args, **kwargs):
        # cooperative init for mixin ordering
        super().__init__(*args, **kwargs)
        # per-instance runtime state
        self.hydraulic_factor = 0.0

    def ac_update_hydraulic_loss_effect(self, telem_data):

        telem_data['_hyd_factor'] = self.hydraulic_factor

        if not self.enable_hydraulic_loss_effect:
            return False
        hydraulic_sys = telem_data.get('HydSys', "n/a")
        hydraulic_pressure = telem_data.get('HydPress', 1)

        if not self.enable_damper_ovd or not self.enable_inertia_ovd or not self.enable_friction_ovd:
            self.flag_error("Hydraulic Loss effect enabled but damper/inertia/friction overrides not enabled - effect requires all three enabled with base values set")
            return False

        if hydraulic_sys == 'n/a':
            return False

        if isinstance(hydraulic_pressure, list):
            hydraulic_pressure = max(hydraulic_pressure)

        if isinstance(hydraulic_sys, int) and (hydraulic_sys == 1 or hydraulic_sys == 0):
            hydraulic_sys = bool(hydraulic_sys)

        if isinstance(hydraulic_sys, list):
            self.hydraulic_factor = max(hydraulic_sys)

        elif isinstance(hydraulic_sys, bool):
            if self._sim_is_dcs() and hydraulic_sys == True and hydraulic_pressure == 0:
                hydraulic_sys = False

            if hydraulic_sys == True:
                self.hydraulic_factor = self.step_value_over_time('hyd_factor', self.hydraulic_factor, 2500, 1, floatpoint=True)
            elif hydraulic_sys == False:
                self.hydraulic_factor = self.step_value_over_time('hyd_factor', self.hydraulic_factor, 2500, 0, floatpoint=True)

            # hydraulic_factor = int(hydraulic_sys)
            telem_data['_hydraulic_factor_test'] = self.hydraulic_factor
        else:
            self.hydraulic_factor = hydraulic_sys

        if self.hydraulic_factor >= self.hydraulic_loss_threshold:
            self.effects["hyd_loss_damper"].destroy()
            self.effects["hyd_loss_inertia"].destroy()
            self.effects["hyd_loss_friction"].destroy()
            self.damper_coeff = int(self.damper_force * 4096)
            self.inertia_coeff = int(self.inertia_force * 4096)
            self.friction_coeff = int(self.friction_force * 4096)
            return False

        damper = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_damper, self.damper_force))
        inertia = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_inertia, self.inertia_force))
        friction = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_friction, self.friction_force))

        self.damper_coeff = utils.clamp(int(damper * 4096), 0, 4096)
        self.inertia_coeff = utils.clamp(int(inertia * 4096), 0, 4096)
        self.friction_coeff = utils.clamp(int(friction * 4096), 0, 4096)

        self.effects["damper"].destroy()
        self.effects["inertia"].destroy()
        self.effects["friction"].destroy()

        if not self.effects["hyd_loss_damper"].started or self.anything_has_changed('_hyd_loss_damper', self.damper_coeff):
            self.effects["hyd_loss_damper"].damper(self.damper_coeff, self.damper_coeff).start()
        if not self.effects["hyd_loss_inertia"].started or self.anything_has_changed('_hyd_loss_inertia', self.inertia_coeff):
            self.effects["hyd_loss_inertia"].inertia(self.inertia_coeff, self.inertia_coeff).start()
        if not self.effects["hyd_loss_friction"].started or self.anything_has_changed('_hyd_loss_friction', self.friction_coeff):
            self.effects["hyd_loss_friction"].friction(self.friction_coeff, self.friction_coeff).start()

        return True
    
    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        hyd_loss = self.ac_update_hydraulic_loss_effect(telem_data)
        if not hyd_loss: 
            self.ac_update_ffb_forces(telem_data)