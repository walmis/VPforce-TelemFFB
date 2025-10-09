from telemffb.hw.ffb_rhino import FFBReport_SetCondition


class DynamicSpringMixin:
    """Provides dynamic spring condition holders for devices that need them."""
    def __init__(self):
        # these are set to real FFB report condition objects during instance initialization
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)