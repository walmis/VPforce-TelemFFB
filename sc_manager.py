from simconnect import *
from ctypes import byref, cast, sizeof
import time
import threading

REQ_ID = 0xfeed

class SimVar:
    def __init__(self, name, var, sc_unit, unit=None, type=DATATYPE_FLOAT64, scale=None):
        self.name = name
        self.var = var
        self.scale = scale
        self.sc_unit = sc_unit
        self.unit = unit
        self.datatype = type
        self.parent = None
        self.index = None # index for multivariable simvars
        if self.sc_unit.lower() in ["bool", "enum"]:
            self.datatype = DATATYPE_INT32


    @property
    def c_type(self):
        types = {
            DATATYPE_FLOAT64: c_double,
            DATATYPE_FLOAT32: c_float,
            DATATYPE_INT32: c_long,
            DATATYPE_STRING32: c_char*32,
            DATATYPE_STRING128: c_char*128
        }
        return types[self.datatype]

class SimVarArray:
    def __init__(self, name, var, unit, type=DATATYPE_FLOAT64, scale=None, min=0, max=1):
        self.name = name
        self.unit = unit
        self.type = type
        self.scale = scale
        self.vars = []
        self.values = []
        self.min = min
        for index in range(min, max+1):
            if index < min:
                self.values.append(0)
            else:
                v = SimVar(name, f"{var}:{index}", unit, None, type, scale)
                v.index = index
                v.parent = self
                self.vars.append(v)
                self.values.append(0)

    
