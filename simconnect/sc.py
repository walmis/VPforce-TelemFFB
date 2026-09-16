from typing import Callable, Dict, List, Optional, Type, Any, Union
from dataclasses import dataclass
from ctypes import byref, sizeof, cast, POINTER, c_void_p, c_double, create_string_buffer
import itertools
import logging
import os
from time import time, sleep
from .scdefs import (
    _decls, DispatchProc,
    RECV, DATA_REQUEST_FLAG_CHANGED, DATA_REQUEST_FLAG_TAGGED,
    OBJECT_ID_USER, PERIOD_SECOND, PERIOD_ONCE,
    GROUP_PRIORITY_HIGHEST, EVENT_FLAG_GROUPID_IS_PRIORITY,
    HANDLE, windll,
    RECV_ENUMERATE_INPUT_EVENTS, RECV_GET_INPUT_EVENT, RECV_SUBSCRIBE_INPUT_EVENT,
    INPUT_EVENT_TYPE_DOUBLE, INPUT_EVENT_TYPE_STRING,
)
from .receiver import Receiver, ReceiverInstance, _default_receivers
from .datadef import SimVarsSpec, DataDefinition, SimData, SimDataHandler, _norm_simvars, map_event_id, clear_event_ids


RECV_P = POINTER(RECV)


@dataclass(frozen=True)
class InputEvent:
    """One of the loaded aircraft's input events (a "B:" variable).

    The sim addresses these by hash, not name, and the hashes are only
    known after enumerate_input_events() - they can differ per aircraft.
    """
    name: str      # without the "B:" prefix
    hash: int
    type: int      # INPUT_EVENT_TYPE_DOUBLE or INPUT_EVENT_TYPE_STRING


def input_event_name(name: str) -> str:
    """An input event name as the sim enumerates it: the "B:" prefix removed."""
    return name[2:] if name[:2].upper() == "B:" else name

# to change the default logging, set the LOGLEVEL environment variable, e.g. LOGLEVEL=DEBUG
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

_dll_path = os.path.join(os.path.dirname(__file__), 'SimConnect.dll')


