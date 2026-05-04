"""
HID Diagnostic Tool — WinWing Orion 2 / F-15EX Vibration Probe

Enumerates all HID devices, identifies WinWing hardware, and attempts
to probe output/feature reports to locate vibration motor controls.

Usage:
    python hid_probe.py           — list all devices + WinWing details
    python hid_probe.py --buzz    — send a short test pulse to WinWing
    python hid_probe.py --sniff   — read 3 seconds of input from each WinWing interface
"""

import sys
import os
import time
import ctypes
import argparse

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── locate hidapi.dll (same folder as this script or in dll\ subdir) ──────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
for _candidate in [
    os.path.join(_script_dir, "dll", "hidapi.dll"),
    os.path.join(_script_dir, "hidapi.dll"),
    "hidapi.dll",
]:
    if os.path.exists(_candidate):
        os.add_dll_directory(os.path.dirname(os.path.abspath(_candidate)))
        hidapi = ctypes.cdll.LoadLibrary(_candidate)
        break
else:
    sys.exit("ERROR: hidapi.dll not found. Run from the project root.")


# ── ctypes setup ──────────────────────────────────────────────────────────────
class _DeviceInfo(ctypes.Structure):
    pass

_DeviceInfo._fields_ = [
    ("path",               ctypes.c_char_p),
    ("vendor_id",          ctypes.c_ushort),
    ("product_id",         ctypes.c_ushort),
    ("serial_number",      ctypes.c_wchar_p),
    ("release_number",     ctypes.c_ushort),
    ("manufacturer_string",ctypes.c_wchar_p),
    ("product_string",     ctypes.c_wchar_p),
    ("usage_page",         ctypes.c_ushort),
    ("usage",              ctypes.c_ushort),
    ("interface_number",   ctypes.c_int),
    ("next",               ctypes.POINTER(_DeviceInfo)),
]

hidapi.hid_init.restype = ctypes.c_int
hidapi.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
hidapi.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
hidapi.hid_free_enumeration.restype = None
hidapi.hid_open_path.argtypes = [ctypes.c_char_p]
hidapi.hid_open_path.restype = ctypes.c_void_p
hidapi.hid_close.argtypes = [ctypes.c_void_p]
hidapi.hid_close.restype = None
hidapi.hid_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
hidapi.hid_write.restype = ctypes.c_int
hidapi.hid_read_timeout.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
hidapi.hid_read_timeout.restype = ctypes.c_int
hidapi.hid_get_feature_report.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
hidapi.hid_get_feature_report.restype = ctypes.c_int
hidapi.hid_send_feature_report.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
hidapi.hid_send_feature_report.restype = ctypes.c_int
hidapi.hid_set_nonblocking.argtypes = [ctypes.c_void_p, ctypes.c_int]
hidapi.hid_error.argtypes = [ctypes.c_void_p]
hidapi.hid_error.restype = ctypes.c_wchar_p

hidapi.hid_init()


# ── known VIDs ────────────────────────────────────────────────────────────────
KNOWN_VENDORS = {
    0xFFFF: "VPForce",
    0x4098: "WinWing",
    0x044F: "Thrustmaster",
    0x046D: "Logitech",
    0x045E: "Microsoft",
    0x0483: "STMicro",
}

WINWING_VID = 0x4098

