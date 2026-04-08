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

from .Aircraft import Aircraft
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
# removed local 'overrides' helper in favor of typing.override


class PropellerAircraft(Aircraft):
    """Generic Class for Prop aircraft"""
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    # run on every telemetry frame
    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        ### Propeller Aircraft Class Telemetry Handler
        if telem_data.N is None:
            return
        telem_data.AircraftClass = "PropellerAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