class SimConnect:
    def __init__(
            self,
            name='pySimConnect',
            dll_path=_dll_path,
            default_receivers=_default_receivers,
            poll_interval_seconds=0.05,
            log_warning=False):
        try:
            dll = windll.LoadLibrary(dll_path)
        except Exception:
            logging.error(f"Failed to load SimConnect DLL from {dll_path}")
            raise
        self._decls = _decls(dll)
        self.hsc = HANDLE()
        # All methods other than open pass the sc HANDLE as the first arg
        try:
            self._decls['Open'](byref(self.hsc), name.encode('utf-8'), None, 0, 0, 0)
        except OSError:
            if log_warning:
                logging.warning(f"Unable to connect to SimConnect, is MSFS Running?")
            raise
        self._reqid_iter = itertools.count()
        self._receivers: List[ReceiverInstance] = default_receivers[:]
        self.poll_interval_seconds = poll_interval_seconds
        clear_event_ids() # clear any events that have been mapped
        DataDefinition.reset() # clear any data defs that have been stored
        self._input_events: Dict[str, InputEvent] = {}

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.Close()

    def __getattr__(self, k):
        if k in self._decls:
            return self._dispatch(self._decls[k])
        # Default behaviour
        raise AttributeError

    def _dispatch(self, f):
        """Dispatch to a registered method, using our open handle"""
        def _callable(*args):
            args = [arg.encode('utf-8') if isinstance(arg, str) else arg for arg in args]
            return f(self.hsc, *args)
        return _callable

    def add_receiver(self, rtype: Type[RECV], receiver: Receiver):
        """
        Adds a receiver to handle type recv_type
        Any additional kwargs will be passed to the receiver when it's called
        Returns an identifer for the receiver which can be used to remove it
        """
        self._receivers.append(ReceiverInstance(rtype, receiver))

    def remove_receiver(self, receiver) -> bool:
        """Remove a receiver, given either the ReceiverInstance add_receiver
        appended or the callable it wraps; True when one was found."""
        n = len(self._receivers)
        self._receivers = [r for r in self._receivers
                           if r is not receiver and r._receiver is not receiver]
        return len(self._receivers) < n

    def _dispatcher(self, pRecv, nSize, pContext):
        """Dispatch to all our handlers whenever we get a RECV object"""
        recv = ReceiverInstance.cast_recv(pRecv)
        logging.debug(f"receive: got {recv.__class__.__name__} with size {nSize} and context {pContext}")
        self._received = sum(r.receive(recv) for r in self._receivers)
        if not self._received:
            logging.warn(f"receive: no receiver found for {recv.__class__.__name__}")

    def receive(self, timeout_seconds=None) -> bool:
        """
        Poll the SDK for messages, dispatching to registered receivers.
        Poll at least once, until either we receive a message or timeout expires
        Returns true if we received a message
        """
        tmax = time() + timeout_seconds if timeout_seconds else None
        self._received = 0
        while True:
            self.CallDispatch(DispatchProc(self._dispatcher), None)
            if self._received or not tmax or time() > tmax:
                break
            sleep(self.poll_interval_seconds)
        return self._received > 0

    def get_simdatum(
            self,
            name,
            units=None,
            timeout_seconds=1) -> Any:
        """get a one-off value of a single simvar variable, see also subscribe_simdata"""
        spec = dict(name=name, units=units)
        simdata = self.get_simdata([spec], timeout_seconds)
        return list(simdata.values())[0] if simdata else None

    def get_simdata(
            self,
            simvars: SimVarsSpec,
            timeout_seconds=1) -> SimData:
        """
        get a snapshot of one or more simvars.
        if you plan to query the same data frequently use subscribe_simdata instead.
        waits up to timeout_seconds for a response,
        returning SimData (a dictionary that supports changedsince, see changedict.py)
        """
        dd = self.subscribe_simdata(simvars, period=PERIOD_ONCE, repeat_count=1, flags=0)
        tmax = time() + (timeout_seconds or 1)
        # we'll potentially receive other messages while we're looking for this result
        # so wait up to timeout_seconds total while there are messages and we haven't got data yet
        while self.receive(tmax - time()) and not dd.simdata:
            pass
        self._receivers.pop()
        return dd.simdata

    def subscribe_simdata(
            self, simvars: SimVarsSpec,
            period=PERIOD_SECOND,
            skip_periods=0,   # number of periods to skip before starting
            interval=1,       # number of periods between updates
            repeat_count=0,   # number of updates before stopping (0 = forever)
            flags=DATA_REQUEST_FLAG_CHANGED | DATA_REQUEST_FLAG_TAGGED,
            callback: Optional[SimDataHandler] = None
            ):
        """
        Create and subscribe to a data definition.
        The returned data definition provides methods to access the
        current data (via dd.simdata) and inferred units etc.
        If the optional callback is provided, it will be called
        with dd.simdata whenever the data is changed.
        """
        dd = DataDefinition.create(self, simvars)
        req_id = next(self._reqid_iter)
        dd.add_receiver(self, req_id, callback)
        # note the SDK doc for first two args is misleading/wrong
        self.RequestDataOnSimObject(
            req_id,
            dd.id,
            OBJECT_ID_USER,
            period,
            flags,
            skip_periods,
            interval,
            repeat_count
        )
        return dd

    def set_simdatum(self, name, value, units=None):
        """Set a single simulator variable"""
        self.set_simdata([dict(name=name, units=units, value=value)])

    def set_simdata(self, simdata: SimVarsSpec):
        """
        Set one or more simulator variables, based on a list
        of dictionary of {name: , value: , [units: ], [type: ]}
        """
        sds = _norm_simvars(simdata)
        dd = DataDefinition.create(self, sds, settable=True)
        assert all(['value' in d for d in sds]), "set_simdata: must specify value for each item"
        values = {d['name']: d['value'] for d in sds}
        data = dd._pack_data(values)
        logging.debug(f"setting simdata {sds} with {sizeof(data)} bytes")
        #TODO: some data types can be also be set as array,
        # e.g. any number of waypoints can be given to an AI object using a single call to this function,
        # and any number of marker state structures can also be combined into an array
        # in that case number of items would be greater than 1
        self.SetDataOnSimObject(
            dd.id,
            OBJECT_ID_USER,
            0,  # flags
            0,  # number of items, 0 is also interpreted as 1...
            sizeof(data),  # DWORD size of each item
            cast(byref(data), c_void_p),  # pointer to start of data
        )

    def send_event(self, event, data=0):
        """Send an event to FlightSim, see datadef.EVENTS"""
        client_id = map_event_id(self, event)
        self.TransmitClientEvent(
            OBJECT_ID_USER,
            client_id,
            data,
            GROUP_PRIORITY_HIGHEST,
            EVENT_FLAG_GROUPID_IS_PRIORITY,
        )

    # --- Input events: the loaded aircraft's "B:" variables -----------------
    # Available from MSFS 2020 SU12 on.  Unlike simvars they are addressed
    # by a per-aircraft hash, so every call below resolves a name through
    # the table enumerate_input_events() filled in.

    @property
    def input_events(self) -> Dict[str, InputEvent]:
        """The table from the last enumerate_input_events(), by name."""
        return self._input_events

    def resolve_input_event(self, event: Union[str, int, InputEvent]) -> int:
        """The hash for an input event given by name ("B:" optional),
        InputEvent, or hash.  KeyError when the name was not enumerated."""
        if isinstance(event, InputEvent):
            return event.hash
        if isinstance(event, int):
            return event
        key = input_event_name(event)
        if key not in self._input_events:
            raise KeyError(f"input event {event!r} not found; enumerate_input_events() first")
        return self._input_events[key].hash

    def enumerate_input_events(self, timeout_seconds=2) -> Dict[str, InputEvent]:
        """Ask the sim for the loaded aircraft's input events and wait for
        the (possibly chunked) answer.  Replaces the cached table, so call
        it again after an aircraft change."""
        req_id = next(self._reqid_iter)
        found: Dict[str, InputEvent] = {}
        done = False

        def receiver(recv: RECV_ENUMERATE_INPUT_EVENTS) -> bool:
            nonlocal done
            if recv.dwRequestID != req_id:
                return False
            for d in recv.descriptors():
                name = d.Name.decode("utf-8", errors="replace")
                found[name] = InputEvent(name, d.Hash, d.eType)
            if recv.dwEntryNumber + 1 >= recv.dwOutOf:
                done = True
            return True

        ri = ReceiverInstance(RECV_ENUMERATE_INPUT_EVENTS, receiver)
        self._receivers.append(ri)
        try:
            self.EnumerateInputEvents(req_id)
            tmax = time() + timeout_seconds
            while not done and time() < tmax:
                self.receive(tmax - time())
        finally:
            self._receivers.remove(ri)
        self._input_events = found
        return found

    def get_input_event(self, event, timeout_seconds=1) -> Any:
        """One-off read of an input event's value (float or str); None on
        timeout.  For repeated reads prefer subscribe_input_event()."""
        h = self.resolve_input_event(event)
        req_id = next(self._reqid_iter)
        result = []

        def receiver(recv: RECV_GET_INPUT_EVENT) -> bool:
            if recv.dwRequestID != req_id:
                return False
            result.append(recv.value)
            return True

        ri = ReceiverInstance(RECV_GET_INPUT_EVENT, receiver)
        self._receivers.append(ri)
        try:
            self.GetInputEvent(req_id, h)
            tmax = time() + timeout_seconds
            while not result and time() < tmax:
                self.receive(tmax - time())
        finally:
            self._receivers.remove(ri)
        return result[0] if result else None

    def set_input_event(self, event, value):
        """Set an input event: a number is sent as a double, a str as a
        NUL-terminated string."""
        h = self.resolve_input_event(event)
        if isinstance(value, str):
            buf = create_string_buffer(value.encode("utf-8"))
            self.SetInputEvent(h, sizeof(buf), cast(buf, c_void_p))
        else:
            d = c_double(float(value))
            self.SetInputEvent(h, sizeof(d), cast(byref(d), c_void_p))

    def subscribe_input_event(self, event, callback: Callable[[Any], None]) -> ReceiverInstance:
        """Have the sim report every change of an input event; callback
        receives the new value.  Returns the receiver, for
        unsubscribe_input_event().  The sim sends changes only, so pair
        with get_input_event() for the starting value."""
        h = self.resolve_input_event(event)

        def receiver(recv: RECV_SUBSCRIBE_INPUT_EVENT) -> bool:
            if recv.Hash != h:
                return False
            callback(recv.value)
            return True

        ri = ReceiverInstance(RECV_SUBSCRIBE_INPUT_EVENT, receiver)
        self._receivers.append(ri)
        self.SubscribeInputEvent(h)
        return ri

    def unsubscribe_input_event(self, event, receiver: Optional[ReceiverInstance] = None):
        h = self.resolve_input_event(event)
        self.UnsubscribeInputEvent(h)
        if receiver is not None:
            self.remove_receiver(receiver)
