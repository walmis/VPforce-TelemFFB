"""Device inventory: physical placement of every connected VPForce device.

The inventory is the user-facing answer to "what hardware do I have, and
where does it sit relative to me?". It's the input the ``EffectRouter`` uses
to decide which layer of which effect plays on which device.

Storage:

- ``[devices]`` section in ``config.ini`` — JSON-encoded list, source of
  truth for the user's setup.
- ``telemffb/data/device_inventory_default.json`` — empty default + reference
  presets shipped with the app.

The Device datatype is intentionally narrow: it carries only identity
(``device_id``, ``type``, ``usb_pid``) and placement (``positions``,
``xy_offset``). Per-device tunings (gains, override toggles) live elsewhere
(``defaults.xml``, ``ConfiguratorDialog``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Device-type vocabulary. Mirrors the auto-launch list in main.py:131-135 and
# the mapping in _setup_device_configuration. ``rudder`` is new — it's a
# logical type that defaults to PID-mapped pedals slot but can be placed
# independently (e.g. "VPForce Rudder vorn").
KNOWN_DEVICE_TYPES = (
    "joystick", "pedals", "rudder", "collective", "trimwheel", "shaker", "other",
)

# Position tag vocabulary. Free-form strings would invite typos; an enum-style
# list keeps the matrix selectors stable. Multiple tags per device are allowed
# (e.g. a sit-on shaker is ``["seat", "center"]``).
KNOWN_POSITIONS = (
    "front", "center", "rear", "left", "right",
    "floor", "seat", "desk", "side",
)


@dataclass
class Device:
    device_id: str
    type: str = "joystick"
    positions: List[str] = field(default_factory=list)
    xy_offset: Optional[dict] = None        # {"x": float, "y": float} in cm relative to seat
    usb_pid: Optional[str] = None           # "vid:pid" string, e.g. "FFFF:2055"
    label: str = ""
    master: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.type not in KNOWN_DEVICE_TYPES:
            logger.warning("Device %r has unknown type %r; keeping as-is",
                           self.device_id, self.type)
        for p in self.positions:
            if p not in KNOWN_POSITIONS:
                logger.warning("Device %r has unknown position tag %r",
                               self.device_id, p)
        if self.xy_offset is not None:
            if not isinstance(self.xy_offset, dict) or not {"x", "y"} <= self.xy_offset.keys():
                logger.warning("Device %r has malformed xy_offset %r; clearing",
                               self.device_id, self.xy_offset)
                self.xy_offset = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Device":
        # Reject unknown keys to surface schema drift early.
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in known}
        if len(clean) != len(raw):
            dropped = set(raw) - set(clean)
            logger.warning("Device.from_dict: dropping unknown keys %s", dropped)
        return cls(**clean)


# --- load / save ---------------------------------------------------------

def load_inventory_from_json(path: str) -> List[Device]:
    """Read a list of Devices from a JSON file. Missing/malformed -> []."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.exception("Failed to read device inventory from %s", path)
        return []

    raw_devices = data.get("devices") if isinstance(data, dict) else data
    if not isinstance(raw_devices, list):
        logger.warning("device inventory at %s has no 'devices' list", path)
        return []

    out: List[Device] = []
    seen_ids: set[str] = set()
    for entry in raw_devices:
        if not isinstance(entry, dict):
            continue
        try:
            dev = Device.from_dict(entry)
        except TypeError:
            logger.exception("Skipping malformed device entry %r", entry)
            continue
        if dev.device_id in seen_ids:
            logger.warning("Duplicate device_id %r in inventory; keeping first",
                           dev.device_id)
            continue
        seen_ids.add(dev.device_id)
        out.append(dev)
    return out


def save_inventory_to_json(path: str, devices: List[Device]) -> None:
    """Atomically persist the inventory."""
    payload = {"version": 1, "devices": [d.to_dict() for d in devices]}
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def load_inventory_from_ini(ini_value: Optional[str]) -> List[Device]:
    """Decode the ``[devices].inventory`` JSON blob from config.ini.

    None / empty / malformed -> []. Caller is expected to fall back to the
    setup wizard when the result is empty.
    """
    if not ini_value:
        return []
    try:
        data = json.loads(ini_value)
    except Exception:
        logger.exception("Failed to parse [devices] inventory JSON")
        return []
    if isinstance(data, dict) and "devices" in data:
        data = data["devices"]
    if not isinstance(data, list):
        return []
    out: List[Device] = []
    for entry in data:
        if isinstance(entry, dict):
            try:
                out.append(Device.from_dict(entry))
            except TypeError:
                logger.exception("Skipping malformed device %r", entry)
    return out


def encode_inventory_for_ini(devices: List[Device]) -> str:
    """Inverse of ``load_inventory_from_ini``."""
    return json.dumps([d.to_dict() for d in devices], separators=(",", ":"))


# --- helpers -------------------------------------------------------------

def find_self(devices: List[Device], device_id: str) -> Optional[Device]:
    """Return the ``Device`` matching ``device_id`` or None."""
    for d in devices:
        if d.device_id == device_id:
            return d
    return None


def first_of_type(devices: List[Device], device_type: str) -> Optional[Device]:
    """Return the first enabled device with the given type, or None.

    Handy for default-fill cases where the caller has no ``device_id`` yet
    (e.g. CLI launched with ``--type pedals`` but no inventory yet).
    """
    for d in devices:
        if d.enabled and d.type == device_type:
            return d
    return None
