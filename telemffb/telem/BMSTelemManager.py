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

"""
Falcon BMS Telemetry Manager

This module provides telemetry management for Falcon BMS using shared memory.
It integrates with the TelemFFB architecture to provide force feedback effects
based on BMS flight data, following the same structure as IL2Manager.

The main class BMSManager inherits from TelemParserBase and implements the
process_packet() method to read BMS shared memory and return formatted telemetry.

Key Features:
- Direct shared memory access to BMS FlightData and IVibeData
- Compatible with existing TelemFFB telemetry parser architecture
- Real-time flight data processing with pause detection
- Focus-based simulation pausing (similar to IL2)
- Weapon firing and damage event tracking via IVibe

Usage:
    bms_manager = BMSManager()
    # Called by telemetry system:
    telemetry_data = bms_manager.process_packet(b"")  # packet not used for BMS
"""

import logging
import math
import time
from typing import Optional, Dict, Any, List
import pygetwindow as get_focus_window

from .BMSSharedMem import BMSSharedMemory, FlightData, FlightData2, IVibeData, LightBits, LightBits2, LightBits3, HsiBits
from .TelemParserBase import TelemParserBase
import telemffb.globals as G
import telemffb.utils as utils

logger = logging.getLogger(__name__)

# Conversion constants
knots_to_ms = 0.514444
ft_to_m = 0.3048
fpss2gs = 1 / 32.17405


