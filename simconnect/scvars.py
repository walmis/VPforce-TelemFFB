from typing import Optional, List, Dict, Any
import re
import os
import json
import logging
from difflib import get_close_matches
from .scdefs import DATATYPE_INT32, DATATYPE_FLOAT64, DATATYPE_STRING256


def validate_simvar(name: str, settable: bool) -> Optional[Dict[str, Any]]:
    """Match a simvar name and settable flag to known variables"""
    base = _namestd(name)
    if base == 'L':
        sv = {
            'name': {name},
            'units': 'number',
            'settable': True,
            'url': '',
            'page': 'LVARS',
            'section': 'LVARS',
            'name_std': {name},
            'units_std': 'number',
            'dimensions': 'Miscellaneous Units',
            'indexed': False
        }
    else:
        sv = SIMVARS.get(base, {})
    if not sv:
        logging.warning(f"SimConnect: unrecognized simvar '{base}', {_closemsg(base, SIMVARS)}")
    else:
        if sv['indexed'] and ':' not in name:
            logging.warning(f"SimConnect: expected indexed simvar, e.g. {name}:3")
        if settable and not sv.get('settable'):
            logging.warning(f"SimConnect: simvar {name} is not settable")
    return sv


def validate_event(event) -> str:
    s = _eventstd(event)
    if s not in EVENTS:
        logging.warn(f"Simconnect: Unrecognized event {event}")
    return s


def validate_units(name: str, units: Optional[str], simvar: Optional[Dict[str, Any]]) -> str:
    # lookup default units if not provided, note units='' is valid
    if units is None:
        if simvar is not None:
            # If not specified, try inferring units from variable
            ustd = simvar.get('units_std', '')
        else:
            ustd = ''
        if not ustd:
            logging.warning(f"SimConnect: no units specified or inferred for {name}")
    elif units == '':
        # deliberate empty string
        ustd = units
    else:
        # get the canonical unit name
        ustd = _unitstd(units)[0]
        if ustd in UNITS:
            ustd = UNITS[ustd]['name_std']
        else:
            possibilities: List[str] = []
            if simvar:
                possibilities = DIMENSIONS.get(simvar['dimensions'])
            if not possibilities:
                possibilities = [u['name_std'] for u in UNITS.values()]
            msg = _closemsg(ustd, possibilities)
            logging.warning(f"SimConnect: unrecognized units '{units}' for {name}, {msg}")
    return ustd


def type_for_unit(unit: str) -> int:
    unit = unit.upper()
    if unit in UNITS:
        u = UNITS[unit]
        if u['dimensions'] == 'Miscellaneous Units':
            if u['name_std'] in ('Bool', 'Boolean', 'Enum', 'BCO16', 'mask', 'flags'):
                return DATATYPE_INT32
            elif u['name_std'] == 'string':
                return DATATYPE_STRING256
            elif u['name_std'] in ('position', 'number'):
                return DATATYPE_FLOAT64
            else:
                warn = f"SimConnect: unrecognized Miscellaneous Unit in typefor({unit})"
        elif u['dimensions'] == 'Structs And Other Complex Units':
            warn = f"SimConnect: complex types not supported for {unit}"
        else:
            return DATATYPE_FLOAT64
    else:
        warn = f"SimConnect: unrecognized unit in typefor({unit})"

    logging.warning(warn + "; using float64 as fallback")
    return DATATYPE_FLOAT64


def _closemsg(s, ss):
    xs = get_close_matches(s, ss)
    if xs:
        msg = f"perhaps one of {', '.join(xs)}?"
    else:
        options = (ss[:3] + ['...']) if len(ss) > 3 else ss
        msg = f"found no similar options among: {', '.join(options)}"
    return msg


def _namestd(s) -> str:
    return s.rsplit(':', 1)[0].upper().replace('_', ' ')


def _unitstd(ss, canonical=True) -> List[str]:
    vs = []
    ss = re.sub(r'^Struct:\n\s*', '', ss)
    for s in ss.split('\n')[0].split(','):
        s = s.strip().rstrip(':')
        if canonical:
            s = s.upper().replace('-', ' ').replace('_', ' ')
            s = re.sub(r'\s+PER\s+', '/', s)        # "Feet per second"
            s = re.sub(r'\s*\([^()]+\)\s*', '', s)  # "Feet (ft) / second"
            s = re.sub(r'\s*\([^)]+$', '', s)       # "pounds per square inch (psi"
            s = s.replace('SCALAR', 'SCALER')
            s = s.replace('POUNDS/SQUARE FOOT', 'PSF')
            s = s.replace('POUND FORCE/SQUARE FOOT', 'PSF')
            s = s.replace('POUNDS/SQUARE INCH', 'PSI')
            s = s.replace('SLUGS/FEET SQUARED', 'SLUGS FEET SQUARED')
            s = s.replace('KILO PASCAL', 'KILOPASCAL')
            s = s.replace('FOOT POUNDS/SECOND', 'FT LB/SECOND')
            s = re.sub(r'^SIMCONNECT DATA\s+(.*?)(\s+STRUCT(URE)?)?$', r'\1', s)
        vs.append(s.strip())
    return vs


def _eventstd(s):
    return s.upper()


# Load SDK definitions scraped from documentation, see ../scripts/scrapevars.py
try:
    # with-block, not a bare open(): the abandoned handle was otherwise
    # garbage-collected at an arbitrary later moment, raising a
    # ResourceWarning inside __del__ - which surfaces as a test error in
    # any consumer running pytest with warnings-as-errors.
    with open(os.path.join(os.path.dirname(__file__), 'scvars.json')) as f:
        _scvars = json.load(f)
except Exception:
    logging.warning('Failed to load scvars.json')
    _scvars = dict(SIMVARS={}, EVENTS={}, UNITS={}, DIMENSIONS={})

# Expose the standardized definitions scraped from docs
SIMVARS = _scvars['VARIABLES']
EVENTS = _scvars['EVENTS']
UNITS = _scvars['UNITS']
DIMENSIONS = _scvars['DIMENSIONS']
