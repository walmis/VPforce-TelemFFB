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
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QMessageBox, QPushButton, QSlider, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import telemffb.globals as G
from telemffb.routing import (
    DirectionPolicy, EffectRoute, EffectRoutesPack, RouteLayer,
    load_routes_pack,
)

logger = logging.getLogger(__name__)

# Aircraft classes the dialog can patch independently. Class name strings
# match the Python class names in telemffb/sim/aircraft_base.py and
# aircrafts_*.py — set by TelemManager via ``EffectRouter.set_aircraft_class``.
# "" is the global scope (no class filter).
SCOPE_GLOBAL = ""
KNOWN_AIRCRAFT_CLASSES = (
    "JetAircraft", "PropellerAircraft", "TurbopropAircraft",
    "GliderAircraft", "Helicopter", "HPGHelicopter", "SASHelicopter",
)


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
                 *, default_layer: Optional[RouteLayer] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{effect_name} → {device_label}")
        self._layer = layer
        self._default_layer = default_layer
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
        # Reset-to-bundled-default button is only shown when the caller can
        # supply the bundled-default layer to compare against. Skipped for
        # synthetic / user-created layers (no default to revert to).
        if self._default_layer is not None:
            self.btn_reset = QPushButton("Reset to default")
            self.btn_reset.setToolTip(
                "Discard edits to this layer and restore the bundled "
                "default values from effect_routes_default.json.")
            self.btn_reset.clicked.connect(self._reset_fields_to_default)
            bb.addButton(self.btn_reset,
                         QDialogButtonBox.ButtonRole.ResetRole)
        layout.addWidget(bb)

    def _reset_fields_to_default(self) -> None:
        """Repopulate the form widgets from ``self._default_layer``.

        The actual mutation of the working layer happens on accept via
        ``apply_to``; this only refreshes the UI so the user can see the
        defaults before committing them.
        """
        if self._default_layer is None:
            return
        d = self._default_layer
        self.cb_enabled.setChecked(d.enabled)
        self.sp_gain.setValue(d.gain)
        self.sp_freq.setValue(d.freq_factor)
        if d.osc_type in ("sine", "impulse", "bandpass_noise", "passthrough"):
            self.cb_osc.setCurrentText(d.osc_type)
        if d.direction_policy in DirectionPolicy.ALL:
            self.cb_dir.setCurrentText(d.direction_policy)
        self.sp_dir_value.setValue(
            d.direction_value if d.direction_value is not None else 0.0)
        self.sp_dir_value.setEnabled(d.direction_policy == DirectionPolicy.FIXED)
        self.sp_center.setValue(d.center_hz if d.center_hz else 0.0)
        self.sp_bw.setValue(d.bandwidth_hz if d.bandwidth_hz else 0.0)
        self.sp_atk.setValue(d.attack_ms if d.attack_ms else 0.0)
        self.sp_dec.setValue(d.decay_ms if d.decay_ms else 0.0)

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
        # ``_working`` is ALWAYS the global merged view. Class scopes read
        # /write straight to ``_user.aircraft_class_overrides`` so the user
        # file persists per-class patches losslessly.
        self._working = self._merge_packs(self._defaults, self._user)
        self._scope: str = SCOPE_GLOBAL  # active aircraft-class filter
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
            "envelope. Apply reloads routing live."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        # Scope selector: global routes vs per-aircraft-class patches.
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope:"))
        self.cb_scope = QComboBox()
        self.cb_scope.addItem("Global (all aircraft)", userData=SCOPE_GLOBAL)
        for cls in KNOWN_AIRCRAFT_CLASSES:
            self.cb_scope.addItem(cls, userData=cls)
        self.cb_scope.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(self.cb_scope)
        scope_row.addStretch(1)
        # Import / Export of the routes file. Useful for sharing tunings
        # across machines or with the community.
        self.btn_export = QPushButton("Export…")
        self.btn_export.setToolTip(
            "Save the current routing pack (defaults + user overrides + "
            "class patches) to a JSON file you can share or back up.")
        self.btn_export.clicked.connect(self._on_export)
        scope_row.addWidget(self.btn_export)
        self.btn_import = QPushButton("Import…")
        self.btn_import.setToolTip(
            "Load a routing pack from a JSON file. Replaces the working "
            "view; press Apply to make it permanent.")
        self.btn_import.clicked.connect(self._on_import)
        scope_row.addWidget(self.btn_import)
        self.lbl_scope_hint = QLabel("")
        self.lbl_scope_hint.setStyleSheet("color: #888;")
        scope_row.addWidget(self.lbl_scope_hint)
        layout.addLayout(scope_row)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents,
        )
        # Context menu on the effect-name (vertical header) for whole-row
        # operations like "reset this effect to bundled defaults".
        v_header = self.table.verticalHeader()
        v_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v_header.customContextMenuRequested.connect(self._on_row_context_menu)
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

    def _active_routes_dict(self) -> dict[str, EffectRoute]:
        """Return the routes dict the table currently edits.

        For the global scope this is ``_working.routes`` (defaults + user
        overrides merged). For a class scope it's the per-class patch
        living on ``_user.aircraft_class_overrides`` — the table only shows
        effects that already have a class-level patch; "(not set)" rows are
        rendered as disabled cells so the user can opt in.
        """
        if self._scope == SCOPE_GLOBAL:
            return self._working.routes
        return self._user.aircraft_class_overrides.setdefault(self._scope, {})

    def _populate_table(self) -> None:
        cols = self._device_columns()
        if self._scope == SCOPE_GLOBAL:
            effects = sorted(self._working.routes.keys())
            self.lbl_scope_hint.setText(
                "Editing global routes — applies to all aircraft.")
        else:
            # Class scope: list ALL known effects so the user can opt in
            # any of them; class patches that already exist are pre-filled.
            effects = sorted(self._working.routes.keys())
            n_set = len(self._user.aircraft_class_overrides.get(self._scope, {}))
            self.lbl_scope_hint.setText(
                f"Class patch for {self._scope}: {n_set} effect(s) overridden. "
                f"Effects without a patch fall through to global routing.")
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(effects))
        self.table.setHorizontalHeaderLabels(
            [self._format_header(d) for d in cols])
        self.table.setVerticalHeaderLabels(effects)

        # Cache layers by (effect_name, device_id) for the Apply path.
        self._cell_editors: dict[tuple[int, int], _CellEditor] = {}
        active_routes = self._active_routes_dict()
        for r, effect_name in enumerate(effects):
            route = active_routes.get(effect_name)
            if route is None and self._scope == SCOPE_GLOBAL:
                # Should not happen — _working.routes was just iterated.
                route = self._working.routes[effect_name]
            for c, (dev_id, dev_type, positions) in enumerate(cols):
                layer = (self._first_layer_for(route, dev_id, dev_type, positions)
                         if route is not None else None)
                editor = _CellEditor(layer)
                editor.cb.stateChanged.connect(
                    lambda _s, rr=r, cc=c: self._mark_dirty(rr, cc))
                editor.sl.valueChanged.connect(
                    lambda _v, rr=r, cc=c: self._mark_dirty(rr, cc))
                self.table.setCellWidget(r, c, editor)
                self._cell_editors[(r, c)] = editor

        self.table.resizeColumnsToContents()

    def _on_scope_changed(self, _idx: int) -> None:
        new_scope = self.cb_scope.currentData() or SCOPE_GLOBAL
        self._scope = str(new_scope)
        self._populate_table()

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

        # Resolve which dict to mutate. In a class scope, we lazily clone
        # the global route on first edit so the patch is independent.
        if self._scope == SCOPE_GLOBAL:
            target_dict = self._working.routes
            route = target_dict.get(effect_name)
        else:
            target_dict = self._user.aircraft_class_overrides.setdefault(
                self._scope, {})
            route = target_dict.get(effect_name)
            if route is None:
                # First edit on this effect within the class patch — seed
                # from the global route so the user starts from sensible
                # values rather than a blank slate.
                base = self._working.routes.get(effect_name)
                base_layers = (
                    [RouteLayer(**l.to_dict()) for l in base.layers]
                    if base is not None else []
                )
                route = EffectRoute(name=effect_name, layers=base_layers)
                target_dict[effect_name] = route

        if route is None:
            return

        layer = self._first_layer_for(route, dev_id, dev_type, positions)
        if layer is None:
            # No layer yet for this device — implicitly create an id-targeted
            # layer so the cell now controls something concrete.
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
        # Mutate via the active scope so per-class detail edits land in the
        # right dict (same logic as ``_mark_dirty``).
        if self._scope == SCOPE_GLOBAL:
            route = self._working.routes.get(effect_name)
        else:
            route = self._user.aircraft_class_overrides.get(
                self._scope, {}).get(effect_name)
        if route is None:
            QMessageBox.information(
                self, "No layer in this scope",
                "No layer is set for this effect at the current scope. "
                "Enable the checkbox first to seed one from the global route.")
            return
        layer = self._first_layer_for(route, dev_id, dev_type, positions)
        if layer is None:
            QMessageBox.information(
                self, "No layer",
                "No layer is currently routed to this device. Enable the "
                "checkbox first to create one.")
            return
        # Find the matching layer in the bundled defaults so the detail
        # dialog can offer a "Reset to default" button. None when the
        # bundled defaults have no layer for this effect+device.
        default_layer: Optional[RouteLayer] = None
        default_route = self._defaults.routes.get(effect_name)
        if default_route is not None:
            default_layer = self._first_layer_for(
                default_route, dev_id, dev_type, positions)
        dlg = EffectDetailDialog(effect_name, dev_id, layer,
                                 default_layer=default_layer, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to(layer)
            # Re-read magnitude/enabled into the cell so the matrix shows
            # the new values.
            editor.cb.setChecked(layer.enabled)
            editor.sl.setValue(int(round(layer.gain * 100)))
            self._edited_layers[(effect_name, dev_id)] = layer

    # --- per-row context menu ---

    def _on_row_context_menu(self, pos) -> None:
        """Right-click on the effect-name header opens a per-row menu.

        Currently exposes "Reset this effect to bundled defaults" which
        replaces the whole row's layer set with the corresponding entry
        from ``effect_routes_default.json``. Useful when a user wants to
        undo experimentation without resetting the entire matrix.
        """
        v_header = self.table.verticalHeader()
        row = v_header.logicalIndexAt(pos)
        if row < 0 or row >= self.table.rowCount():
            return
        item = self.table.verticalHeaderItem(row)
        if item is None:
            return
        effect_name = item.text()
        menu = QMenu(self)
        reset_act = QAction(f"Reset '{effect_name}' to bundled default", self)
        # Disable the action when there is nothing to reset to (e.g. a
        # user-added effect that has no entry in defaults).
        reset_act.setEnabled(effect_name in self._defaults.routes)
        reset_act.triggered.connect(
            lambda: self._reset_effect_to_default(effect_name))
        menu.addAction(reset_act)
        menu.exec(v_header.mapToGlobal(pos))

    def _reset_effect_to_default(self, effect_name: str) -> None:
        """Restore one effect's layers from the bundled defaults.

        Operates on the active scope:
        - Global: replaces ``_working.routes[effect_name]`` with a fresh
          deep-copy of the bundled default route.
        - Class scope: removes the per-class patch for this effect, so it
          falls through to the global routing again.
        """
        default_route = self._defaults.routes.get(effect_name)
        if default_route is None:
            return
        if self._scope == SCOPE_GLOBAL:
            self._working.routes[effect_name] = EffectRoute(
                name=effect_name,
                layers=[RouteLayer(**l.to_dict()) for l in default_route.layers],
            )
        else:
            patch = self._user.aircraft_class_overrides.get(self._scope, {})
            patch.pop(effect_name, None)
            if not patch:
                self._user.aircraft_class_overrides.pop(self._scope, None)
        # Drop any pending in-memory edits on this effect.
        self._edited_layers = {
            k: v for k, v in self._edited_layers.items() if k[0] != effect_name
        }
        self._populate_table()

    # --- apply / save ---

    def _on_apply(self) -> None:
        ok = self._save_working_to_user()
        if not ok:
            return
        live = self._reload_live_router()
        if live:
            QMessageBox.information(
                self, "Saved",
                "Routing changes saved to effect_routes_user.json and "
                "applied live to the active EffectRouter.",
            )
        else:
            QMessageBox.information(
                self, "Saved",
                "Routing changes saved to effect_routes_user.json. "
                "Restart TelemFFB to apply.",
            )

    def _on_ok(self) -> None:
        if self._save_working_to_user():
            self._reload_live_router()
            self.accept()

    def _reload_live_router(self) -> bool:
        """Hot-reload the user overrides into the running EffectRouter.

        Returns True iff there was a router to reload — children that
        haven't been backend-swapped (G.effect_router is None) just take
        the new file on their next start, which is fine.
        """
        router = getattr(G, "effect_router", None)
        if router is None:
            return False
        path = _user_routes_path()
        try:
            return bool(router.reload_user_overrides(path))
        except Exception:
            logger.exception("Live router reload failed")
            return False

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

    # --- import / export ---

    def _on_export(self) -> None:
        """Write the current working pack to a user-chosen path.

        Always exports the FULL working view (defaults + user overrides +
        class patches), so the file is self-contained — a recipient who
        imports it gets the same routing without needing the bundled
        defaults to be byte-equal.
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "Export routing pack", "effect_routes_export.json",
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        pack = EffectRoutesPack(
            version=4,
            routes=self._working.routes,
            aircraft_class_overrides=self._user.aircraft_class_overrides,
        )
        try:
            self._write_pack_to(path, pack)
            QMessageBox.information(
                self, "Exported", f"Wrote routing pack to:\n{path}")
        except Exception:
            logger.exception("Export failed")
            QMessageBox.critical(self, "Export failed",
                                 "Could not write the file. See log.")

    def _on_import(self) -> None:
        """Load a routing pack from a user-chosen path into the working view.

        Does NOT save automatically — the user must press Apply or OK to
        commit. This lets people preview a shared pack before adopting it.
        Importing is non-destructive on cancel and refuses files that
        don't parse as a v4 routes pack.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Import routing pack", "",
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        loaded = load_routes_pack(path)
        if loaded is None:
            QMessageBox.warning(
                self, "Import failed",
                "File could not be parsed as a routes pack.")
            return
        # Treat the imported file as a complete user override: its
        # ``effects`` map becomes the new user routes, its
        # ``aircraft_class_overrides`` replaces the current per-class
        # patches. The bundled defaults are unchanged.
        self._user = loaded
        self._working = self._merge_packs(self._defaults, self._user)
        self._edited_layers.clear()
        self._populate_table()
        QMessageBox.information(
            self, "Imported",
            f"Loaded {len(loaded.routes)} effect(s) and "
            f"{len(loaded.aircraft_class_overrides)} class patch(es) "
            "from the file.\n\nReview the matrix and press Apply to "
            "save and reload routing live.")

    def _save_working_to_user(self) -> bool:
        path = _user_routes_path()
        if not path:
            QMessageBox.warning(
                self, "No userconfig",
                "Userconfig path is not set; cannot save routing.")
            return False
        # Build the user file from:
        # - global routes from ``_working.routes`` (defaults + user merged)
        # - class overrides from ``_user.aircraft_class_overrides`` (only
        #   the deltas the user actually edited live here)
        try:
            pack = EffectRoutesPack(
                version=4,
                routes=self._working.routes,
                aircraft_class_overrides=self._user.aircraft_class_overrides,
            )
            self._write_pack_to(path, pack)
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
                if routes  # skip empty class buckets
            },
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
