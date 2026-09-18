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
"""Named color tokens for TelemFFB's dark/light themes.

This is the single source of truth for the brand/accent colors that used to
be duplicated as module-level hex constants in a handful of UI files, and
for the per-mode values that `styles.render_stylesheet()` fills into the QSS
template. Mode-independent brand constants (accent purple, the "effect is
active" green) live at module level; everything that differs between dark
and light mode lives on `DARK` / `LIGHT`, instances of `ThemeTokens`.
"""
from dataclasses import dataclass

# Mode-independent brand constants.
PURPLE = "#ab37c8"          # rgb(171, 55, 200) - the VPforce/TelemFFB accent
PURPLE_HOVER = "#c473d9"    # accent, lightened for hover/link states
ACTIVE_GREEN = "#17c411"    # the green the live loop paints active effects


@dataclass(frozen=True)
class ThemeTokens:
    """One mode's worth of QSS color/structure tokens.

    Most fields are plain color strings substituted into the shared QSS
    template in `styles.py`. A few (suffixed `_block`) hold a whole
    rule-block of QSS text, for the handful of places where dark and light
    mode don't just differ in color but in which rules exist at all -
    keeping those as opaque blocks (rather than trying to force them
    through more color placeholders) keeps the template faithful to the
    original, near-duplicated stylesheets.
    """
    name: str

    # QPushButton:!pressed text color (the accent-gradient button label).
    button_text_color: str

    # QPushButton:hover:!pressed - dark sets an explicit label color, light
    # leaves it to the palette.
    hover_pressed_color_rule: str  # "color: white;" in dark, "" in light

    # QPushButton[buttonType="erase_button"]
    erase_button_font_family: str
    erase_button_size_rules: str    # the min-width/min-height lines (order differs per mode)
    erase_button_hover_bg: str
    erase_button_pressed_bg: str

    # QPushButton[buttonType="p_m_button"]
    p_m_button_color: str
    p_m_button_hover_bg: str
    p_m_button_pressed_extra: str  # e.g. "border: 1px solid #ab37c8;" in dark, "" in light

    # Shared disabled-glyph color for the two borderless button types above.
    disabled_glyph_color: str

    # QToolButton[buttonType="expand_button"]
    expand_button_color: str
    expand_button_extra_rule: str   # "padding: 0px;" in light, "" in dark
    expand_button_hover_bg: str
    # The hover/pressed selectors use a different attribute name than the
    # base rule in dark mode ("button_type", a pre-existing typo that makes
    # those two rules never match) - preserved as-is, not "fixed", to keep
    # this refactor visually identical to the original stylesheet.
    expand_button_selector_attr: str

    # QLineEdit/QPlainTextEdit/QTextEdit: dark overrides the palette in full
    # (background/color/border/:focus/:disabled); light only tweaks the
    # selection color and otherwise relies on the default palette.
    lineedit_block: str

    # QSlider::groove:horizontal
    slider_groove_border: str
    slider_groove_stop0: str
    slider_groove_stop1: str

    # QMenuBar (+ ::item:selected/:pressed in dark only)
    menubar_block: str

    # QMenu (+ ::item/::item:selected/::item:disabled)
    menu_block: str

    # QCheckBox:disabled - dark only, empty string in light.
    checkbox_disabled_block: str


DARK = ThemeTokens(
    name="dark",
    button_text_color="white",
    hover_pressed_color_rule="color: white;",
    erase_button_font_family="Cascadia Code",
    erase_button_size_rules="min-width: 25px;\n    min-height: 25px;",
    erase_button_hover_bg="palett(window)",
    erase_button_pressed_bg="#666",
    p_m_button_color=PURPLE,
    p_m_button_hover_bg="palett(window)",
    p_m_button_pressed_extra=f"border: 1px solid {PURPLE};",
    disabled_glyph_color="#808080",
    expand_button_color=PURPLE_HOVER,
    expand_button_extra_rule="",
    expand_button_hover_bg="#666",
    expand_button_selector_attr="button_type",
    lineedit_block="""QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: #414141;
    color: #ffffff;
    border: 1px solid #666666;
    border-radius: 2px;
    padding: 1px;
    selection-background-color: #ab37c8;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #ab37c8;  /* match your accent */
}

QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    color: palette(disabled, text);
    background-color: #3a3a3a;     /* optional, a touch darker */
    border-color: #555555;         /* optional */
}""",
    slider_groove_border="#333333",
    slider_groove_stop0="#5a5a5a",
    slider_groove_stop1="#3e3e3e",
    menubar_block="""QMenuBar {
    background-color: #353535;
    color: palette(text);
}

QMenuBar::item:selected {
    background-color: #ab37c8;
    color: palette(text);
}

QMenuBar::item:pressed {
    background-color: #ab37c8;
    color: palette(text);
}""",
    menu_block="""QMenu {
    background-color: #2b2b2b;
    color: palette(text);
    border: 1px solid #444444;
}

QMenu::item {
    padding: 6px 20px;
    background-color: transparent;
}

QMenu::item:selected {
    background-color: #ab37c8;
    color: palette(text);
}

QMenu::item:disabled {
    color: #7f7f7f;
}""",
    checkbox_disabled_block="""QCheckBox:disabled {
  color: rgb(155, 155, 155);  /* lighter grey for better visibility */
}""",
)

LIGHT = ThemeTokens(
    name="light",
    button_text_color="#dddddd",
    hover_pressed_color_rule="",
    erase_button_font_family="Arial Black",
    erase_button_size_rules="min-height: 25px;\n    min-width: 25px;",
    erase_button_hover_bg="#ddd",
    erase_button_pressed_bg="#bbb",
    p_m_button_color="black",
    p_m_button_hover_bg="#ddd",
    p_m_button_pressed_extra="",
    disabled_glyph_color="#a0a0a0",
    expand_button_color="black",
    expand_button_extra_rule="padding: 0px;",
    expand_button_hover_bg="#ddd",
    expand_button_selector_attr="buttonType",
    lineedit_block="""QLineEdit, QPlainTextEdit, QTextEdit {
    selection-background-color: #ab37c8;
}""",
    slider_groove_border="#565a5e",
    slider_groove_stop0="#e6e6e6",
    slider_groove_stop1="#bfbfbf",
    menubar_block="""QMenuBar {
    background-color: #f0f0f0;
}""",
    menu_block="""QMenu::item {
    background-color: transparent;
}

QMenu::item:selected {
    color: palett(text);
    background-color: #ab37c8;
}""",
    checkbox_disabled_block="",
)
