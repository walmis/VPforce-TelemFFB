"""
Python Shared Memory Reader for Windows
Reads data from shared memory created by C++ writer process using Windows API.
"""

import ctypes
import ctypes.wintypes
import struct
import time
import sys
from typing import Optional, NamedTuple
from dataclasses import dataclass

# Windows API constants
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_MAP_READ = 0x0004
FILE_MAP_WRITE = 0x0002
FILE_MAP_ALL_ACCESS = 0x001f
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
INVALID_HANDLE_VALUE = -1
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
SYNCHRONIZE = 0x00100000

# Shared memory configuration (must match C++ constants)
MESSAGE_BUFFER_SIZE = 65000
SHARED_DATA_VERSION = 1

_kernel32 = None

def _get_kernel32():
    """Lazy-load kernel32 to allow module import on non-Windows platforms."""
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    if sys.platform != "win32":
        raise OSError("SharedMemReader requires Windows")
    _kernel32 = ctypes.windll.kernel32
    # Define function prototypes
    _kernel32.OpenFileMappingA.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.c_char_p]
    _kernel32.OpenFileMappingA.restype = ctypes.wintypes.HANDLE
    _kernel32.MapViewOfFile.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
                                        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t]
    _kernel32.MapViewOfFile.restype = ctypes.wintypes.LPVOID
    _kernel32.UnmapViewOfFile.argtypes = [ctypes.wintypes.LPVOID]
    _kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL
    _kernel32.CreateEventA.argtypes = [ctypes.wintypes.LPVOID, ctypes.wintypes.BOOL, ctypes.wintypes.BOOL, ctypes.c_char_p]
    _kernel32.CreateEventA.restype = ctypes.wintypes.HANDLE
    _kernel32.WaitForMultipleObjects.argtypes = [ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.HANDLE),
                                                 ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    _kernel32.WaitForMultipleObjects.restype = ctypes.wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    _kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    _kernel32.OpenEventA.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.c_char_p]
    _kernel32.OpenEventA.restype = ctypes.wintypes.HANDLE
    return _kernel32

class WindowsEvent:
    """Wrapper for Windows Event objects"""
    
    def __init__(self, name: bytes, access: int = SYNCHRONIZE, create: bool = False, manual_reset: bool = True):
        self.handle = None
        self.name = name
        self.manual_reset = manual_reset
        if self._open(access):
            return
        if create:
            self._create(access)
        
    def _create(self, access: int) -> bool:
        """Create or open existing event"""
        self.handle = _get_kernel32().CreateEventA(None, self.manual_reset, False, self.name)
        return self.handle is not None
        
    def _open(self, access: int) -> bool:
        """Open existing event"""
        self.handle = _get_kernel32().OpenEventA(access, False, self.name)
        return self.handle is not None
        
    def is_valid(self) -> bool:
        """Check if event handle is valid"""
        return self.handle is not None
        
    def close(self):
        """Close event handle"""
        if self.handle:
            _get_kernel32().CloseHandle(self.handle)
            self.handle = None

    def wait(self, timeout: Optional[int] = None) -> bool:
        """Wait for the event to be signaled. Returns True if signaled, False on timeout or error."""
        if not self.is_valid():
            return False
        
        if timeout is None:
            timeout = INFINITE
            
        result = _get_kernel32().WaitForSingleObject(self.handle, timeout)
        
        if result == WAIT_OBJECT_0:
            return True
        elif result == WAIT_TIMEOUT:
            return False
        else:
            raise ctypes.WinError()      
    
    def __del__(self):
        self.close()

