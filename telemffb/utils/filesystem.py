#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import hashlib
import logging
import ntpath
import os
import subprocess
import sys
import tempfile
import zipfile
import zlib
from datetime import datetime, timedelta

from PyQt6.QtCore import QCoreApplication

import telemffb.globals as G

__all__ = [
    "unsafe_install_location_reason",
    "archive_logs",
    "prune_log_files",
    "calculate_checksum",
    "calculate_crc",
    "get_script_path",
    "get_resource_path",
    "format_dict",
    "get_install_path",
    "exit_application",
    "ChildPopen",
    "check_launch_instance",
    "hexdump",
]

def _shared_shell_locations() -> dict:
    """Shared user locations that must never BE the app's install folder.

    Shell folders are resolved via the registry so folder redirection
    (e.g. a OneDrive-synced Desktop) is handled correctly.  Values map a
    human-readable label (used in the refusal message) to the folder path.
    """
    locations = {}
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            for label, value_name in (
                    ("your Desktop", "Desktop"),
                    ("your Documents folder", "Personal"),
                    ("your Downloads folder", "{374DE290-123F-4565-9164-39C4925E467B}")):
                try:
                    locations[label] = winreg.QueryValueEx(key, value_name)[0]
                except OSError:
                    pass
    except Exception:
        logging.exception("Unable to resolve shell folders for the install-location check")

    for label, env_var in (
            ("your user profile folder", "USERPROFILE"),
            ("the OneDrive root folder", "OneDrive"),
            ("the Program Files folder", "ProgramFiles"),
            ("the Program Files (x86) folder", "ProgramFiles(x86)"),
            ("the Windows folder", "SystemRoot")):
        value = os.environ.get(env_var)
        if value:
            locations[label] = value

    public = os.environ.get("PUBLIC")
    if public:
        locations["the Public Desktop"] = os.path.join(public, "Desktop")

    return locations


def unsafe_install_location_reason(app_dir: str, locations: dict = None):
    """Return a human-readable reason when app_dir is an unsafe place for a
    TelemFFB installation, or None when it is acceptable.

    The auto-updater manages the ENTIRE folder containing the executable
    (backing up and replacing its contents), so running from a shared
    location would sweep unrelated files into the update process.  Field
    incident: a release unzipped directly onto the Desktop - the updater
    moved the user's whole Desktop into the previous-version backup folder.

    Subfolders of shared locations are fine (e.g. Desktop\\TelemFFB); only
    the shared folder ITSELF (or a drive root, or anywhere under the temp
    directory) is refused.
    """
    def norm(p):
        # ntpath, not os.path: the app runs on Windows, so these are Windows
        # paths; ntpath IS os.path on Windows, while on other hosts it still
        # normalizes and case-folds drive-qualified paths correctly
        return ntpath.normcase(ntpath.abspath(p)).rstrip('\\/')

    root = norm(app_dir)

    # drive roots (C:\, D:\, ...)
    drive, tail = ntpath.splitdrive(root)
    if drive and not tail.strip('\\/'):
        return f"the root of drive {drive.upper()}\\"

    if locations is None:
        locations = _shared_shell_locations()

    for label, path in locations.items():
        if path and root == norm(path):
            return label

    # anywhere under the temp directory usually means the executable was
    # launched directly from inside the downloaded .zip
    tmp = norm(tempfile.gettempdir())
    if root == tmp or root.startswith(tmp + '\\'):
        return "a temporary folder (was it started from inside the .zip file?)"

    return None


