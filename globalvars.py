import os

current_sim = ''
current_aircraft_name = ''
current_class = ''
current_pattern = ''
defaults_path = 'defaults.xml'
userconfig_rootpath = os.path.join(os.environ['LOCALAPPDATA'],"VPForce-TelemFFB")
userconfig_path = os.path.join(userconfig_rootpath , 'userconfig.xml')

device = 'joystick'
