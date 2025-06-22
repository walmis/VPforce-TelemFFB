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

from abc import ABC, abstractmethod

class TelemParserBase(ABC):
    """Abstract base class for telemetry parsers"""
    
    @abstractmethod
    def process_packet(self, packet: bytes) -> bytes:
        """
        Process incoming telemetry packet and return formatted data

        Args:
            packet: Raw telemetry packet bytes
            
        Returns:
            Formatted telemetry data as bytes (typically key=value pairs)
        """
        ...
    
