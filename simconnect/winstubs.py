# hack to avoid simconnect import error on non-windows platform
# allows us to run some supporting tools, but not simconnect SDK connection
from ctypes import c_char, c_ushort, c_ulong, c_void_p, c_char_p


HRESULT = c_ushort
BYTE = c_char
WORD = c_ushort
DWORD = c_ulong
HANDLE = c_void_p
LPCSTR = c_char_p
HWND = c_void_p


def WINFUNCTYPE(*args, **kwargs):
    pass


class windll:
    def LoadLibrary(self, *args, **kwargs):
        pass