class BMSManager(TelemParserBase):
    """
    Telemetry parser for Falcon BMS shared memory data
    Following the same structure as IL2Manager
    """
    
    def __init__(self):
        # BMS shared memory interface
        self.bms_memory = BMSSharedMemory()
        
        # Aircraft information
        self.ac_name: str = ""
        self.ac_type: str = ""
        
        # Engine data
        self.engine_rpm: List[float] = [0.0]
        self.engine_rpm2: List[float] = [0.0]  # For twin-engine aircraft
        self.engine_ftit: List[float] = [0.0]
        self.engine_ftit2: List[float] = [0.0]
        self.nozzle_pos: List[float] = [0.0]
        self.nozzle_pos2: List[float] = [0.0]
        self.fuel_flow: float = 0.0
        self.fuel_flow2: float = 0.0

        # Weapon systems - match aircraft_base expectations
        self.gun_data: List = []
        self.guns_fired: List[int] = [0]
        self.bombs_released: List[int] = [0, 0]
        self.missiles_fired: List[int] = [0, 0]
        self.chaff_count: float = 0.0
        self.flare_count: float = 0.0
        
        # Weapon counters for change detection
        self.last_bullets_fired: int = 0
        self.last_bombs_dropped: int = 0
        self.last_aa_missiles: int = 0
        self.last_ag_missiles: int = 0
        self.last_flares_dropped: int = 0
        self.last_chaff_dropped: int = 0
        
        # Flight data
        self.acceleration_Gs: List[float] = [0, 0, 0]
        self.airspeed_kias: float = 0.0
        self.airspeed_mach: float = 0.0
        self.alpha_deg: float = 0.0
        self.altitude_agl: float = 0.0
        self.altitude_msl: float = 0.0
        
        # Landing gear
        self.gear_positions: List[float] = [1.0, 1.0, 1.0]  # nose, left, right
        self.weight_on_wheels: List[float] = [0.0, 0.0, 0.0]
        self.bump_intensity: float = 0.0
        
        # Control surfaces
        self.flaps_position: float = 0.0
        self.speedbrake_position: float = 0.0
        self.lef_position: float = 0.0  # Leading edge flaps
        
        # System states
        self.on_ground: bool = True
        self.engine_running: bool = False
        self.gear_down: bool = True
        
        # Events and counters
        self.hit_events: int = 0
        self.damage_events: int = 0
        self.collision_counter: int = 0
        
        # IVibe data
        self.ivibe_data: Optional[IVibeData] = None
        
        # Pause detection
        self.last_paused_data: List = []
        
        # Main telemetry data structure
        self.telem_data: Dict[str, Any] = {
            "src": "BMS", 
            "N": "", 
            "AircraftClass": "JetAircraft",  # BMS is primarily jets
            "AircraftType": "JetAircraft"    # Match aircraft_base expectations
        }
        
        # Connection state
        self._connected = False
        self._connection_attempts = 0

        self._pause_state = False
        
    def process_packet(self, packet: bytes) -> bytes:
        """
        Process BMS shared memory data and return formatted telemetry
        This is the main method called by the telemetry system
        """
        # For BMS, we don't use packet data - we read from shared memory
        # This maintains compatibility with the TelemParserBase interface
        
        try:
            # Try to connect if not connected
            if not self._connected:
                self._try_connect()
                if not self._connected:
                    return b""
            
            # Read and process BMS data
            self._read_bms_data()
            self._process_flight_data()
            self._process_ivibe_data()
            
            # Format and return telemetry data
            pause = self.telem_data.get('SimPaused')
            packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in self.telem_data.items()]), "utf-8")
            if pause and not self._pause_state:
                self._pause_state = True
                return packet
            elif not pause:
                self._pause_state = False
                return packet
            else:
                return None


        except Exception as e:
            logger.error(f"Error processing BMS data: {e}")
            self._connected = False
            return b""
    
    def _try_connect(self) -> bool:
        """Try to connect to BMS shared memory"""
        if self._connection_attempts % 6 == 0:  # Log every 6th attempt (1 minute)
            logger.info(f"Trying to connect to BMS shared memory...")
        try:
            if self.bms_memory.connect():
                self._connected = True
                self._connection_attempts = 0
                logger.info("Connected to BMS shared memory")
                return True
            else:
                if self._connection_attempts % 6 == 0:  # Log every 6th attempt (1 minute)
                    logger.info(f"Failed to connect to BMS - is BMS Running?")
                self._connection_attempts += 1
                time.sleep(10)
                return False
        except Exception as e:
            self._connection_attempts += 1
            if self._connection_attempts % 10 == 0:
                logger.error(f"Error connecting to BMS: {e}")
            return False
    
    def _read_bms_data(self):
        """Read current data from BMS shared memory"""
        try:
            # Get flight data
            flight_data = self.bms_memory.get_flight_data()
            flight_data2 = self.bms_memory.get_flight_data2()
            ivibe_data = self.bms_memory.get_ivibe_data()
            
            if flight_data:
                self._process_flight_data_struct(flight_data)
            if flight_data2:
                self._process_flight_data2_struct(flight_data2)
            if ivibe_data:
                self.ivibe_data = ivibe_data
                
        except Exception as e:
            logger.error(f"Error reading BMS data: {e}")
            raise
    
    def _process_flight_data_struct(self, data: FlightData):
        """Process FlightData structure"""
        # Aircraft identification
        ac_from_sm = self.bms_memory.get_string_by_name("AcName")
        if ac_from_sm and ac_from_sm.strip():
            self.ac_name = ac_from_sm.strip()
        else:
            self.ac_name = self.ac_name or "F-16"

        # Engine data - match aircraft_base format
        self.engine_rpm = [data.rpm]
        self.engine_ftit = [data.ftit]
        self.nozzle_pos = [data.nozzlePos]
        self.fuel_flow = [data.fuelFlow]

        # Flight parameters
        self.airspeed_kias = data.kias
        self.airspeed_mach = data.mach
        
        # G-forces - BMS provides normal G in flight data
        # aircraft_base expects [lateral, normal, longitudinal] format
        self.acceleration_Gs = [0.0, data.gs, 0.0]  # Normal G in middle position

        self.altitude_agl = data.z * ft_to_m  # Simplified - BMS doesn't provide direct AGL in basic FlightData

        # AoA for ownship
        self.alpha_deg = data.alpha
        
        # Landing gear - match aircraft_base format
        self.gear_positions = [data.NoseGearPos, data.LeftGearPos, data.RightGearPos]
        self.gear_down = data.gearPos > 0.5
        
        # Weight on wheels calculation (simplified)
        # In BMS, we can estimate based on gear position and on-ground state
        if self.on_ground and self.gear_down:
            self.weight_on_wheels = [1.0, 1.0, 1.0]  # Full weight on all gear
        else:
            self.weight_on_wheels = [0.0, 0.0, 0.0]  # No weight on wheels
        
        # Control surfaces
        self.speedbrake_position = data.speedBrake
        
        # System states
        self.on_ground = bool(data.lightBits3 & LightBits3.OnGround)
        
        # Consumables
        self.chaff_count = data.ChaffCount
        self.flare_count = data.FlareCount
        
        # Additional flight data for aircraft_base compatibility
        self.engine_running = data.rpm > 5.0  # Engine considered running if RPM > 5%

    def _process_flight_data2_struct(self, data: FlightData2):
        """Process FlightData2 structure"""
        # Twin engine data (for aircraft that have it)
        self.engine_rpm2 = [data.rpm2] if hasattr(data, 'rpm2') else [0.0]
        self.engine_ftit2 = [data.ftit2] if hasattr(data, 'ftit2') else [0.0]
        self.nozzle_pos2 = [data.nozzlePos2] if hasattr(data, 'nozzlePos2') else [0.0]
        self.fuel_flow2 = [data.fuelFlow2] if hasattr(data, 'fuelFlow2') else [0.0]
        self.bump_intensity = data.bumpIntensity

        # Position and altitude (convert from feet to meters)
        self.altitude_msl = abs(data.AAUZ) * ft_to_m
        
        # Control surfaces - match aircraft_base field names
        if hasattr(data, 'lefPos'):
            self.lef_position = data.lefPos  # Leading edge flaps
            self.flaps_position = data.lefPos  # Use LEF as flaps for compatibility
            
        # Radar altitude for better AGL calculation
        if hasattr(data, 'RALT'):
            if data.RALT > 0:
                # RALT data appears to be - when inactive/unavailable
                self.altitude_agl = abs(data.RALT * ft_to_m)  # Convert radar altitude to meters

    def _process_flight_data(self):
        """Process and format flight data for telemetry output"""
        # Set basic telemetry data
        self.telem_data["src"] = "BMS"
        if self.ac_name:
            self.telem_data["N"] = self.ac_name
        
        # Aircraft classification for effects compatibility
        self.telem_data["AircraftClass"] = "JetAircraft"
        self.telem_data["AircraftType"] = "JetAircraft"
        
        # Flight parameters - match aircraft_base field names
        self.telem_data['TAS'] = self.airspeed_kias * knots_to_ms  # Convert to m/s
        self.telem_data['IAS'] = self.airspeed_kias * knots_to_ms  # aircraft_base uses same for both
        self.telem_data['Mach'] = self.airspeed_mach
        self.telem_data['AGL'] = self.altitude_agl
        self.telem_data['MSL'] = self.altitude_msl
        
        # Angle of attack (estimate for F-16)
        self.telem_data['AoA'] = self.alpha_deg
        
        # Engine data - match aircraft_base expectations
        if len(self.engine_rpm) == 1:
            self.telem_data['EngRPM'] = self.engine_rpm  # Single engine format
        else:
            self.telem_data['EngRPM'] = self.engine_rpm + self.engine_rpm2  # Multi-engine
        
        self.telem_data['RPM'] = self.engine_rpm  # For prop aircraft compatibility
        self.telem_data['FTIT'] = self.engine_ftit
        self.telem_data['NozzlePos'] = self.nozzle_pos + self.nozzle_pos2
        
        # Afterburner status (estimate from nozzle position)
        max_noz = max(self.nozzle_pos + self.nozzle_pos2)
        max_ff = max(self.fuel_flow + self.fuel_flow2)
        if max_noz >= 0.1 and max_ff >= 8000:
            self.telem_data['Afterburner'] = utils.scale_clamp(max(self.nozzle_pos + self.nozzle_pos2), (0.1, 1.0), (0.0, 1.0)) # Assume AB if nozzle > 80% open
        else:
            self.telem_data['Afterburner'] = 0.0
        # self.telem_data['Afterburner'] = utils.scale_clamp(self.nozzle_pos[0], (0.8, 1.0), (0.0, 1.0)) # Assume AB if nozzle > 80% open

        self.telem_data['FuelFlow'] = self.fuel_flow + self.fuel_flow2

        # Landing gear - match aircraft_base format
        self.telem_data['Gear'] = self.gear_positions  # For MSFS compatibility
        self.telem_data['GearPos'] = self.gear_positions  # For DCS compatibility
        self.telem_data['gear_value'] = max(self.gear_positions)  # Single value version
        self.telem_data['WeightOnWheels'] = self.weight_on_wheels
        self.telem_data['SimOnGround'] = 1 if self.on_ground else 0
        self.telem_data['BumpIntensity'] = self.bump_intensity

        # G-forces - match aircraft_base format exactly
        self.telem_data['ACCs'] = self.acceleration_Gs  # DCS/IL2 format
        self.telem_data['G'] = self.acceleration_Gs[1]  # MSFS format (normal G only)
        
        # Control surfaces - match aircraft_base field names
        self.telem_data['Flaps'] = [self.flaps_position]  # List format for compatibility
        self.telem_data['flaps_value'] = self.flaps_position  # Single value format
        self.telem_data['Speedbrakes'] = [self.speedbrake_position]
        self.telem_data['SpeedbrakePos'] = [self.speedbrake_position]
        self.telem_data['speedbrakes_value'] = self.speedbrake_position
        
        # Weapons and consumables - match aircraft_base expectations
        self.telem_data['ChaffCount'] = self.chaff_count
        self.telem_data['FlareCount'] = self.flare_count
        self.telem_data["Gun"] = self.guns_fired
        self.telem_data["Bombs"] = self.bombs_released
        self.telem_data["Missiles"] = self.missiles_fired
        self.telem_data["PayloadInfo"] = self.bombs_released  + self.missiles_fired
        self.telem_data["Flares"] = [int(self.flare_count)]  # Current count as list
        self.telem_data["Chaff"] = [int(self.chaff_count)]   # Current count as list
        
        # Events
        self.telem_data["Hits"] = self.hit_events
        self.telem_data["Damage"] = self.damage_events
        self.telem_data["Collisions"] = self.collision_counter
        
        # Additional fields for aircraft_base compatibility
        self.telem_data["RetractableGear"] = [1, 1, 1]  # F-16 has retractable gear
        self.telem_data["VerticalSpeed"] = 0.0  # Not available in basic BMS telemetry
        # self.telem_data["StallAoA"] = 25.0  # F-16 approximate stall AoA
        # self.telem_data["DesignSpeed"] = [250.0, 150.0, 180.0]  # Approximate F-16 speeds (vc, vs0, vs1)

    def _process_ivibe_data(self):
        """Process IVibe data for haptic feedback"""
        if not self.ivibe_data:
            return
            
        ivibe = self.ivibe_data
        
        # Update weapon firing counts - detect changes for effects
        ## ivibe 'bullets_fired' seems to be dead in the telemetry (confirmed in bmsflightdata test tool)
        ## just monitor ivibe.IsFiringGun and increment the guns_fired counter
        # if hasattr(ivibe, 'BulletsFired'):
        #     bullets_fired = ivibe.BulletsFired - self.last_bullets_fired
        #     if bullets_fired > 0:
        #         self.guns_fired = [bullets_fired]
        #     else:
        #         self.guns_fired = [0]
        #     self.last_bullets_fired = ivibe.BulletsFired
        if hasattr(ivibe, 'IsFiringGun'):
            if ivibe.IsFiringGun:
                self.guns_fired = [self.guns_fired[0] + 1]
            else:
                self.guns_fired = [0]

        if hasattr(ivibe, 'BombDropped'):
            bombs_dropped = ivibe.BombDropped - self.last_bombs_dropped
            if bombs_dropped > 0:
                self.bombs_released = [bombs_dropped, 0]
            else:
                self.bombs_released = [0, 0]
            self.last_bombs_dropped = ivibe.BombDropped
            
        if hasattr(ivibe, 'AAMissileFired') and hasattr(ivibe, 'AGMissileFired'):
            aa_fired = ivibe.AAMissileFired - self.last_aa_missiles
            ag_fired = ivibe.AGMissileFired - self.last_ag_missiles
            
            if aa_fired > 0 or ag_fired > 0:
                self.missiles_fired = [max(0, aa_fired), max(0, ag_fired)]
            else:
                self.missiles_fired = [0, 0]
                
            self.last_aa_missiles = ivibe.AAMissileFired
            self.last_ag_missiles = ivibe.AGMissileFired
        
        # Update countermeasure counts for change detection
        if hasattr(ivibe, 'FlareDropped'):
            flares_dropped = ivibe.FlareDropped - self.last_flares_dropped
            if flares_dropped > 0:
                # Update current count for display, fired count for effects
                self.telem_data["Flares"] = [flares_dropped]
            self.last_flares_dropped = ivibe.FlareDropped
            
        if hasattr(ivibe, 'ChaffDropped'):
            chaff_dropped = ivibe.ChaffDropped - self.last_chaff_dropped
            if chaff_dropped > 0:
                # Update current count for display, fired count for effects
                self.telem_data["Chaff"] = [chaff_dropped]
            self.last_chaff_dropped = ivibe.ChaffDropped
        
        # Update event counters
        if hasattr(ivibe, 'CollisionCounter'):
            new_collisions = ivibe.CollisionCounter - self.collision_counter
            if new_collisions > 0:
                self.hit_events += new_collisions
            self.collision_counter = ivibe.CollisionCounter
        
        # G-force from IVibe (more accurate than flight data)
        if hasattr(ivibe, 'Gforce'):
            # Update the normal G component with IVibe data
            self.acceleration_Gs[1] = ivibe.Gforce
        
        # System states from IVibe
        if hasattr(ivibe, 'IsOnGround'):
            self.on_ground = ivibe.IsOnGround
        if hasattr(ivibe, 'IsPaused') and hasattr(ivibe, 'IsEndFlight') and hasattr(ivibe, 'IsEjecting') and hasattr(ivibe, 'In3D'):
            if ivibe.IsPaused or ivibe.IsEndFlight or ivibe.IsEjecting:
                # When sim is paused or pilot ejected/died
                self.telem_data["SimPaused"] = 1
            elif not ivibe.IsPaused and not ivibe.IsPaused and not ivibe.IsEjecting and not ivibe.In3D:
                # When sim is started, all flags are false
                self.telem_data["SimPaused"] = 1
            else:
                self.telem_data["SimPaused"] = 0

        if hasattr(ivibe, 'In3D'):
            self.telem_data["In3D"] = 1 if ivibe.In3D else 0
    
    
    def fmt(self, val):
        """Format values for telemetry output (same as IL2Manager)"""
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val

