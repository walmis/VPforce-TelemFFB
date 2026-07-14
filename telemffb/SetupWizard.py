"""``SetupWizard`` — first-run multi-device routing onboarding.

Walks the user through:

1. **Welcome + scan**: enumerate connected Rhinos via
   ``FFBRhino.enumerate()`` so the wizard can pre-fill the rest.
2. **Device assignment**: pick a type for each detected USB PID
   (joystick / pedals / rudder / collective / trimwheel / shaker / other),
   and an optional friendly label.
3. **Position tags**: per-device multi-select of position tags
   (front, seat, floor, ...). Optional X/Y in cm relative to the seat.
4. **Preset picker**: filtered to presets whose ``requires`` list is fully
   covered by the inventory. The user can also select "Custom — keep
   bundled defaults" to skip preset overrides.
5. **Summary + Apply**: writes the inventory to ``[devices]
   .deviceInventory`` and any preset ``route_overrides`` to
   ``effect_routes_user.json``, then sets
   ``[system].setup_wizard_done = true`` so the wizard doesn't reopen on
   next start.

Triggered from ``main.py`` (after settings load) when
``setup_wizard_done`` is False AND the inventory is empty. Also reachable
manually from the ``Help`` menu.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
    QWidget, QWizard, QWizardPage,
)

import telemffb.globals as G
from telemffb.device_inventory import (
    Device, KNOWN_DEVICE_TYPES, KNOWN_POSITIONS,
    encode_inventory_for_ini,
)

logger = logging.getLogger(__name__)


def _presets_path() -> Optional[str]:
    from telemffb import utils
    try:
        return utils.get_resource_path(
            os.path.join("telemffb", "data", "setup_presets.json"),
            prefer_root=True,
        )
    except Exception:
        return None


def _user_routes_path() -> Optional[str]:
    if not G.userconfig_rootpath:
        return None
    return os.path.join(G.userconfig_rootpath, "effect_routes_user.json")


def load_presets() -> list[dict]:
    """Return the list of presets from the bundled JSON, or [] on error."""
    path = _presets_path()
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.exception("Failed to read setup_presets.json")
        return []
    return data.get("presets", []) if isinstance(data, dict) else []


# --- detected hardware ---------------------------------------------------

class _DetectedDevice:
    """Bag for "we saw this Rhino" rows. Kept simple so Page2/3 can pass
    them around without re-enumerating USB.
    """

    def __init__(self, *, usb_pid: str, product_string: str = ""):
        self.usb_pid = usb_pid
        self.product_string = product_string or ""
        # User-editable choices populated by the pages:
        self.type: str = self._guess_type(product_string)
        self.label: str = product_string
        self.positions: List[str] = []
        self.xy: Optional[dict] = None
        self.enabled: bool = True
        self.master: bool = False
        self.device_id: str = ""

    @staticmethod
    def _guess_type(name: str) -> str:
        n = (name or "").lower()
        for cand in ("pedals", "rudder", "collective", "trimwheel", "shaker"):
            if cand in n:
                return cand
        return "joystick"


def _enumerate_rhinos() -> list[_DetectedDevice]:
    """Best-effort wrapper around ``FFBRhino.enumerate()``."""
    try:
        from telemffb.hw.ffb_rhino import FFBRhino
    except Exception:
        logger.warning("FFBRhino module unavailable; wizard will run with empty list")
        return []
    try:
        infos = FFBRhino.enumerate()
    except Exception:
        logger.exception("FFBRhino.enumerate failed")
        return []
    return [
        _DetectedDevice(
            usb_pid=f"FFFF:{info.product_id:04X}",
            product_string=getattr(info, "product_string", "") or "",
        )
        for info in infos
    ]


# --- page 1: welcome + scan ----------------------------------------------

class _WelcomePage(QWizardPage):
    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self.setTitle("Welcome")
        self.setSubTitle(
            "Configure how effects are routed across your VPForce hardware.")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<p>This wizard helps you describe your setup so TelemFFB can "
            "route effects (rumble, gear bumps, runway, gunfire, …) to the "
            "right device with the right strength.</p>"
            "<p>You'll go through four steps:</p>"
            "<ol>"
            "<li>Confirm connected Rhinos and pick a type for each.</li>"
            "<li>Tag each device with its position (seat / floor / front / …).</li>"
            "<li>Choose a preset (e.g. <i>Helicopter</i>, <i>Stick + Shaker</i>) "
            "or skip and keep the defaults.</li>"
            "<li>Review and apply.</li>"
            "</ol>"
            "<p><b>Restart required</b> to apply routing changes.</p>"
        ))
        self.lbl_status = QLabel("Press <b>Next</b> to scan for connected devices.")
        self.lbl_status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.lbl_status)
        layout.addStretch(1)

    def initializePage(self) -> None:
        # Re-enumerate every time the page is shown so reconnects are picked up.
        # Preserve any manually-added devices (no usb_pid) the user has already
        # entered on the next page — re-enumerating shouldn't wipe their work.
        detected = _enumerate_rhinos()
        manual = [d for d in self._w.detected if not d.usb_pid]
        self._w.detected = detected + manual
        if detected:
            lines = [f"<b>Detected {len(detected)} device(s):</b>", "<ul>"]
            for d in detected:
                lines.append(
                    f"<li>{d.usb_pid} — {d.product_string or '(no product string)'}</li>"
                )
            lines.append("</ul>")
            self.lbl_status.setText("".join(lines))
        else:
            self.lbl_status.setText(
                "<i>No VPForce Rhino devices detected. You can still proceed "
                "and add devices manually on the next page.</i>")


# --- page 2: device type + label -----------------------------------------

class _TypePage(QWizardPage):
    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self._rows: list[tuple[_DetectedDevice, QComboBox, QLineEdit]] = []
        self.setTitle("Device Types")
        self.setSubTitle(
            "Pick what each device represents. Auto-guesses come from the "
            "USB product string. Use \"Add device\" to describe a shaker / "
            "transducer that isn't enumerated as a Rhino on USB.")
        self._layout = QVBoxLayout(self)
        self._scroll_holder = QWidget()
        self._grid = QGridLayout(self._scroll_holder)
        self._grid.addWidget(QLabel("<b>USB PID</b>"), 0, 0)
        self._grid.addWidget(QLabel("<b>Type</b>"), 0, 1)
        self._grid.addWidget(QLabel("<b>Label</b>"), 0, 2)
        self._layout.addWidget(self._scroll_holder)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("Add device")
        self._btn_add.setToolTip(
            "Add a device that wasn't detected on USB (e.g. a shaker driven "
            "by a separate amp/controller).")
        self._btn_add.clicked.connect(self._add_manual_device)
        btn_row.addWidget(self._btn_add)
        btn_row.addStretch(1)
        self._layout.addLayout(btn_row)
        self._layout.addStretch(1)

    def initializePage(self) -> None:
        self._render_rows()

    def _render_rows(self) -> None:
        # Drop any prior rows from a previous wizard run, keeping the header.
        for i in reversed(range(self._grid.count())):
            item = self._grid.itemAt(i)
            if item and item.widget() and self._grid.getItemPosition(i)[0] > 0:
                w = item.widget()
                self._grid.removeWidget(w)
                w.deleteLater()
        self._rows.clear()
        for r, dev in enumerate(self._w.detected, start=1):
            lbl_pid = QLabel(dev.usb_pid or "(manual)")
            self._grid.addWidget(lbl_pid, r, 0)
            cb = QComboBox()
            cb.addItems(KNOWN_DEVICE_TYPES)
            cb.setCurrentText(dev.type)
            self._grid.addWidget(cb, r, 1)
            le = QLineEdit(dev.label)
            le.setPlaceholderText("Friendly name (optional)")
            self._grid.addWidget(le, r, 2)
            btn_remove = QPushButton("Remove")
            btn_remove.clicked.connect(
                lambda _checked, d=dev: self._remove_device(d))
            self._grid.addWidget(btn_remove, r, 3)
            self._rows.append((dev, cb, le))
        if not self._w.detected:
            self._grid.addWidget(
                QLabel("<i>No devices detected. Click \"Add device\" below "
                       "to describe your hardware manually.</i>"),
                1, 0, 1, 4)

    def _commit_row_edits(self) -> None:
        for dev, cb, le in self._rows:
            dev.type = cb.currentText()
            dev.label = le.text().strip()

    def _add_manual_device(self) -> None:
        # Persist the user's in-progress edits before tearing down the rows.
        self._commit_row_edits()
        # Default to "shaker" — manual entries exist precisely because the
        # device isn't enumerated as a Rhino, and the most common case in
        # this fork is a transducer/shaker driven outside FFBRhino.
        new = _DetectedDevice(usb_pid="", product_string="")
        new.type = "shaker"
        new.label = "Shaker"
        self._w.detected.append(new)
        self._render_rows()

    def _remove_device(self, dev: _DetectedDevice) -> None:
        self._commit_row_edits()
        try:
            self._w.detected.remove(dev)
        except ValueError:
            return
        self._render_rows()

    def validatePage(self) -> bool:
        self._commit_row_edits()
        for dev in self._w.detected:
            # Synthesize a stable id once, on first validation.
            if not dev.device_id:
                base = dev.type
                used = {d.device_id for d in self._w.detected if d.device_id}
                idx = 1
                cand = base
                while cand in used:
                    idx += 1
                    cand = f"{base}_{idx}"
                dev.device_id = cand
        # First non-shaker device is master by default; user can change it
        # later in the Devices tab.
        for d in self._w.detected:
            if d.type != "shaker":
                d.master = True
                break
        return True


# --- page 3: positions ---------------------------------------------------

class _PositionPage(QWizardPage):
    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self._rows: list[tuple[_DetectedDevice, dict[str, QCheckBox],
                               QDoubleSpinBox, QDoubleSpinBox]] = []
        self.setTitle("Position Tags")
        self.setSubTitle(
            "Tags drive selectors like 'pos:seat' and the auto direction "
            "policy. X/Y are optional (cm relative to seat).")
        self._layout = QVBoxLayout(self)

    def initializePage(self) -> None:
        # Tear down old groups.
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()
        if not self._w.detected:
            self._layout.addWidget(QLabel(
                "<i>No devices to configure. Skip to the next step.</i>"))
            self._layout.addStretch(1)
            return
        for dev in self._w.detected:
            box = QGroupBox(f"{dev.label or dev.device_id}  ({dev.type})")
            box_layout = QVBoxLayout(box)
            tags_row = QHBoxLayout()
            checks: dict[str, QCheckBox] = {}
            for tag in KNOWN_POSITIONS:
                cb = QCheckBox(tag)
                if tag in dev.positions:
                    cb.setChecked(True)
                tags_row.addWidget(cb)
                checks[tag] = cb
            tags_row.addStretch(1)
            box_layout.addLayout(tags_row)

            xy_row = QFormLayout()
            sp_x = QDoubleSpinBox()
            sp_x.setRange(-200.0, 200.0)
            sp_x.setDecimals(0)
            sp_x.setSpecialValueText("—")
            sp_x.setValue(dev.xy["x"] if dev.xy else sp_x.minimum())
            sp_y = QDoubleSpinBox()
            sp_y.setRange(-200.0, 200.0)
            sp_y.setDecimals(0)
            sp_y.setSpecialValueText("—")
            sp_y.setValue(dev.xy["y"] if dev.xy else sp_y.minimum())
            xy_row.addRow("X (cm)", sp_x)
            xy_row.addRow("Y (cm)", sp_y)
            box_layout.addLayout(xy_row)

            self._layout.addWidget(box)
            self._rows.append((dev, checks, sp_x, sp_y))
        self._layout.addStretch(1)

    def validatePage(self) -> bool:
        for dev, checks, sp_x, sp_y in self._rows:
            dev.positions = [t for t, cb in checks.items() if cb.isChecked()]
            if (sp_x.value() <= sp_x.minimum() + 0.5
                    and sp_y.value() <= sp_y.minimum() + 0.5):
                dev.xy = None
            else:
                dev.xy = {"x": float(sp_x.value()), "y": float(sp_y.value())}
        return True


# --- page 4: preset picker ----------------------------------------------

class _PresetPage(QWizardPage):
    CUSTOM_OPTION_DATA = "__custom__"

    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self.setTitle("Preset")
        self.setSubTitle(
            "Pick a starting point. Only presets compatible with the "
            "detected hardware are listed.")
        layout = QHBoxLayout(self)
        self.lst = QListWidget()
        self.lst.itemSelectionChanged.connect(self._update_description)
        layout.addWidget(self.lst, stretch=1)
        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        layout.addWidget(self.txt, stretch=2)

    def initializePage(self) -> None:
        self.lst.clear()
        # Custom option is always available.
        item = QListWidgetItem("Custom — keep bundled defaults")
        item.setData(Qt.ItemDataRole.UserRole, self.CUSTOM_OPTION_DATA)
        self.lst.addItem(item)

        types_have = {d.type for d in self._w.detected if d.enabled}
        for preset in self._w.presets:
            req_types = {r.get("type") for r in preset.get("requires", [])
                         if isinstance(r, dict)}
            if req_types and not req_types.issubset(types_have):
                continue
            item = QListWidgetItem(preset.get("name", "(unnamed)"))
            item.setData(Qt.ItemDataRole.UserRole, preset)
            self.lst.addItem(item)
        # Default selection: first non-custom preset, else Custom.
        if self.lst.count() > 1:
            self.lst.setCurrentRow(1)
        else:
            self.lst.setCurrentRow(0)

    def _update_description(self):
        item = self.lst.currentItem()
        if item is None:
            self.txt.clear()
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data == self.CUSTOM_OPTION_DATA:
            self.txt.setHtml(
                "<p><b>Custom.</b> Don't apply any preset overrides — the "
                "bundled <code>effect_routes_default.json</code> applies as-is. "
                "Use the Effect Routing dialog later to fine-tune.</p>")
            return
        if not isinstance(data, dict):
            return
        html = [f"<h3>{data.get('name', '')}</h3>",
                f"<p>{data.get('description', '')}</p>"]
        req = data.get("requires", [])
        if req:
            html.append("<p><b>Requires:</b> "
                        + ", ".join(r.get("type", "") for r in req if isinstance(r, dict))
                        + "</p>")
        overrides = data.get("route_overrides", {})
        if overrides:
            html.append(f"<p><b>Touched effects:</b> {len(overrides)}</p>")
        self.txt.setHtml("".join(html))

    def validatePage(self) -> bool:
        item = self.lst.currentItem()
        if item is None:
            self._w.selected_preset = None
            return True
        data = item.data(Qt.ItemDataRole.UserRole)
        if data == self.CUSTOM_OPTION_DATA:
            self._w.selected_preset = None
        else:
            self._w.selected_preset = data
        return True


# --- page 5: WinWing optional accessory ----------------------------------

class _WinWingPage(QWizardPage):
    """Optional page: detect SimAppPro and offer to enable the UDP bridge."""

    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self.setTitle("WinWing Handles (optional)")
        self.setSubTitle(
            "If you have WinWing Orion 2 F-15EX / F-16 handles with vibration "
            "motors, TelemFFB can forward live telemetry to SimAppPro so the "
            "handles vibrate alongside your primary FFB device."
        )

        layout = QVBoxLayout(self)

        self._status_label = QLabel("Checking for SimAppPro…")
        layout.addWidget(self._status_label)

        self._cb = QCheckBox("Enable WinWing SimAppPro UDP bridge")
        self._cb.setChecked(bool(G.system_settings and
                                 G.system_settings.get("winwingSimAppPro", False)))
        layout.addWidget(self._cb)

        note = QLabel(
            "<i>Requires WinWing SimAppPro to be running. "
            "You can also enable / disable this later under "
            "Settings → System Settings → WinWing.</i>"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def initializePage(self) -> None:
        running = self._probe_simapppro()
        if running:
            self._status_label.setText(
                "SimAppPro detected — port 16536 is active."
            )
            self._status_label.setStyleSheet("color: green;")
            self._cb.setEnabled(True)
        else:
            self._status_label.setText(
                "SimAppPro not detected (port 16536 not responding). "
                "You can still enable the bridge; it will activate "
                "automatically when SimAppPro is running."
            )
            self._status_label.setStyleSheet("color: #888;")
            self._cb.setEnabled(True)

    def validatePage(self) -> bool:
        if G.system_settings:
            G.system_settings.setValue("winwingSimAppPro", self._cb.isChecked())
        return True

    @staticmethod
    def _probe_simapppro() -> bool:
        import socket as _socket
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.sendto(b'{"func":"net","msg":"ready"}', ("127.0.0.1", 16536))
            import subprocess
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return ":16536 " in out
        except Exception:
            return False


# --- page 6: summary -----------------------------------------------------

class _SummaryPage(QWizardPage):
    def __init__(self, parent: "SetupWizard"):
        super().__init__(parent)
        self._w = parent
        self.setTitle("Review & Apply")
        self.setSubTitle("Click Finish to save. Restart TelemFFB after closing.")
        layout = QVBoxLayout(self)
        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        layout.addWidget(self.txt)

    def initializePage(self) -> None:
        rows = []
        for d in self._w.detected:
            pos = ", ".join(d.positions) if d.positions else "—"
            rows.append(
                f"<tr><td>{d.device_id}</td><td>{d.type}</td>"
                f"<td>{pos}</td><td>{d.usb_pid}</td>"
                f"<td>{'master' if d.master else ''}</td></tr>"
            )
        body = (
            "<h3>Inventory</h3>"
            "<table border='1' cellpadding='4' cellspacing='0'>"
            "<tr><th>ID</th><th>Type</th><th>Positions</th>"
            "<th>USB PID</th><th>Role</th></tr>"
            + "".join(rows)
            + "</table>"
        )
        if self._w.selected_preset:
            body += (f"<h3>Preset</h3><p><b>{self._w.selected_preset.get('name')}</b><br>"
                     f"<i>{self._w.selected_preset.get('description', '')}</i></p>")
            n = len(self._w.selected_preset.get("route_overrides", {}))
            body += f"<p>{n} effect route(s) will be added to your user overrides.</p>"
        else:
            body += ("<h3>Preset</h3><p><i>None — bundled defaults apply.</i></p>")
        ww_on = bool(G.system_settings and
                     G.system_settings.get("winwingSimAppPro", False))
        body += (
            f"<h3>WinWing SimAppPro bridge</h3>"
            f"<p>{'<b>Enabled</b> — telemetry will be forwarded to SimAppPro on port 16536.' if ww_on else '<i>Disabled.</i>'}</p>"
        )
        body += ("<hr><p><b>On Finish:</b> writes "
                 "<code>[devices].deviceInventory</code> in config.ini and, "
                 "if a preset is selected, <code>effect_routes_user.json</code> "
                 "in the userconfig directory.</p>")
        self.txt.setHtml(body)


# --- wizard host ---------------------------------------------------------

class SetupWizard(QWizard):
    """Top-level multi-step wizard. ``exec()`` returns ``Accepted`` on Finish."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TelemFFB — Multi-Device Setup")
        # ModernStyle uses Qt-rendered chrome that respects the application
        # palette. The default AeroStyle on Windows forces a white page
        # background, which collides with TelemFFB's dark-mode palette
        # (light-grey QLabel text → invisible on white).
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        # Belt-and-braces: explicitly bind label/text colors to the palette
        # so any leftover white-background regions still render readable text.
        self.setStyleSheet(
            "QWizardPage { background-color: palette(window); } "
            "QWizardPage QLabel { color: palette(window-text); } "
            "QWizard > QWidget { background-color: palette(window); } "
            "QWizard QLabel { color: palette(window-text); }"
        )
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setMinimumSize(720, 540)

        self.detected: list[_DetectedDevice] = []
        self.presets: list[dict] = load_presets()
        self.selected_preset: Optional[dict] = None

        self.addPage(_WelcomePage(self))
        self.addPage(_TypePage(self))
        self.addPage(_PositionPage(self))
        self.addPage(_PresetPage(self))
        self.addPage(_WinWingPage(self))
        self.addPage(_SummaryPage(self))

    def accept(self) -> None:
        if self._save_inventory_and_preset():
            super().accept()

    # --- save logic ---

    def _save_inventory_and_preset(self) -> bool:
        # Build the Device list from detected (+ template-fill from preset
        # for devices that don't exist in the detected set yet).
        devices: list[Device] = []
        for d in self.detected:
            if not d.enabled:
                continue
            devices.append(Device(
                device_id=d.device_id,
                type=d.type,
                positions=list(d.positions),
                xy_offset=d.xy,
                usb_pid=d.usb_pid,
                label=d.label,
                master=d.master,
                enabled=True,
            ))

        if self.selected_preset:
            # Augment the inventory with template entries that aren't yet
            # present (e.g. a shaker that wasn't auto-detected because the
            # user hasn't plugged it in yet — keep the entry so the
            # routing makes sense once it's connected).
            existing_types = {d.type for d in devices}
            for tmpl in self.selected_preset.get("inventory_template", []):
                if not isinstance(tmpl, dict):
                    continue
                if tmpl.get("type") in existing_types:
                    continue
                try:
                    devices.append(Device.from_dict(tmpl))
                    existing_types.add(tmpl.get("type"))
                except TypeError:
                    logger.exception("Bad inventory template %r", tmpl)

        try:
            G.system_settings.setValue(
                "deviceInventory", encode_inventory_for_ini(devices))
            G.system_settings.setValue("setup_wizard_done", True)
        except Exception:
            logger.exception("SetupWizard: failed to persist inventory")
            QMessageBox.warning(self, "Save failed",
                                "Could not write the device inventory.")
            return False
        G.devices = devices

        # Broadcast to any already-running child instances so they pick
        # up the inventory without a restart. Children launched after
        # this point will read it from config.ini directly.
        ipc = getattr(G, "ipc_instance", None)
        if ipc is not None and getattr(G, "master_instance", False):
            try:
                ipc.broadcast_inventory(encode_inventory_for_ini(devices))
            except Exception:
                logger.exception("SetupWizard: inventory broadcast failed")

        # Save preset route_overrides to effect_routes_user.json.
        if self.selected_preset:
            overrides = self.selected_preset.get("route_overrides", {})
            user_path = _user_routes_path()
            if user_path and overrides:
                try:
                    self._write_routes_file(user_path, overrides)
                except Exception:
                    logger.exception("SetupWizard: failed to write user routes")
                    QMessageBox.warning(
                        self, "Routes save failed",
                        "Inventory was saved, but the preset's effect-route "
                        "overrides could not be written. You can apply them "
                        "via the Effect Routing dialog instead.")
                    return True

        # Refresh the Devices tab (if present) so the inventory is visible
        # without a restart for the data layer.
        if hasattr(G, "main_window") and G.main_window is not None:
            tab = getattr(G.main_window, "device_inventory_tab", None)
            if tab is not None and hasattr(tab, "reload_from_settings"):
                try:
                    tab.reload_from_settings()
                except Exception:
                    logger.exception("Could not refresh DeviceInventoryTab")

        # Live-reload the routes into the running EffectRouter so the
        # preset overrides take effect without a restart. The inventory
        # itself still needs a restart to re-evaluate which devices the
        # router filters for, but the routes update happily.
        router = getattr(G, "effect_router", None)
        if router is not None:
            try:
                router.reload_user_overrides(_user_routes_path())
            except Exception:
                logger.exception("SetupWizard: live router reload failed")

        return True

    @staticmethod
    def _write_routes_file(path: str, overrides: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # If the user already has a routes file, merge: preset overrides
        # win on key collisions, but unrelated user-tuned effects survive.
        existing: dict = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                logger.exception("SetupWizard: existing routes file unreadable; replacing")
                existing = {}
        merged_effects = dict(existing.get("effects", {}) if isinstance(existing, dict) else {})
        for name, route in overrides.items():
            merged_effects[name] = route
        data = {
            "version": 4,
            "effects": merged_effects,
            "aircraft_class_overrides": (
                existing.get("aircraft_class_overrides", {})
                if isinstance(existing, dict) else {}
            ),
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


def should_offer_wizard() -> bool:
    """Heuristic for first-run detection: wizard not yet finished AND no
    inventory yet. Either condition implies a clean install or a manual
    reset.
    """
    if not G.system_settings:
        return False
    if G.system_settings.get("setup_wizard_done", False):
        return False
    blob = G.system_settings.get("deviceInventory", "")
    return not bool(blob)
