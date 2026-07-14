#
# WinWing SimAppPro UDP bridge
#
# Forwards TelemFFB telemetry to SimAppPro (localhost:16536) using the
# JSON-over-UDP protocol that SimAppPro also accepts from DCS/IL-2/MSFS.
# SimAppPro then drives the vibration motors in WinWing handles autonomously.
#
# Protocol reference: wwtNetwork.lua inside SimAppPro installation
#   %LOCALAPPDATA%\Programs\SimAppPro\resources\app.asar.unpacked\Events\wwt\
#
# Key protocol facts (from reverse-engineering dcs-lua.js and DCSShakeEffect.js):
#   - addCommon args keys must NOT have a "Direct_" prefix; SimAppPro strips
#     that prefix when building directParam from the effectConfig.  Sending
#     e.g. "trueAirSpeed" is correct; "Direct_trueAirSpeed" is silently ignored.
#   - gearValue / speedbrakesValue are raw 0-1 floats; SimAppPro applies
#     scale=100 internally to produce scaleValue.
#   - Touchdown is detected via gearLeftRod/RightRod/NoseRod 0→non-zero
#     transition (not via a Leaf_ key in addCommon).
#   - Cannon fire is detected via cannonShellsCount decreasing.
#   - Payload release via payloadStations array element changes.
#

import json
import logging
import socket
import time

log = logging.getLogger(__name__)

SIMAPPPRO_HOST = "127.0.0.1"
SIMAPPPRO_PORT = 16536
HEARTBEAT_INTERVAL = 3.0  # seconds, matches SimAppPro's heartbeat check

# Minimum seconds between consecutive on_telemetry forwards (SimAppPro
# updates at ~50 Hz internally; we don't need to push every frame).
_SEND_INTERVAL = 0.05  # 20 Hz

# Map TelemFFB's internal aircraft names / MSFS titles to DCS aircraft IDs
# that SimAppPro has effect profiles for.  The first substring match wins.
_AIRCRAFT_MAP = [
    ("FA-18",       "FA-18C_hornet"),
    ("F/A-18",      "FA-18C_hornet"),
    ("Hornet",      "FA-18C_hornet"),
    ("F-16",        "F-16C_50"),
    ("F16",         "F-16C_50"),
    ("Viper",       "F-16C_50"),
    ("F-15",        "F-15C"),
    ("F15",         "F-15C"),
    ("F-14",        "F-14B"),
    ("Tomcat",      "F-14B"),
    ("A-10",        "A-10C"),
    ("Ka-50",       "Ka-50"),
    ("Mi-8",        "Mi-8MT"),
    ("UH-1",        "UH-1H"),
    ("Huey",        "UH-1H"),
    ("AH-64",       "AH-64D_BLK_II"),
    ("Apache",      "AH-64D_BLK_II"),
    ("Spitfire",    "SpitfireLFMkIX"),
    ("P-51",        "P-51D"),
    ("TF-51",       "TF-51D"),
    ("F-5",         "F-5E-3"),
    ("M-2000",      "M-2000C"),
    ("Mirage",      "M-2000C"),
    ("Su-25",       "Su-25T"),
    ("Su-27",       "Su-27"),
    ("Su-33",       "Su-33"),
    ("MiG-29",      "MiG-29A"),
    ("MiG-21",      "MIG-21bis"),
]
_FALLBACK_AIRCRAFT = "FA-18C_hornet"


def _map_aircraft(name: str) -> str:
    if not name:
        return _FALLBACK_AIRCRAFT
    for key, dcs_id in _AIRCRAFT_MAP:
        if key.lower() in name.lower():
            return dcs_id
    return _FALLBACK_AIRCRAFT


