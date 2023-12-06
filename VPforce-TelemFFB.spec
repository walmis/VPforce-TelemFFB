# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('dll/hidapi.dll', 'dll'), ('simconnect/simconnect.dll', 'simconnect'), ('updater/updater.exe', 'updater') ],
    datas=[('export/*', 'export'), ('config.ini', '.'), ('simconnect/*.json', 'simconnect'), ('image/*', 'image'), ('config.user.ini.README', '.'), ('_RELEASE_NOTES.txt', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5.QtQuick', 'PyQt5.QtQuickWidgets', 'PyQt5.QtQuick3D', 'PyQt5.QtQml', 'PyQt5.QtOpenGL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

exclude_bin = ["QtWebEngineProcess.exe",
		"Quick.dll", 
		"QuickWidgets.dll", 
		"Xml",
		"Sql",
		"PositioningQuick",
		"Positioning",
		"Bluetooth",
		"Network",
		"Test",
		"Nfc",
		"WebChannel",
		"WebSockets",
		"RemoteObjects",
		"PrintSupport",
		"TextToSpeech",
		"QmlModels",
		"Help",
		"api-ms-win", 
		"opengl32sw.dll",
		"MSVCP140_1.dll",
		"Qt5Qml.dll",
		"Qt5WebEngineCore.dll",
		"d3dcompiler_47.dll",
		"qsqlite.dll",
		"dbghelp.dll",
		"dbgcore.dll",
		"VCRUNTIME",
		"Bluetooth",
		"MSVCP",
		"ucrtbase.dll",
		"Sensors",
		"WebEngine",
		"Location",
		"GLES",
		"Multimedia",
		"libeay32.dll",
		"libEGL",
		"DBus",
		"geoservices",
		"sensorgestures",
		"libegl",
		"libgles",
		"dsengine",
		"qtmedia",
		"wmfengine",
		"qwebp",
		"qtaudio"]
		
exclude_data = ["qtwebengine", "translations", "icudtl"]

def filter_bin(a):
	ret = not (True in [x in a[0] for x in exclude_bin])
	if not ret:
		print("EXCL", a[0])
	return ret
	
def filter_data(a):
	ret = not (True in [x in a[0] for x in exclude_data])
	if not ret:
		print("EXCL", a[0])
	return ret
	
a.binaries = TOC(list(filter(filter_bin, a.binaries)))
a.datas = TOC(list(filter(filter_data, a.datas)))
	
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VPforce-TelemFFB',
    icon='image/vpforceicon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VPforce-TelemFFB',
)
