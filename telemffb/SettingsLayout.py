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


import inspect
import json
import logging
import os
import re

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QIcon, QFont, QColor, QPixmap
from PyQt6.QtWidgets import (QGridLayout, QLabel, QPushButton, QStyle, QMessageBox,
                             QToolButton, QCheckBox, QComboBox, QLineEdit, QFileDialog, QSpinBox, QHBoxLayout)

from telemffb.ButtonPressThread import ButtonPressThread
from telemffb.custom_widgets import (InfoLabel, NoWheelSlider, NoWheelNumberSlider, vpf_purple, t_purple, Toggle, EraseButton, NoWheelComboBox)
from telemffb.ConfiguratorDialog import ConfiguratorDialog
from telemffb.AdvancedSpringDialog import AdvancedSpringDialog
from telemffb.AdvancedGDialog import AdvancedGDialog
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.utils import validate_vpconf_profile, dbprint, HiDpiPixmap
import telemffb.utils as utils
import styles
from . import globals as G
from . import xmlutils

class SettingsLayout(QGridLayout):
    expanded_items = []
    prereq_list = []
    ##########
    # debug settings
    show_col_debug = False      # shows column, span, indent in label tooltip
    show_slider_debug = False   # set to true for slider values shown
    show_order_debug = False    # set to true for order numbers shown
    show_replaced = False
    show_settings_names = False # show setting internal name instead of displayname
    bump_up = True              # set to false for no row bumping up

    all_sliders = []
    incrvalue = 1

    # Grid column of the value/entry widgets (sliders, dropdowns, buttons).
    # Each indent level consumes one grid column to its left (expander,
    # checkbox, label), so this value also sets how deep the row layout can
    # nest: a label needs ENTRY_COL - 3 >= indent to keep a non-zero span.
    # ENTRY_COL = 6 supports up to indent 3 (e.g. Axis Control > Trim Following
    # > AP Following > child); generate_settings_row clamps anything deeper so
    # labels never collapse to zero width. The value/erase/stretch columns are
    # all derived from this, so it is the single knob for the entry position.
    ENTRY_COL = 6

    def __init__(self, parent=None, mainwindow=None):
        super(SettingsLayout, self).__init__(parent)
        self.exclusive_list = []
        self.parent_expander_dict = {}
        self.revert_targets = {}  # per-setting: value the setting resolves to without the user override
        result = None
        if G.settings_mgr.current_sim != 'nothing':
            a, b, result = xmlutils.read_single_model(G.settings_mgr.current_sim, G.settings_mgr.current_aircraft_name)

        self.mainwindow = mainwindow
        if result:
            self.build_rows(result)
        else:
            self._build_empty_notice()
        self.device = HapticEffect()
        self.setColumnMinimumWidth(self.ENTRY_COL + 2, 20)  # value column
        self.trigger_form_reload = True
        self.advanced_spring_settings = None
        self.adv_spr_dialog = None
        self.advanced_g_settings = None
        self.adv_g_dialog = None
        self.unit_previous_values = {}  # Track previous unit for conversion

    def handleScrollKeyPressEvent(self, event):
        # Forward key events to each slider in the layout
        for i in range(self.count()):
            item = self.itemAt(i)
            if isinstance(item.widget(), NoWheelSlider()):
                item.widget().handleKeyPressEvent(event)

    def append_prereq_count(self, datalist):
        for item in datalist:
            item['prereq_count'] = ''
            item['has_expander'] = ''
            for pr in self.prereq_list:
                if item['name'] == pr['prereq']:
                    item['prereq_count'] = pr['count']
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])

            if p_count > 1 or (p_count == 1 and item['hasbump'] != 'true'):
                item['has_expander'] = 'true'
            if '.0' in item['order']:
                item['has_expander'] = 'false'

    def has_bump(self, datalist):
        for item in datalist:
            item['hasbump'] = ''
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            if bumped_up:
                for b in datalist:
                    if item['prereq'] == b['name']:
                        b['hasbump'] = 'true'

    def get_parent_indent(self, datalist):
        name_map = {item['name']: item for item in datalist}

        for item in datalist:
            indent = 0
            parent_name = item.get('prereq')

            # Check if order meets the ".endswith('1')" and contains '.' condition
            order = item.get('order', '')
            skip_increment = (
                    isinstance(order, str) and
                    '.' in order and
                    order.strip().endswith('1')
            )

            while parent_name in name_map:
                parent = name_map[parent_name]

                # If parent is a group, force indent = 0 and stop
                if parent.get('datatype') == 'group':
                    break

                if not skip_increment:
                    indent += 1
                skip_increment = False  # Only skip increment for the first level

                parent_name = parent.get('prereq')

            item['indent'] = indent

    def add_expanded(self, datalist):
        for item in datalist:
            item['parent_expanded'] = ''
            for exp in self.expanded_items:
                if item['prereq'] == exp:
                    item['parent_expanded'] = 'true'
                else:
                    item['parent_expanded'] = 'false'

    def is_top_level_expanded(self, item, datalist, _visited=None):
        """
        Recursively check if the top-level parent of `item` is in self.expanded_items.

        `_visited` guards against cyclic / self-referential prereq chains. One can
        arise when a mid-chain parent is dropped during the settings merge: e.g. with
        `telemffb_controls_axes=false`, eliminate_no_prereq removes `enable_custom_x_axis`
        but leaves its child `custom_x_axis` orphaned. The substring parent lookup below
        then matches the orphan's prereq (`enable_custom_x_axis`) against the only
        surviving name that is a substring of it -- `custom_x_axis` itself -- so the row
        becomes its own parent. Without this guard that recurses until Python's limit and
        takes the whole settings form down with a RecursionError (wiping every setting).
        """
        if _visited is None:
            _visited = set()
        if item['name'] in _visited:
            # Cycle / dangling self-reference: treat as not expanded so the orphaned
            # row is simply hidden rather than crashing the form.
            return False
        _visited.add(item['name'])

        prereq = item.get('prereq', '')
        if not prereq:
            # Reached the top-level item
            if '.0' in item['order']:
                return True
            else:
                return item['name'] in self.expanded_items

        # Find the parent item by its name
        parent_item = next((x for x in datalist if x['name'] in prereq), None)

        if parent_item is None:
            # Broken link; treat as not expanded
            return False

        # Recurse upward
        return self.is_top_level_expanded(parent_item, datalist, _visited)

    def is_visible(self, datalist):
        for item in datalist:
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            iv = 'false'
            cond = ''

            if item['prereq'] == '':
                iv = 'true'
                cond = 'no prereq needed'
                pcount = 0
                if item['datatype'] == 'group':
                    for prereqs in datalist:
                        if item['name'] in prereqs['prereq']:
                            pcount += 1
                    if pcount == 0:
                        iv = 'false'
            else:
                if not self.is_top_level_expanded(item, datalist):
                    iv = 'false'
                else:
                    for p in datalist:
                        if p['name'] in item['prereq']:
                            #  check if the parent's value is part of the prereq string
                            if p['value'] in item['prereq'] or p['value'].lower() == 'true':
                                if p.get('has_expander', 'false').lower() == 'true':
                                    if p['name'] in self.expanded_items and p.get('is_visible', 'false') == 'true':
                                        iv = 'true'
                                        cond = 'item parent expanded'
                                    elif p.get('hasbump', 'false').lower() == 'true' and bumped_up:
                                        iv = 'true'
                                        cond = 'parent hasbump & bumped'
                                elif p.get('is_visible', 'false').lower() == 'true':
                                    if p.get('hasbump', 'false').lower() == 'true' and bumped_up:
                                        iv = 'true'
                                        cond = 'parent hasbump & bumped no expander par vis'
                                    if '.0' in p['order']:
                                        iv = 'true'
                            break
            if item['datatype'] == 'convert':
                iv = 'false'
            item['is_visible'] = iv

            # for things not showing debugging:
            # if iv.lower() == 'true':
            #     print (f"{item['displayname']} visible because {cond}")

    def apply_conditional_gates(self, datalist):
        """Apply the cross-tree `render_prereq` / `enable_prereq` gates.

        Unlike `prereq` (which is both tree parentage and visibility), these
        gate a setting on the current value of *other* bool settings anywhere
        in the tree:

          - `render_prereq` failing -> hide the row (is_visible = 'false').
          - `enable_prereq` failing -> keep it visible but disabled, with an
            explanatory tooltip stored on the item.

        Grammar (both fields): comma-separated tokens; `name` requires that
        setting be true, `!name` requires it false; ALL tokens must pass.
        A referenced setting absent from the resolved set is treated as false.
        Runs after is_visible() so it can override visibility, and before
        eliminate_invisible() so hidden rows are dropped.
        """
        values = {it['name']: (it.get('value') or '') for it in datalist}
        displaynames = {it['name']: (it.get('displayname') or it['name']).strip() for it in datalist}

        def evaluate(expr):
            """Return (passed, [(ref, is_true), ...failing tokens])."""
            failing = []
            for token in expr.split(','):
                token = token.strip()
                if not token:
                    continue
                want_true = not token.startswith('!')
                ref = token[1:].strip() if token.startswith('!') else token
                is_true = str(values.get(ref, 'false')).strip().lower() == 'true'
                if is_true != want_true:
                    failing.append((ref, is_true))
            return (not failing), failing

        def reason(failing):
            parts = [f"{displaynames.get(ref, ref)} is {'enabled' if is_true else 'disabled'}"
                     for ref, is_true in failing]
            return "Disabled because " + " and ".join(parts)

        for item in datalist:
            item['force_disabled'] = False
            item['disabled_reason'] = ''

            render_expr = (item.get('render_prereq') or '').strip()
            if render_expr and not evaluate(render_expr)[0]:
                item['is_visible'] = 'false'

            enable_expr = (item.get('enable_prereq') or '').strip()
            if enable_expr and item.get('is_visible', 'false').lower() == 'true':
                passed, failing = evaluate(enable_expr)
                if not passed:
                    item['force_disabled'] = True
                    item['disabled_reason'] = reason(failing)
        return datalist

    def eliminate_invisible(self, datalist):
        newlist = []
        for item in datalist:
            if item['is_visible'] == 'true':
                newlist.append(item)

        for item in newlist:
            item['prereq_count'] = '0'
            pcount = 0
            for row in newlist:
                if row['prereq'] == item['name']:
                    pcount += 1
                    if row['has_expander'].lower() == 'true':
                        pcount -= 1
            item['prereq_count'] = str(pcount)

        return newlist

    def read_active_prereqs(self, datalist):
        p_list = []
        for item in datalist:
            found = False
            count = 0
            itemname = item.get('name','')
            for p in datalist:
                prereq = p.get('prereq', '')
                if itemname in prereq:
                    if ('.' in prereq or itemname == prereq):
                        count += 1
                        found = True
                # If 'prereq' is not in the list, add a new entry
            if found:
                p_list.append({'prereq': item['name'], 'value': item['value'], 'count': count})
        return p_list

    def set_mode(self, mode, setting, datalist):
        #save the new mode to file
        for item in datalist:
            if item['name'] == setting:
                item['value'] = mode
                G.settings_mgr.write_to_xml(G.settings_mgr.current_sim,
                              G.settings_mgr.current_class,
                              G.settings_mgr.current_pattern,
                              item['value'],
                              item['name'])

    def convert_to_springmode(self, datalist):
        # read old value from xml, remap & erase old setting
        # List of (name, new_mode)
        mode_map = {
            "adv_spr_override_enabled": "ADVANCED",
            "aircraft_is_spring_centered": "CENTER",
            "aircraft_is_fbw": "FBW",
            "override_spring_enabled": "CUSTOM"
        }
        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    G.settings_mgr.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, "spring_mode", datalist)

    def convert_msfs_forcetrim_to_springmode(self, datalist):
        mode_map = {
            "force_trim_enabled": "FORCETRIM",
        }
        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    G.settings_mgr.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, "spring_mode", datalist)

    def convert_collective_to_springmode(self, datalist):
        mode_map = {
            "collective_ft_ovd_enabled": "FORCETRIM",
        }

        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    G.settings_mgr.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, "spring_mode", datalist)

    def convert_dcs_il2_pedal_to_springmode(self, datalist):
        mode_map = {
            "Dynamic Spring": "DYNAMIC",
            "Static Spring": "STATIC",
            "No Spring": "NOSPRING",
            "Sim Default": "NONE",
        }

        for item in datalist:
            if item['name'] == 'pedal_spring_mode':
                spring_mode = mode_map.get(item['value'])
                if spring_mode:
                    G.settings_mgr.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        item['name']
                    )
                    self.set_mode(spring_mode, 'spring_mode', datalist)

    def set_gforce_effect(self, mode, datalist):
        for item in datalist:
            if item['name'] == 'gforce_effect':
                item['value'] = mode
        G.settings_mgr.write_to_xml(G.settings_mgr.current_sim,
                              G.settings_mgr.current_class,
                              G.settings_mgr.current_pattern,
                              item['name'],
                              item['value'])

    def convert_to_gforcemode(self, datalist):
        mode_map = {
            'gforce_effect_enable': 'LEGACY',
            'new_gforce_effect_enable': 'NEW',
        }

        for item in datalist:
            for name, mode in mode_map.items():
                if item['name'] == name and item['value'] == 'true':
                    G.settings_mgr.erase_from_xml(
                        G.settings_mgr.current_sim,
                        G.settings_mgr.current_class,
                        G.settings_mgr.current_pattern,
                        name
                    )
                    self.set_mode(mode, "gforce_effect_mode", datalist)


    def build_rows(self, datalist):
        sorted_data = sorted(datalist, key=lambda x: float(x['order']))
        # self.prereq_list = xmlutils.read_prereqs()
        self.convert_to_springmode(sorted_data)
        self.convert_dcs_il2_pedal_to_springmode(sorted_data)
        self.convert_msfs_forcetrim_to_springmode(sorted_data)
        self.convert_collective_to_springmode(sorted_data)
        self.convert_to_gforcemode(sorted_data)
        self.prereq_list = self.read_active_prereqs(sorted_data)
        self.has_bump(sorted_data)
        self.append_prereq_count(sorted_data)
        self.add_expanded(sorted_data)
        self.is_visible(sorted_data)
        self.apply_conditional_gates(sorted_data)
        self.get_parent_indent(sorted_data)
        newlist = self.eliminate_invisible(sorted_data)

        def is_expanded(item):
            if item['name'] in self.expanded_items:
                return True
            for row in sorted_data:
                if row['name'] == item['prereq'] and is_expanded(row):
                    return True
            return False

        i = 0
        for item in newlist:
            bumped_up = item['order'][-1:] == '1' and '.' in item['order']
            rowdisabled = bool(item.get('force_disabled', False))
            addrow = False
            is_expnd = is_expanded(item)
            # print(f"{item['order']} - {item['value']} - b {bumped_up} - hb {item['hasbump']} - ex {is_expnd} - hs {item['has_expander']} - pex {item['parent_expanded']} - iv {item['is_visible']} - pcount {item['prereq_count']} - {item['displayname']} - pr {item['prereq']}")
            if item['is_visible'].lower() == 'true':
                i += 1
                if bumped_up:
                    if self.bump_up:  # debug
                        i -= 1   # bump .1 setting onto the enable row
                self.generate_settings_row(item, i, rowdisabled)

        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)
        self.addItem(spacerItem, i+1, 1, 1, 1)

        # set expander column minimum size so it does not shrink and shift layout when no expanders are visible
        self.setColumnMinimumWidth(0, 30)

        # Give entry column a high stretch factor, all others remain default 0.
        # When window is resized, the entry column will grow to take up all the new space
        self.setColumnStretch(self.ENTRY_COL, 10)

        # print (f"{i} rows with {self.count()} widgets")

    def reload_caller(self):
        # caller_frame = inspect.currentframe().f_back
        # caller_name = caller_frame.f_code.co_name
        # dbprint("blue", f"RELOAD_CALLER was called by {caller_name}")
        if G.master_instance and G.device_type != G.current_device_config_scope and G.settings_mgr.timed_out:
            G.ipc_instance.send_broadcast_message(f'RELOAD CALLER:{G.current_device_config_scope}')
        if not self.trigger_form_reload:
            self.trigger_form_reload = True  # reset back to true for next iteration
        else:
            self.reload_layout(None)

    def reload_layout(self, result=None):
        # stack = inspect.stack()
        # for frame_info in stack:
        #     dbprint("green", f"Function {frame_info.function} in {frame_info.filename} at line {frame_info.lineno}")
        # Remember where the user was looking so the rebuilt form lands back at the
        # same row, instead of drifting by the pixel height of whatever changed above.
        scroll_anchor = self._capture_scroll_anchor()
        self.clear_layout(show_empty_notice=False)
        # Clear unit tracking when reloading layout
        self.unit_previous_values = {}
        self.revert_targets = {}
        if result is None:
            cls, pat, result = xmlutils.read_single_model(G.settings_mgr.current_sim, G.settings_mgr.current_aircraft_name, G.settings_mgr.current_class)
            G.settings_mgr.current_pattern = pat
        if result:
            self.build_rows(result)
            # Restore once the event loop has applied the freshly built geometry.
            QtCore.QTimer.singleShot(0, lambda: self._restore_scroll_anchor(scroll_anchor))
        else:
            self._build_empty_notice()

    def _settings_scroll_area(self):
        """The QScrollArea hosting this settings layout, or None when it is not
        reachable (headless child instance, teardown, detached-tab edge cases)."""
        mw = getattr(self, 'mainwindow', None)
        if mw is None:
            return None
        return getattr(mw, 'settings_area', None)

    def _capture_scroll_anchor(self):
        """Before a rebuild, pick a setting row to keep visually pinned and record
        where it currently sits in the viewport.

        Hybrid choice of anchor:
          1. the row under the mouse cursor -- the one being edited, i.e. "keep it
             where my mouse is";
          2. failing that (cursor not over the form), the top-most row visible in
             the viewport.

        Returns (setting_name, viewport_y) or None if there is nothing to anchor to.
        The raw scrollbar value is deliberately NOT used: it is a pixel offset, so
        any change in the height of content above the viewport shifts every row.
        """
        try:
            area = self._settings_scroll_area()
            if area is None:
                return None
            viewport = area.viewport()
            rows = []
            for i in range(self.count()):
                w = self.itemAt(i).widget()
                if w is None or not w.isVisible():
                    continue
                obj = w.objectName()
                if not obj.startswith('namelabel_'):
                    continue
                y = w.mapTo(viewport, QtCore.QPoint(0, 0)).y()
                rows.append((obj[len('namelabel_'):], y))
            if not rows:
                return None
            vp_h = viewport.height()
            cur = viewport.mapFromGlobal(QCursor.pos())
            if 0 <= cur.x() <= viewport.width() and 0 <= cur.y() <= vp_h:
                # The row the cursor is in = greatest label-top not below the cursor.
                at_or_above = [r for r in rows if r[1] <= cur.y()]
                if at_or_above:
                    return max(at_or_above, key=lambda r: r[1])
            # Fallback: the row closest to the top edge of the viewport.
            in_view = [r for r in rows if 0 <= r[1] <= vp_h]
            return min(in_view or rows, key=lambda r: abs(r[1]))
        except Exception:
            logging.exception("scroll-anchor capture failed")
            return None

    def _restore_scroll_anchor(self, anchor, _prev_y=None, _tries=0):
        """After the rebuild, scroll so the anchored setting is back at the same
        viewport offset it had before. No-op if the setting is gone (e.g. it was
        hidden by the very change that triggered the reload).

        Freshly built rows are not positioned synchronously: Qt performs the grid
        layout on a later event-loop pass, and until it does a new row's
        mapTo(content) reports y=0 (activate()/adjustSize() do NOT force it). If we
        committed the scroll then, the target would clamp to the wrong row -- the
        "jumps a row" bug, which only surfaced once the form was tall enough that
        the correct offset was non-zero. So re-post via singleShot until the
        measured position is stable across two passes, then set the scrollbar
        exactly once: correct, and without a visible flicker to a wrong spot.
        """
        if not anchor:
            return
        try:
            name, target_y = anchor
            area = self._settings_scroll_area()
            if area is None:
                return
            content = area.widget()
            if content is None:
                return
            target = None
            for i in range(self.count()):
                w = self.itemAt(i).widget()
                if w is not None and w.objectName() == f'namelabel_{name}':
                    target = w
                    break
            if target is None or not target.isVisible():
                return
            new_y = target.mapTo(content, QtCore.QPoint(0, 0)).y()
            if new_y != _prev_y and _tries < 8:
                # Layout not settled yet -- wait one more pass before committing.
                QtCore.QTimer.singleShot(
                    0, lambda: self._restore_scroll_anchor(anchor, new_y, _tries + 1))
                return
            area.verticalScrollBar().setValue(new_y - target_y)
        except Exception:
            logging.exception("scroll-anchor restore failed")

    def _build_empty_notice(self):
        """Populate the (empty) layout with guidance instead of leaving the
        Settings tab an unexplained blank when there is nothing to edit."""
        notice = QLabel()
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setWordWrap(True)
        if G.settings_mgr.offline_mode:
            notice.setText(
                "<h3>Offline editing mode</h3>"
                "<p>Use the <i>Offline Editor Setup</i> panel above to choose what to edit — "
                "the settings will appear here. The scope you select determines how widely "
                "your changes apply:</p>"
                "<ul>"
                "<li><b>Sim only</b> — changes apply to every class and aircraft within that "
                "sim, unless overridden at a lower level.</li>"
                "<li><b>Sim and class</b> — changes apply to every aircraft of that class "
                "within the sim, unless overridden for a specific aircraft.</li>"
                "<li><b>Specific aircraft</b> — also select the profile you wish to edit; "
                "changes apply to that aircraft/profile only.</li>"
                "</ul>")
        else:
            text = (
                "<h3>No aircraft loaded</h3>"
                "<p>Settings are per-aircraft: start one of your enabled sims and load an "
                "aircraft, and its settings will appear here automatically.</p>")
            if G.master_instance:
                text += (
                    "<p>To view or edit profiles without running a sim, open the offline editor "
                    "(<i>Profiles &rarr; Offline Profile\\Sim Default\\Class Default Mode</i>).</p>")
            notice.setText(text)
        self.addWidget(notice, 0, 1, 1, 6)
        if not G.settings_mgr.offline_mode and G.master_instance and self.mainwindow is not None:
            btn = QPushButton('Open Offline Editor')
            btn.clicked.connect(lambda checked=False: self.mainwindow.toggle_offline_mode(True))
            self.addWidget(btn, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        spacer = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)
        self.addItem(spacer, 2, 1)
        self.setColumnStretch(self.ENTRY_COL, 10)

    def clear_layout(self, show_empty_notice=True):
        layout = self.layout()
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                else:
                    sub_layout = item.layout()
                    if sub_layout:
                        self._clear_sub_layout(sub_layout)
                        sub_layout.deleteLater()
        # Direct clears (sim exit, offline transitions) leave the tab empty;
        # show the guidance notice rather than a blank page. reload_layout
        # passes False because it decides rows-vs-notice itself.
        if show_empty_notice:
            self._build_empty_notice()

    def _clear_sub_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout:
                    self._clear_sub_layout(sub_layout)
                    sub_layout.deleteLater()

    def do_move_to_class(self, csim, cclass, value, setting, model, unit):

        reply = QMessageBox.question(
            None,
            "Confirmation",
            f"Are you sure you want to move {setting} ({value}) from {model} to all {csim} {cclass} class aircraft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No  # Default button
        )

        if reply == QMessageBox.StandardButton.Yes:
            logging.info(f"Moving {setting} setting ({value}) from {model} to all {csim} {cclass} class aircraft")
            xmlutils.write_class_to_xml(csim, cclass, value, setting, unit)
            xmlutils.erase_models_from_xml(csim, model, setting)

    def do_move_to_sim(self, csim, value, setting, model, unit):

        reply = QMessageBox.question(
            None,
            "Confirmation",
            f"Are you sure you want to move {setting} ({value}) from {model} to ALL {csim} aircraft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No  # Default button
        )
        if reply == QMessageBox.StandardButton.Yes:
            logging.info(f"Moving {setting} setting ({value}) from {model} to {csim} Sim for all aircraft")
            xmlutils.write_sim_to_xml(csim,value,setting, unit)
            xmlutils.erase_models_from_xml(csim,model,setting)

    def generate_settings_row(self, item, i,  rowdisabled=False ):
        self.setRowMinimumHeight(i, 25)
        entry_colspan = 2
        lbl_colspan = 3

        exp_col = 0
        chk_col = 1
        lbl_col = 2
        entry_col = self.ENTRY_COL
        unit_col = entry_col + 1
        val_col = unit_col + 1
        erase_col = val_col + 1
        fct_col = 10
        ord_col = 11

        validvalues = item['validvalues'].split(',')

        if self.show_order_debug:
            order_lbl = QLabel()
            order_lbl.setText(item['order'])
            order_lbl.setMaximumWidth(45)
            self.addWidget(order_lbl, i, ord_col)

        if self.show_replaced:
            order_lbl = QLabel()
            order_lbl.setText(item['replaced'])
            order_lbl.setMaximumWidth(70)
            self.addWidget(order_lbl, i, ord_col)

        # right click erase button to move the setting up in hierarchy
        show_move_menu = True if item['replaced'] == 'Model (user)' else False
        erase_button = EraseButton(csim=G.settings_mgr.current_sim,
                                   cclass=G.settings_mgr.current_class,
                                   cmodel=G.settings_mgr.current_pattern,
                                   csetting=item['name'],
                                   cvalue=item['value'],
                                   cunit=item['unit'],
                                   enable_context_menu=show_move_menu) # Create erase button at beginning so behavior can be modified per widget type if necessary
        if show_move_menu:
            erase_button.move_to_class_signal.connect(self.do_move_to_class)
            erase_button.move_to_sim_signal.connect(self.do_move_to_sim)

        # everything has a name, except for things that have a checkbox *and* slider
        # some labels show Move right click menu

        label = InfoLabel(text=f"{item['name']}") if self.show_settings_names else (
            InfoLabel(text=f"{item['displayname']}"))

        label.setObjectName(f'namelabel_{item["name"]}')
        label.setToolTip(item['info'])
        label.setMinimumHeight(20)
        label.setMinimumWidth(20)

        #determine indentation

        if item['datatype'] == 'group':
            lbl_col = 0
            item['indent'] = -1
        else:
            # Each indent level shifts expander/checkbox/label one grid column
            # right. Clamp so the label column can never reach entry_col (which
            # would leave the label a zero-width, mis-rendered cell). Nesting
            # deeper than the grid can show still reads via the leading-space
            # indentation baked into the displayname.
            eff_indent = min(item['indent'], entry_col - 3)
            exp_col = eff_indent
            chk_col = exp_col + 1
            lbl_col = chk_col + 1
        lbl_colspan = max(1, entry_col - lbl_col)


        # booleans get a checkbox
        if item['datatype'] == 'bool':
            # print(f"Exclusive: >{item['exclusive_with']}<")
            if item['exclusive_with'] != '':
                pair = [item['name'],item['exclusive_with']]
                if not pair in self.exclusive_list:
                    self.exclusive_list.append(pair)
            # print(item)
            checkbox = Toggle(
                checked_color=vpf_purple,
                bar_color=t_purple
            )

            # checkbox = AnimatedToggle(
            #     checked_color="#ab37c8",
            #     pulse_checked_color="#44ab37c8"
            # )
            checkbox.setMaximumSize(QtCore.QSize(45, 30))
            checkbox.setMinimumSize(QtCore.QSize(45, 30))
            checkbox.setObjectName(f"cb_{item['name']}")
            # checkbox.blockSignals(True)
            if item['value'].lower() == 'false':
                checkbox.setChecked(0)
                rowdisabled = True
            else:
                checkbox.setChecked(2)
            checkbox.blockSignals(False)

            if self.show_col_debug:
                checkbox.setToolTip(f"{chk_col}")
            self.addWidget(checkbox, i, chk_col)
            checkbox.stateChanged.connect(lambda state, name=item['name']: self.checkbox_changed(name, state))
            if item.get('force_disabled'):
                # enable_prereq: the toggle is inert (another setting overrides
                # it), so disable it too — rowdisabled only greys value widgets.
                checkbox.setDisabled(True)

        if item['unit'] is not None and item['unit'] != '':
            entry_colspan = 1
            unit_dropbox = NoWheelComboBox()
            unit_dropbox.blockSignals(True)
            if item['unit'] == 'hz':
                unit_dropbox.addItem('hz')
                unit_dropbox.setCurrentText('hz')
            elif item['unit'] == 'deg':
                unit_dropbox.addItem('deg')
                unit_dropbox.setCurrentText('deg')
            else:
                unit_dropbox.addItems(validvalues)
                unit_dropbox.setCurrentText(item['unit'])
            unit_dropbox.setObjectName(f"ud_{item['name']}")
            unit_dropbox.currentIndexChanged.connect(self.unit_dropbox_changed)
            self.addWidget(unit_dropbox, i, unit_col)
            unit_dropbox.blockSignals(False)
            unit_dropbox.setDisabled(rowdisabled)
            
            # Store the initial unit value for conversion tracking
            self.unit_previous_values[item['name']] = item['unit']


        if item['order'][-1:] == '1' and '.' in item['order']:
            olditem = self.itemAtPosition(i, lbl_col)
            if olditem is not None:
                self.remove_widget(olditem)

        cdb = f"{lbl_col} {lbl_colspan} ind:{item['indent']} " if self.show_col_debug else ''
        tip = item['info']
        if item.get('force_disabled') and item.get('disabled_reason'):
            # While disabled, show ONLY the reason: the config info is moot when
            # the setting is inert, and appending it (plain text) alongside the
            # info's HTML <br> tags breaks Qt's tooltip rich-text detection so
            # the <br>s render literally.
            tip = item['disabled_reason']
        label.setToolTip(f"{cdb}{tip}")
        self.addWidget(label, i, lbl_col, 1, lbl_colspan)

        slider = NoWheelSlider()
        slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        slider.setObjectName(f"sld_{item['name']}")

        n_slider = NoWheelNumberSlider()
        n_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        n_slider.setObjectName(f"sld_{item['name']}")

        d_slider = NoWheelSlider()
        d_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        d_slider.setObjectName(f"dsld_{item['name']}")

        df_slider = NoWheelSlider()
        df_slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        df_slider.setObjectName(f"dfsld_{item['name']}")

        m_butt = QPushButton("-")
        m_butt.setProperty('buttonType', 'p_m_button')
        m_butt.setFixedSize(20, 20)

        # Create the "+" button
        p_butt = QPushButton("+")
        p_butt.setProperty('buttonType', 'p_m_button')
        p_butt.setFixedSize(20, 20)

        line_edit = QLineEdit()
        line_edit.blockSignals(True)
        # unit?
        line_edit.setText(item['value'])
        line_edit.blockSignals(False)
        line_edit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        line_edit.setObjectName(f"vle_{item['name']}")
        line_edit.setMinimumWidth(150)
        line_edit.editingFinished.connect(self.line_edit_changed)

        expand_button = QToolButton()
        expand_button.setProperty('buttonType', 'expand_button')
        if item['name'] in self.expanded_items:
            expand_button.setArrowType(Qt.ArrowType.DownArrow)
        else:
            expand_button.setArrowType(Qt.ArrowType.RightArrow)
        expand_button.setMaximumWidth(24)
        expand_button.setMinimumWidth(24)
        expand_button.setObjectName(f"ex_{item['name']}")
        expand_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        expand_button.clicked.connect(self.expander_clicked)

        usb_button_text = f"Button {item['value']}"
        if item['value'] == '0':
            usb_button_text = 'Click to Configure'
        self.usbdevice_button = QPushButton(usb_button_text)
        self.usbdevice_button.setMinimumWidth(150)
        self.usbdevice_button.setObjectName(f"pb_{item['name']}")
        self.usbdevice_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.usbdevice_button.clicked.connect(self.usb_button_clicked)

        value_label = QLabel()
        value_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        value_label.setMaximumWidth(50)
        value_label.setObjectName(f"vl_{item['name']}")
        sliderfactor = QLabel(f"{item['sliderfactor']}")
        if self.show_slider_debug:
            sliderfactor.setMaximumWidth(20)
        else:
            sliderfactor.setMaximumWidth(0)
        sliderfactor.setObjectName(f"sf_{item['name']}")

        if item['datatype'] == 'float' or item['datatype'] == 'negfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")
            factor = float(item['sliderfactor'])
            if '%' in item['value']:
                floatval = float(item['value'].replace('%', ''))
                val = floatval / 100
            else:
                val = float(item['value'])

            pctval = int((val / factor) * 100)
            if self.show_slider_debug:
                logging.debug(f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')

            slider.valueChanged.connect(lambda value: self.slider_changed(write=False))  # update label on every change
            slider.delayedValueChanged.connect(lambda value: self.slider_changed(write=True))  # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(slider.decrease_single_step)
            p_butt.clicked.connect(slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col, alignment=Qt.AlignmentFlag.AlignVCenter)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)
        if item['datatype'] == 'pct_float':
            # stored value is decimal fraction (e.g., 0.043)
            d_val = float(item['value'])
            factor = float(item['sliderfactor'])  # e.g., 0.1  (so 1 step = 0.1%)
            # slider position is the percent value divided by factor
            # (0.043 * 100) / 0.1 = 43.0 → shows 43.0% on the slider
            val = float(round((d_val * 100.0) / factor))

            dfs = df_slider  # reuse your existing "direct float" slider instance for this row
            dfs.blockSignals(True)
            if validvalues and validvalues != '':
                dfs.setRange(int(validvalues[0]), int(validvalues[1]))  # e.g., 0..15  (percent domain)
            dfs.setValue(round(val))

            # label shows percent with 3 decimals
            value_label.setText(f"{d_val * 100.0:.2f}%")

            dfs.valueChanged.connect(lambda _v: self.pct_df_slider_changed(write=False))
            dfs.delayedValueChanged.connect(lambda _v: self.pct_df_slider_changed(write=True))

            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(dfs.decrease_single_step)
            p_butt.clicked.connect(dfs.increase_single_step)
            sl_layout.addWidget(m_butt);
            sl_layout.addWidget(dfs);
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)
            dfs.blockSignals(False)

        if item['datatype'] == 'n_float' :

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")
            factor = float(item['sliderfactor'])
            if '%' in item['value']:
                floatval = float(item['value'].replace('%', ''))
                val = floatval / 100
            else:
                val = float(item['value'])

            pctval = int((val / factor) * 100)
            if self.show_slider_debug:
                logging.debug(f"read value: {item['value']}  factor: {item['sliderfactor']} slider: {pctval}")
            n_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                n_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            n_slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            # value_label.setToolTip(f"Actual Value: %{int(val * 100)}")
            n_slider.valueChanged.connect(lambda value: self.slider_changed(write=False))  # update label on every change
            n_slider.delayedValueChanged.connect(lambda value: self.slider_changed(write=True)) # only write config when delay timer triggers
            # n_slider.sliderPressed.connect(self.sldDisconnect)
            # n_slider.sliderReleased.connect(self.sldReconnect)
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(n_slider.decrease_single_step)
            p_butt.clicked.connect(n_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(n_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col, alignment=Qt.AlignmentFlag.AlignVCenter)
            self.addWidget(sliderfactor, i, fct_col)

            n_slider.blockSignals(False)

        if item['datatype'] == 'd_float':

            d_val = float(item['value'])
            factor = float(item['sliderfactor'])
            val = float(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   df_slider: {val}")
            df_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                df_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            df_slider.setValue(round(val))
            value_label.setText(str(d_val))
            df_slider.valueChanged.connect(lambda value: self.df_slider_changed(write=False))  # update label on every change
            df_slider.delayedValueChanged.connect(lambda value: self.df_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(df_slider.decrease_single_step)
            p_butt.clicked.connect(df_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(df_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)
            df_slider.blockSignals(False)

        if item['datatype'] == 'cfgfloat':

            # print(f"label {value_label.objectName()} for slider {slider.objectName()}")

            if '%' in item['value']:
                pctval = int(item['value'].replace('%', ''))
            else:
                pctval = int(float(item['value']) * 100)
            pctval = int(round(pctval))
            # print (f"configurator value: {item['value']}   slider: {pctval}")
            slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                slider.setRange(int(validvalues[0]), int(validvalues[1]))
            slider.setValue(pctval)
            value_label.setText(str(pctval) + '%')
            slider.valueChanged.connect(lambda value: self.cfg_slider_changed(write=False))  # update label on every change
            slider.delayedValueChanged.connect(lambda value: self.cfg_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(slider.decrease_single_step)
            p_butt.clicked.connect(slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            slider.blockSignals(False)

        if item['datatype'] == 'd_int':

            d_val = int(item['value'])
            factor = float(item['sliderfactor'])
            val = int(round(d_val / factor))
            if self.show_slider_debug:
                print(f"read value: {item['value']}   d_slider: {val}")
            d_slider.blockSignals(True)
            if validvalues is None or validvalues == '':
                pass
            else:
                d_slider.setRange(int(validvalues[0]), int(validvalues[1]))
            d_slider.setValue(val)
            value_label.setText(str(d_val))
            d_slider.valueChanged.connect(lambda value: self.d_slider_changed(write=False))  # update label on every change
            d_slider.delayedValueChanged.connect(lambda value: self.d_slider_changed(write=True)) # only write config when delay timer triggers
            sl_layout = QHBoxLayout()
            m_butt.clicked.connect(d_slider.decrease_single_step)
            p_butt.clicked.connect(d_slider.increase_single_step)
            sl_layout.addWidget(m_butt)
            sl_layout.addWidget(d_slider)
            sl_layout.addWidget(p_butt)
            self.addLayout(sl_layout, i, entry_col, 1, entry_colspan)
            self.addWidget(value_label, i, val_col)
            self.addWidget(sliderfactor, i, fct_col)

            d_slider.blockSignals(False)

        if item['datatype'] == 'text':
            textbox = QLineEdit()
            textbox.setObjectName(f"le_{item['name']}")
            textbox.setText(item['value'])
            textbox.editingFinished.connect(self.textbox_changed)
            self.addWidget(textbox, i, entry_col, 1, entry_colspan)

        if item['datatype'] == 'spin_int':
            spin_box = QSpinBox()
            spin_box.blockSignals(True)
            spin_box.setValue(int(item['value']))
            spin_box.blockSignals(False)
            spin_box.setMinimumWidth(80)
            spin_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            spin_box.setObjectName(f"sb_{item['name']}")
            if validvalues is None or validvalues == '':
                pass
            else:
                spin_box.setMinimum(int(validvalues[0]))
                spin_box.setMaximum(int(validvalues[1]))
            spin_box.valueChanged.connect(self.spin_box_changed)
            self.addWidget(spin_box, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] in ('list', 'anylist', 'enumlist'):
            dropbox = NoWheelComboBox()

            if item['datatype'] == 'anylist':
                dropbox.setEditable(True)
                dropbox.lineEdit().setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            # else:
            #     dropbox = CenteredClickableComboBox()
            dropbox.setMinimumWidth(150)
            dropbox.blockSignals(True)

            if item['datatype'] == 'enumlist':
                dropbox.setObjectName(f"edb_{item['name']}")

                label_dict_name = item['validvalues']
                label_dict = getattr(G.settings_mgr, label_dict_name, None)

                if not isinstance(label_dict, dict):
                    dropbox.addItem(f"Label dict '{label_dict_name}' not found or invalid")
                else:
                    try:
                        enum_class = type(next(iter(label_dict)))
                    except StopIteration:
                        dropbox.addItem("Label dictionary is empty")
                        enum_class = None

                    for enum_member, the_label in label_dict.items():
                        dropbox.addItem(the_label, enum_member)

                    if enum_class and item['value']:
                        try:
                            enum_member = enum_class[item['value']]
                            for index in range(dropbox.count()):
                                if dropbox.itemData(index) == enum_member:
                                    dropbox.setCurrentIndex(index)
                                    break
                        except (KeyError, ValueError):
                            for index in range(dropbox.count()):
                                if dropbox.itemText(index) == item['value']:
                                    dropbox.setCurrentIndex(index)
                                    enum_member = dropbox.itemData(index)
                                    if enum_member:
                                        item['value'] = enum_member.name
                                    break
                            else:
                                dropbox.addItem(f"Invalid enum value: {item['value']}")


            else:
                dropbox.setObjectName(f"db_{item['name']}")
                dropbox.addItems(validvalues)
                dropbox.setCurrentText(item['value'])

            if item['name'] == 'type' and 'Default' in item['replaced']:
                # block editing of type for default craft
                # dropbox.lineEdit().removeEventFilter(dropbox) # Disable expand of items when clicking on lineEdit area (req'd for custom widget type)
                dropbox.setDisabled(True)
            else:
                if item['datatype'] == 'anylist':
                    # dropbox.lineEdit().setReadOnly(True)
                    dropbox.lineEdit().setObjectName(f"db_{item['name']}")
                    dropbox.lineEdit().editingFinished.connect(self.dropbox_changed)
                else:
                    dropbox.currentTextChanged.connect(self.dropbox_changed)
                dropbox.blockSignals(False)
            self.addWidget(dropbox, i, entry_col, 1, entry_colspan)
            # dropbox.currentTextChanged.connect(self.dropbox_changed)

        if item['datatype'] == 'path':
            self.vpconf_browse_button = QPushButton()
            self.vpconf_browse_button.blockSignals(True)
            self.vpconf_browse_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.vpconf_browse_button.setMinimumWidth(150)
            self.vpconf_browse_button.setObjectName('path_vpconf')
            if item['value'] == '-':
                self.vpconf_browse_button.setText('Browse...')
            else:
                fname = os.path.basename(item['value'])
                button_text = fname
                self.vpconf_browse_button.setToolTip(item['value'])
                # p_length = len(item['value'])
                # if p_length > 45:
                #     button_text = f"{button_text[:40]}...{button_text[-25:]}"
                self.vpconf_browse_button.setText(button_text)
            self.vpconf_browse_button.blockSignals(False)
            self.vpconf_browse_button.setMaximumHeight(25)
            self.vpconf_browse_button.clicked.connect(self.browse_for_config)
            self.addWidget(self.vpconf_browse_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'int' or item['datatype'] == 'anyfloat':
            self.addWidget(line_edit, i, entry_col, 1, entry_colspan)

        if item['datatype'] == 'button':
            self.addWidget(self.usbdevice_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'advgs':
            self.advanced_g_settings = item['value']
            b_txt = 'Configure Settings' if item['value'] == "none" else "Edit Settings"
            # print(f"ADVANCED SPRING {self.advanced_spring_settings}")
            self.adv_g_button = QPushButton(b_txt)
            self.adv_g_button.setMinimumWidth(150)
            self.adv_g_button.setMinimumHeight(25)
            self.adv_g_button.setObjectName(f"advspr_{item['name']}")
            self.adv_g_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.adv_g_button.clicked.connect(lambda: self.advanced_g_button_clicked(self.advanced_g_settings))
            self.addWidget(self.adv_g_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'advspr':
            self.advanced_spring_settings = item['value']
            b_txt = 'Configure Settings' if item['value'] == "none" else "Edit Settings"
            # print(f"ADVANCED SPRING {self.advanced_spring_settings}")
            self.adv_spr_button = QPushButton(b_txt)
            self.adv_spr_button.setMinimumWidth(150)
            self.adv_spr_button.setMinimumHeight(25)
            self.adv_spr_button.setObjectName(f"advspr_{item['name']}")
            self.adv_spr_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.adv_spr_button.clicked.connect(lambda: self.advanced_spring_button_clicked(self.advanced_spring_settings))
            self.addWidget(self.adv_spr_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'trimcal':
            b_txt = 'Calibrate...' if item['value'] == "none" else "View / Recalibrate..."
            self.trim_cal_button = QPushButton(b_txt)
            self.trim_cal_button.setMinimumWidth(150)
            self.trim_cal_button.setMinimumHeight(25)
            self.trim_cal_button.setObjectName(f"trimcal_{item['name']}")
            self.trim_cal_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self.trim_cal_button.clicked.connect(self.trim_cal_button_clicked)
            self.addWidget(self.trim_cal_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)

        if item['datatype'] == 'configurator':
            self.configurator_button = QPushButton("Configure Gain Overrides")

            try:
                gaindict = json.loads(item['value'])
                self.configurator_button = QPushButton("Edit Gain Overrides")
            except:
                pass

            self.configurator_button.setMinimumWidth(150)
            self.configurator_button.setMaximumHeight(25)
            self.configurator_button.setObjectName(f"config_{item['name']}")
            self.configurator_button.setCursor(QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            cfg_value = item['value']

            try:
                self.configurator_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass

            self.configurator_button.clicked.connect(self.configurator_button_clicked)
            erase_button.clicked.connect(self.erase_configurator_overrides)
            self.addWidget(self.configurator_button, i, entry_col, 1, entry_colspan, alignment=Qt.AlignmentFlag.AlignLeft)



        if item['has_expander'].lower() == 'true':
            self.addWidget(expand_button, i, exp_col)
            expand_button.setHidden(rowdisabled)

        if not rowdisabled:
            # for p_item in self.prereq_list:
            #     if p_item['prereq'] == item['name'] : # and p_item['count'] > 1:
            p_count = 0
            if item['prereq_count'] != '':
                p_count = int(item['prereq_count'])
            if self.show_col_debug:
                expand_button.setToolTip(f"{exp_col}")
            if item['has_expander'].lower() == 'true':
                if item['name'] in self.expanded_items:
                    row_count = p_count
                    if item['hasbump'].lower() != 'true':
                        row_count += 1
                    expand_button.setMaximumHeight(200)
        # grouping collapsible header
        if item['datatype'] == 'group':
            expand_button.setVisible(False)


        # Keep the InfoLabel enabled when force-disabled by enable_prereq so its
        # info-icon tooltip still shows the reason on hover (disabled widgets
        # swallow tooltip events); grey its text via stylesheet instead so it
        # still reads as disabled. Normal (bool-off) rows disable it outright.
        if item.get('force_disabled'):
            label.setTextStyleSheet("color: #808080;")
        else:
            label.setDisabled(rowdisabled)
        slider.setDisabled(rowdisabled)
        d_slider.setDisabled(rowdisabled)
        df_slider.setDisabled(rowdisabled)
        line_edit.setDisabled(rowdisabled)
        expand_button.setDisabled(rowdisabled)
        # The +/- step buttons and value readout weren't disabled, so a
        # "disabled" slider was still nudge-able and the value looked live.
        m_butt.setDisabled(rowdisabled)
        p_butt.setDisabled(rowdisabled)
        value_label.setDisabled(rowdisabled)
        if rowdisabled:
            # Qt's default disabled frame looks bad on these borderless +/-
            # glyphs; keep them borderless and just grey the glyph colour.
            _pm_disabled_css = ('QPushButton[buttonType="p_m_button"] { color: #808080; '
                                'border: none; background-color: transparent; }')
            m_butt.setStyleSheet(_pm_disabled_css)
            p_butt.setStyleSheet(_pm_disabled_css)

        self.parent().parent().parent().addSlider(slider)
        self.parent().parent().parent().addSlider(d_slider)
        self.parent().parent().parent().addSlider(df_slider)

        # erase_button = QToolButton()
        icon = QIcon()
        pixmap = HiDpiPixmap(":/image/delete_button.png")
        #pixmap = pixmap.scaled(15, 15, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon.addPixmap(pixmap)

        # Create the erase button

        erase_button.setObjectName(f"eb_{item['name']}")
        erase_button.setIcon(icon)
        erase_button.setToolTip("")
        erase_button.clicked.connect(lambda _, name=item['name']: self.erase_setting(name))
        sp_retain = erase_button.sizePolicy()
        sp_retain.setRetainSizeWhenHidden(True)
        erase_button.setSizePolicy(sp_retain)
        erase_button.setVisible(False)
        #
        # create info icon for later use
        info_icon = QIcon()
        info_pixmap = HiDpiPixmap(":/image/info_icon.png")
        info_pixmap = info_pixmap.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        info_icon.addPixmap(info_pixmap)
        info_label = QLabel()
        info_label.setPixmap(info_pixmap)
        info_label.setVisible(False)
        # info_label.setMaximumSize(25, 25)
        # info_label.setMinimumSize(25, 25)

        replace_scope = "model" + " (user)" if not G.settings_mgr.offline_mode else G.settings_mgr.offline_scope.lower() + ' (user)'
        action_item = erase_button
        include_action = False
        if item['replaced'].lower() == replace_scope.lower():
            if item['name'] != 'type':  # dont erase type on mainwindow settings
                logging.debug(f"show erase {G.settings_mgr.offline_scope}")
                erase_button.setToolTip("Reset to Default, Right-Click for more options")
                action_item = erase_button
                include_action = True

        if item['replaced'].lower() == "class (user)" and (not G.settings_mgr.offline_mode or G.settings_mgr.offline_scope.lower() != "class"):
            if item['name'] != 'type':
                action_item = info_label
                info_label.setToolTip("Class level user override is in use")
                info_label.setEnabled(False)
                include_action = True

        elif item['replaced'].lower() == "sim (user)"  and (not G.settings_mgr.offline_mode or G.settings_mgr.offline_scope.lower() != "sim"):
            if item['name'] != 'type':
                action_item = info_label
                info_label.setToolTip("Sim level user override is in use")
                info_label.setEnabled(False)
                include_action = True

        self.addWidget(action_item, i, erase_col, alignment=Qt.AlignmentFlag.AlignCenter)

        if include_action:
            action_item.setVisible(True)

        # Remember what this setting would revert to if the user-scope override
        # were erased, so write_or_revert() can auto-erase the override when the
        # user dials the value back to it. Overridden rows revert to the value
        # the merge stashed beneath the override; clean rows "revert" to their
        # current value (writing that back would just create a redundant override).
        # Only the action_item widget is in the layout, so when the row shows the
        # sim/class override info icon instead of the erase button, toggling the
        # icon requires a form rebuild - record which one this row got.
        eb_in_layout = action_item is erase_button
        if item['replaced'].lower() == replace_scope.lower():
            self.revert_targets[item['name']] = (item.get('prior_value'), item.get('prior_unit', ''), True, eb_in_layout)
        else:
            self.revert_targets[item['name']] = (item['value'], item['unit'], False, eb_in_layout)

        self.setRowStretch(i, 0)


        if item['has_expander'].lower() == 'true' or item['datatype'] == 'group':

            labeltext = item["name"] if self.show_settings_names else item["displayname"]
            if '.0' not in item['order']:
                # label.text_label.setText(f'<a href="#" style="color: {color};">{labeltext}</a>')
                can_expand = item['name'] not in self.expanded_items
                clickaction = "Expand" if can_expand else "Collapse"
                label.text_label.setToolTip(f'Click to {clickaction}')
                label.setClickable(True)
                label.clicked.connect(expand_button.click)
                if item['datatype'] == 'group':
                    label.text_label.setStyleSheet(styles.GROUP_LABEL_STYLESHEET)
                    symbol = "►" if can_expand else "▼"
                    label.text_label.setText(f"{symbol} {labeltext}")

                else:
                    label.text_label.setStyleSheet(styles.EXPAND_LABEL_STYLESHEET)

            else:
                label.text_label.setStyleSheet(styles.LOCKED_GROUP_LABEL_STYLESHEET)

        if item['order'][-1:] == '1' and '.' in item['order']:
            # For ".1" config objects, they take the place of the parent setting in the row when enabled so #we
            # need to find the expander button from the prerequisite object
            parent = item['prereq']
            parent_expand_button = self.mainwindow.findChild(QToolButton, f"ex_{parent}")
            if parent_expand_button is not None:
                # label.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                # label.text_label.setOpenExternalLinks(False)
                labeltext = item["name"] if self.show_settings_names else item["displayname"]
                color = "#cc7ee0" if G.useDarkMode else "#ab37c8"
                # label.text_label.setText(f'<a href="#" style="color: {color};">{labeltext}</a>')
                label.text_label.setStyleSheet(styles.EXPAND_LABEL_STYLESHEET)
                label.setClickable(True)
                label.clicked.connect(lambda parent_name=parent: self.expander_hyperlink_clicked(parent_name))

    def expander_hyperlink_clicked(self, parent):
        # dbprint("red", f"EXPANDER: {parent}")
        parent_expand_button = self.mainwindow.findChild(QToolButton, f"ex_{parent}")
        if parent_expand_button is not None:
            parent_expand_button.click()

    # expander.click()
    def show_erase_button(self, setting_name=None):
        if setting_name is None:
            setting_name = self.sender().objectName()

        # This regex pattern matches any prefix followed by an underscore and captures the rest as the settingname
        pattern = r'^[^_]+_(.+)$'
        setting = re.match(pattern, setting_name).group(1)
        # dbprint("red", f"SETTING: {setting}")
        eb = self.mainwindow.findChild(QPushButton, f"eb_{setting}")
        if eb is not None:
            eb.setVisible(True)
            eb.setToolTip("Reset to Default")
        else:
            logging.info(f"Can't find erase button for '{setting}'.  Probably child device being configured via master")

    def hide_erase_button(self, setting_name):
        eb = self.mainwindow.findChild(QPushButton, f"eb_{setting_name}")
        if eb is not None:
            eb.setVisible(False)
            eb.setToolTip("")
        else:
            logging.info(f"Can't find erase button for '{setting_name}'.  Probably child device being configured via master")

    @staticmethod
    def setting_values_equal(new_value, target_value):
        if target_value is None:
            return False
        a = str(new_value).strip()
        b = str(target_value).strip()
        if a.lower() == b.lower():
            return True
        # settings are stored as strings; equal numbers can be formatted
        # differently (e.g. '0.5' from a spinbox vs '0.50' in defaults)
        try:
            return float(a) == float(b)
        except ValueError:
            return False

    def write_or_revert(self, setting_name, value, unit=''):
        """Write the setting to the user config, unless the new value matches what
        the setting would resolve to without the user-scope override - in that case
        erase the override (or skip the redundant write) so the setting reverts to
        its inherited/default value and the erase button disappears.
        Returns True when the change was handled by erasing/skipping."""
        target = self.revert_targets.get(setting_name)
        if target is not None and setting_name != 'type':
            t_value, t_unit, has_override, eb_in_layout = target
            if (unit or '') == (t_unit or '') and self.setting_values_equal(value, t_value):
                if has_override:
                    logging.debug(f"Setting '{setting_name}' changed back to its inherited value '{t_value}' - erasing the override")
                    G.settings_mgr.erase_from_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, setting_name)
                    self.revert_targets[setting_name] = (t_value, t_unit, False, eb_in_layout)
                    if eb_in_layout:
                        self.hide_erase_button(setting_name)
                    # rebuild the form (same as a manual erase click) so the
                    # action icon reflects the layer now in effect, e.g. a
                    # sim/class override icon hiding under the erased override
                    self.trigger_form_reload = True
                return True
        G.settings_mgr.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, value, setting_name, unit)
        if target is not None:
            t_value, t_unit, _, eb_in_layout = target
            self.revert_targets[setting_name] = (t_value, t_unit, True, eb_in_layout)
            if not eb_in_layout:
                # the row shows the sim/class override info icon instead of the
                # erase button; only a form rebuild can swap in the erase button
                self.trigger_form_reload = True
                return False
        self.show_erase_button(f'eb_{setting_name}')
        return False

    def remove_widget(self, olditem):
        widget = olditem.widget()
        if widget is not None:
            widget.deleteLater()
            self.removeWidget(widget)

    def enforce_exclusives(self, name, state, exclusive_list):
        """Used to force disable bool settings that are mutually exclusive with others"""
        if state != 2:
            # we are only interested in bool settings that have been enabled (state = 2)
            return None
        for pair in exclusive_list:  # Iterate through prebuilt exclusivity list to find one matching this call
            a = pair[0]  # This is the enforcing setting
            b = pair[1]  # This is the setting(s) which need to be disabled if 'a' is enabled (comma separated)
            if a == name:  # Find if there is pair that matches this function call
                disable_list = b.split(',')
                return disable_list
        return None


    def checkbox_changed(self, name, state):
        self.trigger_form_reload = True
        logging.debug(f"Checkbox {name} changed. New state: {state}")
        value = 'false' if state == 0 else 'true'
        enforce_list = self.enforce_exclusives(name, state, self.exclusive_list)  # Check for and enforce exclusive settings
        if value == 'true' and enforce_list is not None:
            for exclusive in enforce_list:
                #enforce_list is a list of settings that must not be true if 'value' is true (per exclusive attribute in defauls.xml)
                self.write_or_revert(exclusive, "false")

        self.write_or_revert(name, value)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def erase_setting(self, name):
        self.trigger_form_reload = True
        logging.debug(G.settings_mgr.offline_scope)
        logging.debug(f"Erase {name} clicked")
        G.settings_mgr.erase_from_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, name, )
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def browse_for_config(self):
        self.trigger_form_reload = False
        options = QFileDialog.Option(0)
        # options |= QFileDialog.Option.DontUseNativeDialog
        calling_button = self.sender()
        starting_dir = os.getcwd()
        if calling_button:
            tooltip_text = calling_button.toolTip()
            # print(f"Tooltip text of the calling button: {tooltip_text}")
            if os.path.isfile(tooltip_text):
                # Use the existing file path as the starting point
                starting_dir = os.path.dirname(tooltip_text)

        # Open the file browser dialog
        file_path, _ = QFileDialog.getOpenFileName(self.mainwindow, "Choose File", starting_dir, "vpconf Files (*.vpconf)", options=options)

        if file_path:
            cfg_scope = xmlutils.device
            dev_type_cap = cfg_scope.capitalize()
            usbpid = str(G.system_settings.get(f'pid{dev_type_cap}', '2055'))

            if validate_vpconf_profile(file_path, pid=usbpid, dev_type=cfg_scope):
                #lprint(f"Selected File: {file_path}")
                self.write_or_revert('vpconf', file_path)
                self.vpconf_browse_button.setText(os.path.basename(file_path))
                if G.settings_mgr.timed_out:
                    self.reload_caller()

    def spin_box_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sb_', '')
        value = str(self.sender().value())
        logging.debug(f"Spin Box {setting_name} changed. New value: {value}")
        self.write_or_revert(setting_name, value)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def textbox_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('le_', '')
        value = self.sender().text()
        logging.debug(f"Textbox {setting_name} changed. New value: {value}")
        self.write_or_revert(setting_name, value)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def dropbox_changed(self):
        self.trigger_form_reload = True
        sender = self.sender()

        if isinstance(sender, QtWidgets.QLineEdit):
            combo = sender.parent()  # QComboBox parent of QLineEdit

            # If the popup is open, this "editingFinished" is just focus moving
            # into the dropdown list — don't treat it as a commit.
            if combo.view() is not None and combo.view().isVisible():
                return
        else:
            combo = sender

        object_name = combo.objectName()
        setting_name = object_name.replace('edb_', '').replace('db_', '')  # handle both db_ and edb_

        # If it's an enum dropbox, get the enum member name
        if object_name.startswith("edb_"):
            enum_member = combo.itemData(combo.currentIndex())
            value = enum_member.name if enum_member else combo.currentText()
        else:
            value = combo.currentText()

        logging.debug(f"Dropbox {setting_name} changed. New value: {value}")
        self.write_or_revert(setting_name, value)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    @staticmethod
    def convert_unit_value(value_str, from_unit, to_unit):
        """
        Convert a value from one unit to another using the canonical
        utils._UNIT_CONVERSIONS factors (the same table the load-time value
        parser uses).
        Returns the converted value as a string, or the original if the
        conversion is not supported.
        """
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            return value_str

        converted_value = utils.convert_between_units(value, from_unit, to_unit)
        if converted_value is None:
            return value_str

        # Tenths are plenty: the telemetry these values compare against isn't
        # meaningful below that, and one decimal keeps conversion round trips
        # stable (7 m/s -> 13.6 kt -> 7 m/s).
        formatted = f"{converted_value:.1f}"
        # trim trailing zeros so "14.0" writes as "14"
        return formatted.rstrip('0').rstrip('.') or '0'

    def unit_dropbox_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('ud_', '')
        line_edit_name = 'vle_' + self.sender().objectName().replace('ud_', '')
        line_edit = self.mainwindow.findChild(QLineEdit, line_edit_name)
        new_unit = self.sender().currentText()

        # Get the previous unit for this setting
        old_unit = self.unit_previous_values.get(setting_name, new_unit)

        if line_edit is None:
            # No text-entry widget for this row (no such rows exist today):
            # record the unit but never write an empty value over the setting.
            self.unit_previous_values[setting_name] = new_unit
            logging.warning(f"Unit change on {setting_name} has no value widget; not writing")
            return

        old_value = line_edit.text()

        # Convert value if units changed
        if old_unit != new_unit:
            value = self.convert_unit_value(old_value, old_unit, new_unit)
            # Update the line edit with converted value
            line_edit.blockSignals(True)
            line_edit.setText(value)
            line_edit.blockSignals(False)
            logging.debug(f"Unit conversion: {old_value}{old_unit} -> {value}{new_unit}")
        else:
            value = old_value

        # Store the current unit for next time
        self.unit_previous_values[setting_name] = new_unit

        logging.debug(f"Unit {self.sender().objectName()} changed. New value: {value}{new_unit}")
        self.write_or_revert(setting_name, value, new_unit)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def line_edit_changed(self):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('vle_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('vle_', '')
        unit_dropbox = self.mainwindow.findChild(NoWheelComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value = self.sender().text()
        logging.debug(f"Text box {self.sender().objectName()} changed. New value: {value}{unit}")
        self.write_or_revert(setting_name, value, unit)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def expander_clicked(self):
        self.trigger_form_reload = True
        logging.debug(f"expander {self.sender().objectName()} clicked.  value: {self.sender().text()}")
        settingname = self.sender().objectName().replace('ex_', '')
        if self.sender().arrowType() == Qt.ArrowType.RightArrow:
            # print ('expanded')

            self.expanded_items.append(settingname)
            self.sender().setArrowType(Qt.ArrowType.DownArrow)

            self.reload_caller()
        else:
            # print ('collapsed')
            new_exp_items = []
            for ex in self.expanded_items:
                if ex != settingname:
                    new_exp_items.append(ex)
            self.expanded_items = new_exp_items
            self.sender().setArrowType(Qt.ArrowType.DownArrow)

            self.reload_caller()

    def usb_button_clicked(self):
        if G.master_instance and G.device_type != G.current_device_config_scope:
            # button is being bound for non master device
            target_device = G.current_device_config_scope
        else:
            target_device = None

        button_name = self.sender().objectName().replace('pb_', '')
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText("Push a button! ")
        # listen for button loop
        # Start a thread to fetch button press with a timeout
        self.thread = ButtonPressThread(self.device, self.sender(), target_device)
        self.thread.button_pressed.connect(self.update_button)
        self.thread.start()

    def update_button(self, button_name, value):
        self.trigger_form_reload = False
        the_button = self.mainwindow.findChild(QPushButton, f'pb_{button_name}')
        the_button.setText(str(value))
        if str(value) != '0':
            self.write_or_revert(button_name, str(value))
        else:
            the_button.setText("Click to Configure")
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def advanced_g_button_clicked(self, value=None):
        if value is None:
            value = self.advanced_g_settings
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW ADV SPR:{G.current_device_config_scope}')
        else:
            if self.adv_g_dialog is None:
                self.adv_g_dialog = AdvancedGDialog(parent=G.main_window, settings=value, device=G.current_device_config_scope)
                self.adv_g_dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                self.adv_g_dialog.accepted.connect(self.update_advanced_g_effect)
                self.adv_g_dialog.show()
            else:
                self.adv_g_dialog.showme(settings=value)

    def advanced_spring_button_clicked(self, value=None):
        if value is None:
            value = self.advanced_spring_settings
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW ADV SPR;{G.current_device_config_scope};{value}')  # use semicolon for delimiter as value contains colons
        else:
            if self.adv_spr_dialog is None:
                self.adv_spr_dialog = AdvancedSpringDialog(parent=G.main_window, settings=value, device=G.current_device_config_scope, sim=G.settings_mgr.current_sim)
                self.adv_spr_dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                self.adv_spr_dialog.accepted.connect(self.update_advanced_spring_gains)
                self.adv_spr_dialog.show()
            else:
                self.adv_spr_dialog.showme(settings=value, sim=G.settings_mgr.current_sim)

    def configurator_button_clicked(self):
        """
        User clicked the configurator gain override settings button.
        If the current settings scope is not the master device, send the command over IPC to have the child instance
        pop its dialog window.
        """
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'SHOW GAIN OVD:{G.current_device_config_scope}')
        else:
            # utils.dbprint('red', f"CFG Clicked: {value}")
            # dbprint("red", f"Device = {G.device_type}")
            # dbprint("green", f"Scope = {G.current_device_config_scope}")
            value = G.settings_mgr.read_setting_from_xml("configurator_gains", G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern)
            if not value or value == 'none':
                gains_state = None
                # utils.dbprint('green', f'{G.device_type}" RESET UI!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
            else:
                gains_state = json.loads(value)
                # utils.dbprint('blue', f'{G.device_type}" LOADING SETTINGS!!!!{gains_state}')

            G.gain_override_dialog.accepted.connect(self.update_configurator_overrides)
            G.gain_override_dialog.load_and_show(gains_state)

    def erase_configurator_overrides(self):
        if G.master_instance and G.device_type != G.current_device_config_scope:
            G.ipc_instance.send_broadcast_message(f'ERASE GAIN OVD:{G.current_device_config_scope}')
        else:
            G.gain_override_dialog.revert_gains()
            G.gain_override_dialog.reset_to_vpconf()
            # Keep the canonical flag in sync (children report it to the
            # master over IPC) and refresh scope-aware rather than directly.
            if G.telem_manager is not None:
                G.telem_manager.gain_overrides_active = False
            G.main_window.refresh_scope_status_indicators(force=True)

    def update_advanced_g_effect(self, g_effect_curves: str):
        self.trigger_form_reload = True
        """
        Called when signal received that user saved the advanced g-effect settings
        """
        G.settings_mgr.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, g_effect_curves,"gforce_effect_adv_curve")
        self.show_erase_button("config_gforce_effect_adv_curve")
        # self.adv_spr_button.setText("Edit Spring Gains")
        # self.adv_spr_button.clicked.disconnect(lambda: self.advanced_spring_button_clicked(spring_gain_curves))
        self.reload_caller()

    def trim_cal_button_clicked(self):
        """Open the elevator trim calibration dialog from the settings row.

        Reuses the MainWindow-owned dialog instance (created on first use)
        so the settings-row button and the Utilities menu share one window.
        """
        G.main_window.open_trim_calibration_dialog()

    def save_trim_calibration(self, payload_json: str):
        """Persist the auto-calibration state for the current aircraft.

        Connected to TrimCalibrationDialog.result_saved. The payload carries
        the multi-speed curve family ("curves", written as the family JSON),
        the use-curve choice, and optionally the trimmed-stick-position
        mode. A legacy "curve" (single dict) payload is accepted and
        wrapped. The static virtual_y gain is deliberately NOT written —
        the curve is the product, and the gain is entangled with the
        physical-gain setting at solve time. Mirrors the write + reload
        pattern of the advanced spring editor.
        """
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            logging.error("Invalid trim calibration payload; nothing saved")
            return
        self.trigger_form_reload = True
        sim = G.settings_mgr.current_sim
        cls = G.settings_mgr.current_class
        pattern = G.settings_mgr.current_pattern
        curves = payload.get("curves")
        if curves is None:
            curve = payload.get("curve")
            curves = [curve] if curve else []
        speeds = ", ".join(f"{float(c.get('ias_kt') or 0):.0f}" for c in curves)
        logging.info(
            f"Trim calibration saved: {len(curves)} speed(s) ({speeds} kt) "
            f"for '{pattern}' [{sim}]"
            + (f", stick position '{payload['stick_position']}'"
               if payload.get("stick_position") else ""))
        G.settings_mgr.write_to_xml(
            sim, cls, pattern,
            json.dumps({"curves": curves}) if curves else "none",
            "joystick_trim_follow_curve_y")
        G.settings_mgr.write_to_xml(
            sim, cls, pattern,
            "true" if payload.get("use_curve") else "false",
            "joystick_trim_follow_use_curve_y")
        if payload.get("stick_position"):
            G.settings_mgr.write_to_xml(
                sim, cls, pattern,
                payload["stick_position"], "joystick_trim_follow_stick_position")
        # Reveal the erase-override button on the curve row: with shipped
        # default calibrations, erasing the user override means "revert to
        # the factory calibration".
        self.show_erase_button("config_joystick_trim_follow_curve_y")
        self.reload_caller()

    def save_trim_position_mode(self, value: str):
        """Persist the trimmed-stick-position mode alone — the calibration
        dialog's pulldown is usable post-calibration without a fresh run."""
        self.trigger_form_reload = True
        logging.info(f"Trimmed stick position set to '{value}' for "
                     f"'{G.settings_mgr.current_pattern}'")
        G.settings_mgr.write_to_xml(
            G.settings_mgr.current_sim, G.settings_mgr.current_class,
            G.settings_mgr.current_pattern, value,
            "joystick_trim_follow_stick_position")
        self.reload_caller()

    def update_advanced_spring_gains(self, spring_gain_curves: str, scale: str, units: str):
        self.trigger_form_reload = True
        """
        Called when signal received that user saved the advanced spring gain settings
        """
        G.settings_mgr.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, spring_gain_curves, "adv_spr_gains")
        # G.settings_mgr.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, str(scale), "vne_override", unit=units)
        self.show_erase_button("config_adv_spr_gains")
        # self.adv_spr_button.setText("Edit Spring Gains")
        # self.adv_spr_button.clicked.disconnect(lambda: self.advanced_spring_button_clicked(spring_gain_curves))
        self.reload_caller()

    def update_configurator_overrides(self, gain_dict):
        self.trigger_form_reload = False
        """
        Called when signal received that user saved the configurator gain settings
        convert dictionary to text string and write to config.
        """
        gain_dict_json = json.dumps(gain_dict)
        G.settings_mgr.write_to_xml(G.settings_mgr.current_sim, G.settings_mgr.current_class, G.settings_mgr.current_pattern, gain_dict_json, "configurator_gains")
        self.reload_layout(None)
        self.show_erase_button("config_configurator_gains")
        if hasattr(self, 'configurator_button'):
            # button may not exist in child instance if master is setting on behalf of child instance
            self.configurator_button.setText("Edit Gain Overrides")

    def pct_df_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('dfsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dfsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dfsld_', '')

        unit_dropbox = self.mainwindow.findChild(NoWheelComboBox, unit_dropbox_name)
        unit = unit_dropbox.currentText() if unit_dropbox is not None else ''

        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)

        factor = 1.0
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())

        # slider integer → percent float
        slider_pos = self.sender().value()  # e.g., 43 when 43.0%
        percent_value = slider_pos * factor  # with factor 0.1 → 4.3%
        decimal_value = round(percent_value / 100.0, 4)  # → 0.043

        if value_label is not None:
            decimals = 3 if factor < 0.1 else 2 if factor < 1.0 else 1
            value_label.setText(f"{percent_value:.{decimals}f}%")

        if self.show_slider_debug:
            logging.debug(
                f"pct_df_Slider {self.sender().objectName()} changed. "
                f"pos={slider_pos} factor={factor} percent={percent_value:.3f}% save={decimal_value}"
            )

        if write:
            # stored as decimal percent
            self.write_or_revert(setting_name, str(decimal_value), unit)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('sld_', '')
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        value_to_save = str(round(value * factor / 100, 4))
        if self.show_slider_debug:
            logging.debug(f"Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}")
        if write:
            self.write_or_revert(setting_name, value_to_save)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def cfg_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('sld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('sld_', '')

        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        value = 0
        factor = 1.0
        if value_label is not None:
            value_label.setText(str(self.sender().value()) + '%')
            value = int(self.sender().value())

        value_to_save = str(round(value / 100, 4))
        if self.show_slider_debug:
            logging.debug(f"Slider {self.sender().objectName()} cfg changed. New value: {value}  saving: {value_to_save}")
        if write:
            self.write_or_revert(setting_name, value_to_save)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def d_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('dsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dsld_', '')
        unit_dropbox = self.mainwindow.findChild(NoWheelComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        factor = 1
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        if value_label is not None:
            value = self.sender().value()
            value_to_save = str(round(value * factor))
            value_label.setText(value_to_save)
        if self.show_slider_debug:
            logging.debug(f"d_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        if write:
            self.write_or_revert(setting_name, value_to_save, unit)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    def df_slider_changed(self, write=True):
        self.trigger_form_reload = False
        setting_name = self.sender().objectName().replace('dfsld_', '')
        value_label_name = 'vl_' + self.sender().objectName().replace('dfsld_', '')
        sliderfactor_name = 'sf_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox_name = 'ud_' + self.sender().objectName().replace('dfsld_', '')
        unit_dropbox = self.mainwindow.findChild(NoWheelComboBox, unit_dropbox_name)
        unit = ''
        if unit_dropbox is not None:
            unit = unit_dropbox.currentText()
        value_label = self.mainwindow.findChild(QLabel, value_label_name)
        sliderfactor_label = self.mainwindow.findChild(QLabel, sliderfactor_name)
        factor = 1
        if sliderfactor_label is not None:
            factor = float(sliderfactor_label.text())
        if value_label is not None:
            value = self.sender().value()
            value_to_save = str(round(value * factor, 2))
            value_label.setText(value_to_save)
        if self.show_slider_debug:
            logging.debug(f"df_Slider {self.sender().objectName()} changed. New value: {value} factor: {factor}  saving: {value_to_save}{unit}")
        if write:
            self.write_or_revert(setting_name, value_to_save, unit)
        if G.settings_mgr.timed_out:
            self.reload_caller()

    # # prevent slider from sending values as you drag
    # def sldDisconnect(self):
    #     self.sender().valueChanged.disconnect()
    #
    # # reconnect slider after you let go
    # def sldReconnect(self):
    #     self.sender().valueChanged.connect(self.slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def cfg_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.cfg_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def d_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.d_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())
    #
    # def df_sldReconnect(self):
    #     self.sender().valueChanged.connect(self.df_slider_changed)
    #     self.sender().valueChanged.emit(self.sender().value())