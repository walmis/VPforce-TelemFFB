from typing import Callable, Type, Dict
import logging
from ctypes import cast, POINTER
from .scdefs import RECV, RECV_OPEN, RECV_EXCEPTION
from . import scdefs


Receiver = Callable[[RECV], bool]

_exc_map = dict(
    (getattr(scdefs, s), s)
    for s in dir(scdefs)
    if s.startswith('EXCEPTION_')
)

def all_subclasses(cls):
    return set(cls.__subclasses__()).union(
        [s for c in cls.__subclasses__() for s in all_subclasses(c)])


class ReceiverInstance:
    # SDK calls trigger responses which are subclasses of RECV
    # indicated by a corresponding RECV_ID_* constant
    # for example a response with recv.dwID = RECV_ID_OPEN
    # indicates the full response is a RECV_OPEN structure
    _recv_map: Dict[str, type] = {
        getattr(scdefs, kls.__name__.replace('RECV_', 'RECV_ID_'), None): kls
        for kls in all_subclasses(RECV)
    }

    def __init__(self, rtype: Type[RECV], receiver: Receiver):
        self._rtype = rtype
        self._receiver = receiver

    @staticmethod
    def cast_recv(pRecv) -> RECV:   #TODO type annotation : pointer[RECV] per https://github.com/python/mypy/issues/7540
        recv_id = pRecv.contents.dwID
        if recv_id in ReceiverInstance._recv_map:
            pRecv = cast(pRecv, POINTER(ReceiverInstance._recv_map[recv_id]))
        return pRecv.contents

    def receive(self, recv: RECV) -> bool:
        if isinstance(recv, self._rtype):
            return self._receiver(recv)
        else:
            return False


def receiveException(recv: RECV_EXCEPTION) -> bool:
    excid = recv.dwException
    excname = _exc_map.get(excid, '<unknown>')
    logging.warning(
        f"sc.receiveNext: exception {excname}({excid}), sendID {recv.dwSendID}, index {recv.dwIndex}"
    )
    return True


def receiveOpen(recv: RECV_OPEN) -> bool:
    appVer = f"v{recv.dwApplicationVersionMajor}.{recv.dwApplicationVersionMinor}"
    appBuild = f"build {recv.dwApplicationBuildMajor}.{recv.dwApplicationBuildMinor}"
    scVer = f"v{recv.dwSimConnectVersionMajor}.{recv.dwSimConnectVersionMinor}"
    scBuild = f"build {recv.dwSimConnectBuildMajor}.{recv.dwSimConnectBuildMinor}"
    logging.info(
        f"Open: App {recv.szApplicationName} {appVer} {appBuild} SimConnect: {scVer} {scBuild}"
    )
    return True


_default_receivers = [
    ReceiverInstance(RECV_EXCEPTION, receiveException), 
    ReceiverInstance(RECV_OPEN, receiveOpen),
]