def archive_logs(directory):
    today = datetime.today().strftime('%Y%m%d')

    for filename in os.listdir(directory):
        if filename.endswith('.log'):
            file_date = filename[:-4][-8:]  # Extract the date part
            if file_date != today:
                zip_filename = f"TelemFFB_Log_Archive_{file_date}.zip"
                readable_date = datetime.strptime(file_date, "%Y%m%d").strftime('%B %d, %Y')
                logging.info(f"Archiving logs from {readable_date} into {zip_filename}")
                zip_path = os.path.join(directory, zip_filename)

                with zipfile.ZipFile(zip_path, 'a', compression=zipfile.ZIP_LZMA, compresslevel=9) as zip_file:
                    log_file_path = os.path.join(directory, filename)
                    zip_file.write(log_file_path, os.path.basename(log_file_path))
                    os.remove(log_file_path)  # Remove the original log file
    # the fallbacks mirror SystemSettings.defaults, which is what is actually
    # returned; they only matter if a key is missing there too
    if G.system_settings.get("pruneLogs", True):
        num = G.system_settings.get('pruneLogsNum', 1)
        unit = G.system_settings.get('pruneLogsUnit', "Week(s)")
        prune_log_files(directory, num, unit)


def prune_log_files(path, number, unit):
    # Define mapping from unit strings to timedelta units
    units_mapping = {
        "Day(s)": 1,
        "Week(s)": 7,
        "Month(s)": 30  # approximate month as 30 days
    }

    # Get the timedelta unit from the mapping
    num_days = number * units_mapping.get(unit)
    if num_days is None:
        raise ValueError("Invalid unit. Valid units are: 'Day(s)', 'Week(s)', 'Month(s)'.")


    # Calculate the cutoff date
    cutoff_date = datetime.now() - timedelta(**{"days": num_days + 1})
    # Iterate over log files and delete files older than the cutoff date
    for filename in os.listdir(path):
        if filename.endswith(".zip") and filename.startswith("TelemFFB_Log_Archive_"):
            # Extract date from filename
            date_str = filename.split("_")[-1].split(".")[0]
            try:
                file_date = datetime.strptime(date_str, "%Y%m%d")
            except ValueError:
                # Skip files with invalid date format
                continue

            # Delete file if older than cutoff date
            if file_date < cutoff_date:
                os.remove(os.path.join(path, filename))
                logging.info(f'Deleting log archive: {filename} as it has exceeded the pruning threshold of {num_days} Days')


def calculate_checksum(file_path):
    crc = zlib.crc32(open(file_path, 'rb').read())
    return crc


def calculate_crc(file_path):
    # Calculate CRC for a file
    crc = hashlib.md5()
    with open(file_path, 'rb') as file:
        for chunk in iter(lambda: file.read(4096), b''):
            crc.update(chunk)
    return crc.hexdigest()

def get_script_path():
    if getattr(sys, 'frozen', False):
        # we are running in a bundle
        script_dir = os.path.dirname(sys.executable)
    else:
        # we are running in a normal Python environment
        script_dir = os.path.dirname(os.path.abspath(__file__))
    return script_dir


def get_resource_path(relative_path, prefer_root=False, force=False):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if getattr(sys, 'frozen', False):
        # we are running in a bundle
        bundle_dir = sys._MEIPASS
        script_dir = os.path.dirname(sys.executable)
    else:
        # we are running in a normal Python environment
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
        bundle_dir = os.path.abspath(os.path.join(bundle_dir, ".."))
        script_dir = bundle_dir

    if prefer_root:
        # if prefer_root is true, look in 'script dir' to find the relative path
        f_path = os.path.join(script_dir, relative_path)
        if os.path.isfile(f_path) or force:
            # if the file exists, return the path
            return f_path
        else:
            logging.debug(
                f"get_resource_path, root_prefer=True.  Did not find {relative_path} relative to script/exe dir.. looking in bundle dir...")
            # fall back to bundle dir if not found it script dir, log warning if still not found
            # note, script dir and bundle dir are same when running from source
            f_path = os.path.join(bundle_dir, relative_path)
            if not os.path.isfile(f_path):
                logging.warning(
                    f"Warning, get_resource_path, root_prefer=True, did not find file in script/exe folder or bundle folder: {f_path}")
            logging.debug(f"get_resource_path, Found {relative_path} located at {f_path}")
            return f_path
    else:
        f_path = os.path.join(bundle_dir, relative_path)
        if not os.path.isfile(f_path):
            logging.warning(f"Warning, get_resource_path did not find file in bundle folder: {f_path}")
        return f_path


