"""
Stylesheet definitions for TelemFFB application.

DARK_MODE_STYLESHEET and LIGHT_MODE_STYLESHEET are rendered from a single
QSS template plus the per-mode color tokens in
telemffb.ui.theme.tokens.DARK / LIGHT, so the two near-duplicate
stylesheets can't drift out of sync on everything but color.
"""
from string import Template

from telemffb.ui.theme.tokens import DARK, LIGHT, ThemeTokens

_STYLESHEET_TEMPLATE = Template("""
QComboBox {
    /* dropdowns expand DOWNWARD as a plain list (capped by
       maxVisibleItems) instead of the style's overlay popup, which
       opens over the control and flips direction with its position on
       the form */
    combobox-popup: 0;
}
QPushButton:!pressed, #styledButton:!pressed {
    background-color: qlineargradient(spread:pad, x1:1, y1:1, x2:0, y2:0.0397727, stop:0 rgba(160, 0, 200, 255), stop:1 rgba(174, 106, 206, 255));
    border-radius: 6px;
    padding: 2px;
    color: $button_text_color; /* Ensures consistency */
    border: 1px solid #9d30b3;
    min-width: 70px;
}

QPushButton:disabled:!pressed, #styledButton:disabled:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #e1e1e1, stop: 0.2 #cccccc,
                                      stop: 0.5 #bbbbbb, stop: 0.8 #aaaaaa, stop: 1.0 #999999);
    color: #666666;
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    border: 1px solid #999999;
}

QPushButton:pressed, #styledButton:pressed {
    background-color: qlineargradient(
        x1:0, y1:1, x2:1, y2:0,
        stop: 0 #6e1d6f,
        stop: 1.0 #ab37c8
    );
    border-radius: 6px;
    padding: 4px 8px;
    border: 1px solid #ab37c8;
}

QPushButton:hover:!pressed, #styledButton:hover:!pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                      stop: 0 #f0b0f0, stop: 0.2 #d897d8,
                                      stop: 0.5 #c07ec0, stop: 0.8 #a965a9, stop: 1.0 #914b91);
    border-radius: 5px;
    padding: 3px;
    margin: 0px;
    $hover_pressed_color_rule
    border: 1px solid #8e1da8;
}

QPushButton[buttonType="erase_button"] {
    font-size: 16px;  /* Adjust the font size */
    font-family: $erase_button_font_family;
    font-weight: bold;
    color: black;
    padding: 0px;
    border: none;  /* Remove any border */
    margin: 0px;   /* Remove any margin */
    background-color: transparent;  /* Transparent background */
    $erase_button_size_rules
    border-radius:6px;
}

QPushButton[buttonType="erase_button"]:hover {
    background-color: $erase_button_hover_bg;  /* Optional: Change background on hover */
    min-height: 25px;
    min-width: 25px;
    border-radius:6px;
}

QPushButton[buttonType="erase_button"]:pressed {
    background-color: $erase_button_pressed_bg;  /* Optional: Change background on press */
    border: 1px solid #ab37c8;
    min-height: 25px;
    min-width: 25px;
    border-radius: 6px;
}

QPushButton[buttonType="p_m_button"] {
    font-size: 16px;  /* Adjust the font size */
    font-family: Cascadia Code;
    font-weight: bold;
    color: $p_m_button_color;
    padding: 0px;
    border: none;  /* Remove any border */
    margin: 0px;   /* Remove any margin */
    background-color: transparent;  /* Transparent background */
    min-width: 20px;
    border-radius:4px;
}

QPushButton[buttonType="p_m_button"]:hover {
    background-color: $p_m_button_hover_bg;  /* Optional: Change background on hover */
    min-width: 20px;
    border-radius: 4px;
}

QPushButton[buttonType="p_m_button"]:pressed {
    background-color: #666;  /* Optional: Change background on press */
    $p_m_button_pressed_extra
    min-width: 20px;
    border-radius: 4px;
}

/* Borderless glyph buttons stay borderless when disabled (a preview holding
   the row, a setting whose toggle is off): the generic disabled gradient box
   above is for framed buttons.  Just dim the glyph. */
QPushButton[buttonType="p_m_button"]:disabled:!pressed,
QPushButton[buttonType="erase_button"]:disabled:!pressed {
    color: $disabled_glyph_color;
    background-color: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
}

QToolButton[buttonType="expand_button"] {
    font-size: 16px;  /* Adjust the font size */
    font-family: Cascadia Code;
    font-weight: bold;
    color: $expand_button_color;
    $expand_button_extra_rule
    border: none;  /* Remove any border */
    margin: 0px;   /* Remove any margin */
    background-color: transparent;  /* Transparent background */
}

QToolButton[$expand_button_selector_attr="expand_button"]:hover {
    background-color: $expand_button_hover_bg;  /* Optional: Change background on hover */
}

QToolButton[$expand_button_selector_attr="expand_button"]:pressed {
    background-color: #bbb;  /* Optional: Change background on press */
}

$lineedit_block

QSlider::handle:horizontal {
    background: #ab37c8;
    border: 1px solid #565a5e;
    width: 16px;
    height: 20px;
    border-radius: 5px;
    margin-top: -5px;
    margin-bottom: -5px;
    margin-left: -1px;
    margin-right: -1px;
}

QSlider::handle:horizontal:disabled {
    background: #888888;
}

QSlider::groove:horizontal {
    border: 1px solid $slider_groove_border;
    height: 8px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 $slider_groove_stop0, stop: 1 $slider_groove_stop1
    );
    margin: 0;
    border-radius: 3px;
}

$menubar_block

$menu_block

$checkbox_disabled_block

QLabel#OfflineBannerLabel {
    background-color: rgba(255, 165, 0, 100);  /* Orange-ish translucent */
    color: palette(windowText);
    padding: 6px 10px;
    font: Cascadia Mono;
    font-weight: bold;
    border: 1px solid palette(dark);
    border-radius: 6px;
}

QLabel#StatusLabel:hover {
    padding-right: 5px;
    color: #ab37c8;
    text-decoration: underline;
    background-color: transparent;
}

QLabel#StatusLabel:!hover {
    padding-right: 5px;
    color: #c473d9;
    text-decoration: underline;
    background-color: transparent;
}

MiniDeviceChip[clickable="true"] {
    border-radius: 4px;
}

MiniDeviceChip[clickable="true"]:hover {
    background-color: rgba(128, 128, 128, 60);
}

QGroupBox {
    font-weight: bold;
    border: 1px solid gray;
    border-radius: 5px;
    margin-top: 6px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 3px 0 3px;
}

/* The main window's tab pane, drawn with the same one-pixel gray line as
   the group boxes: the style's own pane frame is a two-pixel bevel whose
   visible line sits a pixel inside the Application Status box above it,
   so the two never look flush. */
QTabWidget#mainTabs::pane {
    border: 1px solid gray;
    top: -1px;  /* under the tab bar, so the selected tab still opens into the pane */
}
""")


