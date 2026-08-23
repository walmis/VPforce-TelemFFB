#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
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
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Finding the sims the DirectInput tap can be installed into, and reporting what is
there now.

The tap is a ``dinput8.dll`` wrapper that sits beside a game's executable, so
managing it means knowing where that executable is.  Which is not one
question but several: a sim may be a standalone install or a Steam one, may
be recorded in the registry or only known to TelemFFB, and - IL-2 Korea -
may nest its game directory one level deeper than its sibling title.

A sim is identified by its *signature*: the paths its executables occupy
relative to the game root.  A candidate directory is the sim if the
signature is there.  That avoids depending on install folder names, which
differ between storefronts and are the least reliable thing about a game
install.

Alongside detection this module installs and removes the wrapper, and writes
the ``dinput8.ini`` that decides which devices it hands to TelemFFB.
Keeping that file in step with the devices afterwards - reconciling a
swap, filling a gap, taking the tap back out - is ``tap_reconcile``.
"""

import logging
import os
import re
import shutil
from pathlib import Path
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from telemffb.tap_config import Rule
from telemffb.utils import (DEVICE_ROLES, device_ident_key,
                            device_ids_key, il2_korea_game_root,
                            parse_usb_ids, usb_ids_from_devpath)


#: Strings the wrapper carries.  What distinguishes it from an unrelated
#: dinput8.dll - a ReShade shim, another wrapper, or the upstream project
#: this one is built from.  Older builds carry no version resource, so the
#: strings are the identity and the version is only ever extra.
TAP_MARKERS = (b"FFB tap:", b"TelemFFB gate:")

#: What the wrapper is called once installed, and the optional ini beside it.
WRAPPER_NAME = "dinput8.dll"
WRAPPER_CONFIG = "dinput8.ini"


class WrapperState:
    """What is sitting in a target directory."""
    ABSENT = "absent"        # nothing there
    TAP = "tap"              # our wrapper
    FOREIGN = "foreign"      # any other dinput8.dll
    UNREADABLE = "unreadable"  # present but could not be examined


@dataclass(frozen=True)
class TapSim:
    """One sim the tap can be installed into."""
    key: str
    name: str
    #: Executables, relative to the resolved game root.  Their directories
    #: are where the wrapper goes; a root is only accepted as this sim if at
    #: least one of them is present.
    exe_relpaths: Tuple[str, ...]
    #: The setting holding a path the user already configured, if any.
    settings_key: Optional[str] = None
    #: Name of the module-level function returning candidate roots from
    #: the registry.  A name rather than the function itself: holding the
    #: object would bind it at import, so the lookup could not be
    #: substituted - by a test, or by anything else.
    registry_lookup: Optional[str] = None
    #: Some installs nest the game below the directory the user points at.
    normalize_root: Optional[Callable[[str], str]] = None
    #: Roles this sim renders force feedback to.  A plain table, meant to be
    #: edited when a sim gains support: there is no point offering to hand a
    #: sim a device it will never send an effect to, and doing so would look
    #: like a TelemFFB fault when nothing happened.
    ffb_roles: Tuple[str, ...] = ("joystick",)
    #: The setting that turns this sim on in TelemFFB.  IL-2 Korea shares
    #: Great Battles' toggle - there is only one IL-2 switch in the UI - so
    #: this is not simply derivable from the key.
    enable_key: str = ""
    #: The setting that opts this sim into the DirectInput Tap.  Separate from
    #: enable_key and per title, including IL-2's two: the tap is a thing
    #: most VPforce owners never need, and showing it as part of ordinary
    #: sim setup implies otherwise.
    tap_enable_key: str = ""
    #: Whether we offer to put tapped devices first in this sim's device
    #: enumeration.  DCS only for now: it hands force feedback to the
    #: devices it sees first, and we have watched it strand a stick that
    #: was merely further down the list.  Not offered elsewhere because
    #: some games - IL-2 among them - identify a device by its position,
    #: and reordering underneath one of those would disturb its bindings.
    supports_ordering: bool = False

    def renders_to(self, role: str) -> bool:
        return role in self.ffb_roles


@dataclass
class TargetStatus:
    """One directory the wrapper belongs in."""
    directory: str
    state: str
    has_config: bool = False
    #: Version of the installed wrapper, when it declares one.  Builds from
    #: before the version resource was added report None and are identified
    #: by their marker strings alone.
    version: Optional[str] = None


@dataclass
class TargetOutcome:
    """What happened to one directory when installing or removing."""
    directory: str
    ok: bool
    action: str          # installed | updated | removed | skipped | failed
    detail: str = ""


@dataclass
class SimStatus:
    """Everything known about one sim's tap installation."""
    sim: TapSim
    root: Optional[str] = None
    provenance: str = "not found"
    targets: List[TargetStatus] = field(default_factory=list)
    #: Tap rules naming a device no longer configured - the config has
    #: drifted from the hardware.  Populated by all_status, which knows what
    #: is configured; sim_status on its own leaves it empty.
    stale_rules: List["Rule"] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.root is not None

    @property
    def installed(self) -> bool:
        """True only when every target carries our wrapper.

        DCS has two - a partial install is the interesting case, because the
        game may well launch the executable that was missed.
        """
        return bool(self.targets) and all(
            t.state == WrapperState.TAP for t in self.targets)

    @property
    def partially_installed(self) -> bool:
        states = {t.state for t in self.targets}
        return WrapperState.TAP in states and len(states) > 1


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def _registry_values(hive, subkeys: Sequence[str], value_name: str) -> List[str]:
    """Read one value from the first of several keys that has it."""
    import winreg
    found = []
    for subkey in subkeys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
            if value:
                found.append(str(value))
        except OSError:
            continue
    return found


