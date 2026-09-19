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

# Mode-independent semantic status colors, reused across dialogs that don't
# otherwise vary by G.useDarkMode (guided-flow trackers, validity borders,
# inline warnings).
ERROR_RED = "#cc3333"
OK_GREEN = "#33aa33"
WARNING_AMBER = "#e6a817"
INFO_BLUE = "#2a7fd4"
WARNING_BANNER_BG = "#b36b00"    # opaque amber banner background
DANGER_BANNER_BG = "#cc3300"     # opaque red-orange banner background
WARNING_TEXT_AMBER = "#cc7a00"   # inline warning text/value color
DIVIDER_GRAY = "#555555"
BORDER_GRAY = "#777777"
DISABLED_GRAY = "#808080"
ROW_MATCH_GRAY = "#888888"       # "already matches" table-row text
ROW_SKIP_FG_GRAY = "#a0a0a0"     # skipped-row text, same in both modes
WARNING_LABEL_FG = "#dddddd"     # excluded-defaults warning label text
PURPLE_HOVER_FILL = "#44" + PURPLE[1:]  # PURPLE at ~27% alpha, translucent hover pill
# Translucent input-validation fills (behind a field's own text).
FIELD_REQUIRED_BG = "rgba(255, 85, 85, 0.3)"  # mandatory field left empty
FIELD_MATCH_BG = "rgba(0, 128, 0, 0.2)"       # pattern matches
FIELD_NO_MATCH_BG = "rgba(255, 0, 0, 0.2)"    # pattern does not match
WARNING_LABEL_BG = "rgba(255, 50, 50, 30)"    # excluded-defaults warning label tint

# Keyed off the widget's own palette lightness rather than G.useDarkMode
# (see SystemSettingsDialog._with_download_link / _attention_color), so
# these stay standalone constants instead of ThemeTokens fields.
LINK_BLUE_DARK = "#8ab4f8"
LINK_BLUE_LIGHT = "#1a5fb4"
ATTENTION_AMBER_DARK = "#e6a823"
ATTENTION_AMBER_LIGHT = "#a86600"


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
    # The hover/pressed selectors use the same attribute name as the base
    # rule ("buttonType") so those rules actually match in both themes.
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

    # Secondary/hint label text (NewAircraftWizard notes, etc.).
    muted_text_color: str

    # Foreground for colored status badges/rows (table-cell highlights,
    # inline error banners) - dark text needs white, light text needs black.
    badge_fg: str

    # ProfileManager inline error label (new/rename profile dialogs).
    error_banner_fg: str
    error_banner_bg: str
    error_banner_border: str

    # ProfileImportDialog table-row highlight backgrounds.
    row_conflict_bg: str   # name/override already in use
    row_invalid_bg: str    # override references a removed/renamed default
    row_ok_bg: str          # rename resolved, no remaining conflict
    row_skip_bg: str        # row excluded from the import


DARK = ThemeTokens(
    name="dark",
    button_text_color="white",
    hover_pressed_color_rule="color: white;",
    erase_button_font_family="Cascadia Code",
    erase_button_size_rules="min-width: 25px;\n    min-height: 25px;",
    erase_button_hover_bg="palette(window)",
    erase_button_pressed_bg="#666",
    p_m_button_color=PURPLE,
    p_m_button_hover_bg="palette(window)",
    p_m_button_pressed_extra=f"border: 1px solid {PURPLE};",
    disabled_glyph_color="#808080",
    expand_button_color=PURPLE_HOVER,
    expand_button_extra_rule="",
    expand_button_hover_bg="#666",
    expand_button_selector_attr="buttonType",
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
    muted_text_color="#a0a0a0",
    badge_fg="white",
    error_banner_fg="#FFE3E3",       # soft light red/pink (good contrast on dark)
    error_banner_bg="rgba(255, 99, 99, 0.16)",   # faint red tint
    error_banner_border="#FF6B6B",   # border a bit brighter
    row_conflict_bg="#5c2b2b",
    row_invalid_bg="#554400",
    row_ok_bg="#2d4b2d",
    row_skip_bg="#3a3a3a",
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
    color: palette(text);
    background-color: #ab37c8;
}""",
    checkbox_disabled_block="",
    muted_text_color="#5a5a5a",
    badge_fg="black",
    error_banner_fg="#B00020",       # material-ish error red
    error_banner_bg="rgba(176, 0, 32, 0.10)",    # faint tint
    error_banner_border="rgba(176, 0, 32, 0.35)",
    row_conflict_bg="#ffd6d6",
    row_invalid_bg="#ffffcc",
    row_ok_bg="#ccffcc",
    row_skip_bg="#e0e0e0",
)


def current_tokens() -> ThemeTokens:
    """DARK or LIGHT, keyed on the same flag the rest of the UI reads."""
    import telemffb.globals as G
    return DARK if G.useDarkMode else LIGHT
