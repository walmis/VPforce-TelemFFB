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

"""The datagrams between TelemFFB and its plot process: one JSON object per
UDP datagram, from TelemFFB to the plotter only.

- ``{"t": seconds, "v": {key: value, ...}}`` - one frame's marked keys.
- ``{"keys": [key, ...]}`` - the marked keys; sent on every change and
  once a second as the keepalive.
- ``{"cmd": "show" | "quit"}`` - show the window; exit.

The plotter exits on its own after ``FEED_TIMEOUT_S`` without a datagram,
so a TelemFFB that dies never leaves a plotter behind.
"""

import json
import re
from typing import Dict, Iterable, Optional

#: How often TelemFFB repeats the key list while the plotter runs.
KEEPALIVE_S = 1.0
#: Silence after which the plotter exits.
FEED_TIMEOUT_S = 3.0

CMD_SHOW = "show"
CMD_QUIT = "quit"


#: A number with a unit tacked on, as some telemetry strings are ("0.345ms").
_NUMBER_WITH_UNIT = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*[A-Za-z%°/]*\s*$")


def plottable(value):
    """The number a telemetry value plots as, or ``None``: numbers as they
    are, bools as 0 or 1, a string only when it is a number with a unit
    suffix (``"0.345ms"`` is 0.345; ``"C172"`` is not plottable)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = _NUMBER_WITH_UNIT.match(value)
        return float(m.group(1)) if m else None
    return None


def _plain(value):
    """A JSON-able copy of a telemetry value: numpy scalars become floats."""
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return float(value)


def encode_frame(t: float, values: Dict) -> bytes:
    return json.dumps({"t": t, "v": {k: _plain(v) for k, v in values.items()}}).encode("utf-8")


def encode_keys(keys: Iterable[str]) -> bytes:
    return json.dumps({"keys": sorted(keys)}).encode("utf-8")


def encode_command(command: str) -> bytes:
    return json.dumps({"cmd": command}).encode("utf-8")


def decode(datagram: bytes) -> Optional[dict]:
    """The datagram's object, or ``None`` for anything that is not one."""
    try:
        message = json.loads(datagram.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None