def dcs_registry_roots() -> List[str]:
    """DCS records its install path; OpenBeta first, as get_dcs_variant does."""
    import winreg
    return _registry_values(
        winreg.HKEY_CURRENT_USER,
        (r"Software\Eagle Dynamics\DCS World OpenBeta",
         r"Software\Eagle Dynamics\DCS World"),
        "Path")


def bms_registry_roots() -> List[str]:
    """BMS records one key per installed version, newest name last."""
    import winreg
    roots = []
    for hive_path in (r"SOFTWARE\WOW6432Node\Benchmark Sims",
                      r"SOFTWARE\Benchmark Sims"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as parent:
                subkeys = []
                index = 0
                while True:
                    try:
                        subkeys.append(winreg.EnumKey(parent, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            continue
        # a newer 4.38 sorts after 4.37, and the newest is the likely target
        for name in sorted(subkeys, reverse=True):
            roots += _registry_values(winreg.HKEY_LOCAL_MACHINE,
                                      (f"{hive_path}\\{name}",), "baseDir")
    return roots


def steam_library_roots() -> List[str]:
    """Every Steam library on the machine, not just the default one.

    Steam records extra libraries in libraryfolders.vdf; a sim is as likely
    to be on a second drive as on the one Steam itself lives on.
    """
    import winreg
    steam_paths = _registry_values(winreg.HKEY_CURRENT_USER,
                                   (r"Software\Valve\Steam",), "SteamPath")
    libraries = []
    for steam in steam_paths:
        steam = os.path.normpath(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            libraries.append(steam)
            continue
        # "path"  "D:\\SteamLibrary"
        for match in re.finditer(r'"path"\s+"([^"]+)"', content):
            libraries.append(os.path.normpath(match.group(1).replace("\\\\", "\\")))
        libraries.append(steam)
    # preserve order, drop duplicates
    seen, ordered = set(), []
    for library in libraries:
        key = library.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(library)
    return ordered


def steam_common_dirs() -> List[str]:
    """Every installed Steam app directory, across all libraries."""
    dirs = []
    for library in steam_library_roots():
        common = os.path.join(library, "steamapps", "common")
        try:
            entries = os.listdir(common)
        except OSError:
            continue
        dirs += [os.path.join(common, entry) for entry in entries
                 if os.path.isdir(os.path.join(common, entry))]
    return dirs


# --------------------------------------------------------------------------
# the sims
# --------------------------------------------------------------------------
SIMS: Tuple[TapSim, ...] = (
    TapSim(
        key="DCS",
        tap_enable_key="enableTapDCS",
        supports_ordering=True,
        enable_key="enableDCS",
        name="DCS World",
        # Both are installed to: the wrapper is inert unless TelemFFB is
        # managing the device, and guessing which executable the user
        # launches would silently do nothing when guessed wrong.
        exe_relpaths=("bin/DCS.exe", "bin-mt/DCS.exe"),
        registry_lookup="dcs_registry_roots",
    ),
    TapSim(
        key="IL2",
        tap_enable_key="enableTapIL2",
        enable_key="enableIL2",
        name="IL-2 Sturmovik Great Battles",
        exe_relpaths=("bin/game/Il-2.exe",),
        settings_key="pathIL2",
    ),
    TapSim(
        key="IL2_K",
        tap_enable_key="enableTapIL2_K",
        enable_key="enableIL2",
        name="IL-2 Korea",
        # Same relative layout as Great Battles, but the standalone release
        # nests it under <root>/game.  il2_korea_game_root already resolves
        # that for startup.cfg, and bin/ is a sibling of data/.
        exe_relpaths=("bin/game/IL2Series.exe",),
        settings_key="pathIL2_K",
        normalize_root=il2_korea_game_root,
        # the only sim so far that renders to anything but the stick
        ffb_roles=("joystick", "pedals"),
    ),
    TapSim(
        key="BMS",
        tap_enable_key="enableTapBMS",
        enable_key="enableBMS",
        name="Falcon BMS",
        # x64 only: arm64 ships alongside but is not what the launcher runs.
        exe_relpaths=("Bin/x64/Falcon BMS.exe",),
        registry_lookup="bms_registry_roots",
    ),
)

SIMS_BY_KEY = {sim.key: sim for sim in SIMS}


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def matches_signature(sim: TapSim, root: str) -> bool:
    """Whether this directory really is the sim, by its own contents."""
    if not root:
        return False
    return any(os.path.isfile(os.path.join(root, rel.replace("/", os.sep)))
               for rel in sim.exe_relpaths)


def candidate_roots(sim: TapSim, configured: Optional[str] = None
                    ) -> List[Tuple[str, str]]:
    """Where this sim might be, as (path, how we came to try it).

    Ordered by how much the answer is worth trusting: a path the user
    configured, then one the installer recorded, then a Steam library scan.
    """
    candidates: List[Tuple[str, str]] = []
    if configured:
        candidates.append((configured, "configured in TelemFFB"))
    if sim.registry_lookup:
        try:
            lookup = globals()[sim.registry_lookup]
            candidates += [(root, "registry") for root in lookup()]
        except Exception:
            logging.exception(f"DirectInput tap: registry lookup failed for {sim.name}")
    candidates += [(path, "Steam library") for path in steam_common_dirs()]
    return candidates


def resolve_root(sim: TapSim, configured: Optional[str] = None
                 ) -> Tuple[Optional[str], str]:
    """The first candidate that actually contains this sim."""
    for path, provenance in candidate_roots(sim, configured):
        root = sim.normalize_root(path) if sim.normalize_root else path
        if matches_signature(sim, root):
            return root, provenance
    return None, "not found"


def target_dirs(sim: TapSim, root: str) -> List[str]:
    """The directories the wrapper belongs in, for the executables present."""
    dirs = []
    for rel in sim.exe_relpaths:
        exe = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(exe):
            directory = os.path.dirname(exe)
            if directory not in dirs:
                dirs.append(directory)
    return dirs


# --------------------------------------------------------------------------
# the devices a config is written for
# --------------------------------------------------------------------------
#: How a devpath announces that the device is driven through the
#: DirectInput backend rather than natively over HID.
DINPUT_PREFIX = "dinput:"

#: Present in the header of a config we generated outright.  Its absence
#: means the file was somebody else's before we touched it, so cleaning up
#: means removing our lines from it rather than deleting it.
GENERATED_MARKER = "written by TelemFFB"

#: Written into every config, generated or adopted, unless a vJoy rule is
#: already there.  vJoy is a virtual joystick that some setups expose as
#: force-feedback capable; there is no case where a game should count it
#: as one, and it has caused enough trouble that the upstream sample
#: blocked it too.
VJOY_RULE = "vJoy=block    ; virtual joystick - never a force feedback device"


@dataclass(frozen=True)
class TapDevice:
    """A configured slot, as far as a tap rule is concerned."""
    role: str
    vid: Optional[int]
    pid: Optional[int]
    ident: str = ""          # display only; may be stale, may be empty
    #: Driven through the DirectInput backend rather than natively over HID.
    #: Decides whether the tap is optional or the only way in: TelemFFB
    #: reaches a VPforce device directly and needs the tap only to render
    #: the game's own effects, but a generic DirectInput device is reachable
    #: *only* through the tap, so without a rule nothing happens at all.
    directinput: bool = False

    @property
    def usable(self) -> bool:
        """Whether a rule can be written for it.

        Only the USB ids matter. The name is a label - owners rename
        VPforce devices and vendors reword models - so a rule is never
        keyed on it.
        """
        return self.vid is not None and self.pid is not None

    @property
    def key(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}" if self.usable else ""


def configured_devices(settings) -> List[TapDevice]:
    """Every slot that has a device assigned to it.

    Assignment, not connection: the device path persists in settings while
    the hardware is unplugged, and the USB ids come out of that path - so a
    rule can be written for a stick that is switched off.
    """
    devices = []
    for role in DEVICE_ROLES:
        devpath = settings.get(f"devpath_{role}", "") or ""
        if not devpath:
            continue
        # what the device reported, when we have it.  Falling back to the
        # path covers settings written before the ids were stored, and only
        # ever works for HID devices - a DirectInput path is a GUID.
        ids = (parse_usb_ids(settings.get(device_ids_key(role), ""))
               or usb_ids_from_devpath(devpath))
        devices.append(TapDevice(
            role=role,
            vid=ids[0] if ids else None,
            pid=ids[1] if ids else None,
            ident=str(settings.get(device_ident_key(role), "") or ""),
            directinput=str(devpath).startswith(DINPUT_PREFIX)))
    return devices


def rule_line(device: TapDevice) -> str:
    """The config line handing one device to TelemFFB.

    The trailing name is for whoever reads the file later; the wrapper never
    parses it, so a stale name means the hardware changed, not that the rule
    stopped working.
    """
    return "{}=tap    ; {} ({})".format(
        device.key, device.ident or "unnamed", device.role)


def block_line(device: TapDevice) -> str:
    """A rule keeping force feedback away from one device.

    Offered for a device in a role the sim does not render to: the game
    sends it nothing anyway, but while it is enumerated as a force feedback
    device it can still take a slot from the stick that wanted one.
    """
    return "{}=block    ; {} ({}) - not rendered by this sim".format(
        device.key, device.ident or "unnamed", device.role)


def order_line(device: TapDevice, position: int) -> str:
    """One ``[DeviceOrder]`` line for a device.

    Keyed on ids like the tap rule it accompanies, so the two cannot drift
    apart when a product name changes.
    """
    return "{}={}    ; {} ({})".format(
        position, device.key, device.ident or "unnamed", device.role)


def generate_config(devices: Sequence[TapDevice],
                    ordered: Sequence[TapDevice] = (),
                    blocked: Sequence[TapDevice] = ()) -> str:
    """A dinput8.ini with tap rules for the given devices.

    Shaped like the wrapper's own sample ini, so a user who has seen one
    recognizes the other: every setting the wrapper reads is present, the
    ones left at their defaults commented out.  Only devices the caller
    passes get a rule; a device with no rule is untouched by the wrapper.
    """
    from datetime import date

    lines = [
        "; dinput8.ini - {} on {}.".format(GENERATED_MARKER,
                                            date.today().isoformat()),
        ";",
        "; Settings at their defaults are commented out; uncomment one to change",
        "; it.  TelemFFB adds to this file when your device selection changes and",
        "; never rewrites what is already here - edit freely.",
        "",
        "[General]",
        "; Enable the wrapper (false = pure pass-through, no interception)",
        ";Enabled=true",
        "",
        "; Log level: 0=none, 1=error, 2=warn, 3=info, 4=debug",
        ";LogLevel=3",
        "",
        "; Apply 'tap' and 'sink' rules only while TelemFFB is running, checked",
        "; once as the game starts.  Leave this true: a wrapper left behind in a",
        "; game folder then goes inert instead of silently swallowing the game's",
        "; force feedback.  'block' and scaling rules are never gated by this.",
        "RequireTelemFFB=true",
        "",
        "[FFB]",
        "; Global force feedback enable (false = block FFB on ALL devices)",
        ";Enabled=true",
        "",
        "; Log all FFB operations (effect create/start/stop/params, commands)",
        ";LogEffects=true",
        "",
        "; Default force scale for all devices (0-100, 100 = full force)",
        ";DefaultScale=100",
        "",
        "; Restart effects automatically after a device reconnects",
        ";AutoRestart=true",
        "",
        "[FFBDevices]",
        "; Per-device policy.  First matching rule wins; a device with no matching",
        "; rule is untouched by the wrapper.",
        ";",
        "; Format: VVVV:PPPP=action             (USB vendor:product ids, hex)",
        ";         DeviceNameSubstring=action   (case-insensitive)",
        ";",
        "; Actions:",
        ";   tap     - the game's effects on this device are intercepted and",
        ";             relayed to TelemFFB, which can then render them locally,",
        ";             depending on the spring mode setting.  The game still",
        ";             sees a normal force feedback device and believes it is",
        ";             rendering.",
        ";   block   - no force feedback on this device at all",
        ";   allow   - explicitly allow force feedback (with the global enable off)",
        ";   sink    - like tap without the relay: the game's effects go nowhere",
        ";   0-100   - scale force to this percentage (0 = effectively block)",
        ";",
        "; USB ids are what TelemFFB writes: a name can be changed by its owner,",
        "; reworded by a vendor, or match more devices than intended.  A trailing",
        "; comment names the device for readability; it is never read back, so a",
        "; stale one means the hardware changed, not that the rule stopped working.",
    ]
    for device in devices:
        if device.usable:
            lines.append(rule_line(device))
    for device in blocked:
        if device.usable:
            lines.append(block_line(device))
    lines.append(VJOY_RULE)

    if ordered:
        lines.extend([
            "",
            "[DeviceOrder]",
            "; Which devices the game sees first, as position=VVVV:PPPP.  A game",
            "; that gives force feedback to the first devices it sees keeps",
            "; giving it to the same ones, so a tapped device further down the",
            "; list can be given no effects at all - and then there is nothing",
            "; for TelemFFB to render.  Nothing is reordered unless listed here.",
        ])
        for position, device in enumerate(
                (d for d in ordered if d.usable), start=1):
            lines.append(order_line(device, position))
    lines.append("")
    return "\r\n".join(lines)   # a Windows game config


def open_config(path: str, mode: str):
    """Open a dinput8.ini so that reading and writing round-trip its bytes.

    Three things the obvious ``open`` got wrong, each of which broke the
    promise that a file we adopt survives byte for byte:

    * text-mode newline translation.  A generated config is already CRLF,
      and writing it through translation doubled every CR; reading that
      back split each line in two.  ``newline=""`` passes them through.
    * ``utf-8-sig`` stripped a byte order mark before amend could keep it.
      The mark stays in the text; tap_config knows to step over it.
    * a hand-edited file saved as ANSI - a German or French user with an
      accent in a comment - raised UnicodeDecodeError out of the settings
      dialog.  ``surrogateescape`` carries such bytes through unchanged.
    """
    return open(path, mode, encoding="utf-8", errors="surrogateescape",
                newline="")


def config_paths(status: SimStatus) -> List[str]:
    """Every dinput8.ini this sim actually has, as paths."""
    return [os.path.join(t.directory, WRAPPER_CONFIG) for t in status.targets
            if os.path.isfile(os.path.join(t.directory, WRAPPER_CONFIG))]


def config_label(path: str, root: Optional[str] = None) -> str:
    """How to name a config when more than one is on offer.

    A sim can hold two that differ, and two links both reading
    "open dinput8.ini" say nothing about which is which.  Named by the
    directory the game launches from, which is what tells them apart.
    """
    if root:
        try:
            return "open " + os.path.relpath(path, root)
        except ValueError:
            pass
    return "open " + os.path.basename(path)


def config_link(path: str, label: str = "open dinput8.ini") -> str:
    """A link that opens the file in whatever the system uses for text.

    Offered wherever we describe what a config contains or ask what to do
    with it: everything we say about someone's file is a summary, and the
    file itself is the only thing that settles an argument with it.
    """
    return '<a href="{}">{}</a>'.format(Path(path).as_uri(), label)


def read_configs(status: SimStatus) -> List[Tuple[str, str]]:
    """Every config this sim holds, as ``(directory, text)``.

    Usually one, shared.  Where a sim has two executables the user may have
    a different file beside each, and they have to be handled apart.

    Read raw - see open_config - so what comes back can be amended and
    written again with every byte we did not touch still in place.
    """
    out = []
    for target in status.targets:
        path = os.path.join(target.directory, WRAPPER_CONFIG)
        if not os.path.isfile(path):
            continue
        try:
            with open_config(path, "r") as handle:
                out.append((target.directory, handle.read()))
        except OSError:
            logging.exception(f"DirectInput tap: could not read {path}")
    return out


def write_one_config(directory: str, text: str) -> TargetOutcome:
    """Replace the config beside one executable."""
    path = os.path.join(directory, WRAPPER_CONFIG)
    try:
        with open_config(path, "w") as handle:
            handle.write(text)
        return TargetOutcome(directory, True, "configured")
    except OSError as e:
        return TargetOutcome(directory, False, "failed", str(e))


def read_config(status: SimStatus) -> Optional[str]:
    """The first config this sim holds, or None if it has none.

    For a sim-wide summary - drift, status - where one file is enough to
    describe.  Anything that *writes* goes through read_configs instead:
    a sim with two executables can hold two files that differ, and they
    are handled apart.
    """
    for target in status.targets:
        path = os.path.join(target.directory, WRAPPER_CONFIG)
        if not os.path.isfile(path):
            continue
        try:
            with open_config(path, "r") as handle:
                return handle.read()
        except OSError:
            logging.exception(f"DirectInput tap: could not read {path}")
    return None


# --------------------------------------------------------------------------
# the copy we ship
# --------------------------------------------------------------------------
def bundled_wrapper() -> Optional[str]:
    """The wrapper TelemFFB ships, ready to copy into a game folder.

    Kept in a subdirectory rather than beside the executable: a file named
    dinput8.dll next to TelemFFB.exe sits in Windows' own DLL search path,
    and being loaded into TelemFFB is the last thing a DirectInput hook
    should do.
    """
    if getattr(sys, "frozen", False):
        roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    else:
        roots = [os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))]
    for root in roots:
        if not root:
            continue
        candidate = os.path.join(root, "ffb_tap", WRAPPER_NAME)
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(root, "dll", "ffb_tap", WRAPPER_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------
# installing and removing
# --------------------------------------------------------------------------
def install(status: SimStatus,
            config_text: Optional[str] = None,
            overwrite_foreign: bool = False) -> List[TargetOutcome]:
    """Put the wrapper beside every executable this sim has.

    Every target gets the same treatment, because a sim with two
    executables will launch whichever the user picked and a wrapper beside
    only one of them does nothing at all.

    A dinput8.dll that is not ours is left alone unless ``overwrite_foreign``
    says otherwise.  We cannot tell what one is - several tools install a
    proxy under this name - so whether replacing it is safe is the user's
    call, not something to infer from the file.
    """
    source = bundled_wrapper()
    if source is None:
        return [TargetOutcome(t.directory, False, "failed",
                              "TelemFFB's copy of the wrapper is missing")
                for t in status.targets]

    outcomes = []
    for target in status.targets:
        destination = os.path.join(target.directory, WRAPPER_NAME)
        if target.state == WrapperState.FOREIGN and not overwrite_foreign:
            outcomes.append(TargetOutcome(
                target.directory, False, "skipped",
                "a dinput8.dll that is not ours is already here"))
            continue
        if target.state == WrapperState.UNREADABLE:
            outcomes.append(TargetOutcome(
                target.directory, False, "skipped",
                "a dinput8.dll is here but could not be read"))
            continue
        try:
            shutil.copyfile(source, destination)
        except PermissionError:
            outcomes.append(TargetOutcome(
                target.directory, False, "failed",
                "the file is in use - close the game and try again"))
            continue
        except OSError as e:
            outcomes.append(TargetOutcome(
                target.directory, False, "failed", str(e)))
            continue
        outcomes.append(TargetOutcome(
            target.directory, True, {
                WrapperState.TAP: "updated",
                WrapperState.FOREIGN: "replaced",
            }.get(target.state, "installed")))

    if config_text is not None:
        # written only where there is none: an existing file is the user's,
        # whether they wrote it or an earlier install did
        for outcome in outcomes:
            if not outcome.ok:
                continue
            destination = os.path.join(outcome.directory, WRAPPER_CONFIG)
            if os.path.isfile(destination):
                continue
            try:
                with open_config(destination, "w") as handle:
                    handle.write(config_text)
            except OSError:
                logging.exception(f"DirectInput tap: could not write {destination}")

    _match_config_across_targets(status, outcomes)
    return outcomes


def _match_config_across_targets(status: SimStatus, outcomes: List[TargetOutcome]):
    """Give every target the same dinput8.ini, where one exists.

    A sim with two executables reads the config beside whichever one it
    launched, so a config in one target and not the other makes behavior
    depend on a choice the user does not associate with it. Existing files
    are never overwritten, and none is invented: with no config anywhere,
    the wrapper's built-in defaults apply to all of them equally.
    """
    installed = [o.directory for o in outcomes if o.ok]
    if len(status.targets) < 2 or not installed:
        return
    sources = [os.path.join(t.directory, WRAPPER_CONFIG)
               for t in status.targets
               if os.path.isfile(os.path.join(t.directory, WRAPPER_CONFIG))]
    if not sources:
        return
    for directory in installed:
        destination = os.path.join(directory, WRAPPER_CONFIG)
        if os.path.isfile(destination):
            continue
        try:
            shutil.copyfile(sources[0], destination)
            logging.info(f"DirectInput tap: copied {WRAPPER_CONFIG} to {directory} "
                         "so both targets behave the same")
        except OSError:
            logging.exception(f"DirectInput tap: could not copy {WRAPPER_CONFIG} "
                              f"to {directory}")


def remove(status: SimStatus) -> List[TargetOutcome]:
    """Take our wrapper out, and only ours.

    The dinput8.ini beside it is left alone. It may well predate this
    installation - written for the upstream wrapper this one is built from -
    and removing a file we did not create is not ours to do.
    """
    outcomes = []
    for target in status.targets:
        path = os.path.join(target.directory, WRAPPER_NAME)
        if target.state == WrapperState.ABSENT:
            outcomes.append(TargetOutcome(target.directory, True, "skipped",
                                          "nothing installed here"))
            continue
        if target.state != WrapperState.TAP:
            outcomes.append(TargetOutcome(
                target.directory, False, "skipped",
                "this dinput8.dll is not ours to remove"))
            continue
        try:
            os.remove(path)
        except PermissionError:
            outcomes.append(TargetOutcome(
                target.directory, False, "failed",
                "the file is in use - close the game and try again"))
            continue
        except OSError as e:
            outcomes.append(TargetOutcome(target.directory, False, "failed",
                                          str(e)))
            continue
        outcomes.append(TargetOutcome(target.directory, True, "removed"))
    return outcomes


# --------------------------------------------------------------------------
# what is installed
# --------------------------------------------------------------------------
def file_version(path: str) -> Optional[str]:
    """The FileVersion recorded in a DLL's version resource, if it has one.

    Read from the file rather than by loading it: the wrapper is a
    DirectInput hook, and loading one into TelemFFB's process to ask its
    version would be a poor trade for a string.
    """
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        # the language-neutral fixed block; the string block would need the
        # translation id, and the fixed one carries the same numbers
        if not ctypes.windll.version.VerQueryValueW(
                buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
            return None
        if not length.value:
            return None
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, then the file
        # version as two packed 32-bit halves, most significant first
        header = ctypes.cast(
            value, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        most, least = header[2], header[3]
        return "{}.{}.{}.{}".format(most >> 16, most & 0xFFFF,
                                    least >> 16, least & 0xFFFF)
    except Exception:
        logging.debug(f"DirectInput tap: no readable version resource on {path}")
        return None


def wrapper_state(directory: str) -> str:
    """Whether the dinput8.dll in this directory is ours.

    Identity comes from strings the wrapper contains, not from a version
    resource: older builds have none.  Worth distinguishing: a foreign
    dinput8.dll belongs to
    something else the user installed deliberately, and overwriting it
    would break whatever that is.
    """
    path = os.path.join(directory, WRAPPER_NAME)
    if not os.path.isfile(path):
        return WrapperState.ABSENT
    try:
        with open(path, "rb") as handle:
            content = handle.read()
    except OSError:
        logging.warning(f"DirectInput tap: could not read {path}")
        return WrapperState.UNREADABLE
    return (WrapperState.TAP if any(m in content for m in TAP_MARKERS)
            else WrapperState.FOREIGN)


def sim_status(sim: TapSim, configured: Optional[str] = None) -> SimStatus:
    """Where this sim is, and what is installed in it."""
    root, provenance = resolve_root(sim, configured)
    status = SimStatus(sim=sim, root=root, provenance=provenance)
    if root:
        status.targets = [
            _target_status(directory) for directory in target_dirs(sim, root)]
    return status


def _target_status(directory: str) -> TargetStatus:
    """Everything worth knowing about one target directory."""
    state = wrapper_state(directory)
    version = None
    if state == WrapperState.TAP:
        version = file_version(os.path.join(directory, WRAPPER_NAME))
    return TargetStatus(
        directory=directory,
        state=state,
        has_config=os.path.isfile(os.path.join(directory, WRAPPER_CONFIG)),
        version=version)
