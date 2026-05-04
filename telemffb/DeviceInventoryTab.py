"""``Devices`` tab — physical inventory editor.

Lets the user declare what hardware is connected and where it sits. The
inventory drives ``EffectRouter`` selectors (``id:`` / ``type:`` / ``pos:``)
so that effects can be routed to specific devices regardless of which
process happens to host the master.

Storage: ``[devices].deviceInventory`` in ``config.ini`` (a JSON blob), via
``G.system_settings``. Saving is on-edit (no separate "save" button).
Routing changes take effect on the next process start — a banner in the
tab makes that explicit. Live router re-init without restart is a Phase-5
follow-up.

Auto-detect is best-effort: it calls ``FFBRhino.enumerate()`` and adds
unmapped USB PIDs as new rows; the user assigns type/position.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import telemffb.globals as G
from telemffb.device_inventory import (
    Device, KNOWN_DEVICE_TYPES, KNOWN_POSITIONS,
    encode_inventory_for_ini, load_inventory_from_ini,
)

logger = logging.getLogger(__name__)


# --- per-row position-tag widget ----------------------------------------

class _PositionTagWidget(QWidget):
    """Compact multi-select for position tags. Inline checkboxes; no popup.

    A QComboBox with checkable items would also work, but the inline form
    fits the table-row idiom better and shows the current state at a glance.
    """
    changed = pyqtSignal()

    def __init__(self, selected: List[str] | None = None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self._checks: dict[str, QCheckBox] = {}
        for tag in KNOWN_POSITIONS:
            cb = QCheckBox(tag)
            cb.setStyleSheet("QCheckBox { font-size: 10px; }")
            if selected and tag in selected:
                cb.setChecked(True)
            cb.stateChanged.connect(self._on_changed)
            self._checks[tag] = cb
            layout.addWidget(cb)
        layout.addStretch(1)

    def _on_changed(self, _state):
        self.changed.emit()

    def value(self) -> List[str]:
        return [tag for tag, cb in self._checks.items() if cb.isChecked()]


# --- main tab ------------------------------------------------------------

_COLUMNS = (
    ("Type",      120),
    ("Positions", 360),
    ("X (cm)",    70),
    ("Y (cm)",    70),
    ("USB PID",   100),
    ("Label",     180),
    ("Master",    60),
    ("Enabled",   60),
    ("",          80),  # delete button
)


class DeviceInventoryTab(QWidget):
    """Editable table of ``Device`` entries persisted to ``config.ini``.

    Public API: just instantiate and ``addTab`` it. The widget reads
    ``G.devices`` (already loaded by ``main._setup_routing``) on construction
    and writes back on every cell change via ``G.system_settings``.
    """

    inventoryChanged = pyqtSignal()  # emitted after each persisted edit

    def __init__(self, parent=None):
        super().__init__(parent)
        # Local copy of inventory; keep G.devices in sync after each save.
        self._devices: List[Device] = list(G.devices) if G.devices else []
        self._suppress_writes = False
        self._build_ui()
        self._refresh_table()

    # --- layout ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header explanation + restart banner. Routing init runs once at
        # process start, so changes here don't take effect until next launch.
        info = QLabel(
            "<b>Device Inventory</b> &mdash; declare your physical hardware "
            "and where it sits. Selectors in the Effect Routing dialog "
            "(<i>type:</i>, <i>pos:</i>, <i>id:</i>) match against this "
            "table. Routing changes take effect on the next start."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # Action bar.
        actions = QHBoxLayout()
        self.btn_add = QPushButton("Add Device")
        self.btn_add.clicked.connect(self._add_blank_device)
        actions.addWidget(self.btn_add)

        self.btn_autodetect = QPushButton("Auto-detect Rhinos")
        self.btn_autodetect.setToolTip(
            "Scan connected VPForce Rhino devices and add any USB PIDs "
            "not already in the inventory.")
        self.btn_autodetect.clicked.connect(self._auto_detect)
        actions.addWidget(self.btn_autodetect)

        self.btn_clear = QPushButton("Clear Inventory")
        self.btn_clear.setToolTip(
            "Remove all entries. With an empty inventory, routing reverts "
            "to legacy hard-coded behaviour (stick + shaker only).")
        self.btn_clear.clicked.connect(self._clear_all)
        actions.addWidget(self.btn_clear)

        actions.addStretch(1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #888;")
        actions.addWidget(self.lbl_status)
        layout.addLayout(actions)

        # The table itself.
        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels([h for h, _ in _COLUMNS])
        for col, (_, w) in enumerate(_COLUMNS):
            self.table.setColumnWidth(col, w)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch,
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.AllEditTriggers,
        )
        layout.addWidget(self.table, stretch=1)

        # Faint divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # Footer.
        foot = QLabel(
            "<small>Tip: <i>Master</i> is the device that owns telemetry "
            "ingestion. Position tags are shorthand for the auto direction "
            "policy (e.g. <i>front</i>&nbsp;→ 0°, <i>right</i>&nbsp;→ 90°).</small>"
        )
        foot.setTextFormat(Qt.TextFormat.RichText)
        foot.setWordWrap(True)
        layout.addWidget(foot)

    # --- table population ---

    def _refresh_table(self) -> None:
        self._suppress_writes = True
        try:
            self.table.setRowCount(0)
            for dev in self._devices:
                self._append_row(dev)
            self._update_status()
        finally:
            self._suppress_writes = False

    def _append_row(self, dev: Device) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0: Type
        cb_type = QComboBox()
        cb_type.addItems(KNOWN_DEVICE_TYPES)
        if dev.type in KNOWN_DEVICE_TYPES:
            cb_type.setCurrentText(dev.type)
        cb_type.currentTextChanged.connect(
            lambda val, r=row: self._on_type_changed(r, val))
        self.table.setCellWidget(row, 0, cb_type)

        # 1: Positions
        pos_w = _PositionTagWidget(selected=dev.positions)
        pos_w.changed.connect(lambda r=row: self._on_positions_changed(r))
        self.table.setCellWidget(row, 1, pos_w)

        # 2: X
        sp_x = QDoubleSpinBox()
        sp_x.setRange(-200.0, 200.0)
        sp_x.setDecimals(0)
        sp_x.setSpecialValueText("—")
        sp_x.setValue(dev.xy_offset["x"] if dev.xy_offset else sp_x.minimum())
        sp_x.valueChanged.connect(lambda _v, r=row: self._on_xy_changed(r))
        self.table.setCellWidget(row, 2, sp_x)

        # 3: Y
        sp_y = QDoubleSpinBox()
        sp_y.setRange(-200.0, 200.0)
        sp_y.setDecimals(0)
        sp_y.setSpecialValueText("—")
        sp_y.setValue(dev.xy_offset["y"] if dev.xy_offset else sp_y.minimum())
        sp_y.valueChanged.connect(lambda _v, r=row: self._on_xy_changed(r))
        self.table.setCellWidget(row, 3, sp_y)

        # 4: USB PID
        le_pid = QLineEdit(dev.usb_pid or "")
        le_pid.setPlaceholderText("FFFF:2055")
        le_pid.editingFinished.connect(lambda r=row: self._on_pid_changed(r))
        self.table.setCellWidget(row, 4, le_pid)

        # 5: Label
        le_lbl = QLineEdit(dev.label or "")
        le_lbl.setPlaceholderText(dev.device_id)
        le_lbl.editingFinished.connect(lambda r=row: self._on_label_changed(r))
        self.table.setCellWidget(row, 5, le_lbl)

        # 6: Master radio (single-selection — only one device can be master)
        cb_master = QCheckBox()
        cb_master.setChecked(bool(dev.master))
        cb_master.stateChanged.connect(
            lambda _s, r=row: self._on_master_changed(r))
        wrap_master = QWidget()
        ml = QHBoxLayout(wrap_master)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(cb_master, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row, 6, wrap_master)

        # 7: Enabled
        cb_enabled = QCheckBox()
        cb_enabled.setChecked(bool(dev.enabled))
        cb_enabled.stateChanged.connect(
            lambda _s, r=row: self._on_enabled_changed(r))
        wrap_enabled = QWidget()
        el = QHBoxLayout(wrap_enabled)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(cb_enabled, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row, 7, wrap_enabled)

        # 8: Delete button
        btn_del = QPushButton("Remove")
        btn_del.clicked.connect(lambda _=False, r=row: self._delete_row(r))
        self.table.setCellWidget(row, 8, btn_del)

    # --- row<->device sync ---

    def _device_at(self, row: int) -> Optional[Device]:
        if 0 <= row < len(self._devices):
            return self._devices[row]
        return None

    def _on_type_changed(self, row: int, val: str) -> None:
        d = self._device_at(row)
        if d is None:
            return
        d.type = val
        self._save()

    def _on_positions_changed(self, row: int) -> None:
        d = self._device_at(row)
        if d is None:
            return
        w = self.table.cellWidget(row, 1)
        d.positions = w.value() if w else []
        self._save()

    def _on_xy_changed(self, row: int) -> None:
        d = self._device_at(row)
        if d is None:
            return
        sx = self.table.cellWidget(row, 2)
        sy = self.table.cellWidget(row, 3)
        if sx is None or sy is None:
            return
        # When BOTH are at the special "minimum/sentinel" value, treat as
        # "not specified" -> clear xy_offset to keep the JSON tidy.
        if sx.value() <= sx.minimum() + 0.5 and sy.value() <= sy.minimum() + 0.5:
            d.xy_offset = None
        else:
            d.xy_offset = {"x": float(sx.value()), "y": float(sy.value())}
        self._save()

    def _on_pid_changed(self, row: int) -> None:
        d = self._device_at(row)
        if d is None:
            return
        w = self.table.cellWidget(row, 4)
        d.usb_pid = w.text().strip() if w else ""
        self._save()

    def _on_label_changed(self, row: int) -> None:
        d = self._device_at(row)
        if d is None:
            return
        w = self.table.cellWidget(row, 5)
        d.label = w.text().strip() if w else ""
        self._save()

    def _on_master_changed(self, row: int) -> None:
        # Only one master at a time. Clear all others if the user just
        # checked this row.
        d = self._device_at(row)
        if d is None:
            return
        wrapper = self.table.cellWidget(row, 6)
        cb = wrapper.findChild(QCheckBox) if wrapper else None
        if cb is None:
            return
        new_state = cb.isChecked()
        d.master = bool(new_state)
        if new_state:
            for i, other in enumerate(self._devices):
                if i == row:
                    continue
                if other.master:
                    other.master = False
                    other_wrap = self.table.cellWidget(i, 6)
                    other_cb = other_wrap.findChild(QCheckBox) if other_wrap else None
                    if other_cb:
                        self._suppress_writes = True
                        try:
                            other_cb.setChecked(False)
                        finally:
                            self._suppress_writes = False
        self._save()

    def _on_enabled_changed(self, row: int) -> None:
        d = self._device_at(row)
        if d is None:
            return
        wrapper = self.table.cellWidget(row, 7)
        cb = wrapper.findChild(QCheckBox) if wrapper else None
        if cb is None:
            return
        d.enabled = bool(cb.isChecked())
        self._save()

    # --- row ops ---

    def _add_blank_device(self) -> None:
        # Generate a unique device_id by counting existing entries of the
        # default type. Users typically rename it via Label anyway.
        base = f"device_{len(self._devices) + 1}"
        new = Device(device_id=base, type="joystick", enabled=True)
        self._devices.append(new)
        self._refresh_table()
        self._save()

    def _delete_row(self, row: int) -> None:
        if not (0 <= row < len(self._devices)):
            return
        d = self._devices[row]
        ans = QMessageBox.question(
            self, "Remove device",
            f"Remove '{d.label or d.device_id}' from the inventory?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self._devices[row]
        self._refresh_table()
        self._save()

    def _clear_all(self) -> None:
        if not self._devices:
            return
        ans = QMessageBox.question(
            self, "Clear inventory",
            "Remove all devices? Routing will revert to legacy behaviour "
            "on next start.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._devices.clear()
        self._refresh_table()
        self._save()

    def _auto_detect(self) -> None:
        """Add Rhinos that aren't yet in the inventory.

        Uses ``FFBRhino.enumerate()`` (lazy import — keeps the tab usable
        in environments without the FFB hardware libs at module-load).
        """
        try:
            from telemffb.hw.ffb_rhino import FFBRhino
            devs = FFBRhino.enumerate()
        except Exception:
            logger.exception("Auto-detect: FFBRhino.enumerate failed")
            QMessageBox.warning(
                self, "Auto-detect failed",
                "Could not enumerate Rhino devices. Check the log for details.")
            return
        existing_pids = {d.usb_pid for d in self._devices if d.usb_pid}
        added = 0
        for info in devs:
            pid = f"FFFF:{info.product_id:04X}"
            if pid in existing_pids:
                continue
            ident = (info.product_string or "").lower()
            guess_type = "joystick"
            for cand in ("pedals", "rudder", "collective", "trimwheel", "shaker"):
                if cand in ident:
                    guess_type = cand
                    break
            new = Device(
                device_id=f"{guess_type}_{len(self._devices) + 1}",
                type=guess_type,
                usb_pid=pid,
                label=info.product_string or "",
                enabled=True,
            )
            self._devices.append(new)
            added += 1
        if added:
            self._refresh_table()
            self._save()
            self.lbl_status.setText(f"Added {added} device(s).")
        else:
            self.lbl_status.setText("No new devices found.")

    # --- persistence ---

    def _save(self) -> None:
        if self._suppress_writes:
            return
        # Validate device_id uniqueness — auto-rename duplicates rather
        # than failing silently. Empty ids get a default.
        seen: set[str] = set()
        for i, d in enumerate(self._devices):
            if not d.device_id:
                d.device_id = f"device_{i + 1}"
            base = d.device_id
            n = 2
            while d.device_id in seen:
                d.device_id = f"{base}_{n}"
                n += 1
            seen.add(d.device_id)
        blob = encode_inventory_for_ini(self._devices)
        try:
            G.system_settings.setValue("deviceInventory", blob)
        except Exception:
            logger.exception("Failed to persist deviceInventory")
        # Keep the in-memory mirror in sync; routing reads G.devices on
        # startup but the EffectRoutingDialog reads it live.
        G.devices = list(self._devices)
        # Master broadcasts the new inventory to children so they update
        # their G.devices / G.device_positions live. Children that aren't
        # connected yet will read it from config.ini on their next start.
        ipc = getattr(G, "ipc_instance", None)
        if ipc is not None and getattr(G, "master_instance", False):
            try:
                ipc.broadcast_inventory(blob)
            except Exception:
                logger.exception("Failed to broadcast inventory to children")
        self._update_status()
        self.inventoryChanged.emit()

    def _update_status(self) -> None:
        n = len(self._devices)
        if n == 0:
            self.lbl_status.setText(
                "Empty inventory — legacy routing in effect.")
        else:
            self.lbl_status.setText(
                f"{n} device(s) — restart to apply routing changes.")

    # --- public reload ---

    def reload_from_settings(self) -> None:
        """Re-read the inventory from ``G.system_settings``.

        Useful after the Setup Wizard finishes; it bulk-writes the inventory
        and we want the table to reflect the new state without a restart.
        """
        blob = G.system_settings.get("deviceInventory", "")
        self._devices = load_inventory_from_ini(blob)
        self._refresh_table()
