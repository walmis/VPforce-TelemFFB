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


###################################################################################
###################################################################################
## The IL2 telemetry decoding functions in this module are loosely
## based on the work done by 'sicsix' and the IL2Shaker github project
## https://github.com/sicsix/IL2Shaker
###################################################################################
###################################################################################


import logging
import math
import socket
import threading
import time
import traceback
from typing import Dict, List, Tuple
from typing import List, Iterable
import struct
from enum import IntEnum
from dataclasses import dataclass
from telemffb.telem.TelemParserBase import TelemParserBase
import telemffb.utils as utils
import telemffb.globals as G
import pygetwindow as get_focus_window

from telemffb.utils import hexdump
import json
from telemffb.util import conversions as conv

# use centralized conversion constants
knots = conv.kt2ms
kmh = conv.kmh2ms
deg = math.pi / 180
fpss2gs = conv.fpss2gs
mpss2gs = conv.mpss2gs

dbg_en = 0
dbg_lvl = 0
def dbg(level, *args, **kwargs):
    if dbg_en and level <= dbg_lvl:
        print(*args, **kwargs)


class BinaryDataReader:
    def __init__(self, data, endian='little'):
        self.buffer = data
        self.pointer = 0
        self.endian = endian

    def advance(self, offset):
        self.pointer += offset
   
    def remaining(self):
        return len(self.buffer) - self.pointer

    def _read(self, format_str, size, peek=False):
        if self.pointer + size > len(self.buffer):
            raise ValueError("Not enough data to read")
        endian = "<" if self.endian == "little" else ">"
        value = struct.unpack_from(endian + format_str, self.buffer, self.pointer)[0]
        if peek:
            # don't advance pointer, just return data for analysis
            return value
        else:
            self.pointer += size
            return value
    
    # return slice of remaining data
    def get_data(self, length, peek=False):
        data = self.buffer[self.pointer:self.pointer+length]
        if peek:
            # don't advance pointer, just return data for analysis
            return data
        else:
            self.pointer += length
            return data


    def get_uint32(self):
        return self._read('I', 4)

    def get_uint16(self):
        return self._read('H', 2)

    def get_float(self):
        return self._read('f', 4)

    def get_double(self):
        return self._read('d', 8)

    def get_int32(self):
        return self._read('i', 4)

    def get_int16(self):
        return self._read('h', 2)
    
    def get_uint8(self):
        return self._read('B', 1)
    
    def get_int8(self):
        return self._read('b', 1)
    
    def get_char(self):
        return self._read('c', 1)
    
    def get_vector3f(self):
        return [self.get_float(), self.get_float(), self.get_float()]


class StateType(IntEnum):
    RPM = 0
    ManifoldPressure = 1
    EngineShakeFrequency = 2
    EngineShakeAmplitude = 3
    LandingGearPosition = 4
    LandingGearPressure = 5
    IndicatedAirspeed = 6
    AOA = 7
    Acceleration = 8
    StallBuffet = 9
    AGL = 10
    FlapsPosition = 11
    AirBrakePosition = 12
    AOS = 13
    VerticalSpeed = 14

class EventType(IntEnum):
    VehicleName = 0
    EngineData = 1
    GunData = 2
    WheelData = 3
    BombRelease = 4
    RocketLaunch = 5
    Event6 = 6
    Hit = 7
    Damage = 8
    GunFired = 9
    CurrentSeat = 11
    MPServerInfo = 10
    Event13 = 13
    Event14 = 14


class ForceType(IntEnum):
    Spring = 0
    Const = 1
    Damper = 2


class StateDataStructure:
    tick: int = 0
    paused: int = 0
    engine_count: int = 0
    rpm: list = [0.0]
    intake_manifold_pressure_pa: list[float] = []
    engine_shake_frequency: list[float] = []
    engine_shake_amplitude: list[float] = []
    landing_gear_count: int = 0
    landing_gear_position: list[float] = [1,1,1]
    landing_gear_pressure: list[float] = [0,0,0]
    indicated_air_speed_metres_second: float = 0.0
    angle_of_attack_rad: float = 0.0
    angle_of_sideslip_rad: float = 0.0
    vertical_speed_metres_second: float = 0.0
    acceleration: list[float] = [0,0,0]
    acceleration_Gs: list[float] = [0,0,0]
    stall_buffet_frequency: float = 0.0
    stall_buffet_amplitude: float = 0.0
    above_ground_level_metres: float = 0.0
    flaps_position: float = 0.0
    air_brake_position: float = 0.0


