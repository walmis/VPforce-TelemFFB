from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

class DynamicSpringMixin(AircraftEffectUtilsBase):
    """Provides dynamic spring condition holders for devices that need them."""
    def __init__(self):
        super().__init__()
        # these are set to real FFB report condition objects during instance initialization
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.__spring_handle = None 

    @property
    def _spring_handle(self) -> HapticEffect:
        if self.__spring_handle is None:
            self.__spring_handle = self.effects['spring'].spring()
            self.__spring_handle.name = 'spring'

        return self.__spring_handle