class SharedMemoryMapping:
    """Wrapper for Windows shared memory mapping"""
    
    def __init__(self, name: bytes, size: int, access: int = FILE_MAP_READ):
        self.name = name
        self.size = size
        self.h_map_file = None
        self.p_data = None
        self._open(access)
        
    def _open(self, access: int) -> bool:
        """Open existing shared memory mapping"""
        self.h_map_file = _get_kernel32().OpenFileMappingA(access, False, self.name)
        if not self.h_map_file:
            return False
            
        self.p_data = _get_kernel32().MapViewOfFile(
            self.h_map_file, access, 0, 0, self.size
        )
        return self.p_data is not None
        
    def is_valid(self) -> bool:
        """Check if mapping is valid"""
        return self.h_map_file is not None and self.p_data is not None
        
    def get_pointer(self):
        """Get pointer to mapped memory"""
        return self.p_data
        
    def close(self):
        """Close mapping and file handle"""
        if self.p_data:
            _get_kernel32().UnmapViewOfFile(self.p_data)
            self.p_data = None
            
        if self.h_map_file:
            _get_kernel32().CloseHandle(self.h_map_file)
            self.h_map_file = None
            
    def __del__(self):
        self.close()

class MultiEventWaiter:
    """Helper for waiting on multiple Windows events"""
    
    def __init__(self, events: list[WindowsEvent]):
        self.events = events
        self.event_handles = (ctypes.wintypes.HANDLE * len(events))(
            *[event.handle for event in events]
        )
        
    def wait(self, timeout_ms: int = INFINITE) -> int:
        """Wait for any event to be signaled. Returns event index or -1 on error/timeout"""
        result = _get_kernel32().WaitForMultipleObjects(
            len(self.events), self.event_handles, False, timeout_ms
        )
        
        if WAIT_OBJECT_0 <= result < WAIT_OBJECT_0 + len(self.events):
            return result - WAIT_OBJECT_0
        elif result == WAIT_TIMEOUT:
            return -2  # Timeout
        else:
            return -1  # Error

# Define ctypes structure that matches C++ SharedData layout
class SharedDataStruct(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("data_size", ctypes.c_uint32),
        ("sequence_number", ctypes.c_uint32),
        ("writer_pid", ctypes.c_uint32),
        ("timestamp_us", ctypes.c_uint64),
        ("message", ctypes.c_char * MESSAGE_BUFFER_SIZE),
    ]

@dataclass
class SharedData:
    """Python representation of the C++ SharedData structure"""
    version: int
    data_size: int
    sequence_number: int
    writer_pid: int
    timestamp_us: int
    message: str

class WaitResult:
    DATA_UPDATED = 0
    TIMEOUT = 1
    ERROR = 2

