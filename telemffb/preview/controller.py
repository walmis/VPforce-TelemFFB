"""The effect preview's lifecycle, kept out of the main window.

One controller per instance window.  It owns the three pieces of state a
preview has - the local timed run, the play-all group tracker, and which
settings-row button started the run - and everything that moves them:
starting and stopping, the constant-force confirmation, the children's
IPC round trip, and the settings form's row cue.  The window only hosts
it: dialogs are parented to the window and the row lock searches under
it; nothing else about the window is assumed, so the controller runs
against any widget in a test.

The settings form on the master hosts the play button for EVERY device's
rows (the config scope switches between them), but an effect has to play
on the instance that owns the device.  So a preview is local when the
scope is this instance's own device, and otherwise goes to the owning
child over IPC: the master keeps the popup, the button state and the row
cues, the child does the playing and reports when it is done (with a
timer here as the fallback).
"""
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

import telemffb.globals as G
import telemffb.xmlutils as xmlutils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.preview.engine import (PREVIEW_SPECS, PreviewRunner, TimedPreview,
                              preview_blockers, resolve_preview_target)
from telemffb.ui.widgets.SettingsLayout import lock_preview_rows
from telemffb.utils.device import format_device_gains
from telemffb.telem import TelemManager


class EffectPreviewController:
    def __init__(self, window):
        self.window = window
        self._preview = None    # TimedPreview on this instance's own device
        self._group = None      # play-all tracker: spec, pending, children, timer
        self._slot = None       # 'pv' / 'pvall' button that started the run; None from the debug menu

    # ---- what the settings form asks ----------------------------------

    def scope(self):
        """The device whose rows the settings form is showing."""
        if G.master_instance:
            return getattr(G, 'current_device_config_scope', None) or G.device_type
        return G.device_type

    def is_remote(self):
        return self.scope() != G.device_type

    def blockers(self):
        """Why an effect preview cannot run right now (empty: it can)."""
        if self.is_remote():
            scope = self.scope()
            ipc = getattr(G, 'ipc_instance', None)
            if scope not in (getattr(G, 'launched_instances', None) or {}):
                return [f"no {scope} instance is running"]
            connected = ipc.child_device_connected(scope) if ipc else None
            if connected is None:
                return [f"the {scope} instance has not reported its device yet"]
            if not connected:
                return [f"no {scope} device connected"]
            return []
        manager = getattr(G, 'telem_manager', None)
        return preview_blockers(
            current_aircraft=manager.currentAircraft if manager else None,
            device_alive=HapticEffect.device_alive(),
            telemetry_paused=bool(getattr(manager, 'pause_state', False)) if manager else True)

    @staticmethod
    def _editor_profile():
        """The profile the settings form is showing.  The offline editor can
        be on one that is not the aircraft's mapped profile, and the preview
        has to play what the form shows."""
        return getattr(G.settings_mgr, 'active_profile', None) or None

    def _refuse_unmet(self, spec, devices, interactive=True):
        """True, after saying why, when a setting the spec ``requires`` is
        off on one of ``devices``.  Such an effect returns early every
        frame, so the run would be silent.  Read from the settings rather
        than an aircraft, so the master can answer for a child's device."""
        if not spec.requires:
            return False
        sim, model, cls = resolve_preview_target(G.settings_mgr)
        problems = []
        for dev in devices:
            try:
                params, *_ = TelemManager.resolve_aircraft_config(
                    sim, model, cls, device_type=dev, active_profile=self._editor_profile())
            except Exception:
                logging.exception(f"Effect preview {spec.name}: could not resolve {dev} settings")
                continue
            problems += [label if len(devices) == 1 else f"{label} ({dev})"
                         for label in spec.unmet(params)]
        if not problems:
            return False
        if interactive:
            QMessageBox.information(
                self.window, "Effect Preview",
                "This preview needs these enabled first:\n- " + "\n- ".join(problems)
                + (f"\n\n{spec.requires_note}" if spec.requires_note else ""))
        else:
            logging.warning(f"Effect preview {spec.name} refused, not enabled: " + "; ".join(problems))
        return True

    def running_devices(self):
        """This instance's device plus every launched child whose device
        has reported connected - the candidates for a play-all."""
        devices = [G.device_type]
        if G.master_instance:
            ipc = getattr(G, 'ipc_instance', None)
            for dev in (getattr(G, 'launched_instances', None) or {}):
                if dev != G.device_type and ipc and ipc.child_device_connected(dev):
                    devices.append(dev)
        return devices

    def running_spec(self):
        """(spec, slot) of the preview playing now, else (None, None).
        ``slot`` is the settings-row button that started it - 'pv' play,
        'pvall' play-all - or None from the debug menu.  The settings form
        asks this when it rebuilds, to redraw the playing row as held."""
        if self._group is not None:
            return self._group['spec'], self._slot
        if self._preview is not None and self._preview.running:
            return self._preview.runner.spec, self._slot
        return None, None

    def running(self, spec):
        return self.running_spec()[0] is spec

    # ---- starting and stopping ----------------------------------------

    def toggle(self, spec, devices=None):
        """Settings-row play buttons: start this preview on ``devices``
        (default: the device the form is scoped to), or stop it if it is
        the one playing.  While it plays the row is held - slider, value
        and erase locked, the starting button a stop - and released when
        the run ends, however it ends; the settings form finds the row by
        name, so a rebuild in between does not matter.  A set that is only
        this instance's own device plays locally; anything else goes
        through the group path, which drives the children over IPC."""
        if self.running(spec):
            if self._group is not None:
                self._stop_group()
            else:
                self.stop()
            return
        slot = 'pvall' if devices else 'pv'
        devices = list(devices) if devices else [self.scope()]
        if devices == [G.device_type]:
            self.start(spec, slot=slot)
        else:
            self._start_group(spec, devices, slot=slot)

    def start(self, spec, on_finished=None, confirm=True, slot=None, cues=True):
        """Play one effect on the device with synthetic telemetry.

        Builds a throwaway aircraft for the settings tab's current model
        (sim defaults when nothing is selected), with its own effect
        table so a loaded aircraft is untouched, and drives the spec's
        effect method from a timer.  Refused only while telemetry is
        actively streaming or the device is gone.  Returns True when the
        preview started.  ``cues`` is the settings-row hold for the run
        (off when a group run manages the cue for all its devices);
        ``slot`` names the button that started it, see toggle.
        """
        self.stop()
        blockers = self.blockers()
        if blockers:
            if confirm:
                QMessageBox.information(self.window, "Effect Preview",
                                        "Cannot preview now:\n- " + "\n- ".join(blockers))
            else:
                logging.warning(f"Effect preview {spec.name} refused: " + "; ".join(blockers))
            return False
        if self._refuse_unmet(spec, [G.device_type], interactive=confirm):
            return False
        if confirm and spec.constant_force and not self.confirm_constant_force(spec):
            return False
        sim, model, cls = resolve_preview_target(G.settings_mgr)
        # the settings as they are now: a child re-reads userconfig only
        # while telemetry streams, and the slider the user just moved was
        # written by the master
        if xmlutils.refresh_if_changed():
            logging.info("Effect preview: settings re-read from disk")
        try:
            aircraft = TelemManager.build_aircraft(sim, model, cls_name=cls,
                                                   active_profile=self._editor_profile())
            runner = PreviewRunner(aircraft, spec, sim)
        except Exception as e:
            logging.exception(f"Effect preview {spec.name} could not start")
            if confirm:
                QMessageBox.warning(self.window, "Effect Preview", f"Could not start preview:\n{e}")
            return False
        gains = format_device_gains()
        logging.info(f"Effect preview: {spec.name} on {sim} / {cls or '-'} / {model} "
                     f"({type(aircraft).__name__}), {runner.steps_total} frames "
                     f"at {runner.frame_rate:g} Hz" + (f", gains {gains}" if gains else ""))
        if cues:
            self._hold_rows(spec, True, slot=slot)

        def finished():
            logging.info(f"Effect preview finished: {spec.name}")
            if cues:
                self._hold_rows(spec, False)
            if on_finished is not None:
                on_finished()

        self._preview = TimedPreview(runner, on_finished=finished)
        self._preview.start()
        return True

    def stop(self):
        """Stop this instance's own preview (the local runner).  A group
        run's children are stopped by _stop_group; a child told to stop
        over IPC lands here and its finished callback reports back."""
        preview = self._preview
        if preview is not None and preview.running:
            preview.stop()
        self._preview = None

    def confirm_constant_force(self, spec):
        """The heads-up before a constant-force preview: an unattended axis
        can be driven to its stops.  Asked every time, on purpose.  The
        safety wording is fixed; only the spec's reference line varies."""
        answer = QMessageBox.warning(
            self.window, "Constant Force Preview",
            "Constant force effect previews may move the axis in unexpected ways.\n\n"
            "Please firmly grasp the controls before proceeding.\n\n"
            f"This preview plays {spec.reference}.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Ok

    def _hold_rows(self, spec, held, slot=None):
        """The settings form's cue for a playing preview, on this window's
        form: rows held (or released) and the starting button a stop."""
        self._slot = slot if held else None
        lock_preview_rows(self.window, spec, held, slot=slot)

    # ---- play-all across devices --------------------------------------

    def _start_group(self, spec, devices, on_finished=None, slot=None):
        """Play ``spec`` on a set of devices at once: this instance's own
        device locally (if in the set) and each child over IPC.  The
        master keeps the confirmation, the row cues and a fallback
        timer in case a child never reports back; the run is over when
        every device has finished."""
        self._stop_group()
        self.stop()
        blockers = self.blockers() if len(devices) == 1 else []
        if blockers:
            QMessageBox.information(self.window, "Effect Preview",
                                    "Cannot preview now:\n- " + "\n- ".join(blockers))
            return False
        if self._refuse_unmet(spec, devices):
            return False
        if spec.constant_force and not self.confirm_constant_force(spec):
            return False
        children = [d for d in devices if d != G.device_type]
        local = G.device_type in devices
        logging.info(f"Effect preview: {spec.name} on {', '.join(devices)}")
        fallback = QTimer(self.window)
        fallback.setSingleShot(True)
        fallback.setInterval(int((spec.duration + spec.tail + 2.0) * 1000))
        fallback.timeout.connect(lambda: self._finish_group(reason="no reply from a child"))
        self._group = {'spec': spec, 'pending': set(children) | ({G.device_type} if local else set()),
                       'children': children, 'on_finished': on_finished, 'timer': fallback}
        self._hold_rows(spec, True, slot=slot)
        for dev in children:
            G.ipc_instance.send_preview(dev, spec.name)
        if local:
            started = self.start(
                spec, confirm=False, cues=False,
                on_finished=lambda: self._group_device_done(G.device_type))
            if not started:
                self._group_device_done(G.device_type)
        fallback.start()
        return True

    def _group_device_done(self, device):
        group = self._group
        if group is None:
            return
        group['pending'].discard(device)
        if not group['pending']:
            self._finish_group(reason="all devices done")

    def _stop_group(self):
        group = self._group
        if group is None:
            return
        for dev in group['children']:
            G.ipc_instance.send_preview_stop(dev)
        self._finish_group(reason="stopped")
        self.stop()

    def _finish_group(self, reason=""):
        group = self._group
        if group is None:
            return
        self._group = None
        group['timer'].stop()
        self._hold_rows(group['spec'], False)
        logging.info(f"Effect preview finished: {group['spec'].name} ({reason})")
        if group['on_finished'] is not None:
            group['on_finished']()

    # ---- the IPC round trip -------------------------------------------

    def on_child_done(self, device, name):
        """IPC: the child owning ``device`` finished the named preview."""
        group = self._group
        if group is not None and group['spec'].name == name:
            self._group_device_done(device)

    def start_child(self, name):
        """IPC: the master asked this instance to play a named preview on
        its device.  No confirmation here - the master already asked, and
        this window is hidden - and the master is told when it ends."""
        spec = PREVIEW_SPECS.get(name)
        if spec is None:
            logging.warning(f"Effect preview {name!r} requested by the master is unknown here")
            G.ipc_instance.send_preview_done(name)
            return
        started = self.start(
            spec, on_finished=lambda: G.ipc_instance.send_preview_done(name), confirm=False)
        if not started:
            G.ipc_instance.send_preview_done(name)
