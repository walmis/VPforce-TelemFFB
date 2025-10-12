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

from telemffb.sim.msfs_xp.PropellerAircraft import PropellerAircraft
from telemffb.util.conversions import kt2ms

class TurbopropAircraft(PropellerAircraft):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        self.spoiler_spd_thresh_low = 120 * kt2ms
        self.spoiler_spd_thresh_hi = 260 * kt2ms
        self.speedbrake_speed_thresh = 120 * kt2ms

    @override
    def on_telemetry(self, telem_data):
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "TurbopropAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
