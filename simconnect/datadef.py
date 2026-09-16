from typing import List, Sequence, Union, Dict, Any, Callable, Optional, Type, TYPE_CHECKING
import logging
import json
from hashlib import sha1
from ctypes import cast, byref, sizeof, POINTER, c_float, c_double, c_longlong, c_char

from .scvars import validate_simvar, validate_units, validate_event, type_for_unit
from .scdefs import (
    Struct1, RECV_SIMOBJECT_DATA, DATA_REQUEST_FLAG_TAGGED,
    DATATYPE_INT32, DATATYPE_INT64, DATATYPE_FLOAT32, DATATYPE_FLOAT64,
    DATATYPE_STRING256, DWORD
)
from .changedict import ChangeDict
if TYPE_CHECKING:
    from .sc import SimConnect


EPSILON_DEFAULT = 1e-4

"""
simvars can be specified as:
- a single simvar as a string like "Indicated Altitude"
- a single simvar as a dict of {name: , [units: ], [type: ], [epsilon: ]}
- a list containing a one or more individual simvars

name: a string matching a SDK variable (case insenstive),
    note that simvar names are typically space-separated words,
    while event names are underscore-separated.
    Indexed variables need a 1-based index, e.g. "ENG MANIFOLD PRESSURE:1"

units: a string from scvars.json::UNITS as defined in the SDK,
    if not present will be inferred using the default from the SDK,
    via scvars.json::VARIABLES

type: one of the supported scdefs.py::DATATYPE_* constants enumerated in _dtyps below.
    The default DATATYPE_FLOAT64 (a python float) is good for most simvars.
    Strings are supported via DATATYPE_STRING256 (a string of max length 256),
    note fixed length is required for datadefs, and enum, boolean, mask and flag simvars
    can be represented as DATATYPE_INT32 (a python int)

epsilon: the precision to detect changes (see SDK), defaulting to 0.0001
"""
SimData = Dict[str, Any]
SimDataHandler = Callable[[SimData], None]
SimVarSpec = Union[str, Dict[str, Any]]
SimVarsSpec = Union[SimVarSpec, Sequence[SimVarSpec]]


def _norm_simvars(simvars: SimVarsSpec) -> Sequence[Dict[str, Any]]:
    """convert simvars into a seq of dict"""
    ds = simvars if isinstance(simvars, (list, tuple)) else [simvars]
    return [dict(name=d) if isinstance(d, str) else d for d in ds]


class DataDefinition:
    _instances: Dict[str, 'DataDefinition'] = {}

    @classmethod
    def create(kls, sc: 'SimConnect', simvars: SimVarsSpec, settable=False) -> 'DataDefinition':
        """create or retrieve a data definition for the specified variables"""
        defs: List[Dict[str, Any]] = []
        svs = _norm_simvars(simvars)
        for d in svs:
            name = d['name']
            sv = validate_simvar(name, settable)
            units = validate_units(name, d.get('units'), sv)
            dtyp = d.get('type') or type_for_unit(units)
            # see https://forums.flightsimulator.com/t/how-to-read-simvar-of-type-string/502402
            if dtyp == DATATYPE_STRING256: units = ''
            epsilon = d.get('epsilon', EPSILON_DEFAULT if dtyp == DATATYPE_FLOAT64 else 0)
            defs.append(dict(name=name, units=units, dtyp=dtyp, epsilon=epsilon))

        # if we already have this data definition, re-use it
        key = sha1(json.dumps(defs, sort_keys=True).encode('utf-8')).hexdigest()
        if key not in kls._instances:
            kls._instances[key] = kls(sc, len(kls._instances), defs)
        return kls._instances[key]

    @classmethod
    def reset(kls):
        # clears the data definition dictionary so stale entries are not maintained on a re-init
        kls._instances.clear()

    def __init__(self, sc: 'SimConnect', def_id: int, defs: List[Dict[str, Any]]):
        self.id = def_id
        self.simdata: SimData = ChangeDict()
        self._struct: Optional[Type[Struct1]] = None
        self.defs = defs
        for i, d in enumerate(self.defs):
            sc.AddToDataDefinition(self.id, d['name'], d['units'], d['dtyp'], d['epsilon'], i)

    def get_units(self) -> Dict[str, str]:
        return {d['name']: d['units'] for d in self.defs}

    def add_receiver(self, sc: 'SimConnect', req_id: int, callback: Optional[SimDataHandler] = None):
        """Create a receiver for this DataDefinition, given req_id and optional callback"""
        def _receiver(recv: RECV_SIMOBJECT_DATA) -> bool:
            if recv.dwRequestID != req_id:
                return False

            logging.debug(f"DataDefinition[{self.id}]: Reading RECV_SIMOBJECT_DATA for request {req_id}")
            # dwData is a placeholder for where the data values start
            # so get a void* to that location and cast appropriately
            offset = RECV_SIMOBJECT_DATA.dwData.offset
            tagged = recv.dwFlags & DATA_REQUEST_FLAG_TAGGED > 0
            idx = -1
            for _ in range(recv.dwDefineCount):
                if tagged:
                    idx = cast(byref(recv, offset), POINTER(DWORD))[0]
                    offset += sizeof(DWORD)
                else:
                    idx += 1
                d = self.defs[idx]
                ctyp = _dtyps[d['dtyp']]
                ptr = cast(byref(recv, offset), POINTER(ctyp))
                if ctyp == c_char:  # STRING256
                    val = ptr[:256].rstrip(b'\x00').decode('ascii')
                    offset += 256
                else:
                    val = ptr[0]
                    offset += sizeof(ctyp)
                self.simdata[d['name']] = val

            if callback:
                callback(self.simdata)
            return True

        sc.add_receiver(RECV_SIMOBJECT_DATA, _receiver)

    def _pack_data(self, simdata: Dict[str, Any]) -> Struct1:
        if self._struct is None:
            class kls(Struct1):
                _fields_ = [(d['name'], _dtyps[d['dtyp']]) for d in self.defs]
            self._struct = kls

        return self._struct(**simdata)


def map_event_id(sc: 'SimConnect', event: str) -> int:
    s = validate_event(event)
    client_id = _event_ids.get(s)
    if client_id is None:
        client_id = len(_event_ids)
        _event_ids[s] = client_id
        sc.MapClientEventToSimEvent(client_id, s)
    return client_id


def clear_event_ids():
    # Clears the event id dictionary so stale subscriptions are not maintained during a re-init
    _event_ids.clear()

# Track client-mapped event ids
_event_ids: Dict[str, int] = {}


# Map scdefs type flags to ctypes
_dtyps = {
    DATATYPE_INT32: DWORD,   # 32-bit integer number
    DATATYPE_INT64: c_longlong,   # 64-bit integer number
    DATATYPE_FLOAT32: c_float,   # 32-bit floating-point number (float)
    DATATYPE_FLOAT64: c_double,   # 64-bit floating-point number (double)
    DATATYPE_STRING256: c_char,  # variable length string
}