def render_stylesheet(tokens: ThemeTokens) -> str:
    """Render the shared QSS template with one mode's color tokens."""
    return _STYLESHEET_TEMPLATE.substitute(
        button_text_color=tokens.button_text_color,
        hover_pressed_color_rule=tokens.hover_pressed_color_rule,
        erase_button_font_family=tokens.erase_button_font_family,
        erase_button_size_rules=tokens.erase_button_size_rules,
        erase_button_hover_bg=tokens.erase_button_hover_bg,
        erase_button_pressed_bg=tokens.erase_button_pressed_bg,
        p_m_button_color=tokens.p_m_button_color,
        p_m_button_hover_bg=tokens.p_m_button_hover_bg,
        p_m_button_pressed_extra=tokens.p_m_button_pressed_extra,
        disabled_glyph_color=tokens.disabled_glyph_color,
        expand_button_color=tokens.expand_button_color,
        expand_button_extra_rule=tokens.expand_button_extra_rule,
        expand_button_hover_bg=tokens.expand_button_hover_bg,
        expand_button_selector_attr=tokens.expand_button_selector_attr,
        lineedit_block=tokens.lineedit_block,
        slider_groove_border=tokens.slider_groove_border,
        slider_groove_stop0=tokens.slider_groove_stop0,
        slider_groove_stop1=tokens.slider_groove_stop1,
        menubar_block=tokens.menubar_block,
        menu_block=tokens.menu_block,
        checkbox_disabled_block=tokens.checkbox_disabled_block,
    )


DARK_MODE_STYLESHEET = render_stylesheet(DARK)
LIGHT_MODE_STYLESHEET = render_stylesheet(LIGHT)

GROUP_LABEL_STYLESHEET = """
QLabel {
    color: #ab37c8;
    font-family: "Black Ops One";
    font-size: 14pt;
}

QLabel:hover {
    color: #c473d9;
    /*text-decoration: underline; */
}

QLabel:!hover {
    color: #ab37c8;
    /*text-decoration: underline; */
}
"""

LOCKED_GROUP_LABEL_STYLESHEET = """
QLabel {
    color: #ab37c8;
    font-family: "Black Ops One";
    font-size: 14pt;
}

"""


EXPAND_LABEL_STYLESHEET = """
QLabel:hover {
    color: #ab37c8;
    text-decoration: underline;
}

QLabel:!hover {
    color: #c473d9;
    text-decoration: underline;
}
"""