# Known WinWing PIDs (incomplete — we discover the rest via enumeration)
WINWING_KNOWN_PIDS = {
    0x0001: "Orion Throttle Base",
    0x0002: "Orion Throttle Base (alt)",
    0x0010: "Orion2 Throttle",
    0x0011: "Orion2 Throttle (alt)",
    0x0020: "Orion2 F-16EX Stick Base",
    0x0021: "Orion2 F-15EX Stick Base",
    0x0022: "Orion2 F-18 Stick Base",
    0x0030: "Orion2 F-15EX Left Handle",
    0x0031: "Orion2 F-15EX Right Handle",
    0x0100: "UFC",
    0x0200: "MFD",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _enumerate_all():
    devices = []
    info = hidapi.hid_enumerate(0, 0)
    c = info
    while c:
        d = c.contents
        devices.append({
            "path":         d.path,
            "vid":          d.vendor_id,
            "pid":          d.product_id,
            "serial":       d.serial_number or "",
            "manufacturer": d.manufacturer_string or "",
            "product":      d.product_string or "",
            "usage_page":   d.usage_page,
            "usage":        d.usage,
            "interface":    d.interface_number,
        })
        c = d.next
    hidapi.hid_free_enumeration(info)
    return devices


def _hex_dump(data: bytes, indent: str = "    ") -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{i:04X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def _probe_feature_reports(handle, report_ids=None):
    """Try reading feature reports 0x00–0xFF; return a dict of {id: bytes}."""
    found = {}
    if report_ids is None:
        report_ids = range(0x00, 0x100)
    buf = ctypes.create_string_buffer(256)
    for rid in report_ids:
        buf[0] = rid
        n = hidapi.hid_get_feature_report(handle, buf, 256)
        if n > 1:
            found[rid] = bytes(buf.raw[:n])
    return found


def _read_input(handle, seconds=3.0, max_reports=50):
    """Read input reports for `seconds` seconds, return list of unique ones."""
    hidapi.hid_set_nonblocking(handle, 0)  # blocking
    buf = ctypes.create_string_buffer(256)
    seen = set()
    reports = []
    deadline = time.time() + seconds
    while time.time() < deadline and len(reports) < max_reports:
        remaining_ms = int((deadline - time.time()) * 1000)
        if remaining_ms <= 0:
            break
        n = hidapi.hid_read_timeout(handle, buf, 256, min(remaining_ms, 100))
        if n > 0:
            raw = bytes(buf.raw[:n])
            if raw not in seen:
                seen.add(raw)
                reports.append(raw)
    return reports


def _try_buzz(handle, duration_ms=500):
    """
    Attempt to trigger WinWing vibration via several candidate report formats.
    Returns (report_id, payload) if one did not error, else None.
    """
    # WinWing uses a 64-byte HID output report.
    # Candidate formats based on community reverse-engineering:
    candidates = [
        # (report_id, payload_bytes)
        # Format 1: simple rumble — byte[1]=command, byte[2-3]=motor intensities
        (0x00, bytes([0x00, 0x01, 0xFF, 0xFF] + [0x00] * 60)),
        # Format 2: seen in WinWing SimAppPro USB captures
        (0x00, bytes([0x00, 0x04, 0x01, 0xFF, 0x00, duration_ms & 0xFF] + [0x00] * 58)),
        # Format 3: feature-report style
        (0x01, bytes([0x01, 0x04, 0x01, 0xFF, 0x00, duration_ms & 0xFF] + [0x00] * 58)),
        # Format 4: output report with 0x02 command
        (0x00, bytes([0x00, 0x02, 0xFF, 0xFF, 0x00, 0x00] + [0x00] * 58)),
    ]

    for report_id, payload in candidates:
        try:
            n = hidapi.hid_write(handle, payload, len(payload))
            if n >= 0:
                time.sleep(duration_ms / 1000.0 + 0.1)
                # Stop command
                stop = bytes([payload[0], payload[1], 0x00, 0x00] + [0x00] * 60)
                hidapi.hid_write(handle, stop, len(stop))
                return (report_id, payload)
        except Exception:
            continue
    return None


# ── main listing ──────────────────────────────────────────────────────────────
def cmd_list(args):
    all_devs = _enumerate_all()

    # Group by vendor
    by_vendor: dict[int, list] = {}
    for d in all_devs:
        by_vendor.setdefault(d["vid"], []).append(d)

    print("=" * 70)
    print("  ALL HID DEVICES")
    print("=" * 70)
    for vid in sorted(by_vendor):
        vendor_name = KNOWN_VENDORS.get(vid, "Unknown")
        devs = by_vendor[vid]
        print(f"\n[VID 0x{vid:04X}] {vendor_name}  ({len(devs)} interface(s))")
        for d in sorted(devs, key=lambda x: (x["pid"], x["interface"])):
            pid_name = ""
            if vid == WINWING_VID:
                pid_name = WINWING_KNOWN_PIDS.get(d["pid"], "?")
            print(f"  PID 0x{d['pid']:04X}  iface={d['interface']}  "
                  f"UP=0x{d['usage_page']:04X} U=0x{d['usage']:04X}  "
                  f"{d['product']!r}  {pid_name}")

    # ── WinWing deep-dive ──────────────────────────────────────────────────
    ww_devs = [d for d in all_devs if d["vid"] == WINWING_VID]
    if not ww_devs:
        print("\n[!] No WinWing devices found.")
        print("    Make sure the device is plugged in and not exclusively opened by another process.")
        return

    print("\n" + "=" * 70)
    print("  WINWING DEEP-DIVE")
    print("=" * 70)

    for d in sorted(ww_devs, key=lambda x: (x["pid"], x["interface"])):
        pid_name = WINWING_KNOWN_PIDS.get(d["pid"], "unknown product")
        print(f"\n  -- PID 0x{d['pid']:04X}  iface={d['interface']}  {pid_name}")
        print(f"     Product : {d['product']!r}")
        print(f"     Mfr     : {d['manufacturer']!r}")
        print(f"     Serial  : {d['serial']!r}")
        print(f"     Usage   : page=0x{d['usage_page']:04X}  usage=0x{d['usage']:04X}")
        print(f"     Path    : {d['path']}")

        handle = hidapi.hid_open_path(d["path"])
        if not handle:
            print("     [!] Could not open — in use by another process (WinWing SimAppPro?)")
            continue

        print("     [+] Opened successfully")

        # probe feature reports (0x00–0x0F first, then 0x10–0xFF)
        print("     Probing feature reports 0x00–0xFF …", end="", flush=True)
        feat = _probe_feature_reports(handle, range(0x00, 0x100))
        if feat:
            print(f" found {len(feat)}")
            for rid, data in feat.items():
                print(f"       Report 0x{rid:02X} ({len(data)} bytes):")
                print(_hex_dump(data))
        else:
            print(" none responded")

        # read a few input reports
        print("     Reading input for 1 s …", end="", flush=True)
        hidapi.hid_set_nonblocking(handle, 1)
        buf = ctypes.create_string_buffer(256)
        seen = set()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            n = hidapi.hid_read_timeout(handle, buf, 256, 50)
            if n > 0:
                raw = bytes(buf.raw[:n])
                if raw not in seen:
                    seen.add(raw)

        if seen:
            print(f" {len(seen)} unique report(s)")
            for raw in seen:
                print(f"       Report ({len(raw)} bytes):")
                print(_hex_dump(raw))
        else:
            print(" no data (interface may be output-only or idle)")

        hidapi.hid_close(handle)

    print("\n[i] Tip: run with --buzz to test vibration, --sniff for live input monitoring.")


def cmd_buzz(args):
    all_devs = _enumerate_all()
    ww_devs = [d for d in all_devs if d["vid"] == WINWING_VID]
    if not ww_devs:
        print("[!] No WinWing devices found.")
        return

    print(f"Found {len(ww_devs)} WinWing interface(s). Trying vibration on each …\n")
    for d in sorted(ww_devs, key=lambda x: (x["pid"], x["interface"])):
        pid_name = WINWING_KNOWN_PIDS.get(d["pid"], "?")
        print(f"  PID 0x{d['pid']:04X}  iface={d['interface']}  {pid_name}")
        handle = hidapi.hid_open_path(d["path"])
        if not handle:
            print("    [!] Cannot open (in use?)")
            continue

        result = _try_buzz(handle)
        if result:
            rid, payload = result
            print(f"    [+] Buzz sent — report_id=0x{rid:02X}  payload={payload[:8].hex()} …")
            print(f"    Did you feel anything? Note which interface/PID responded.")
        else:
            print("    [-] All buzz candidates failed or returned error.")

        hidapi.hid_close(handle)


def cmd_sniff(args):
    all_devs = _enumerate_all()
    ww_devs = [d for d in all_devs if d["vid"] == WINWING_VID]
    if not ww_devs:
        print("[!] No WinWing devices found.")
        return

    duration = getattr(args, "duration", 3)
    print(f"Sniffing {len(ww_devs)} WinWing interface(s) for {duration}s each …\n")
    for d in sorted(ww_devs, key=lambda x: (x["pid"], x["interface"])):
        pid_name = WINWING_KNOWN_PIDS.get(d["pid"], "?")
        print(f"  PID 0x{d['pid']:04X}  iface={d['interface']}  {pid_name}  — press buttons/axes …")
        handle = hidapi.hid_open_path(d["path"])
        if not handle:
            print("    [!] Cannot open (in use?)")
            continue

        reports = _read_input(handle, seconds=duration)
        if reports:
            print(f"    {len(reports)} unique input report(s):")
            for raw in reports:
                print(f"      ({len(raw)} bytes) {raw.hex(' ')}")
        else:
            print("    No input reports received.")
        hidapi.hid_close(handle)


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HID probe / WinWing vibration tester")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list",  help="List all devices + WinWing details (default)")
    sub.add_parser("buzz",  help="Send test vibration to every WinWing interface")
    sniff_p = sub.add_parser("sniff", help="Read input reports from WinWing interfaces")
    sniff_p.add_argument("--duration", type=float, default=3.0,
                         help="Seconds to listen per interface (default: 3)")

    args = parser.parse_args()

    if args.cmd == "buzz":
        cmd_buzz(args)
    elif args.cmd == "sniff":
        cmd_sniff(args)
    else:
        cmd_list(args)
