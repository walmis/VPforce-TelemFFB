"""``Effect Routing`` dialog — matrix view of effects × devices.

Layout: rows are routable effects (the union of bundled-default keys and
user-override keys), columns are devices from ``G.devices``. Each cell
holds a tiny widget with an ``enabled`` checkbox and a ``gain`` slider.

Doubleclick on a cell opens the per-layer detail dialog (frequency factor,
direction policy, oscillator type, attack/decay).

Persistence: the dialog edits an in-memory ``EffectRoutesPack`` and writes
it to ``effect_routes_user.json`` on Apply / OK. Live router re-init is a
P5 follow-up; for now changes take effect on next launch (consistent with
the Devices tab).
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import telemffb.globals as G
from telemffb.routing import (
    DirectionPolicy, EffectRoute, EffectRoutesPack, RouteLayer,
    load_routes_pack,
)

logger = logging.getLogger(__name__)


def _user_routes_path() -> Optional[str]:
    if not G.userconfig_rootpath:
        return None
    return os.path.join(G.userconfig_rootpath, "effect_routes_user.json")


# --- per-cell editor ----------------------------------------------------

class _CellEditor(QWidget):
    """Small enabled+gain widget used for every (effect, device) cell.

    The widget represents the *first* (Phase-2) layer that targets this
    device. Multi-layer detail is reachable via doubleclick.
    """

    GAIN_SLIDER_MAX = 200  # 0–200% in increments of 1

    def __init__(self, layer: Optional[RouteLayer], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self.cb = QCheckBox()
        self.cb.setChecked(layer.enabled if layer else False)
        layout.addWidget(self.cb)
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, self.GAIN_SLIDER_MAX)
        self.sl.setValue(int(round((layer.gain if layer else 1.0) * 100)))
        self.sl.setEnabled(layer is not None)
        self.sl.setMaximumWidth(110)
        layout.addWidget(self.sl)
        self.lbl = QLabel(f"{self.sl.value()}%")
        self.lbl.setMinimumWidth(36)
        layout.addWidget(self.lbl)
        self.sl.valueChanged.connect(
            lambda v: self.lbl.setText(f"{v}%"))
        self.cb.stateChanged.connect(self._sync_slider_enabled)
        self._has_layer = layer is not None

    def _sync_slider_enabled(self, _state):
        if self._has_layer:
            self.sl.setEnabled(self.cb.isChecked())

    def enabled(self) -> bool:
        return self.cb.isChecked()

    def gain(self) -> float:
        return self.sl.value() / 100.0

    def has_layer(self) -> bool:
        return self._has_layer


# --- per-effect detail dialog -------------------------------------------

class EffectDetailDialog(QDialog):
    """Per-(effect, device) editor for the layer parameters that don't fit
    into a single matrix cell: oscillator type, frequency factor, direction
    policy, attack/decay, bandpass center.
    """

    def __init__(self, effect_name: str, device_label: str, layer: RouteLayer,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{effect_name} → {device_label}")
        self._layer = layer
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cb_enabled = QCheckBox()
        self.cb_enabled.setChecked(layer.enabled)
        form.addRow("Enabled", self.cb_enabled)

        self.sp_gain = QDoubleSpinBox()
        self.sp_gain.setRange(0.0, 5.0)
        self.sp_gain.setSingleStep(0.05)
        self.sp_gain.setDecimals(2)
        self.sp_gain.setValue(layer.gain)
        form.addRow("Gain", self.sp_gain)

        self.sp_freq = QDoubleSpinBox()
        self.sp_freq.setRange(0.0, 10.0)
        self.sp_freq.setSingleStep(0.1)
        self.sp_freq.setDecimals(2)
        self.sp_freq.setValue(layer.freq_factor)
        form.addRow("Frequency factor", self.sp_freq)

        self.cb_osc = QComboBox()
        self.cb_osc.addItems(("sine", "impulse", "bandpass_noise", "passthrough"))
        if layer.osc_type in ("sine", "impulse", "bandpass_noise", "passthrough"):
            self.cb_osc.setCurrentText(layer.osc_type)
        form.addRow("Oscillator", self.cb_osc)

        self.cb_dir = QComboBox()
        self.cb_dir.addItems(DirectionPolicy.ALL)
        if layer.direction_policy in DirectionPolicy.ALL:
            self.cb_dir.setCurrentText(layer.direction_policy)
        form.addRow("Direction policy", self.cb_dir)

        self.sp_dir_value = QDoubleSpinBox()
        self.sp_dir_value.setRange(-180.0, 359.0)
        self.sp_dir_value.setDecimals(0)
        self.sp_dir_value.setValue(
            layer.direction_value if layer.direction_value is not None else 0.0)
        self.sp_dir_value.setEnabled(layer.direction_policy == DirectionPolicy.FIXED)
        self.cb_dir.currentTextChanged.connect(
            lambda v: self.sp_dir_value.setEnabled(v == DirectionPolicy.FIXED))
        form.addRow("Direction value (°)", self.sp_dir_value)

        layout.addLayout(form)

        # Optional bandpass / impulse params, in a divider'd block.
        adv = QFrame()
        adv.setFrameShape(QFrame.Shape.StyledPanel)
        adv_layout = QFormLayout(adv)
        adv_layout.addRow(QLabel("<i>Optional shaper parameters</i>"))

        self.sp_center = QDoubleSpinBox()
        self.sp_center.setRange(0.0, 500.0)
        self.sp_center.setSpecialValueText("auto")
        self.sp_center.setValue(layer.center_hz if layer.center_hz else 0.0)
        adv_layout.addRow("Center Hz (bandpass)", self.sp_center)

        self.sp_bw = QDoubleSpinBox()
        self.sp_bw.setRange(0.0, 200.0)
        self.sp_bw.setSpecialValueText("auto")
        self.sp_bw.setValue(layer.bandwidth_hz if layer.bandwidth_hz else 0.0)
        adv_layout.addRow("Bandwidth Hz (bandpass)", self.sp_bw)

        self.sp_atk = QDoubleSpinBox()
        self.sp_atk.setRange(0.0, 500.0)
        self.sp_atk.setSpecialValueText("auto")
        self.sp_atk.setValue(layer.attack_ms if layer.attack_ms else 0.0)
        adv_layout.addRow("Attack ms (impulse)", self.sp_atk)

        self.sp_dec = QDoubleSpinBox()
        self.sp_dec.setRange(0.0, 1000.0)
        self.sp_dec.setSpecialValueText("auto")
        self.sp_dec.setValue(layer.decay_ms if layer.decay_ms else 0.0)
        adv_layout.addRow("Decay ms (impulse)", self.sp_dec)

        layout.addWidget(adv)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def apply_to(self, layer: RouteLayer) -> None:
        """Mutate ``layer`` with the dialog values."""
        layer.enabled = self.cb_enabled.isChecked()
        layer.gain = float(self.sp_gain.value())
        layer.freq_factor = float(self.sp_freq.value())
        layer.osc_type = self.cb_osc.currentText()
        layer.direction_policy = self.cb_dir.currentText()
        layer.direction_value = (
            float(self.sp_dir_value.value())
            if layer.direction_policy == DirectionPolicy.FIXED else None
        )
        layer.center_hz = float(self.sp_center.value()) or None
        layer.bandwidth_hz = float(self.sp_bw.value()) or None
        layer.attack_ms = float(self.sp_atk.value()) or None
        layer.decay_ms = float(self.sp_dec.value()) or None


# --- main dialog --------------------------------------------------------

class EffectRoutingDialog(QDialog):
    """Matrix editor for the effective routing pack.

    The dialog operates on a working copy of the merged ``EffectRoutesPack``
    (defaults + user overrides). Apply writes the user-overlay layer to
    ``effect_routes_user.json``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Effect Routing")
        self.resize(1100, 600)

        self._defaults = self._load_defaults()
        self._user = self._load_user()
        self._working = self._merge_packs(self._defaults, self._user)
        # Track which (effect, device_id) cells the user has actually
        # touched, so unchanged cells stay out of the user file.
        self._dirty: dict[tuple[str, str], _CellEditor] = {}
        self._edited_layers: dict[tuple[str, str], RouteLayer] = {}

        self._build_ui()
        self._populate_table()

    # --- loading ---

    def _load_defaults(self) -> EffectRoutesPack:
        from telemffb import utils
        path = utils.get_resource_path(
            os.path.join("telemffb", "data", "effect_routes_default.json"),
            prefer_root=True,
        )
        return load_routes_pack(path) or EffectRoutesPack()

    def _load_user(self) -> EffectRoutesPack:
        path = _user_routes_path()
        if path:
            loaded = load_routes_pack(path)
            if loaded is not None:
                return loaded
        return EffectRoutesPack()

    @staticmethod
    def _merge_packs(defaults: EffectRoutesPack,
                     user: EffectRoutesPack) -> EffectRoutesPack:
        merged_routes: dict[str, EffectRoute] = {}
        for name, route in defaults.routes.items():
            merged_routes[name] = EffectRoute(
                name=name,
                layers=[RouteLayer(**l.to_dict()) for l in route.layers],
            )
        for name, route in user.routes.items():
            merged_routes[name] = EffectRoute(
                name=name,
                layers=[RouteLayer(**l.to_dict()) for l in route.layers],
            )
        return EffectRoutesPack(routes=merged_routes)

    # --- ui ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(
            "<b>Effect Routing Matrix.</b> Each cell controls one layer of "
            "an effect on a device. Toggle the checkbox to enable it; the "
            "slider sets gain (0–200%). <b>Double-click a cell</b> for "
            "frequency, oscillator type, direction policy and impulse "
            "envelope. Changes apply on next start."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table, stretch=1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Reset,
        )
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._on_apply)
        bb.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._on_reset_to_default)
        layout.addWidget(bb)

    # --- table population ---

    def _device_columns(self) -> List[tuple[str, str, list[str]]]:
        """Return [(device_id, type, positions)] for table columns."""
        if not G.devices:
            # Fall back to two synthetic columns so the dialog isn't empty
            # for users who haven't run the wizard yet.
            return [("__stick__", "joystick", []), ("__shaker__", "shaker", [])]
        return [(d.device_id, d.type, list(d.positions))
                for d in G.devices if d.enabled]

    def _populate_table(self) -> None:
        cols = self._device_columns()
        effects = sorted(self._working.routes.keys())
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(effects))
        self.table.setHorizontalHeaderLabels(
            [self._format_header(d) for d in cols])
        self.table.setVerticalHeaderLabels(effects)

        # Cache layers by (effect_name, device_id) for the Apply path.
        self._cell_editors: dict[tuple[int, int], _CellEditor] = {}
        for r, effect_name in enumerate(effects):
            route = self._working.routes[effect_name]
            for c, (dev_id, dev_type, positions) in enumerate(cols):
                layer = self._first_layer_for(route, dev_id, dev_type, positions)
                editor = _CellEditor(layer)
                editor.cb.stateChanged.connect(
                    lambda _s, rr=r, cc=c: self._mark_dirty(rr, cc))
                editor.sl.valueChanged.connect(
                    lambda _v, rr=r, cc=c: self._mark_dirty(rr, cc))
                self.table.setCellWidget(r, c, editor)
                self._cell_editors[(r, c)] = editor

        self.table.resizeColumnsToContents()

    def _format_header(self, col: tuple[str, str, list[str]]) -> str:
        dev_id, dev_type, positions = col
        if dev_id.startswith("__"):
            return f"({dev_type})"
        pos_str = ",".join(positions) if positions else "—"
        return f"{dev_id}\n[{dev_type}] {pos_str}"

    @staticmethod
    def _first_layer_for(route: EffectRoute, dev_id: str, dev_type: str,
                         positions: list[str]) -> Optional[RouteLayer]:
        candidates = route.layers_for(
            device_id=dev_id, device_type=dev_type,
            device_positions=positions,
        )
        return candidates[0] if candidates else None

    # --- cell change tracking ---

    def _mark_dirty(self, row: int, col: int) -> None:
        editor = self._cell_editors.get((row, col))
        if editor is None:
            return
        effect_name = self.table.verticalHeaderItem(row).text()
        cols = self._device_columns()
        dev_id, dev_type, positions = cols[col]
        # Apply the cell-level change to the working copy IN PLACE so a
        # subsequent doubleclick sees the latest value.
        route = self._working.routes.get(effect_name)
        if route is None:
            return
        layer = self._first_layer_for(route, dev_id, dev_type, positions)
        if layer is None:
            # Cell had no layer; user enabling it implicitly creates one
            # targeted by id. Sensible default: type-targeted.
            new = RouteLayer(target=f"id:{dev_id}",
                             enabled=editor.enabled(),
                             gain=editor.gain())
            route.layers.append(new)
            self._edited_layers[(effect_name, dev_id)] = new
        else:
            layer.enabled = editor.enabled()
            layer.gain = editor.gain()
            self._edited_layers[(effect_name, dev_id)] = layer

    # --- doubleclick details ---

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        editor = self._cell_editors.get((row, col))
        if editor is None:
            return
        effect_name = self.table.verticalHeaderItem(row).text()
        cols = self._device_columns()
        dev_id, dev_type, positions = cols[col]
        route = self._working.routes.get(effect_name)
        if route is None:
            return
        layer = self._first_layer_for(route, dev_id, dev_type, positions)
        if layer is None:
            QMessageBox.information(
                self, "No layer",
                "No layer is currently routed to this device. Enable the "
                "checkbox first to create one.")
            return
        dlg = EffectDetailDialog(effect_name, dev_id, layer, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to(layer)
            # Re-read magnitude/enabled into the cell so the matrix shows
            # the new values.
            editor.cb.setChecked(layer.enabled)
            editor.sl.setValue(int(round(layer.gain * 100)))
            self._edited_layers[(effect_name, dev_id)] = layer

    # --- apply / save ---

    def _on_apply(self) -> None:
        ok = self._save_working_to_user()
        if ok:
            QMessageBox.information(
                self, "Saved",
                "Routing changes saved to effect_routes_user.json. "
                "Restart TelemFFB to apply.",
            )

    def _on_ok(self) -> None:
        if self._save_working_to_user():
            self.accept()

    def _on_reset_to_default(self) -> None:
        ans = QMessageBox.question(
            self, "Reset routing",
            "Discard all user overrides and revert to the bundled defaults?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._user = EffectRoutesPack()
        self._working = self._merge_packs(self._defaults, self._user)
        self._edited_layers.clear()
        self._populate_table()

    def _save_working_to_user(self) -> bool:
        path = _user_routes_path()
        if not path:
            QMessageBox.warning(
                self, "No userconfig",
                "Userconfig path is not set; cannot save routing.")
            return False
        # The user file is the WORKING pack minus anything that exactly
        # equals the bundled default (avoids bloat and surprise when users
        # update). Simpler in P3: just save the whole working pack as the
        # user override; defaults stay read-only.
        try:
            self._write_pack_to(path, self._working)
            return True
        except Exception:
            logger.exception("Failed to save effect_routes_user.json")
            QMessageBox.critical(
                self, "Save failed",
                "Could not write effect_routes_user.json. See log.")
            return False

    @staticmethod
    def _write_pack_to(path: str, pack: EffectRoutesPack) -> None:
        import json
        data = {
            "version": 4,
            "effects": {
                name: {"layers": [l.to_dict() for l in route.layers]}
                for name, route in pack.routes.items()
            },
            "aircraft_class_overrides": {
                cls: {
                    name: {"layers": [l.to_dict() for l in route.layers]}
                    for name, route in routes.items()
                }
                for cls, routes in pack.aircraft_class_overrides.items()
            },
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
