#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2026 Valmantas Palikša.
# Copyright (c) 2026 Micah Frisby
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

"""Live monitor for the DirectInput Tap shared-memory mirror.

A port of the wrapper repo's console viewer (tools/ffb_tap_viewer.py)
into a dialog on the Utilities menu, for remote troubleshooting: when a
user says "no forces in DCS", this shows in one window whether the game
is publishing at all, which devices the wrapper captured, and what every
effect slot is being told - and keeps a timestamped log of each change,
which is the artifact to ask the user for.  Parameter values are far too
chatty to log per-frame; the zero/non-zero transition is exactly the
interesting event, because a game can silence an effect either by
stopping it or by zeroing its parameters while leaving it "playing".

Values are shown in raw DirectInput units (+-10000): this is a
wire-level tool, and translating would hide exactly the discrepancies it
exists to reveal.

The ctypes structures and the seqlock snapshot live in
telemffb.hw.ffb_tap - the production reader - so there is one copy of
the wire protocol in this repo, not two.
"""

import ctypes
import logging
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QSplitter, QVBoxLayout)
from PyQt6.QtCore import Qt

from telemffb.hw.ffb_tap import (DIEFF_POLAR, FfbTapReader, SHM_NAME,
                                 TAP_MAGIC, TAP_VERSION, gate_refusal)

EFFECT_TYPE_NAMES = ["-", "Constant", "Ramp", "Square", "Sine", "Triangle",
                     "SawUp", "SawDown", "Spring", "Damper", "Inertia",
                     "Friction", "Custom"]

#: raw wire codes, deliberately not the ffb_rhino frozensets: this module
#: formats whatever the wrapper mirrors, including the spring the reader's
#: own CONDITION_TYPES excludes
_CONDITION = frozenset((8, 9, 10, 11))
_PERIODIC = frozenset((3, 4, 5, 6, 7))


def effect_type_name(t: int) -> str:
    return EFFECT_TYPE_NAMES[t] if 0 <= t < len(EFFECT_TYPE_NAMES) else "?"


def fmt_direction(e) -> str:
    if e.flags & DIEFF_POLAR:
        return f"{e.direction[0] / 100:.0f}\N{DEGREE SIGN}"
    if e.axisCount == 2:
        return f"cart({e.direction[0]},{e.direction[1]})"
    return f"dir({e.direction[0]})"


def fmt_params(e) -> str:
    """One line of the parameters that matter for this effect type."""
    t = e.effectType
    if t == 1:
        return f"mag={e.u.constant.magnitude:+6d} {fmt_direction(e)}"
    if t == 2:
        return f"start={e.u.ramp.start:+d} end={e.u.ramp.end:+d}"
    if t in _PERIODIC:
        p = e.u.periodic
        return (f"mag={p.magnitude:5d} off={p.offset:+6d} "
                f"period={p.period / 1000:.0f}ms")
    if t in _CONDITION:
        c = e.u.condition
        parts = []
        for i in range(min(c.count, 2)):
            parts.append(f"ax{i}[off={c.offset[i]:+6d} "
                         f"coef={c.positiveCoefficient[i]:+5d}/"
                         f"{c.negativeCoefficient[i]:+5d} "
                         f"sat={c.positiveSaturation[i]:5d}]")
        return " ".join(parts) or "(no condition data)"
    return ""


def commands_force(e) -> bool:
    """Is this effect actually asking for any force right now?"""
    t = e.effectType
    if t in _CONDITION:
        c = e.u.condition
        return any(c.offset[i] or c.positiveCoefficient[i]
                   or c.negativeCoefficient[i]
                   for i in range(min(c.count, 2)))
    if t == 1:
        return bool(e.u.constant.magnitude)
    if t == 2:
        return bool(e.u.ramp.start or e.u.ramp.end)
    if t in _PERIODIC:
        return bool(e.u.periodic.magnitude or e.u.periodic.offset)
    return False


