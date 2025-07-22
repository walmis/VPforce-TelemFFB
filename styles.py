"""
Stylesheet definitions for TelemFFB application.
Contains both dark mode and light mode stylesheets.
"""

DARK_MODE_STYLESHEET = """
QPushButton:!pressed, #styledButton:!pressed {
    background-color: qlineargradient(spread:pad, x1:1, y1:1, x2:0, y2:0.0397727, stop:0 rgba(160, 0, 200, 255), stop:1 rgba(174, 106, 206, 255));
    border-radius: 6px;
    padding: 2px;
    color: white; /* Ensures consistency */
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
    color: white;
    border: 1px solid #8e1da8;
}

QPushButton[buttonType="erase_button"] {
    font-size: 16px;  /* Adjust the font size */
    font-family: Cascadia Code;
    font-weight: bold;
    color: black;
    padding: 0px;
    border: none;  /* Remove any border */
    margin: 0px;   /* Remove any margin */
    background-color: transparent;  /* Transparent background */
    min-width: 25px;
    border-radius:6px;
}
            
QPushButton[buttonType="erase_button"]:hover {
    background-color: palett(window);  /* Optional: Change background on hover */
    min-width: 25px;
    border-radius:6px;
}

QPushButton[buttonType="erase_button"]:pressed {
    background-color: #666;  /* Optional: Change background on press */
    border: 1px solid #ab37c8;
    min-width: 25px;
    border-radius: 6px;
}

QPushButton[buttonType="p_m_button"] {                                  
    font-size: 16px;  /* Adjust the font size */                        
    font-family: Cascadia Code;                                         
    font-weight: bold;                                                  
    color: #ab37c8;                                                       
    padding: 0px;                                                       
    border: none;  /* Remove any border */                              
    margin: 0px;   /* Remove any margin */                              
    background-color: transparent;  /* Transparent background */  
    min-width: 20px;
    border-radius:4px;      
}   
                                                                    
QPushButton[buttonType="p_m_button"]:hover {                            
    background-color: palett(window);  /* Optional: Change background on hover */ 
    min-width: 20px;
    border-radius: 4px;
}    
                                                                   
QPushButton[buttonType="p_m_button"]:pressed {                          
    background-color: #666;  /* Optional: Change background on press */
    border: 1px solid #ab37c8;
    min-width: 20px;
    border-radius: 4px; 
}     

QToolButton[buttonType="expand_button"] {                                                          
    font-size: 16px;  /* Adjust the font size */                       
    font-family: Cascadia Code;                                        
    font-weight: bold;                                                 
    color: #c473d9;                                                    
    border: none;  /* Remove any border */                             
    margin: 0px;   /* Remove any margin */                             
    background-color: transparent;  /* Transparent background */       
}              
                                                        
QToolButton[button_type="expand_button"]:hover {                                                    
    background-color: #666;  /* Optional: Change background on hover */
}   
                                                                   
QToolButton[button_type="expand_button"]:pressed {                                                  
    background-color: #bbb;  /* Optional: Change background on press */
}                                                                      

QLineEdit, QPlainTextEdit, QTextEdit {
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
    border: 1px solid #333333;
    height: 8px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #5a5a5a, stop: 1 #3e3e3e
    );
    margin: 0;
    border-radius: 3px;
}

QMenuBar {
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
}

QMenu {
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

QCheckBox:disabled {
  color: rgb(155, 155, 155);  /* lighter grey for better visibility */
}
QLabel {
    color: palette(text);
    background: transparent;
}

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
"""

LIGHT_MODE_STYLESHEET = """
QPushButton:!pressed, #styledButton:!pressed {
    background-color: qlineargradient(spread:pad, x1:1, y1:1, x2:0, y2:0.0397727, stop:0 rgba(160, 0, 200, 255), stop:1 rgba(174, 106, 206, 255));
    border-radius: 6px;
    padding: 2px;
    color: #dddddd; /* Ensures consistency */
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
    border: 1px solid #8e1da8;
}

QPushButton[buttonType="erase_button"] {
    font-size: 16px;  /* Adjust the font size */
    font-family: Arial Black;
    font-weight: bold;
    color: black;
    padding: 0px;
    border: none;  /* Remove any border */
    margin: 0px;   /* Remove any margin */
    background-color: transparent;  /* Transparent background */
    min-width: 25px;
    border-radius:6px;
}
            
QPushButton[buttonType="erase_button"]:hover {
    background-color: #ddd;  /* Optional: Change background on hover */
    min-width: 25px;
    border-radius:6px;
}

QPushButton[buttonType="erase_button"]:pressed {
    background-color: #bbb;  /* Optional: Change background on press */
    border: 1px solid #ab37c8;
    min-width: 25px;
    border-radius: 6px;
}

QPushButton[buttonType="p_m_button"] {                                  
    font-size: 16px;  /* Adjust the font size */                        
    font-family: Cascadia Code;                                         
    font-weight: bold;                                                  
    color: black;                                                       
    padding: 0px;                                                       
    border: none;  /* Remove any border */                              
    margin: 0px;   /* Remove any margin */                              
    background-color: transparent;  /* Transparent background */
    min-width: 20px;
    border-radius:4px;         
}     
                                                                  
QPushButton[buttonType="p_m_button"]:hover {                            
    background-color: #ddd;  /* Optional: Change background on hover */ 
    min-width: 20px;
    border-radius: 4px;
}                      
                                                 
QPushButton[buttonType="p_m_button"]:pressed {                          
    background-color: #666;  /* Optional: Change background on press */ 
    min-width: 20px;
    border-radius: 4px;
}                                                                       

QToolButton[buttonType="expand_button"] {                                                          
    font-size: 16px;  /* Adjust the font size */                       
    font-family: Cascadia Code;                                        
    font-weight: bold;                                                 
    color: black;                                                      
    padding: 0px;                                                      
    border: none;  /* Remove any border */                             
    margin: 0px;   /* Remove any margin */                             
    background-color: transparent;  /* Transparent background */       
}                                                                      
QToolButton[buttonType="expand_button"]:hover {                                                    
    background-color: #ddd;  /* Optional: Change background on hover */
}      
                                                                
QToolButton[buttonType="expand_button"]:pressed {                                                  
    background-color: #bbb;  /* Optional: Change background on press */
}                                                                      

QLineEdit, QPlainTextEdit, QTextEdit {
    selection-background-color: #ab37c8;
}

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
    border: 1px solid #565a5e;
    height: 8px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #e6e6e6, stop: 1 #bfbfbf
    );
    margin: 0;
    border-radius: 3px;
}

QMenuBar {
    background-color: #f0f0f0;
}

QMenu::item {
    background-color: transparent;
}

QMenu::item:selected {
    color: palett(text);
    background-color: #ab37c8;
}

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
"""

GROUP_LABEL_STYLESHEET = """
QLabel {
    color: #ab37c8;
    font-family: "Black Ops One";
    font-size: 14pt;
}

QLabel:hover {
    color: #c473d9;
    text-decoration: underline; 
}

QLabel:!hover {
    color: #ab37c8;
    text-decoration: underline; 
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