class SharedMemoryReader:
    """Python reader for Windows shared memory created by C++ writer"""
    
    def __init__(self, name: bytes, size: int):
        self.name = name
        self.size = size
        self.shared_memory = None
        self.last_sequence_number = 0
        self.connected = False
        
        # Create update event immediately in __init__ - but don't fail if it doesn't exist yet
        try:
            self.update_event = WindowsEvent(self.name + b"_update_event", create=True)
        except Exception:
            # Event might not exist yet - that's okay, we'll handle this in read()
            self.update_event = None
        
    def open(self) -> bool:
        """Open shared memory connection - only handles shared memory, events are created in __init__"""
        if self.connected:
            return True
            
        try:
            # Try to open shared memory
            self.shared_memory = SharedMemoryMapping(
                self.name, self.size, FILE_MAP_READ
            )
            if not self.shared_memory.is_valid():
                self.shared_memory = None
                return False
                
            # Verify data structure version
            if not self.shared_memory.get_pointer():
                self.shared_memory = None
                return False
                
            shared_struct = ctypes.cast(
                self.shared_memory.get_pointer(), 
                ctypes.POINTER(SharedDataStruct)
            ).contents
            
            if shared_struct.version != SHARED_DATA_VERSION:
                print(f"Shared data version mismatch. Expected: {SHARED_DATA_VERSION}, Found: {shared_struct.version}")
                self.shared_memory.close()
                self.shared_memory = None
                return False
                
            # Read initial sequence number
            self.last_sequence_number = shared_struct.sequence_number
            
            self.connected = True
            return True
            
        except Exception as e:
            print(f"Error opening shared memory: {e}")
            if self.shared_memory:
                self.shared_memory.close()
                self.shared_memory = None
            return False
            
    def read(self, timeout_ms: Optional[int] = None) -> Optional[SharedData]:
        """Blocking read - wait for data update with optional timeout. Auto-opens if not connected."""
        
        # Ensure event is properly initialized
        #if not self._ensure_event_initialized():
        #    return None
            
        # Use infinite timeout if none specified
        if timeout_ms is None:
            timeout_ms = INFINITE
            
        try:
            # Wait for update event
            result = self.update_event.wait(timeout_ms)

            # Auto-open if not connected
            if not self.connected:
                if not self.open():
                    return None
            
            if result:  # Update event
                return self._read_current_data()
            else: 
                return None
                
        except Exception as e:
            print(f"Error during read: {e}")
            return None
            
    def read_current(self) -> Optional[SharedData]:
        """Non-blocking read of current data without waiting for updates"""
        if not self.connected:
            return None
        return self._read_current_data()
        
    def _read_current_data(self) -> Optional[SharedData]:
        """Internal method to read current data from shared memory"""
        if not self.connected or not self.shared_memory:
            return None
            
        try:
            # Get pointer and verify it's valid
            pointer = self.shared_memory.get_pointer()
            if not pointer:
                return None
                
            # Cast shared memory pointer to our ctypes structure
            shared_struct = ctypes.cast(
                pointer, 
                ctypes.POINTER(SharedDataStruct)
            ).contents
            
            # Decode message (null-terminated string)
            message = shared_struct.message.decode('utf-8', errors='replace').rstrip('\x00')
            
            self.last_sequence_number = shared_struct.sequence_number
            
            return SharedData(
                version=shared_struct.version,
                data_size=shared_struct.data_size,
                sequence_number=shared_struct.sequence_number,
                writer_pid=shared_struct.writer_pid,
                timestamp_us=shared_struct.timestamp_us,
                message=message
            )
            
        except Exception as e:
            print(f"Error reading shared data: {e}")
            return None
            
    def close(self):
        """Close connection and clean up resources"""
        if self.update_event:
            self.update_event.close()
            self.update_event = None
            
        if self.shared_memory:
            self.shared_memory.close()
            self.shared_memory = None
            
        self.connected = False
        
    def is_connected(self) -> bool:
        """Check if reader is connected"""
        return self.connected
        
    def get_last_sequence(self) -> int:
        """Get last sequence number read"""
        return self.last_sequence_number
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _ensure_event_initialized(self) -> bool:
        """Ensure update event is properly initialized. Returns True if successful."""
        if (self.update_event is not None and self.update_event.is_valid()):
            return True
        
        try:
            if self.update_event:
                self.update_event.close()
                
            self.update_event = WindowsEvent(UPDATE_EVENT_NAME, SYNCHRONIZE)
            if not self.update_event.is_valid():
                print("Failed to initialize update event.")
                return False
                
            return True
            
        except Exception:
            return False

def main():
    """Example usage of SharedMemoryReader with socket-like API"""
    print("=== Python Shared Memory Reader ===")
    print("This program will read data from shared memory created by the C++ writer.")
    print("Press Ctrl+C to exit.\n")
    
    with SharedMemoryReader(SHARED_MEMORY_NAME, SHARED_MEMORY_SIZE) as reader:
        # Try to connect with retry logic
        print("Attempting to connect to shared memory...")
            
        print("Waiting for data updates...\n")
        
        update_count = 0
        start_time = time.time()
        
        try:
            while True:
                # Blocking read with 5 second timeout
                data = reader.read(1000)
                
                if data is not None:
                    update_count += 1
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    
                    print(f"=== Update #{update_count} (Seq: {data.sequence_number}) at +{elapsed_ms}ms ===")
                    print(f"Message: {data.message}")
                    print(f"Writer PID: {data.writer_pid}")
                    print(f"Timestamp: {data.timestamp_us} μs")
                else:
                    print("No data received in the last 1 seconds.")
                    # Check if we're still connected
                    if not reader.is_connected():
                        print("Connection lost.")
                    else:
                        print("No updates received in the last 1 seconds...")
                    continue
                    
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            
        print(f"Reader shutting down. Total updates received: {update_count}")
        return 0

if __name__ == "__main__":
    sys.exit(main())