def state_digest(shm):
    """Hashable snapshot of everything a change would be interesting for."""
    if shm is None or shm.magic != TAP_MAGIC:
        return None
    out = []
    for di, d in enumerate(shm.devices):
        if not d.used:
            continue
        out.append(('dev', di, d.name.decode('utf-8', 'replace'),
                    d.resetCount, d.pausedState))
        for si, e in enumerate(d.effects):
            if not e.slotUsed:
                continue
            out.append(('fx', di, si, e.effectType, bool(e.playing),
                        e.startCount, e.stopCount, commands_force(e)))
    refusal = gate_refusal(shm)
    if refusal is not None:
        out.append(('gate', 0, '', refusal[0], (refusal[1], refusal[2])))
    return tuple(out)


def describe_changes(prev, cur):
    """Human-readable lines for what changed between two digests."""
    if prev is None:
        if cur is None:
            return []
        # first sight stays one line - except a gate refusal, which must
        # reach the log even when it predates the monitor: the saved log
        # is the artifact support reads, and the wrapper usually stamped
        # the refusal long before the monitor opened
        gate = [k for k in cur if k[0] == 'gate']
        return (["writer appeared"]
                + describe_changes(tuple(), tuple(gate)))
    if cur is None:
        return ["writer gone"]
    pm = {k[:3]: k for k in prev}
    cm = {k[:3]: k for k in cur}
    lines = []
    for key in sorted(set(pm) | set(cm)):
        p, c = pm.get(key), cm.get(key)
        if p == c:
            continue
        if key[0] == 'gate':
            # first appearance and each further refusal read the same way;
            # the wrapper never clears the flag within a session
            if c is not None:
                vid, pid = c[4]
                ids = f" ({vid:04X}:{pid:04X})" if (vid or pid) else ""
                lines.append(f"tap rule IGNORED at bind{ids} - TelemFFB "
                             f"was not running when the game bound the "
                             f"device; restart the game (refusals={c[3]})")
            continue
        if p is None:
            lines.append(f"+ {key[0]} {key[1:]} appeared")
        elif c is None:
            lines.append(f"- {key[0]} {key[1:]} removed")
        elif key[0] == 'fx':
            _, di, si = key
            etype, p_play, p_start, p_stop, p_force = p[3:]
            _, c_play, c_start, c_stop, c_force = c[3:]
            tname = effect_type_name(etype)
            if p_play != c_play:
                lines.append(f"dev{di} slot{si:<2} {tname:<8} "
                             f"{'START' if c_play else 'STOP'}")
            if c_start != p_start or c_stop != p_stop:
                lines.append(f"dev{di} slot{si:<2} {tname:<8} "
                             f"counters start={c_start} stop={c_stop}")
            if p_force != c_force:
                lines.append(f"dev{di} slot{si:<2} {tname:<8} params "
                             f"{'-> FORCE' if c_force else '-> ZEROED'}"
                             f" (still {'playing' if c_play else 'stopped'})")
        else:
            lines.append(f"dev{key[1]} resets={c[3]} paused={c[4]}")
    return lines


def render_snapshot(shm, writer_alive: bool, age_s: float) -> str:
    """The live table: one block per captured device, one row per slot."""
    # magic 0 is what "no game" looks like from this side: TelemFFB's
    # reader creates the mapping when absent (either side may), so an
    # untouched, zero-filled view means nobody is publishing - not an
    # error, just a game that has not started
    if shm is None or shm.magic == 0:
        return ("no tapped game is publishing yet\n\n"
                "Start the game with the DirectInput Tap installed and a\n"
                "device set to 'tap' in its dinput8.ini.  The table appears\n"
                "here the moment the wrapper captures a device.")
    if shm.magic != TAP_MAGIC or shm.version != TAP_VERSION:
        return (f"mapping present but not stamped yet "
                f"(magic={shm.magic:#010x} version={shm.version})")
    # tickMs 0 means the writer has not stamped a heartbeat yet; an age
    # computed against it would read as the machine's uptime
    age = f"{age_s:.1f}s ago" if shm.tickMs else "n/a"
    lines = [f"writer pid={shm.writerPid} "
             f"({'ALIVE' if writer_alive else 'EXITED'})   "
             f"last write {age}"]
    for di, d in enumerate(shm.devices):
        if not d.used:
            continue
        name = d.name.decode("utf-8", "replace")
        lines.append("")
        lines.append(f"[{di}] {name}  vid={d.vid:04X} pid={d.pid:04X}  "
                     f"gen={d.generation} resets={d.resetCount}"
                     f"{'  PAUSED' if d.pausedState else ''}")
        for si, e in enumerate(d.effects):
            if not e.slotUsed:
                continue
            state = "PLAY" if e.playing else "stop"
            lines.append(
                f"  {si:2d} {effect_type_name(e.effectType):<8s} {state}  "
                f"st/sp/up={e.startCount}/{e.stopCount}/{e.updateCount}  "
                f"{fmt_params(e)}")
    refusal = gate_refusal(shm)
    if refusal is not None:
        count, vid, pid = refusal
        ids = f" ({vid:04X}:{pid:04X})" if (vid or pid) else ""
        lines.append("")
        lines.append(f"!! GAME STARTED BEFORE TelemFFB: the wrapper ignored "
                     f"a tap rule at device bind{ids} because TelemFFB was "
                     f"not running.  Restart the game with TelemFFB already "
                     f"running.  (refusals this session: {count})")
    elif shm.deviceCount == 0:
        lines.append("")
        lines.append("(game attached, no tapped devices bound yet)")
    return "\n".join(lines)


