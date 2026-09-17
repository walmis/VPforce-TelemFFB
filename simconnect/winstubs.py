# hack to avoid simconnect import error on non-windows platform
# allows us to run some supporting tools, but not simconnect SDK connection
from ctypes import c_char, c_ushort, c_uint32, c_int32, c_void_p, c_char_p


# widths must match the Windows ABI (ctypes.wintypes) so struct layouts and
# wire-formatted buffers decode identically on all platforms; c_ulong/c_long
# are 8 bytes on 64-bit POSIX, which misaligns every 4-byte field
HRESULT = c_int32
BYTE = c_char
WORD = c_ushort
DWORD = c_uint32
HANDLE = c_void_p
LPCSTR = c_char_p
HWND = c_void_p


def WINFUNCTYPE(*args, **kwargs):
    pass


class windll:
    def LoadLibrary(self, *args, **kwargs):
        pass
