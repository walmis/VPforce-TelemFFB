import logging
import math
import socket
import threading
import time
import traceback
from typing import Dict, List, Tuple
from ctypes import LittleEndianStructure, c_uint32, c_float, c_ushort
from typing import List, Iterable
import struct
from enum import IntEnum
from dataclasses import dataclass
import utils

knots = 0.514444
kmh = 1.0 / 3.6
deg = math.pi / 180
fpss2gs = 1 / 32.17405
mpss2gs = 1 / 9.81

def hexdump(src, length=16, sep='.'):
    """Hex dump bytes to ASCII string, padded neatly
    In [107]: x = b'\x01\x02\x03\x04AAAAAAAAAAAAAAAAAAAAAAAAAABBBBBBBBBBBBBBBBBBBBBBBBBB'
    
    In [108]: print('\n'.join(hexdump(x)))
    00000000  01 02 03 04 41 41 41 41  41 41 41 41 41 41 41 41 |....AAAAAAAAAAAA|
    00000010  41 41 41 41 41 41 41 41  41 41 41 41 41 41 42 42 |AAAAAAAAAAAAAABB|
    00000020  42 42 42 42 42 42 42 42  42 42 42 42 42 42 42 42 |BBBBBBBBBBBBBBBB|
    00000030  42 42 42 42 42 42 42 42                          |BBBBBBBB        |
    """
    FILTER = ''.join([(len(repr(chr(x))) == 3) and chr(x) or sep for x in range(256)])
    lines = []
    for c in range(0, len(src), length):
        chars = src[c: c + length]
        hex_ = ' '.join(['{:02x}'.format(x) for x in chars])
        if len(hex_) > 24:
            hex_ = '{} {}'.format(hex_[:24], hex_[24:])
        printable = ''.join(['{}'.format((x <= 127 and FILTER[x]) or sep) for x in chars])
        lines.append('{0:08x}  {1:{2}s} |{3:{4}s}|'.format(c, hex_, length * 3, printable, length))

    print("\n".join(lines))

class BinaryDataReader:
    def __init__(self, data, endian='little'):
        self.data = data
        self.pointer = 0
        self.endian = endian

    def advance(self, offset):
        self.pointer += offset

    # return slice of remaining data
    def data(self):
        return self.data[self.pointer:]
    
    def remaining(self):
        print(len(self.data), self.pointer)
        return len(self.data) - self.pointer

    def _read(self, format_str, size):
        if self.pointer + size > len(self.data):
            raise ValueError("Not enough data to read")
        endian = "<" if self.endian == "little" else ">"
        value = struct.unpack_from(endian + format_str, self.data, self.pointer)[0]
        self.pointer += size
        return value

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


class StateType(IntEnum):
    RPM = 0
    ManifoldPressure = 1
    Val2 = 2
    Val3 = 3
    LandingGearPosition = 4
    LandingGearPressure = 5
    IndicatedAirspeed = 6
    Val7 = 7
    Acceleration = 8
    StallBuffet = 9
    AGL = 10
    FlapsPosition = 11
    AirBrakePosition = 12

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
    Event13 = 13
    Event14 = 14

@dataclass
class MotionDataStructure(LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("ac_vector_yaw", c_float),
        ("ac_vector_pitch", c_float),
        ("ac_vector_roll", c_float),
        ("rot_velocity_x", c_float),
        ("rot_velocity_y", c_float),
        ("rot_velocity_z", c_float),
        ("rot_accel_x", c_float),
        ("rot_accel_y", c_float),
        ("rot_accel_z", c_float)
    ]


class StateDataStructure:
    tick: int = 0
    paused: int = 0
    engine_count: int = 0
    rpm: list[float] = []
    intake_manifold_pressure_pa: list[float] = []
    val2: list[float] = []
    val3: float = 0.0
    landing_gear_count: int = 0
    landing_gear_position: list[float] = []
    landing_gear_pressure: list[float] = []
    indicated_air_speed_metres_second: float = 0.0
    val7: float = 0.0
    acceleration: list[float] = []
    stall_buffet_frequency: float = 0.0
    stall_buffet_amplitude: float = 0.0
    above_ground_level_metres: float = 0.0
    flaps_position: float = 0.0
    air_brake_position: float = 0.0