class SimConnectManager(threading.Thread):
    sim_vars = [
        SimVar("N", "TITLE", "", type=DATATYPE_STRING128),
        SimVar("G", "G FORCE", "Number"),
        SimVar("Gdot", "SEMIBODY LOADFACTOR YDOT", "Number"),
        SimVar("AirDensity", "AMBIENT DENSITY", "kilograms per cubic meter"),
        SimVar("AoA", "INCIDENCE ALPHA", "degrees"),
        SimVar("SideSlip", "INCIDENCE BETA", "degrees"),
        SimVar("StallAoA", "STALL ALPHA", "degrees"),
        SimVar("ElevDefl", "ELEVATOR DEFLECTION", "degrees"),
        SimVar("ElevDeflPct", "ELEVATOR DEFLECTION PCT", "Percent Over 100"),
        SimVar("ElevTrim", "ELEVATOR TRIM POSITION", "degrees"),
        SimVar("ElevTrimPct", "ELEVATOR TRIM PCT", "Percent Over 100"),
        SimVar("AileronDefl", "AILERON AVERAGE DEFLECTION", "degrees"),
        SimVar("PropThrust1", "PROP THRUST:1", "kilograms", scale=10), #scaled to newtons
        SimVarArray("PropThrust", "PROP THRUST", "kilograms", min=1, max=4, scale=10),#scaled to newtons
        SimVarArray("PropRPM", "PROP RPM", "RPM", min=1, max=4),

        SimVar("DynPressure", "DYNAMIC PRESSURE", "pascal"),
        #SimVar("AileronDeflPct", "AILERON AVERAGE DEFLECTION PCT", "Percent Over 100", scale=100),
        SimVar("RudderDefl", "RUDDER DEFLECTION", "degrees"),
        SimVar("RudderDeflPct", "RUDDER DEFLECTION PCT", "Percent Over 100"),
        #SimVar("RelWndX", "AIRCRAFT WIND X", "meter/second"),
        #SimVar("RelWndY", "AIRCRAFT WIND Y", "meter/second"),
        #SimVar("RelWndZ", "AIRCRAFT WIND Z", "meter/second"),

        SimVar("Pitch", "PLANE PITCH DEGREES", "degrees"),
        SimVar("Roll", "PLANE BANK DEGREES", "degrees"),
        SimVar("Heading", "PLANE HEADING DEGREES TRUE", "degrees"),
        SimVar("PitchRate", "ROTATION VELOCITY BODY X", "degrees per second"),
        SimVar("RollRate", "ROTATION VELOCITY BODY Z", "degrees per second"),
        SimVar("PitchAccel", "ROTATION ACCELERATION BODY X", "degrees per second squared"),
        SimVar("RollAccel", "ROTATION ACCELERATION BODY Z", "degrees per second squared"),
        SimVar("TrueAirspeed", "AIRSPEED TRUE", "meter/second"),
        #SimVar("LinearCLAlpha", "LINEAR CL ALPHA", "Per Radian"),
        #SimVar("SigmaSqrt", "SIGMA SQRT", "Per Radian"),
        SimVar("SimDisabled", "SIM DISABLED", "Bool"),
        SimVar("SimOnGround", "SIM ON GROUND", "Bool"),
        SimVar("Parked", "PLANE IN PARKING STATE", "Bool"),
        SimVar("SurfaceType", "SURFACE TYPE", "Enum"),
        SimVar("EngineType", "ENGINE TYPE", "Enum"),
        SimVarArray("EngVibration", "ENG VIBRATION", "Number", min=1, max=4),

        SimVar("NumEngines", "NUMBER OF ENGINES", "Number", type=DATATYPE_INT32),
        SimVar("AmbWindX", "AMBIENT WIND X", "meter/second"),
        SimVar("AmbWindY", "AMBIENT WIND Y", "meter/second"),
        SimVar("AmbWindZ", "AMBIENT WIND Z", "meter/second"),
        SimVar("VelX", "VELOCITY WORLD X", "meter/second"),
        SimVar("VelY", "VELOCITY WORLD Y", "meter/second"),
        SimVar("VelZ", "VELOCITY WORLD Z", "meter/second"),
        SimVarArray("WeightOnWheels", "CONTACT POINT COMPRESSION", "Number", min=0, max=2)
    ]
    
    def __init__(self):
        threading.Thread.__init__(self, daemon=True)
        self.sc = None
        self._quit = False

        self.subscribed_vars = []

    def _subscribe(self):
        def_id = 0x1234

        i = 0
        for sv in (self.sim_vars):
            if isinstance(sv, SimVarArray):
                for sv in sv.vars:
                    self.sc.AddToDataDefinition(def_id, sv.var, sv.sc_unit, sv.datatype, 0, i)
                    self.subscribed_vars.append(sv)
                    i+=1
            else:    
                self.sc.AddToDataDefinition(def_id, sv.var, sv.sc_unit, sv.datatype, 0, i)
                self.subscribed_vars.append(sv)
                i+=1

        self.sc.RequestDataOnSimObject(
            REQ_ID,  # request identifier for response packets
            def_id,  # the data definition group
            OBJECT_ID_USER,
            PERIOD_SIM_FRAME,
            DATA_REQUEST_FLAG_TAGGED,# DATA_REQUEST_FLAG_CHANGED | DATA_REQUEST_FLAG_TAGGED,
            0,  # number of periods before starting events
            1,  # number of periods between events, e.g. with PERIOD_SIM_FRAME
            0,  # number of repeats, 0 is forever
        )

    # blocks and reads telemetry
    def _read_telem(self) -> bool:
        pRecv = RECV_P()
        nSize = DWORD()
        while not self._quit:
            try:
                #print('Trying')
                self.sc.GetNextDispatch(byref(pRecv), byref(nSize))
            except OSError as e:
                #print(e)
                time.sleep(0.001)
                continue

            recv = ReceiverInstance.cast_recv(pRecv)
            #print(f"got {recv.__class__.__name__}")
            if isinstance(recv, RECV_EXCEPTION):
                print(f"Got exception {recv.dwException}, sendID {recv.dwSendID}, index {recv.dwIndex}")
            if isinstance(recv, RECV_QUIT):
                print("Quit received")
                break
            elif isinstance(recv, RECV_SIMOBJECT_DATA):
                #print(f"Received SIMOBJECT_DATA with {recv.dwDefineCount} data elements, flags {recv.dwFlags}")
                if recv.dwRequestID == REQ_ID:
                    #print(f"Matched request 0x{req_id:X}")
                    data = {}
                    offset = RECV_SIMOBJECT_DATA.dwData.offset
                    for _ in range(recv.dwDefineCount):
                        idx = cast(byref(recv, offset), POINTER(DWORD))[0]
                        offset += sizeof(DWORD)
                        # DATATYPE_FLOAT64 => c_double
                        var = self.subscribed_vars[idx]
                        c_type = var.c_type
                        if var.datatype == DATATYPE_STRING128: #fixme: other string types
                            val = str(cast(byref(recv, offset), POINTER(c_type))[0].value, "utf-8")
                        else:
                            val = cast(byref(recv, offset), POINTER(c_type))[0]
                        offset += sizeof(c_type)
                        if var.scale is not None:
                            val *= var.scale
                        
                        if var.parent: # var is part of array
                            var.parent.values[var.index-var.parent.min] = val
                            data[var.parent.name] = var.parent.values
                        else:
                            data[var.name] = val

                    self.emit_packet(data)
            else:
                print("Received", recv)

    def emit_packet(self, data):
        pass

    def run(self):
        while not self._quit:
            try:
                print("Trying SimConnect")
                with SimConnect("TelemFFB") as self.sc:
                    self._subscribe()
                    self._read_telem()

            except OSError:
                time.sleep(1)
                pass

# run test
if __name__ == "__main__":
    class SimConnectTest(SimConnectManager):
        def emit_packet(self, data):
            print(data)

    s = SimConnectTest()
    s.start()
    while True:
        time.sleep(1)