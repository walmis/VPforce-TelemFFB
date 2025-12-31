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

from typing import override
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.util.conversions import kt2ms
from telemffb.utils import clamp, PerformanceTracker
# from .MsfsXpHeliControlsMixIn import MsfsXpHeliControlsMixIn

import logging
import time

class TaogH500Helicopter(Helicopter):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    @override
    def msfs_send_heli_pedal_pos(self, xvar, xpos, telem_data):
        if not telem_data.get("TaogH500PedalLock", 0):
            self._simconnect.send_event_to_msfs("L:H500C_OH6A-AXIS_ANTI_TORQUE_PEDALS", xpos)
            if not telem_data.get("TaogH500TRDamaged", 0):
                self._simconnect.send_event_to_msfs(xvar, xpos)

    @override
    def msfs_send_heli_cyclic_pos(self, xvar, xpos, yvar, ypos, telem_data):
        if not telem_data.get("TaogH500CyclicFriction", 0):
            self._simconnect.send_event_to_msfs(xvar, xpos)
            self._simconnect.send_event_to_msfs(yvar, ypos)

            self._simconnect.send_event_to_msfs("L:H500C_OH6A-AXIS_CYCLIC_LATERAL", xpos)
            self._simconnect.send_event_to_msfs("L:H500C_OH6A-AXIS_CYCLIC_LONGITUDINAL", ypos)

    @override
    def msfs_send_heli_collective_pos(self, yvar, ypos, telem_data):
        if not telem_data.get("TaogH500CollectiveFriction", 0):
            self._simconnect.send_event_to_msfs(yvar, ypos)
            self._simconnect.send_event_to_msfs("L:H500C_OH6A-AXIS_COLLECTIVE", ypos)