def test_bms_telemetry():
    """Test function for BMS telemetry manager"""
    import time
    import logging
    import faulthandler
    faulthandler.enable()  # Enable fault handler for debugging
    logging.basicConfig(level=logging.INFO)

    bms_manager = BMSManager()

    logging.info("Testing BMS telemetry manager...")
    logging.info("Make sure BMS is running and you're in a flight.")
    logging.info("Press Ctrl+C to exit.")

    try:
        while True:
            # Process telemetry (this is normally called by the telemetry system)
            telemetry_data = bms_manager.process_packet(b"")
            logging.info(f"Telemetry data: {telemetry_data.decode('utf-8') if telemetry_data else 'No data'}")
            
            if telemetry_data:
                # Parse and display some key values
                data_str = telemetry_data.decode('utf-8')
                data_pairs = dict(pair.split('=', 1) for pair in data_str.split(';') if '=' in pair)
                
                print(f"\rConnected: {bms_manager._connected} | "
                      f"Aircraft: {data_pairs.get('N', 'Unknown')} | "
                      f"IAS: {data_pairs.get('IAS', '0')} | "
                      f"ALT: {data_pairs.get('MSL', '0')} | "
                      f"RPM: {data_pairs.get('RPM', '0')} | "
                      f"OnGround: {data_pairs.get('OnGround', '0')}", end='')
            else:
                print(f"\rNot connected to BMS... (attempts: {bms_manager._connection_attempts})", end='')
            
            time.sleep(0.1)  # 10Hz update for testing
            
    except KeyboardInterrupt:
        print("\nTest stopped.")


if __name__ == "__main__":
    test_bms_telemetry()