class WinWingSink:
    """
    Forwards TelemFFB telemetry to WinWing SimAppPro via UDP JSON.

    Call update(telem_data) on every telemetry frame.  Everything else
    (protocol handshake, heartbeat, aircraft change detection) is handled
    internally.
    """

    def __init__(self, host: str = SIMAPPPRO_HOST, port: int = SIMAPPPRO_PORT):
        self._addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._started = False
        self._last_heartbeat = 0.0
        self._last_send = 0.0
        self._current_mod: str | None = None

        # Cannon shells counter — decremented while gun fires so SimAppPro
        # detects cannonShellsCount < oldValue → isFireCannonShells = 1.
        self._cannon_shells: int = 1000
        self._prev_gun: bool = False

        # Payload stations: track previous station list to detect release events.
        self._prev_payload: list = []

    # ------------------------------------------------------------------ start/stop

    def start(self):
        self._send({"func": "net",     "msg": "ready"})
        self._send({"func": "mission", "msg": "ready"})
        self._send({"func": "mission", "msg": "start"})
        self._started = True
        log.info("WinWingSink: connected to SimAppPro at %s:%d", *self._addr)

    def stop(self):
        if self._started:
            self._send({"func": "mission", "msg": "stop"})
        self._sock.close()
        self._started = False
        log.info("WinWingSink: disconnected")

    # ------------------------------------------------------------------ update

    def update(self, telem_data: dict):
        if not self._started:
            return

        now = time.monotonic()

        # rate-limit: SimAppPro doesn't need every TelemFFB frame
        if now - self._last_send < _SEND_INTERVAL:
            return
        self._last_send = now

        # heartbeat
        t_wall = time.time()
        if t_wall - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            self._last_heartbeat = t_wall
            self._send({"func": "heartbeat", "msg": t_wall})

        # aircraft type — triggers SimAppPro to load DVM effect config
        aircraft_raw = telem_data.get("N", "")
        mod = _map_aircraft(aircraft_raw)
        if mod != self._current_mod:
            self._current_mod = mod
            self._send({"func": "mod", "msg": mod})
            log.debug("WinWingSink: aircraft → %s (raw=%r)", mod, aircraft_raw)

        # ------------------------------------------------------------------
        # Build addCommon args.
        #
        # Keys must match effectConfig.directParam names (WITHOUT "Direct_"
        # prefix).  SimAppPro's DCSShakeEffect.js strips that prefix when
        # populating shake.directParam, so anything we send with the prefix
        # lands on an undefined key and is silently dropped.
        # ------------------------------------------------------------------
        args: dict = {}

        # True airspeed [m/s]  — effectConfig range 0–500
        args["trueAirSpeed"] = float(telem_data.get("TAS", 0.0))

        # Angle of attack [degrees]  — effectConfig range -10–50
        args["angleOfAttack"] = float(telem_data.get("AoA", 0.0))

        # Gear position 0–1 (raw; SimAppPro applies scale=100 internally)
        gear = telem_data.get("gear_value", telem_data.get("GearPos", 0.0))
        if isinstance(gear, (list, tuple)):
            gear = max(float(v) for v in gear)
        args["gearValue"] = float(gear)

        # Speedbrakes 0–1 (raw; SimAppPro applies scale=100 internally)
        args["speedbrakesValue"] = float(telem_data.get("speedbrakes_value", 0.0))

        # Accelerations [g]  — effectConfig range ±3 / ±9 Z
        accs = telem_data.get("ACCs") or telem_data.get("AccBody") or (0.0, 0.0, 0.0)
        if len(accs) >= 3:
            args["accelerationX"] = round(float(accs[0]), 3)
            args["accelerationY"] = round(float(accs[1]), 3)
            args["accelerationZ"] = round(float(accs[2]), 3)

        # Vertical velocity [m/s]  — effectConfig range ±500
        args["verticalVelocity"] = float(telem_data.get("VerticalSpeed", 0.0))

        # ---- Gear rod compression (0→1 transition triggers isGearTouchGround) ----
        # WeightOnWheels is typically (nose, left_main, right_main) or similar.
        wow_vals = telem_data.get("WeightOnWheels", (0, 0, 0))
        if hasattr(wow_vals, '__iter__'):
            wow_list = [float(v) for v in wow_vals]
        else:
            val = float(wow_vals)
            wow_list = [val, val, val]

        # Pad to at least 3 elements
        while len(wow_list) < 3:
            wow_list.append(wow_list[-1] if wow_list else 0.0)

        # Map to the three rod names SimAppPro uses for isGearTouchGround.
        # DCS WeightOnWheels order: typically index 0 = nose, 1 = left, 2 = right.
        args["gearNoseRod"]  = min(1.0, wow_list[0])
        args["gearLeftRod"]  = min(1.0, wow_list[1])
        args["gearRightRod"] = min(1.0, wow_list[2])

        # ---- Cannon shells count (decreasing triggers isFireCannonShells) ----
        gun_now = bool(telem_data.get("Gun", 0) or telem_data.get("GunFire", 0))
        if gun_now:
            self._cannon_shells = max(0, self._cannon_shells - 1)
        self._prev_gun = gun_now
        args["cannonShellsCount"] = self._cannon_shells

        # ---- Payload stations (element change triggers payloadStationsChange) ----
        payload_info = telem_data.get("PayloadInfo", None)
        if payload_info is not None:
            stations = []
            for s in payload_info:
                if s and isinstance(s, dict):
                    stations.append({
                        "count":     s.get("count", 0),
                        "container": s.get("container", 0),
                    })
                elif s:
                    stations.append({"count": 1, "container": 1})
                else:
                    stations.append({"count": 0, "container": 0})
            args["payloadStations"] = stations

        self._send({"func": "addCommon", "timestamp": t_wall, "args": args})

    # ------------------------------------------------------------------ internal

    def _send(self, msg: dict):
        try:
            self._sock.sendto(json.dumps(msg).encode(), self._addr)
        except OSError as exc:
            log.debug("WinWingSink: send error: %s", exc)


# ── module-level singleton ────────────────────────────────────────────────────

_sink: WinWingSink | None = None


def init_winwing(host: str = SIMAPPPRO_HOST, port: int = SIMAPPPRO_PORT) -> WinWingSink:
    global _sink
    _sink = WinWingSink(host, port)
    _sink.start()
    return _sink


def shutdown_winwing():
    global _sink
    if _sink is not None:
        _sink.stop()
        _sink = None


def is_initialised() -> bool:
    return _sink is not None and _sink._started


def get_sink() -> WinWingSink | None:
    return _sink
