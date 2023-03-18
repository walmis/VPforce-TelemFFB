a = Analysis(['main.py'],
             pathex=[],
             hiddenimports=[],
             hookspath=None,
             runtime_hooks=None,
             )
options = []
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          options,
          name='main',
          debug=False,
          strip=None,
          upx=True,
          console=True,
          #icon=os.path.join(gooey_root, 'images', 'program_icon.ico')
      )