class IL2Manager():
    def __init__(self):
        self.ac_name: str = ""
        self.engine_info: list = []
        self.engine_rpm: list = []
        self.engine_maxrpm: list = []
        self.gun_data: list = []
        self.guns_fired: list = []
        self.wheel_data: list = []
        self.bombs_data: list = []
        self.bombs_released: int = 0
        self.rockets_data: list = []
        self.rockets_fired: int = 0
        self.ev6_data: list = []
        self.ev13_data: list = []
        self.ev14_data: list = []
        self.hit_data: list = []
        self.damage_data: list = []
        self.seat_data: list = []

        self.acceleration_Gs: list = []
        self.ac_vectors: list = []
        self.rot_velocity: list = []
        self.rot_accel: list = []
        self._changes = {}
        self._change_counter = {}

        self.telem_data = {}

        self.state = StateDataStructure()
        self.motion_data = MotionDataStructure()

    def process_packet(self, packet: bytes) -> Dict[str, List[float]]:
        data = BinaryDataReader(packet)
        packet_header = data.get_uint32()
       
        self.telem_data["src"] = "IL2"

        ##
        ## Run Decoders
        ##
        event_offset = 0

        if packet_header == 0x54000101:
            ## Telemetry/Event Packet
            event_offset = self.decode_telem(data)
            #if event_offset:
                ## If un-parsed data is left after decode_telem, event_offset will indicate the starting point of additional data
            #    self.decode_events(packet, offset=event_offset)

        elif packet_header == 0x494C0100:
            ## Motion telemetry (aircraft orientation, rotational vectors, etc) have a different header signature
            self.decode_motion(packet[8:])

        else:
            logging.error(f'Unknown packet type:  Header=0x{packet_header:X}')

        self.telem_data["N"] = self.ac_name
        self.telem_data['TAS'] = self.state.indicated_air_speed_metres_second
        self.telem_data['RPM'] = list(self.state.rpm)
        self.telem_data['Manifold'] = list(self.state.intake_manifold_pressure_pa)
        self.telem_data['GearPos'] = list(self.state.landing_gear_position)
        self.telem_data['WeightOnWheels'] = list(self.state.landing_gear_pressure)
        self.telem_data['G'] = self.acceleration_Gs
        self.telem_data['StallBuffetFrequency'] = self.state.stall_buffet_frequency
        self.telem_data['StallBuffetAmplitude'] = self.state.stall_buffet_amplitude
        self.telem_data['Alt'] = self.state.above_ground_level_metres
        self.telem_data['Flaps'] = self.state.flaps_position
        self.telem_data['Speedbrakes'] = self.state.air_brake_position
        self.telem_data["EngineMaxRPM"] = self.engine_info
        self.telem_data["GunData"] = self.gun_data
        self.telem_data["GunsFired"] = self.guns_fired
        # self.telem_data["wheeldata"] = self.wheel_data
        self.telem_data["BombData"] = self.bombs_data
        self.telem_data["BombsReleased"] = self.bombs_released
        self.telem_data["RocketData"] = self.rockets_data
        self.telem_data["RocketsFired"] = self.rockets_fired
        self.telem_data["HitData"] = self.hit_data
        self.telem_data["DamageData"] = self.damage_data
        self.telem_data["SeatData"] = self.seat_data
        self.telem_data['unknown_data_2'] = list(self.state.val2)
        self.telem_data['unknown_data_3'] = self.state.val3
        self.telem_data['unknown_evt_6'] = self.ev6_data
        self.telem_data['unknown_data_7'] = self.state.val7
        self.telem_data['unknown_evt_13'] = self.ev13_data
        self.telem_data['unknown_evt_14'] = self.ev14_data
        self.telem_data['ac_vectors'] = self.ac_vectors
        self.telem_data['rot_velocity'] = self.rot_velocity
        self.telem_data['rot_accel'] = self.rot_accel

        return self.telem_data

    def decode_motion(self, packet: bytes):
        self.motion_data = MotionDataStructure.from_buffer_copy(packet)

        self.ac_vectors = [self.motion_data.ac_vector_yaw, self.motion_data.ac_vector_pitch, self.motion_data.ac_vector_roll]
        self.rot_velocity = [self.motion_data.rot_velocity_x, self.motion_data.rot_velocity_y, self.motion_data.rot_velocity_z]
        self.rot_accel = [self.motion_data.rot_accel_x, self.motion_data.rot_accel_y, self.motion_data.rot_accel_z]

        print (self.ac_vectors)

    def decode_telem(self, data: BinaryDataReader) -> int:

        packet_size = data.get_uint16()
        tick = data.get_uint32()

        if packet_size == 12:
            self.telem_data["SimPaused"] = 1
        else:
            self.telem_data["SimPaused"] = 0

        print(f"tick {tick} size {packet_size}")

        self.state.tick = tick

        length = data.get_uint8()
        print("len", length)

        ##
        ## Decode fixed structure telemetry data
        ##
        for _ in range(length):
            
            state_type = data.get_uint16()
            state_length = data.get_uint8()

            print(StateType(state_type), "len",  state_length)
            
            get_state_floats = lambda: [data.get_float() for i in range(0, state_length)]

            if state_type == StateType.RPM:
                self.state.engine_count = state_length
                self.state.rpm = get_state_floats()

            elif state_type == StateType.ManifoldPressure:
                self.state.intake_manifold_pressure_pa = get_state_floats()

            elif state_type == StateType.Val2:
                self.state.val2 = get_state_floats()

            elif state_type == StateType.Val3:
                self.state.val3 = get_state_floats()

            elif state_type == StateType.LandingGearPosition:
                self.state.landing_gear_count = state_length
                self.state.landing_gear_position = get_state_floats()

            elif state_type == StateType.LandingGearPressure:
                self.state.landing_gear_count = state_length
                self.state.landing_gear_pressure = get_state_floats()

            elif state_type == StateType.IndicatedAirspeed:
                self.state.indicated_air_speed_metres_second = data.get_float()

            elif state_type == StateType.Val7:
                self.state.val7 = data.get_float()

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

            else:
                logging.error(f"Unknown state type: {state_type}")


        print("remaining", data.remaining())

    def decode_events(self, packet: bytes, offset) -> int:

        while offset < len(packet):
            eventType = struct.unpack_from('<H', packet, offset)[0]
            try:
                eventType = EventType(eventType)
            except:
                pass
            eventBytes = packet[offset + 2]
            offset += 3
            if eventType == EventType.VehicleName:
                name_length = packet[offset]
                aircraft_name = packet[offset + 1: offset + 1 + name_length].decode('ascii').rstrip('\0')
                if self.anything_has_changed("ac_name", aircraft_name):
                    self.__init__()

                self.ac_name = aircraft_name
            elif eventType == EventType.EngineData:

                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                ed = []
                while ev_type == EventType.EngineData:
                    index = struct.unpack_from('<H', packet, offset)[0]
                    data = struct.unpack_from('<fff', packet, offset + 4)
                    max_rpm = struct.unpack_from('<f', packet, offset + 16)[0]
                    ed.append(max_rpm)
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.engine_info = ed
                offset -= (eventBytes + 3)
            elif eventType == EventType.GunData:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                gd = []
                while ev_type == EventType.GunData:
                    data = struct.unpack_from('<fff', packet, offset + 2)
                    mass = struct.unpack_from('<f', packet, offset + 14)[0]
                    mass = format(mass, '.4f')
                    velocity = struct.unpack_from('<f', packet, offset + 18)[0]
                    gd.append([float(mass), velocity])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.gun_data = gd
                offset -= (eventBytes + 3)

            elif eventType == EventType.GunFired:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                gf = []
                print(">>")
                while ev_type == EventType.GunFired:
                    gun_index = packet[offset]
                    print(f"gun_index:{gun_index}")
                    if len(self.guns_fired) < gun_index + 1:
                        self.guns_fired.append(0)
                    else:
                        self.guns_fired[gun_index] += 1
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass

                offset -= (eventBytes + 3)

            elif eventType == EventType.WheelData:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                wd = []
                while ev_type == EventType.WheelData:
                    index = struct.unpack_from('<H', packet, offset)[0]
                    data = struct.unpack_from('<fff', packet, offset + 4)
                    wd.append(data)
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.wheel_data = wd
                offset -= (eventBytes + 3)

            elif eventType == EventType.BombRelease:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                br = []
                while ev_type == EventType.BombRelease:
                    data = struct.unpack_from('<fff', packet, offset)
                    mass = struct.unpack_from('<f', packet, offset + 12)[0]
                    type = struct.unpack_from('<H', packet, offset + 16)[0]
                    br.append([data, mass, type])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.bombs_data = br
                self.bombs_released += 1


            elif eventType == EventType.RocketLaunch:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                rl = []
                while ev_type == EventType.RocketLaunch:
                    data = struct.unpack_from('<fff', packet, offset)
                    mass = struct.unpack_from('<f', packet, offset + 12)[0]
                    type = struct.unpack_from('<H', packet, offset + 16)[0]
                    rl.append([data, mass, type])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.rockets_data = rl
                self.rockets_fired += 1

                offset -= (eventBytes + 3)

            elif eventType == EventType.Event6:
                vec0 = struct.unpack_from('<fff', packet, offset)
                vec1 = struct.unpack_from('<fff', packet, offset + 12)
                self.ev6_data.append((vec0, vec1))

            elif eventType == EventType.Hit:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                ht = []
                while ev_type == EventType.Hit:
                    data = struct.unpack_from('<fff', packet, offset)
                    force = struct.unpack_from('<fff', packet, offset + 12)
                    ht.append([data, force])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.hit_data = ht
                offset -= (eventBytes + 3)

            elif eventType == EventType.Damage:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                dg = []
                while ev_type == EventType.Damage:
                    data = struct.unpack_from('<fff', packet, offset)
                    float0 = struct.unpack_from('<f', packet, offset + 12)[0]
                    dg.append([data, float0])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.damage_data = dg
                offset -= (eventBytes + 3)

            elif eventType == EventType.CurrentSeat:
                ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                cs = []
                while ev_type == EventType.CurrentSeat:
                    seat = struct.unpack_from('<I', packet, offset)[0]
                    ushort0 = struct.unpack_from('<H', packet, offset + 4)[0]
                    cs.append([seat, ushort0])
                    offset += (eventBytes + 3)
                    ev_type = 0
                    try:
                        ev_type = struct.unpack_from('<H', packet, offset - 3)[0]
                    except:
                        pass
                self.seat_data = cs
                offset -= (eventBytes + 3)

            elif eventType == EventType.Event13:
                data = struct.unpack_from('<fff', packet, offset)
                self.ev13_data = data
            elif eventType == EventType.Event14:
                data1 = struct.unpack_from('<i', packet, offset)[0]
                data2 = struct.unpack_from('<h', packet, offset + 4)[0]
                self.ev14_data = [data1, data2]
            else:
                logging.error(f"Unknown event type: {eventType}")

            offset += eventBytes
        return offset
    
    def has_changed(self, item: str, delta_ms=0, data=None) -> bool:
        if data == None:
            data = self.telem_data

        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = data.get(item)

        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter())

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False

    def anything_has_changed(self, item: str, value, delta_ms=0):
        """track if any parameter, given as key "item" has changed between two consecutive calls of the function
        delta_ms can be used to smooth the effects of telemetry which does not update regularly but is still "moving"
        a positive delta_ms value will allow the data to remain unchanged for that period of time before returning false"""

        prev_val, tm, changed_yet = self._changes.get(item, (None, 0, 0))
        new_val = value
        new_tm = time.perf_counter()
        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        # make sure we do not return true until the key has changed at least once (after init)
        if prev_val == None and not changed_yet:
            self._changes[item] = (new_val, tm, 0)
            prev_val = new_val

        # logging.debug(f"Prev: {prev_val}, New: {new_val}, TM: {tm}")

        if prev_val != new_val:
            self._changes[item] = (new_val, new_tm, 1)

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val, new_tm - tm)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False
    
if __name__ == "__main__":
    import gzip
    import base64

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    s.bind(("", 34385))
    s.settimeout(1)

    il2 = IL2Manager()

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