def _tick_ms() -> int:
    return ctypes.windll.kernel32.GetTickCount()


class TapMonitorDialog(QDialog):
    """Non-modal so the user can fly while it watches.

    The change log accumulates in the lower pane rather than streaming to
    a file: the user plays full-screen, alt-tabs back, and what happened
    while they were away is sitting there to read or save - Save Log...
    is the button support asks them to press.
    """

    POLL_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DirectInput Tap Monitor")
        self.setModal(False)
        self.resize(720, 560)

        self._reader = FfbTapReader()
        self._prev_digest = None
        self._seen_writer = False

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        self.status_label = QLabel(f"polling {SHM_NAME}")
        self.live_view = QPlainTextEdit(readOnly=True)
        self.live_view.setFont(mono)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(mono)
        self.log_view.setPlaceholderText(
            "State changes appear here with timestamps - start/stop, "
            "parameter zeroing, device resets.  Play, alt-tab back, read, "
            "Save Log... to send it in.")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.live_view)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        save_btn = QPushButton("Save Log...")
        save_btn.clicked.connect(self.save_log)
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.log_view.clear)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status_label, stretch=1)
        buttons.addWidget(clear_btn)
        buttons.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        layout.addLayout(buttons)

        self._log_line(f"monitor opened, polling {SHM_NAME}")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(self.POLL_MS)

    # -- polling ---------------------------------------------------------

    def poll(self):
        shm = self._reader.snapshot()
        if shm is not None and shm.magic == TAP_MAGIC:
            alive = self._reader.writer_alive(shm)
            age_s = ((_tick_ms() - shm.tickMs) & 0xFFFFFFFF) / 1000.0
            self._seen_writer = True
            self.status_label.setText(
                f"attached  writer {'alive' if alive else 'EXITED'}")
        else:
            alive, age_s = False, 0.0
            self.status_label.setText(
                "waiting for a tapped game" if not self._seen_writer
                else "writer gone - waiting")

        digest = state_digest(shm)
        if digest != self._prev_digest:
            for line in describe_changes(self._prev_digest, digest):
                self._log_line(line)
            self._prev_digest = digest

        self._set_live_text(render_snapshot(shm, alive, age_s))

    def _set_live_text(self, text: str):
        # setPlainText resets the scroll position, which fights a user
        # reading a long table; only touch the widget when the text moved
        if text != self.live_view.toPlainText():
            bar = self.live_view.verticalScrollBar()
            pos = bar.value()
            self.live_view.setPlainText(text)
            bar.setValue(min(pos, bar.maximum()))

    def _log_line(self, line: str):
        self.log_view.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] {line}")

    # -- log saving ------------------------------------------------------

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save tap monitor log", "tap_monitor_log.txt",
            "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_view.toPlainText() + "\n")
        except OSError as e:
            logging.error(f"tap monitor log save failed: {e}")
            QMessageBox.warning(self, "Save failed", str(e))

    # -- lifecycle -------------------------------------------------------

    def closeEvent(self, event):
        self._timer.stop()
        self._reader.close()
        super().closeEvent(event)
