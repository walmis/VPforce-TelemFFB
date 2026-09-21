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

"""KeyValueTableModel: the row model behind MonitorPanel's telemetry and
active-effects tables.

Both tables are "a list of rows keyed by something stable, refreshed every
telemetry frame" - telemetry rows keyed by their telemetry key (mostly the
same set frame to frame), active-effects rows keyed by the rendered effect
line itself (unique per effect, since it embeds the effect id). One generic
model covers both: construct with the column headers wanted (two for
telemetry - Key/Value - one for the effects list) and feed it ordered
``(row_key, values)`` pairs on every update.

Telemetry runs at 60-120 Hz, but ``on_update_telemetry`` already throttles
calls into this model to at most every 50 ms - still frequent enough that a
naive ``beginResetModel()``/``endResetModel()`` on every update would be
wasteful (it drops the current selection and scroll position, and forces
every attached view to redraw every cell even when nothing changed).
``set_rows`` instead:

- takes the fast path - a plain equality check against the last key order,
  then ``dataChanged`` over just the span of rows whose values actually
  differ - for the overwhelmingly common case where the same keys show up
  in the same order (true for telemetry every frame the key set doesn't
  change, and true for the effects list whenever nothing started or
  stopped);
- otherwise diffs the key sets with plain ``beginRemoveRows``/
  ``beginInsertRows`` calls (batched into contiguous runs), never a full
  reset. This relies on the caller's ordering being a stable function of
  the key set alone (true here: telemetry order is alphabetical plus a
  fixed prefix of special-cased keys, and the effects list order follows
  ``G.effects.dict`` iteration order) - a key that survives between two
  updates never changes position relative to the other surviving keys, so
  "remove the rows that dropped out, then insert the rows that showed up"
  reproduces the target order exactly.
"""

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt


class KeyValueTableModel(QAbstractTableModel):
    """Generic ordered row model, diffed by a caller-supplied row key.

    ``headers`` fixes the column count and their display names. Each row is
    ``(key, values)`` where ``values`` has one entry per header; ``key`` is
    never displayed - it is only how ``set_rows`` matches a row across
    updates.
    """

    def __init__(self, headers: Sequence[str], parent=None):
        super().__init__(parent)
        self._headers: List[str] = list(headers)
        self._keys: List[str] = []
        self._values: Dict[str, Tuple[str, ...]] = {}
        # Optional, and per cell: {row key: (tooltip per column,)}, where a
        # None entry means "no tooltip of its own, show the cell's text".
        # Only the effects table's intensity column uses this; every other
        # column keeps the plain behaviour of hovering its own value.
        self._tooltips: Dict[str, Tuple[Optional[str], ...]] = {}

    # ---- QAbstractTableModel ----------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._keys)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._headers[section]
        return str(section + 1)

    def set_header(self, section: int, text: str) -> None:
        """Change one column's header text."""
        if self._headers[section] != text:
            self._headers[section] = text
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, section, section)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key = self._keys[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            tips = self._tooltips.get(key)
            if tips is not None and tips[index.column()] is not None:
                return tips[index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self._values[key][index.column()]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ---- row key lookup (for copy support) ---------------------------

    def row_key(self, row: int) -> str:
        return self._keys[row]

    def column_values(self, column: int) -> List[str]:
        """Every row's value in ``column``, in row order."""
        return [self._values[key][column] for key in self._keys]

    # ---- update --------------------------------------------------------

    def set_rows(self, items: Sequence[Tuple[str, Tuple[str, ...]]],
                 tooltips: Optional[Mapping[str, Tuple[Optional[str], ...]]] = None) -> None:
        """Replace the model's rows with ``items`` (ordered ``(key,
        values)`` pairs), touching only what changed. See module docstring
        for the diffing strategy.

        ``tooltips`` optionally gives a row its own hover text per column;
        a column left None hovers its displayed value, as every column does
        without this argument. A tooltip that changes while its value does
        not still counts as a change, or the hover would go stale."""
        new_order = [key for key, _ in items]
        new_values = dict(items)
        new_tooltips = dict(tooltips or {})

        if new_order == self._keys:
            changed_rows = [i for i, key in enumerate(new_order)
                             if self._values.get(key) != new_values[key]
                             or self._tooltips.get(key) != new_tooltips.get(key)]
            self._values = new_values
            self._tooltips = new_tooltips
            if changed_rows:
                top = self.index(min(changed_rows), 0)
                bottom = self.index(max(changed_rows), max(len(self._headers) - 1, 0))
                self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole])
            return

        self._tooltips = new_tooltips
        self._diff_update(new_order, new_values)

    def clear(self) -> None:
        if not self._keys:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._keys) - 1)
        self._keys = []
        self._values = {}
        self._tooltips = {}
        self.endRemoveRows()

    # ---- internals -------------------------------------------------------

    def _diff_update(self, new_order: List[str], new_values: Dict[str, Tuple[str, ...]]) -> None:
        keep = set(new_order)

        # 1. Remove rows that dropped out, in contiguous runs, back to front
        #    so earlier removals never shift the indices of a later run.
        i = len(self._keys) - 1
        while i >= 0:
            if self._keys[i] in keep:
                i -= 1
                continue
            j = i
            while j >= 0 and self._keys[j] not in keep:
                j -= 1
            self.beginRemoveRows(QModelIndex(), j + 1, i)
            del self._keys[j + 1:i + 1]
            self.endRemoveRows()
            i = j

        # 2. Insert rows that showed up, in contiguous runs, at their
        #    target position in new_order.
        present = set(self._keys)
        idx = 0
        while idx < len(new_order):
            if new_order[idx] in present:
                idx += 1
                continue
            j = idx
            while j < len(new_order) and new_order[j] not in present:
                j += 1
            self.beginInsertRows(QModelIndex(), idx, j - 1)
            self._keys[idx:idx] = new_order[idx:j]
            present.update(new_order[idx:j])
            self.endInsertRows()
            idx = j

        # 3. Values for every surviving/inserted row. A retained row's
        #    value may *also* have changed this same update (e.g. a new
        #    telemetry key appears the same frame an existing one's value
        #    changes) - one dataChanged over the whole remaining model
        #    covers that; the row-count change already forces the view to
        #    repaint the structural part, so this only adds the value
        #    repaint, and only on the (rare) update that changed the key set.
        self._values = new_values
        if self._keys:
            top = self.index(0, 0)
            bottom = self.index(len(self._keys) - 1, max(len(self._headers) - 1, 0))
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole])
