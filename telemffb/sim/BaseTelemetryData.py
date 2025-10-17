from typing import Optional, List, Union, Dict, Any, Tuple, Iterable, Iterator
import time

class BaseTelemetryData:
    """A mapping-backed telemetry container with attribute type-hints.

    This class intentionally exposes attributes as type hints so IDEs show
    available members, but all storage is kept in an internal dict. It
    behaves like a dictionary while also providing dot-access for known
    attributes.

    Usage:
      d = BaseTelemetryData()
      d['T'] = 123.0
      d.T == 123.0
      d.N = 'F-16'  # sets underlying storage
    """

    # --- Attribute annotations (for IDEs/static type checkers) ---
    src: Optional[str]
    N: Optional[str]
    AircraftClass: Optional[str]
    FFBType: Optional[str]
    ACisFBW: Optional[bool]
    T: Optional[float]

    TAS: Optional[float]
    IAS: Optional[float]
    VerticalSpeed: Optional[float]

    AoA: Optional[float]

    ACCs: Optional[List[float]]
    AccBody: Optional[List[float]]
    G: Optional[float]
    Gaxil: Optional[float]

    WeightOnWheels: Optional[List[float]]
    SimOnGround: Optional[int]

    Flaps: Optional[Union[float, List[float]]]
    Spoilers: Optional[Union[float, List[float]]]
    speedbrakes_value: Optional[float]
    canopy_value: Optional[float]
    gear_value: Optional[Union[float, List[float]]]
    TailHook: Optional[float]
    FuelBoom: Optional[float]
    WingFold: Optional[float]

    EngRPM: Optional[Union[float, List[float]]]
    ActualRPM: Optional[Union[float, List[float]]]
    PropRPM: Optional[Union[float, List[float]]]
    RotorRPM: Optional[Union[float, List[float]]]
    EngPCT: Optional[Union[float, List[float]]]
    RPM: Optional[Union[float, List[float]]]
    Afterburner: Optional[Union[float, List[float]]]

    HydSys: Optional[Union[bool, int, float, List[Union[bool, int, float]]]]
    HydPress: Optional[Union[float, List[float]]]

    PayloadInfo: Optional[Any]
    Gun: Optional[Any]
    Flares: Optional[Any]
    Chaff: Optional[Any]

    Wind: Optional[Tuple[float, float, float]]

    DesignSpeed: Optional[Tuple[float, float, float]]
    StallAoA: Optional[float]
    WarnAlpha: Optional[float]
    Vle: Optional[float]

    VlctVectors: Optional[List[float]]

    # Single backing storage for all fields
    _data: Dict[str, Any]

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        # everything stored in this mapping (timestamp included)
        object.__setattr__(self, '_data', {})
        self._data['_timestamp'] = time.perf_counter()
        # historical default
        self._data['FFBType'] = 'joystick'

        if initial:
            for k, v in initial.items():
                self._data[k] = v

    # Internal helper: known attribute names (from annotations)
    @classmethod
    def _known_fields(cls) -> set:
        return {k for k, v in cls.__annotations__.items() if not k.startswith('_')}

    # --- Mapping-like interface ---
    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data or key in self._known_fields()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> Iterable[str]:
        return set(self._data.keys()) | self._known_fields()

    def items(self) -> Iterator[tuple]:
        for k in self.keys():
            yield k, self._data.get(k)

    def values(self) -> Iterator[Any]:
        for _, v in self.items():
            yield v

    def update(self, other: Union[Dict[str, Any], 'BaseTelemetryData']) -> None:
        if isinstance(other, BaseTelemetryData):
            other = other.to_dict()
        for k, v in other.items():
            self._data[k] = v

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseTelemetryData':
        return cls(initial=data.copy())

    def copy(self) -> 'BaseTelemetryData':
        return BaseTelemetryData(self.to_dict())

    # --- Attribute access that maps to internal dict ---
    def __getattr__(self, name: str) -> Any:
        # called when attribute not found on instance; fetch from dict
        if name in self._data:
            return self._data[name]
        # attribute may be a known field that has not been set yet -> return None
        if name in self._known_fields():
            return None
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!s}")

    def __setattr__(self, name: str, value: Any) -> None:
        # keep internal mapping on the instance
        if name == '_data':
            object.__setattr__(self, name, value)
            return
        # store all known fields and any other attributes in the mapping
        if name in self._known_fields():
            self._data[name] = value
            return
        object.__setattr__(self, name, value)

