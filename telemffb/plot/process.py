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

"""PlotProcess: launches and drives the plot process from TelemFFB.

The plotter is this same program started with ``--plot-window`` (the
restart helper's recipe: the frozen exe, or the interpreter with the
script), handed a free localhost port, the device name for its title and
the theme.  ``send_frame`` is the telemetry thread's per-frame call and
goes straight to the feed; everything else is main-thread control: a 1 s
keepalive that repeats the marked keys and their units, ``show`` (start
if needed, then raise), ``set_keys``, ``set_units``, ``stop``.
"""

import logging
import os
import socket
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple

from PyQt6.QtCore import QTimer

from telemffb.plot.feed import PlotFeed
from telemffb.plot.protocol import CMD_QUIT, CMD_SHOW, KEEPALIVE_S


def free_port() -> int:
    """A UDP port nothing listens on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def plotter_command(port: int, device: str, theme: str) -> List[str]:
    """The command line that starts this program as the plotter."""
    if getattr(sys, 'frozen', False):
        program = [sys.executable]
    else:
        program = [sys.executable, sys.argv[0]]
    return program + ['--plot-window', '--plot-port', str(port),
                      '--plot-device', device, '--plot-theme', theme]


class PlotProcess:
    """The plot process of this instance, started on first use."""

    def __init__(self, device: str, dark: bool):
        self._device = device
        self._theme = 'dark' if dark else 'light'
        self._keys: tuple = ()
        self._units: Dict[str, Tuple[str, float]] = {}
        self._feed: Optional[PlotFeed] = None
        self._process: Optional[subprocess.Popen] = None
        self._keepalive = QTimer()
        self._keepalive.setInterval(int(KEEPALIVE_S * 1000))
        self._keepalive.timeout.connect(self._on_keepalive)

    # ---- control (main thread) -------------------------------------------------

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def show(self) -> None:
        """Start the plotter if it is not running, and bring its window up."""
        if not self.is_alive():
            self._start()
        if self._feed is not None:
            self._feed.send_command(CMD_SHOW)

    def set_keys(self, keys: Iterable[str]) -> None:
        self._keys = tuple(sorted(keys))
        if self._feed is not None:
            self._feed.set_keys(self._keys)

    def set_units(self, units: Dict[str, Tuple[str, float]]) -> None:
        """The unit each marked key is plotted in, ``(unit, factor)``."""
        self._units = dict(units)
        if self._feed is not None:
            self._feed.set_units(self._units)

    def stop(self) -> None:
        """Ask the plotter to exit, and make sure it does."""
        self._keepalive.stop()
        if self._feed is not None:
            self._feed.send_command(CMD_QUIT)
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._feed is not None:
            self._feed.close()
            self._feed = None

    def _start(self) -> None:
        if self._feed is not None:
            self._feed.close()
        port = free_port()
        command = plotter_command(port, self._device, self._theme)
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
        kwargs = dict(env=env, close_fds=True, stdin=subprocess.DEVNULL)
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        logging.info(f"Plot: starting {command}")
        try:
            self._process = subprocess.Popen(command, **kwargs)
        except OSError:
            logging.exception("Plot: the plot process did not start")
            self._process = None
            return
        self._feed = PlotFeed(port)
        self._feed.units = dict(self._units)
        self._feed.set_keys(self._keys)
        self._keepalive.start()

    def _on_keepalive(self) -> None:
        if self._feed is None:
            return
        if not self.is_alive():
            # closed from its own side (or crashed): the next Plot click
            # starts a fresh one
            self._keepalive.stop()
            self._feed.close()
            self._feed = None
            self._process = None
            return
        self._feed.send_keys()

    # ---- telemetry thread --------------------------------------------------------

    def send_frame(self, telem_data) -> None:
        feed = self._feed
        if feed is not None:
            feed.send_frame(telem_data)