class IL2TelemParser(TelemParserBase):
    def __init__(self):
        self.ac_name: str = ""
        self.engine_info: list = []
        self.engine_rpm: list = [0.0]
        self.engine_maxrpm: list = []
        self.rpm_pcts: list = [0]
        self.gun_data: list = []
        self.gun_data_dict: dict = {}
        self.guns_fired: list = [0]
        self.gunfire_dict: dict = {}
        self.wheel_data: list = []
        self.bombs_data: list = []
        self.bombs_released: list = [0,0]
        self.rockets_data: list = []
        self.rockets_fired: list = [0,0]
        self.ev6_data: list = []
        self.ev13_data: list = []
        self.ev14_data: list = []
        self.hit_data: list = []
        self.hit_events: int = 0
        self.ev_damage_data: list = []
        self.damage_events: int = 0
        self.seat_data: list = []
        self.unknown_states: dict = {}
        self._unknown_state_warned: set = set()
        self.ffb_records: list = []

        self.acceleration_Gs: list = []
        self.acc_vectors: list = []
        self.rot_velocity: list = []
        self.rot_accel: list = []
        self.guns_dict = {}
        self._changes = {}
        self._change_counter = {}
        self.last_paused_data: list = []
        self._stale_count: int = 0
        self._il2_stop_state: bool = False
        self.telem_data = {"src": "IL2", "N": "", "AircraftClass": "unknown"}

        self.state = StateDataStructure()

    def process_packet(self, packet: bytes) -> bytes:
        data = BinaryDataReader(packet)
        packet_header = data.get_uint32()
        lenpack = len(packet)
        hex_pack = packet.hex().upper()
        ##
        ## Run Decoders
        ##

        if packet_header == 0x54000101:
            ## Telemetry/Event Packet
            self.decode_telem(data)

        elif packet_header == 0x494C0100:
            ## Motion telemetry (aircraft orientation, rotational vectors, etc) have a different header signature
            self.decode_motion(data)

        elif packet_header == 0x494D0100:
            ## FFB device packet (IL-2 Korea) - raw per-axis force commands (spring/const/damper)
            self.decode_ffb(data)

        else:
            logging.error(f'Unknown packet type:  Header=0x{packet_header:X}')

        self.telem_data["src"] = "IL2"
        if self.ac_name != "":
            self.telem_data["N"] = self.ac_name
        self.telem_data['TAS'] = self.state.indicated_air_speed_metres_second
        self.telem_data['IAS'] = self.state.indicated_air_speed_metres_second
        self.telem_data['AGL'] = self.state.above_ground_level_metres
        self.telem_data['RPM'] = self.state.rpm
        self.telem_data["MaxRPM"] = self.engine_maxrpm
        try:
            self.rpm_pcts = [(a / b) * 100 for a, b in zip(self.state.rpm, self.engine_maxrpm)]
        except: pass
        self.telem_data["EngRPM"] = self.rpm_pcts

        # self.telem_data['Manifold'] = list(self.state.intake_manifold_pressure_pa)
        self.telem_data['GearPos'] = list(self.state.landing_gear_position)

        try:
            ## Reorder gear to match DCS for function re-use
            update_gear_indices = [0, 2, 1]
            self.state.landing_gear_pressure = [self.state.landing_gear_pressure[i] for i in update_gear_indices]
        except: pass

        self.telem_data['T'] = self.state.tick
        self.telem_data['WeightOnWheels'] = list(self.state.landing_gear_pressure)
        self.telem_data['ACCs'] = self.state.acceleration_Gs
        self.telem_data['BuffetFrequency'] = self.state.stall_buffet_frequency
        self.telem_data['BuffetAmplitude'] = self.state.stall_buffet_amplitude * 10  # multiply by factor of 10 to make usable in 0-1.0 range
        self.telem_data['Flaps'] = self.state.flaps_position
        self.telem_data['Speedbrakes'] = self.state.air_brake_position
        self.telem_data["GunData"] = self.gun_data
        self.telem_data['GunDict'] = self.gun_data_dict
        self.telem_data["GunFireData"] = json.dumps(self.gunfire_dict)
        self.telem_data["Gun"] = self.guns_fired
        self.telem_data["Bombs"] = self.bombs_released
        self.telem_data["Rockets"] = self.rockets_fired
        self.telem_data["Hits"] = self.hit_events
        self.telem_data["Damage"] = self.damage_events
        self.telem_data["SeatData"] = self.seat_data
        self.telem_data['EngineShakeFrequency'] = list(self.state.engine_shake_frequency)
        self.telem_data['EngineShakeAmplitude'] = list(self.state.engine_shake_amplitude)
        self.telem_data['AOA_rad'] = self.state.angle_of_attack_rad
        self.telem_data['AOS_rad'] = self.state.angle_of_sideslip_rad
        self.telem_data['AOA'] = self.state.angle_of_attack_rad * conv.rad2deg
        self.telem_data['AOS'] = self.state.angle_of_sideslip_rad * conv.rad2deg
        self.telem_data['VerticalSpeed'] = self.state.vertical_speed_metres_second
        self.telem_data['unknown_evt_6'] = self.ev6_data
        self.telem_data['unknown_evt_13'] = self.ev13_data
        self.telem_data['unknown_evt_14'] = self.ev14_data
        self.telem_data['acc_vectors'] = self.acc_vectors
        self.telem_data['rot_velocity'] = self.rot_velocity
        self.telem_data['rot_accel'] = self.rot_accel
        for state_type, values in self.unknown_states.items():
            self.telem_data[f'unknown_state_{state_type}'] = values
        if G.il2_ffb_device_ordinal is not None:
            self.telem_data['FFBOrdinal'] = G.il2_ffb_device_ordinal
            self.telem_data['FFBRecords'] = [r for r in self.ffb_records if r['dev'] == G.il2_ffb_device_ordinal]
        else:
            # ordinal not resolved (not IL-2 Korea, or not yet resolved) - expose unfiltered records
            self.telem_data['FFBRecords'] = self.ffb_records

        # create structure of the most real-time data to determine if updated telemetry is flowing.  If not, consider
        # the sim paused.  This avoids effects continuing to play when in multiplayer map or after crash since IL-2 never stops sending
        # stale frames.  Require 5 consecutive identical frames to avoid false positives during steady flight.
        paused_data = [
            list(self.state.acceleration_Gs),
            self.state.above_ground_level_metres,
            list(self.state.rpm),
        ]
        if paused_data == self.last_paused_data:
            self._stale_count += 1
        else:
            self._stale_count = 0
        self.telem_data['MPMenu'] = (self._stale_count >= 5)
        self.last_paused_data = paused_data

        packet = bytes(";".join([f"{k}={self.fmt(v)}" for k, v in self.telem_data.items()]), "utf-8")

        # When MPMenu or focus-loss is detected, stop submitting frames so TelemManager
        # times out naturally (same pattern as SimConnectManager for MSFS pause states).
        should_stop = self.telem_data.get('MPMenu', False) or not self.telem_data.get('Focus', 1)
        if should_stop:
            self._il2_stop_state = True
            return None
        else:
            self._il2_stop_state = False
            return packet

    def decode_motion(self, data : BinaryDataReader):
        tick = data.get_uint32()
        self.state.tick = tick

        self.acc_vectors = data.get_vector3f()
        self.rot_velocity = data.get_vector3f()
        self.rot_accel = data.get_vector3f()

        dbg(1,"acc", self.acc_vectors)

    def decode_ffb(self, data: BinaryDataReader):
        tick = data.get_uint32()
        n_records = data.get_uint32()

        records = []
        for _ in range(n_records):
            axis = data.get_uint8()
            dev_no = data.get_uint8()
            force_type_raw = data.get_uint8()
            try:
                force_type = ForceType(force_type_raw)
            except ValueError:
                force_type = force_type_raw
            pos = data.get_float()
            force = data.get_float()
            amp = data.get_float()
            freq = data.get_float()
            records.append({
                'axis': axis,
                'dev': dev_no,
                'type': force_type,
                'pos': pos,
                'force': force,
                'amp': amp,
                'freq': freq,
            })

        dbg(1, "FFB records", records)
        self.ffb_records = records

    def decode_telem(self, data: BinaryDataReader):
        packet_size = data.get_uint16()
        tick = data.get_uint32()

        # print(f"ACTIVE WINDOW: {get_focus_window.getActiveWindow().title}")


        if packet_size == 12:
            self.telem_data["SimPaused"] = 1
        else:
            self.telem_data["SimPaused"] = 0

        # try:
        #     focus_window = get_focus_window.getActiveWindow().title
        # except:
        #     focus_window = "unknown"
        # if G.system_settings.get('focus_pauseIL2', True):
        #     if "Il-2" in focus_window:
        #         self.telem_data["Focus"] = 1
        #         self.telem_data["SimPaused"] = 0
        #     else:
        #         self.telem_data["Focus"] = 0
        #         self.telem_data["SimPaused"] = 1
        # else:
        #     self.telem_data["Focus"] = 1
        #     self.telem_data["SimPaused"] = 0

        dbg(1,f"telem tick {tick} size {packet_size}")

        self.state.tick = tick

        length = data.get_uint8()
        dbg(1,"len", length)

        ##
        ## Decode fixed structure telemetry data
        ##
        for _ in range(length):
            
            state_type = data.get_uint16()
            state_length = data.get_uint8()

            state_type_name = StateType(state_type) if state_type in StateType._value2member_map_ else f"Unknown({state_type})"
            dbg(1, state_type_name, "len", state_length)
            
            get_state_floats = lambda: [data.get_float() for i in range(0, state_length)]

            if state_type == StateType.RPM:
                self.state.engine_count = state_length
                self.state.rpm = get_state_floats()

            elif state_type == StateType.ManifoldPressure:
                self.state.intake_manifold_pressure_pa = get_state_floats()

            elif state_type == StateType.EngineShakeFrequency:
                self.state.engine_shake_frequency = get_state_floats()

            elif state_type == StateType.EngineShakeAmplitude:
                self.state.engine_shake_amplitude = get_state_floats()

            elif state_type == StateType.LandingGearPosition:
                self.state.landing_gear_count = state_length
                self.state.landing_gear_position = get_state_floats()

            elif state_type == StateType.LandingGearPressure:
                self.state.landing_gear_count = state_length
                self.state.landing_gear_pressure = get_state_floats()

            elif state_type == StateType.IndicatedAirspeed:
                self.state.indicated_air_speed_metres_second = data.get_float()

            elif state_type == StateType.AOA:
                self.state.angle_of_attack_rad = data.get_float()

            elif state_type == StateType.Acceleration:
                self.state.acceleration = get_state_floats()
                self.state.acceleration_Gs = [x * mpss2gs for x in self.state.acceleration]

            elif state_type == StateType.StallBuffet:
                self.state.stall_buffet_frequency = data.get_float()
                self.state.stall_buffet_amplitude = data.get_float()

            elif state_type == StateType.AGL:
                self.state.above_ground_level_metres = data.get_float()

            elif state_type == StateType.FlapsPosition:
                self.state.flaps_position = data.get_float()

            elif state_type == StateType.AirBrakePosition:
                self.state.air_brake_position = data.get_float()

            elif state_type == StateType.AOS:
                self.state.angle_of_sideslip_rad = data.get_float()

            elif state_type == StateType.VerticalSpeed:
                self.state.vertical_speed_metres_second = data.get_float()

            else:
                if state_type not in self._unknown_state_warned:
                    logging.warning(f"Unknown state type: {state_type}, storing as unknown_state_{state_type}")
                    self._unknown_state_warned.add(state_type)
                self.unknown_states[state_type] = get_state_floats()

        b = data.get_uint8()
        dbg(1,"last byte", b)

        self.decode_events(data)

    def decode_events(self, data : BinaryDataReader) -> int:
        dbg(1,"decode_events remaining_data:", data.remaining())
        if data.remaining() < 2: return
        # self.engine_maxrpm = []
        dbg(1, "event packet")
        while data.remaining():
            eventType = data.get_uint16()
            eventBytes = data.get_uint8()
            try:
                event = EventType(eventType)
            except:
                logging.warning(f"Unknown Event Type: {eventType}")
                event = 'Unknown'
            dbg(0,"-- event, type", event, "eventBytes", eventBytes)
            dbg(0,hexdump(data.buffer[data.pointer:data.pointer+eventBytes]))
            if eventType == EventType.MPServerInfo:
                logging.debug("MP Server info received")
                return
            if eventType == EventType.VehicleName:
                name_length = data.get_uint8()
                name_data = data.get_data(name_length)
                # print(f"NAMEDATA={name_data}")
                name_hex = name_data.hex().upper()
                aircraft_name = name_data.decode('ascii').rstrip('\x00')
                # print(f"ACNAME={aircraft_name}")
                # aircraft_name = ''.join(c for c in aircraft_name if ord(c) <= 127)
                if aircraft_name != self.ac_name:
                    logging.info(f"aircraft_name={aircraft_name} | self.ac_name={self.ac_name}")
                    self.__init__()
                    
                self.ac_name = aircraft_name

            elif eventType == EventType.EngineData:
                index = data.get_uint16()
                index2 = data.get_uint16()
                engine_data = data.get_vector3f()
                max_rpm = data.get_float()
                if len(self.engine_maxrpm) <= index:
                    self.engine_maxrpm.append(max_rpm)
                else:
                    self.engine_maxrpm[index] = max_rpm
                dbg(1,"EngineData", f"{index=} {index2=} {engine_data=} {max_rpm=}")


            elif eventType == EventType.GunData:
                index = data.get_uint16()
                offset = data.get_vector3f()
                mass = data.get_float()
                velocity = data.get_float()
                dbg(1,"GunData", f"{index=} {offset=} {mass=} {velocity=}")

                if len(self.gun_data) <= index:
                    self.gun_data.append([index, round(mass, 4), velocity])
                else:
                    self.gun_data[index] = ([index, round(mass, 4), velocity])

                self.gun_data_dict[index] = f"{round(mass, 4)}, {velocity}"


            elif eventType == EventType.GunFired:
                gun_index = data.get_uint8()
                dbg(1,"GunFired", gun_index)
                if len(self.guns_fired) < gun_index + 1:
                    self.guns_fired.append(0)
                else:
                    self.guns_fired[gun_index] += 1
                gun_key = self.gun_data_dict.get(gun_index, None)
                if gun_key:
                    if gun_key in self.gunfire_dict.keys():
                        self.gunfire_dict[gun_key] += 1
                    else:
                        self.gunfire_dict[gun_key] = 1

            elif eventType == EventType.WheelData:
                index = data.get_uint16()
                index2 = data.get_uint16()
                offset = data.get_vector3f() # wheel positions in 3d space
                dbg(1,f"{index=} {index2=} {offset=}")


            elif eventType == EventType.BombRelease:
                index = data.get_vector3f()
                print(f"BOMB-{index}")
                mass = data.get_float()
                type = data.get_uint16()

                dbg(1,f"{index=} {mass=} {type=}")

                # self.bombs_data = []
                self.bombs_released[0] = mass
                self.bombs_released[1] += 1

            elif eventType == EventType.RocketLaunch:
                offset = data.get_vector3f()
                mass = data.get_float()
                type = data.get_uint16()
                dbg(1,f"{offset=} {mass=} {type=}")

                self.rockets_fired[0] = mass
                self.rockets_fired[1] += 1

            elif eventType == EventType.Event6:
                vec0 = data.get_vector3f()
                vec1 = data.get_vector3f()
                self.ev6_data = (vec0, vec1)

            elif eventType == EventType.Hit:
                offset = data.get_vector3f()
                force = data.get_vector3f()
                dbg(1, "HIT EVENT", f"{offset=} {force=}")

                self.hit_data = (offset, force)
                self.hit_events += 1

            elif eventType == EventType.Damage:
                offset = data.get_vector3f()
                float0 = data.get_float()
                dbg(1, "DAMAGE", f"{offset=} {float0=}")

                self.ev_damage_data = (offset, float0)
                self.damage_events += 1

            elif eventType == EventType.CurrentSeat:
                # Triggers on seat change     
                # Seems to be all 1s (4294967295) if pilot or co-pilot, and 1023 if any gunner
                # Not sure what the ushort value represents, but seems to be 1 or 2
                seat = data.get_uint32()
                ushort0 = data.get_uint16()

                self.seat_data = [seat, ushort0]
                dbg(1, "SEAT", f"{seat=} {ushort0=}")

            elif eventType == EventType.Event13:
                # ev13_length = data.get_uint8()
                ev13_data = data.get_data(eventBytes)
                ev13_hex = ev13_data.hex().upper()
                # ev13_name = ev13_data.decode('ascii').strip()
                # self.ev13_data = data.get_vector3f()
                # b = data.get_uint8() #get last byte
            elif eventType == EventType.Event14:
                data1 = data.get_int32()
                data2 = data.get_int16()
                dbg(1,"EVENT-14", f"{data1=} {data2=}")
                self.ev14_data = [data1, data2]
            else:
                logging.error(f"Unknown event type: {eventType}")


    def fmt(self, val):
        if isinstance(val, list):
            return "~".join([str(x) for x in val])
        return val


def log_il2_trace():
    import gzip
    import base64

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    s.bind(("", 34385))
    s.settimeout(1)

    il2 = IL2TelemParser()

    tbase = time.time()

    f = gzip.open('il2_test_data.gz', 'w')
    print("Listening")
    while True:
        try:
            try:
                frame, sender = s.recvfrom(65535)
                f.write(f"t={time.time() - tbase}\n".encode("ascii"))
                f.write(base64.b64encode(frame) + b"\n")
                
                #il2.process_packet(frame)
            except TimeoutError:
                continue
            except Exception as e:
                traceback.print_exc()

        except KeyboardInterrupt:
            f.close()
            print("Exit")
            break

def test_il2_trace():
    import gzip
    import base64

    il2 = IL2TelemParser()

    f = gzip.open('il2_test_data.gz', 'r')
    while True:
        line = f.readline()
        if not line: break
        if line.startswith(b"t"):
            t = float(line.split(b"=")[1])
            data = base64.b64decode(f.readline())

            il2.process_packet(data)

if __name__ == "__main__":
    test_il2_trace()
            
        