def format_dict(data, prefix=""):
    output = ""
    for key, value in data.items():
        if isinstance(value, dict):
            output += format_dict(value, prefix + key + ".")
        else:
            output += prefix + key + " = " + str(value) + "\n"
    return output


def get_install_path():
    """ return path where executable or main script is installed"""
    if getattr(sys, 'frozen', False):
        _install_path = os.path.dirname(sys.executable)
    else:
        _install_path = os.path.dirname(os.path.abspath(__file__))
    return _install_path


def exit_application():
    # Perform any cleanup or save operations here
    G.main_window.save_main_window_geometry()
    QCoreApplication.instance().quit()

class ChildPopen(subprocess.Popen):
    udp_port : int

def check_launch_instance(dev_type :str, master_port : int) -> subprocess.Popen:
    """Check prerequisites and launch a new telemFFB instance

    :param dev_type: _description_
    :type dev_type: str
    """
    dev_type_cap = dev_type.capitalize()
    if G.system_settings.get(f'autolaunch{dev_type_cap}', False) and G.device_type != dev_type:
        usbpid = G.system_settings.get(f'pid{dev_type_cap}', '2055')

        if not usbpid:
            logging.warning("Device PID unset for device %s, not launching", dev_type)
            return None
         
        usb_vidpid = f"FFFF:{usbpid}"
    
        args = [sys.argv[0], '-D', usb_vidpid, '-t', dev_type, '--child', '--masterport', str(master_port)]
        if sys.argv[0].endswith(".py"): # insert python interpreter if we launch ourselves as a script
            args.insert(0, sys.executable)

        if G.system_settings.get(f'startMin{dev_type_cap}', False):
            args.append('--minimize')
        if G.system_settings.get(f'startHeadless{dev_type_cap}', False):
            args.append('--headless')

        if G.args.darkmode:
            args.append('--darkmode')
        elif G.args.lightmode:
            args.append('--lightmode')

        logging.info("Auto-Launch: starting instance: %s", args)
        proc = ChildPopen(args)
        try:
            # pids are hex ('2055', or e.g. '1b' for a DirectInput device);
            # QSettings may hand back digit-only values as int
            proc.udp_port = 60000 + int(str(usbpid), 16)
        except (ValueError, TypeError):
            proc.udp_port = 60000
        G.launched_instances[dev_type] = proc
        return proc


def hexdump(src, length=16, sep='.'):
    """Hex dump bytes to ASCII string, padded neatly
    In [107]: x = b'\x01\x02\x03\x04AAAAAAAAAAAAAAAAAAAAAAAAAABBBBBBBBBBBBBBBBBBBBBBBBBB'

    In [108]: print('\n'.join(hexdump(x)))
00000000  01 02 03 04 41 41 41 41  41 41 41 41 41 41 41 41 |....AAAAAAAAAAAA|
    00000010  41 41 41 41 41 41 41 41  41 41 41 41 41 41 42 42 |AAAAAAAAAAAAAABB|
    00000020  42 42 42 42 42 42 42 42  42 42 42 42 42 42 42 42 |BBBBBBBBBBBBBBBB|
    00000030  42 42 42 42 42 42 42 42                          |BBBBBBBB        |
    """
    FILTER = ''.join([(len(repr(chr(x))) == 3) and chr(x) or sep for x in range(256)])
    lines = []
    for c in range(0, len(src), length):
        chars = src[c: c + length]
        hex_ = ' '.join(['{:02x}'.format(x) for x in chars])
        if len(hex_) > 24:
            hex_ = '{} {}'.format(hex_[:24], hex_[24:])
        printable = ''.join(['{}'.format((x <= 127 and FILTER[x]) or sep) for x in chars])
        lines.append('{0:08x}  {1:{2}s} |{3:{4}s}|'.format(c, hex_, length * 3, printable, length))

    return ("\n".join(lines))
