"""The telemetry plot process: the Monitor page's plot window runs in a
process of its own, fed over a localhost UDP socket from the telemetry
thread, so plotting never touches this process's main thread or competes
with telemetry handling for the interpreter.

``protocol`` is the datagram format, ``feed`` the sender (this process),
``process`` the launcher and controller (this process), ``app`` the entry
point of the plot process itself.
"""
