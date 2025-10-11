import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


class FFBForcesMixIn(AircraftEffectUtilsBase):
    # FFB override state

    # user parameters
    keep_forces_on_pause: bool = True
    enable_damper_ovd: bool = False
    damper_force: float = 0.0
    enable_inertia_ovd: bool = False
    inertia_force: float = 0.0
    enable_friction_ovd: bool = False
    friction_force: float = 0.0
    # end of user parameters

    """Mixin to encapsulate FFB override forces (damper/inertia/friction) state and logic.

    Keeps per-instance state in __init__ and provides ac_update_ffb_forces.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # flag moved here
        self.friction_effect_overridden: bool = False

    def ac_update_ffb_forces(self, telem_data):
        """Update explicit override FFB effects (damper, inertia, friction)."""
        # Damper
        if self.enable_damper_ovd:
            if self.anything_has_changed('damper_value', self.damper_force) or not self.effects['damper'].started:
                force = utils.clamp(self.damper_force, 0.0, 1.0)
                self.effects["damper"].damper(int(4096*force), int(4096*force)).start()
        else:
            if self.effects['damper'].started:
                self.effects["damper"].destroy()

        # Inertia
        if self.enable_inertia_ovd:
            if self.anything_has_changed('inertia_value', self.inertia_force) or not self.effects['inertia'].started:
                force = utils.clamp(self.inertia_force, 0.0, 1.0)
                self.effects["inertia"].inertia(int(4096*force), int(4096*force)).start()
        else:
            if self.effects['inertia'].started:
                self.effects["inertia"].destroy()

        # Friction (unless overridden elsewhere)
        if not self.friction_effect_overridden:
            if self.enable_friction_ovd:
                force = utils.clamp(self.friction_force, 0.0, 1.0)
                self.effects['friction'].name = 'friction'
                self.effects["friction"].friction(int(4096*force), int(4096*force)).start()
            else:
                if self.effects['friction'].started:
                    self.effects["friction"].destroy()